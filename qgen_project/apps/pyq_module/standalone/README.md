# Extract structured questions from OCR text

Standalone guide for someone who **already has OCR** of a past-year question paper and wants a clean JSON list of questions (with sub-parts expanded).

This is the same logic used inside EduQGen (`apps/pyq_module/tasks.py`), without Django/Celery.

---

## What you get

Each question object:

```json
{
  "question_text": "Write a short note on Peripetia in about 250 words.",
  "question_type": "SHORT",
  "bloom": "understand",
  "marks": 5.0,
  "topic": "2(a)",
  "options": [],
  "reference_answer": ""
}
```

`question_type` is one of: `RTC` | `SHORT` | `LONG`.

---

## Quick start

```bash
pip install litellm

# Ollama example
export OLLAMA_API_BASE=http://127.0.0.1:11434

python extract_pyq_from_ocr.py paper_ocr.txt -o questions.json
python extract_pyq_from_ocr.py paper_ocr.txt --model ollama/gemma2:9b -o out.json
```

Input: plain `.txt` with full paper OCR (page markers like `===== PAGE 1 =====` are fine).

---

## Repo files (same code)

| File | Role |
|------|------|
| `apps/pyq_module/standalone/extract_pyq_from_ocr.py` | **Runnable script** (copy this) |
| `apps/pyq_module/tasks.py` | In-app Celery version (`EXTRACTION_PROMPT`, `extract_pyq_questions`) |
| `apps/pyq_module/ignou_parser.py` | Instruction / boilerplate filters |

---

## Full code (copy-paste)

Save as `extract_pyq_from_ocr.py`:

