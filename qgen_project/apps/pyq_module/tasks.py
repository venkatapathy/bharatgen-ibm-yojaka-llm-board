import ast
import json
import logging
import re
import time

from celery import shared_task

from apps.core.llm import completion_with_retry, get_litellm_kwargs

from .ignou_parser import is_instruction_question
from .models import BloomLevel, PYQModule, Question, QuestionType, normalize_question_type
from .pdf_text import chunk_text, extract_text

logger = logging.getLogger(__name__)

VALID_QUESTION_TYPES = {choice.value for choice in QuestionType}
VALID_BLOOM_LEVELS = {choice.value for choice in BloomLevel}

EXTRACTION_PROMPT = """
You extract exam / assignment questions from OCR/text of a past-year question (PYQ) paper.

Works for any board/university paper — any language or script (English, Hindi, bilingual).

Goals:
1. Extract EVERY real question students must answer.
2. Expand sub-parts into FULL standalone questions (critical):
   - If a stem has (a)/(b)/(c) or (i)/(ii) or क/ख/ग choices, emit ONE question per
     choice, rewriting the parent instruction into that item so it stands alone.
   - Also expand "answer/write/explain any N of the following" lists the same way.
   - NEVER emit bare topic titles alone (e.g. "Peripetia"). NEVER emit the parent
     instruction alone (e.g. "Write short notes on any two of the following…").
   Examples of correct expansion:
     Stem: "Write short notes on any two of the following in about 250 words each"
     Options: (a) Peripetia  (b) Unity of Time
     → "Write a short note on Peripetia in about 250 words."
     → "Write a short note on Unity of Time in about 250 words."
     Stem: "Explain any four of the following passages with reference to the context…"
     Option (a): <passage quote>
     → "Explain the passage: '<passage quote>'"
     Numbered essay: "3. Comment on the ending of the Iliad." + "350-400 words"
     → "Comment on the ending of the Iliad in about 350-400 words."
3. Skip non-questions: headers, course codes alone, Time/Maximum Marks, section
   titles with no body, mark-scheme-only lines (e.g. 2×10=20), page footers,
   "Note: This paper has three Sections…", and OCR instruction noise.
4. Keep the original language. Do not invent content.
5. question_type: RTC|SHORT|LONG
   - RTC = reference-to-context (passage + explain with reference to context)
   - SHORT = short note / definition / identification
   - LONG = thematic / analytical / comparative / critical essay / long answer
6. bloom: remember|understand|apply|analyze|evaluate|create
7. marks: per-item marks when stated (from N×M=… use M); else infer, else 10.
8. topic: short label like "2(a)" or "3" (question number).
9. options: always [] (this demo does not use MCQ).
10. reference_answer: "" unless an answer key is present.

Return ONLY a JSON array. Each element:
  question_text, question_type, bloom, marks, topic, options, reference_answer

If a chunk has no extractable questions, return [].
"""

# Noun-ish stubs left after a parent stem was split off (not real questions).
_BARE_TOPIC_RE = re.compile(
    r"^(?:[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,5}"
    r"|[क-ह]\S*(?:\s+\S+){0,5})$"
)
_HAS_QUESTION_VERB = re.compile(
    r"\b(explain|write|comment|illustrate|discuss|describe|analyse|analyze|"
    r"evaluate|compare|contrast|examine|outline|define|how|why|what|would|"
    r"do you|is there|are there|can you|elaborate|substantiate|attempt|"
    r"व्याख्या|लिख|विवेचना|विश्लेषण|तुलना|क्यों|क्या|कैसे|सप्रसंग)\b",
    re.I,
)


def _strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def parse_llm_json(raw_text):
    raw_text = _strip_control_chars(raw_text.strip())
    raw_text = re.sub(r"<think>[\s\S]*?</think>", "", raw_text).strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    def _load(candidate: str):
        cleaned = _strip_control_chars(candidate)
        # strict=False: tolerate literal newlines inside JSON strings from LLMs
        try:
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(cleaned)
            except (ValueError, SyntaxError, MemoryError) as exc:
                raise json.JSONDecodeError(
                    f"json.loads and ast.literal_eval failed: {exc}",
                    cleaned,
                    0,
                ) from exc

    candidates = [
        raw_text,
        re.sub(r",\s*([}\]])", r"\1", raw_text),
    ]
    for candidate in candidates:
        try:
            parsed = _load(candidate)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            continue

    match = re.search(r"\[[\s\S]*\]", raw_text)
    if match:
        snippet = re.sub(r",\s*([}\]])", r"\1", match.group(0))
        try:
            parsed = _load(snippet)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not parse LLM JSON", raw_text, 0)


