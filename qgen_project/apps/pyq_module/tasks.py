import json
import logging
import re
import time

from celery import shared_task

from apps.core.llm import completion_with_retry, get_litellm_kwargs

from .models import BloomLevel, PYQModule, Question, QuestionType
from .pdf_text import chunk_text, extract_text

logger = logging.getLogger(__name__)

VALID_QUESTION_TYPES = {choice.value for choice in QuestionType}
VALID_BLOOM_LEVELS = {choice.value for choice in BloomLevel}

EXTRACTION_PROMPT = """
You extract exam / assignment questions from past-year question (PYQ) PDFs.

Works for any board, university, competitive exam, or school paper — any language
or script (English, Hindi/Devanagari, bilingual, etc.).

Goals:
1. Extract EVERY real question students must answer. Prefer atomic items over parent stems.
2. Expand sub-parts: if a stem has (a)/(b)/(c) or (i)/(ii)/(iii) or क/ख/ग choices,
   emit each sub-part as its own question with enough stem context to stand alone.
3. Skip non-questions: cover pages, course codes alone, "Time:", "Maximum Marks",
   section titles with no question body, "Answer any N of the following" without
   the actual items, blank pages, and pure mark-scheme lines (e.g. 2×10=20).
4. Keep the original language and script exactly. Do not translate, transliterate,
   or invent content that is not in the source.
5. question_type: MCQ|SHORT|LONG|FILL|TF|CASE|NUM — pick the best fit.
6. bloom: remember|understand|apply|analyze|evaluate|create — best guess from wording.
7. marks: numeric marks for that item when stated; otherwise 1.
8. topic: short label (question number, unit, or theme; max 80 chars).
9. options: list of choice strings for MCQ; otherwise [].
10. reference_answer: only if an answer key is clearly present; otherwise "".

Return ONLY a JSON array. Each element must have:
  question_text, question_type, bloom, marks, topic, options, reference_answer

If a chunk has no extractable questions, return [].
"""


def _strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def parse_llm_json(raw_text):
    raw_text = _strip_control_chars(raw_text.strip())
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    candidates = [
        raw_text,
        re.sub(r",\s*([}\]])", r"\1", raw_text),
    ]
    for candidate in candidates:
        try:
            parsed = json.loads(_strip_control_chars(candidate))
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            continue

    match = re.search(r"\[[\s\S]*\]", raw_text)
    if match:
        snippet = re.sub(r",\s*([}\]])", r"\1", match.group(0))
        parsed = json.loads(_strip_control_chars(snippet))
        return parsed if isinstance(parsed, list) else [parsed]

    raise json.JSONDecodeError("Could not parse LLM JSON", raw_text, 0)


def normalize_extracted_question(item: dict) -> dict:
    question_type = str(item.get("question_type", "SHORT")).upper()
    if question_type not in VALID_QUESTION_TYPES:
        question_type = QuestionType.SHORT

    bloom = str(item.get("bloom", "remember")).lower()
    if bloom not in VALID_BLOOM_LEVELS:
        bloom = BloomLevel.REMEMBER

    try:
        marks = float(item.get("marks", 1) or 1)
    except (TypeError, ValueError):
        marks = 1.0

    return {
        "question_text": str(item.get("question_text", "")).strip(),
        "question_type": question_type,
        "bloom": bloom,
        "marks": marks,
        "topic": str(item.get("topic", "")).strip()[:256],
        "options": item.get("options", []) or [],
        "reference_answer": str(item.get("reference_answer", "")).strip(),
    }


_INSTRUCTION_RE = re.compile(
    r"^(note|time|maximum marks|instructions?|answer any|attempt any|"
    r"section [a-z]|कुल अंक|समय|निर्देश)\b",
    re.I,
)


def looks_like_instruction_block(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    if len(stripped.split()) < 8 and _INSTRUCTION_RE.search(stripped):
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
        filtered_pages = [
            page["text"] for page in pages if not looks_like_instruction_block(page["text"])
        ]
        text_chunks = chunk_text("\n\n".join(filtered_pages) or "\n\n".join(p["text"] for p in pages))
        llm_extracted = []

        for i, text_chunk in enumerate(text_chunks[:8]):
            if i:
                time.sleep(2)
            try:
                response = completion_with_retry(
                    messages=[
                        {"role": "system", "content": EXTRACTION_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                "Extract all exam questions from this PYQ text chunk. "
                                "Return JSON only.\n\n"
                                f"{text_chunk[:12000]}"
                            ),
                        },
                    ],
                    max_retries=2,
                    **get_litellm_kwargs(config, temperature=0.1, max_tokens=3000),
                )
            except Exception as llm_exc:
                logger.warning(
                    "PYQ LLM extraction failed for %s chunk %s: %s",
                    mod.name,
                    i,
                    llm_exc,
                )
                continue
            raw = response.choices[0].message.content.strip()
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
            if not q.get("question_text") or looks_like_instruction_block(q["question_text"]):
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