```python
#!/usr/bin/env python3
"""Extract structured exam questions from OCR / plain text (standalone).

No Django / Celery required. Needs: pip install litellm

Usage:
  export OLLAMA_API_BASE=http://127.0.0.1:11434
  python extract_pyq_from_ocr.py paper_ocr.txt -o questions.json
  python extract_pyq_from_ocr.py paper_ocr.txt --model ollama/gemma2:9b -o out.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time

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

VALID_TYPES = {"RTC", "SHORT", "LONG"}
VALID_BLOOM = {
    "remember", "understand", "apply", "analyze", "analyse", "evaluate", "create",
}

TYPE_ALIASES = {
    "RTC": "RTC", "REF": "RTC", "REFERENCE": "RTC", "REFERENCE-TO-CONTEXT": "RTC",
    "SHORT": "SHORT", "SHORT-ANSWER": "SHORT", "SHORT NOTE": "SHORT",
    "LONG": "LONG", "LONG-ANSWER": "LONG", "ESSAY": "LONG", "THEMATIC": "LONG",
}

_INSTRUCTION_PATTERNS = [
    r"^attempt any\b", r"^answer any\b", r"^write short notes on any\b",
    r"^explain any\b", r"^note\s*:", r"^section[\s—\-]*[a-z]\b",
    r"^time\s*:\s*\d", r"^maximum marks\b", r"^all questions are compulsory\b",
    r"^term-end examination\b", r"^नोट\s*[:：]", r"^किन्हीं\s+",
    r"^समय\s*[:：]", r"^अधिकतम\s+अंक",
]

_INSTRUCTION_LOOSE = [
    r"attempt any \w+ of the following",
    r"answer any \w+ of the following",
    r"write short notes on any \w+ of the following",
    r"explain any \w+ of the following",
    r"किन्हीं\s+\S+\s+प्रश्नों?\s+के\s+उत्तर",
]

_BARE_TOPIC_RE = re.compile(
    r"^(?:[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,5}"
    r"|[क-ह]\S*(?:\s+\S+){0,5})$"
)
_HAS_QUESTION_VERB = re.compile(
    r"\b(explain|write|comment|illustrate|discuss|describe|analyse|analyze|"
    r"evaluate|compare|contrast|examine|outline|define|how|why|what|would|"
    r"do you|elaborate|substantiate|attempt|"
    r"व्याख्या|लिख|विवेचना|विश्लेषण|तुलना|क्यों|क्या|कैसे|सप्रसंग)\b",
    re.I,
)


def _strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def parse_llm_json(raw_text: str):
    raw_text = _strip_control_chars(raw_text.strip())
    raw_text = re.sub(r"<think>[\s\S]*?</think>", "", raw_text).strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    def _load(candidate: str):
        cleaned = _strip_control_chars(candidate)
        try:
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            return ast.literal_eval(cleaned)

    for candidate in [raw_text, re.sub(r",\s*([}\]])", r"\1", raw_text)]:
        try:
            parsed = _load(candidate)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except (json.JSONDecodeError, ValueError, SyntaxError, MemoryError):
            continue

    match = re.search(r"\[[\s\S]*\]", raw_text)
    if match:
        snippet = re.sub(r",\s*([}\]])", r"\1", match.group(0))
        parsed = _load(snippet)
        return parsed if isinstance(parsed, list) else [parsed]

    raise json.JSONDecodeError("Could not parse LLM JSON", raw_text, 0)


def normalize_question_type(value, *, marks=None, question_text="") -> str:
    raw = str(value or "").strip()
    key = re.sub(r"\s+", " ", raw).upper().replace("_", "-")
    if key in TYPE_ALIASES:
        return TYPE_ALIASES[key]
    text = (question_text or "").lower()
    if any(p in text for p in ("reference to context", "with reference to context", "सप्रसंग")):
        return "RTC"
    try:
        m = float(marks) if marks is not None else None
    except (TypeError, ValueError):
        m = None
    if m is not None and m > 10:
        return "LONG"
    return "SHORT"


def normalize_extracted_question(item: dict) -> dict:
    try:
        marks = float(item.get("marks", 10) or 10)
    except (TypeError, ValueError):
        marks = 10.0
    question_text = str(item.get("question_text", "")).strip()
    question_type = normalize_question_type(
        item.get("question_type", "SHORT"), marks=marks, question_text=question_text
    )
    bloom = str(item.get("bloom", "remember")).lower().replace("analyse", "analyze")
    if bloom not in VALID_BLOOM:
        bloom = "remember"
    return {
        "question_text": question_text,
        "question_type": question_type,
        "bloom": bloom,
        "marks": marks,
        "topic": str(item.get("topic", "")).strip()[:256],
        "options": item.get("options", []) or [],
        "reference_answer": str(item.get("reference_answer", "")).strip(),
    }


def is_instruction_question(text: str) -> bool:
    collapsed = re.sub(r"\s+", " ", (text or "").strip()).lower()
    if not collapsed or len(collapsed) < 8:
        return True
    if collapsed.endswith(":") and len(collapsed.split()) < 18:
        return True
    for pattern in _INSTRUCTION_PATTERNS + _INSTRUCTION_LOOSE:
        if re.search(pattern, collapsed, flags=re.I):
            return True
    if re.search(r"any \w+ of the following", collapsed) and len(collapsed.split()) < 22:
        return True
    return False


def looks_like_bare_topic(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", (text or "").strip())
    if not stripped:
        return True
    words = stripped.split()
    if len(words) <= 6 and not _HAS_QUESTION_VERB.search(stripped):
        if _BARE_TOPIC_RE.match(stripped) or len(words) <= 4:
            return True
    return False


def chunk_text(text: str, max_words: int = 1200) -> list[str]:
    parts = [line.strip() for line in re.split(r"\n\s*\n", text) if line.strip()]
    chunks, current, current_words = [], [], 0
    for part in parts:
        words = len(part.split())
        current.append(part)
        current_words += words
        if current_words >= max_words:
            chunks.append("\n\n".join(current))
            current, current_words = [], 0
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def build_chunks(ocr: str) -> list[str]:
    if len(ocr.split()) <= 3500:
        return [ocr]
    return chunk_text(ocr, max_words=1200)


def dedupe_questions(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for item in items:
        key = re.sub(r"\s+", " ", item.get("question_text", "")).strip().lower()[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def call_llm(ocr_chunk: str, *, model: str, api_base: str | None) -> str:
    from litellm import completion

    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": (
                    "Extract all exam questions from this PYQ paper text. "
                    "Expand every sub-part into a full standalone question. "
                    "Return JSON only.\n\n"
                    f"{ocr_chunk[:20000]}"
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 8192,
    }
    if api_base:
        kwargs["api_base"] = api_base
    response = completion(**kwargs)
    return (response.choices[0].message.content or "").strip()


def extract_from_ocr(ocr: str, *, model: str, api_base: str | None) -> list[dict]:
    llm_extracted = []
    for i, chunk in enumerate(build_chunks(ocr.strip())[:6]):
        if i:
            time.sleep(1)
        raw = call_llm(chunk, model=model, api_base=api_base)
        try:
            llm_extracted.extend(parse_llm_json(raw))
        except json.JSONDecodeError as exc:
            print(f"WARN: JSON parse failed on chunk {i}: {exc}", file=sys.stderr)

    cleaned = []
    for q in llm_extracted:
        if not isinstance(q, dict) or not q.get("question_text"):
            continue
        item = normalize_extracted_question(q)
        text = item["question_text"]
        if is_instruction_question(text) or looks_like_bare_topic(text):
            continue
        cleaned.append(item)
    return dedupe_questions(cleaned)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract structured PYQ questions from OCR text")
    parser.add_argument("ocr_file", help="Path to OCR / plain-text file")
    parser.add_argument("-o", "--output", default="questions.json")
    parser.add_argument("--model", default="ollama/gemma2:9b")
    parser.add_argument("--api-base", default=None, help="e.g. http://127.0.0.1:11434")
    args = parser.parse_args()

    ocr = open(args.ocr_file, encoding="utf-8").read()
    if not ocr.strip():
        print("ERROR: OCR file is empty", file=sys.stderr)
        return 1

    questions = extract_from_ocr(ocr, model=args.model, api_base=args.api_base)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(questions)} questions → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Pipeline (what the code does)

1. Read OCR text  
2. Chunk if longer than ~3500 words  
3. Call LLM with `EXTRACTION_PROMPT` (expand sub-parts, skip boilerplate)  
4. Parse JSON (`parse_llm_json`)  
5. Normalize types / marks  
6. Drop instruction lines + bare topic stubs  
7. Dedupe → write `questions.json`

---

## Notes

- **No PDF OCR here** — paste or save OCR as `.txt` first.
- OpenAI-compatible APIs work via LiteLLM (`--model gpt-4o-mini`, etc.).
- For Hindi Kruti Dev garbage in OCR, clean/normalize text before running this.
- In EduQGen the same extraction is queued from **PYQ Bank → Upload / Re-extract**.