def normalize_extracted_question(item: dict) -> dict:
    try:
        marks = float(item.get("marks", 10) or 10)
    except (TypeError, ValueError):
        marks = 10.0

    question_text = str(item.get("question_text", "")).strip()
    question_type = normalize_question_type(
        item.get("question_type", "SHORT"),
        marks=marks,
        question_text=question_text,
    )

    bloom = str(item.get("bloom", "remember")).lower()
    if bloom not in VALID_BLOOM_LEVELS:
        bloom = BloomLevel.REMEMBER

    return {
        "question_text": question_text,
        "question_type": question_type,
        "bloom": bloom,
        "marks": marks,
        "topic": str(item.get("topic", "")).strip()[:256],
        "options": item.get("options", []) or [],
        "reference_answer": str(item.get("reference_answer", "")).strip(),
    }


def looks_like_instruction_block(text: str) -> bool:
    """True for cover/section boilerplate or parent 'any N of following' stems."""
    return is_instruction_question(text or "")


def looks_like_bare_topic(text: str) -> bool:
    """Reject orphan titles like 'Peripetia' without a real question stem."""
    stripped = re.sub(r"\s+", " ", (text or "").strip())
    if not stripped:
        return True
    words = stripped.split()
    if len(words) <= 6 and not _HAS_QUESTION_VERB.search(stripped):
        if _BARE_TOPIC_RE.match(stripped) or len(words) <= 4:
            return True
    return False


def _dedupe_questions(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        key = re.sub(r"\s+", " ", item.get("question_text", "")).strip().lower()[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _build_extract_chunks(pages: list[dict]) -> list[str]:
    """Prefer one full-paper chunk (like unlimited-ocr) when the QP is short."""
    full = "\n\n".join(p["text"] for p in pages if p.get("text"))
    words = len(full.split())
    # Typical TEE paper fits one shot; matching output_begc102_dec25 pipeline.
    if words <= 3500:
        return [full]
    return chunk_text(full, max_words=1200)


@shared_task(bind=True, max_retries=3)
def extract_pyq_questions(self, pyq_module_id: int, model_config_id: int):
    from apps.core.models import ModelConfig

    mod = PYQModule.objects.get(id=pyq_module_id)
    config = ModelConfig.objects.get(id=model_config_id)
    mod.status = "extracting"
    mod.error_msg = ""
    mod.save(update_fields=["status", "error_msg"])

    try:
        pages = extract_text(mod.source_file.path)
        text_chunks = _build_extract_chunks(pages)
        llm_extracted = []

        for i, text_chunk in enumerate(text_chunks[:6]):
            if i:
                time.sleep(1)
            try:
                response = completion_with_retry(
                    messages=[
                        {"role": "system", "content": EXTRACTION_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                "Extract all exam questions from this PYQ paper text. "
                                "Expand every sub-part into a full standalone question. "
                                "Return JSON only.\n\n"
                                f"{text_chunk[:20000]}"
                            ),
                        },
                    ],
                    max_retries=2,
                    **get_litellm_kwargs(config, temperature=0.1, max_tokens=8192),
                )
            except Exception as llm_exc:
                logger.warning(
                    "PYQ LLM extraction failed for %s chunk %s: %s",
                    mod.name,
                    i,
                    llm_exc,
                )
                continue
            raw = (response.choices[0].message.content or "").strip()
            try:
                llm_extracted.extend(parse_llm_json(raw))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "PYQ chunk JSON parse failed for %s chunk %s: %s",
                    mod.name,
                    i,
                    exc,
                )

        extracted = _dedupe_questions(
            [
                normalize_extracted_question(q)
                for q in llm_extracted
                if isinstance(q, dict) and q.get("question_text")
            ]
        )

        if not extracted:
            raise ValueError("No questions could be extracted from the PDF")

        questions = []
        for q in extracted:
            text = q.get("question_text") or ""
            if looks_like_instruction_block(text) or looks_like_bare_topic(text):
                continue
            questions.append(
                Question(
                    pyq_module=mod,
                    is_generated=False,
                    rag_chunks=[],
                    pyq_examples=[],
                    user_decision=Question.UserDecision.PENDING,
                    user_feedback="",
                    **q,
                )
            )
        if not questions:
            raise ValueError("No questions could be extracted from the PDF")
        Question.objects.filter(pyq_module=mod).delete()
        Question.objects.bulk_create(questions)
        mod.status = "ready"
        mod.error_msg = ""
        mod.save(update_fields=["status", "error_msg"])

    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30)
        mod.status = "error"
        mod.error_msg = str(exc)
        mod.save(update_fields=["status", "error_msg"])
