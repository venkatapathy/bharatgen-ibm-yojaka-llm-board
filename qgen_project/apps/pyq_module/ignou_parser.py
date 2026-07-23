"""IGNOU exam-paper helpers: sub-part expansion and instruction filtering."""

from __future__ import annotations

import re

# Parent prompts that are not standalone questions (choose N of following).
_INSTRUCTION_PATTERNS = [
    r"^attempt any\b",
    r"^answer any\b",
    r"^write short notes on any\b",
    r"^explain any\b",
    r"^explain with reference to context any\b",
    r"^write short notes on any two\b",
    r"^note\s*:",
    r"^section[\s—\-]*[a-z]\b",
    r"^time\s*:\s*\d",
    r"^maximum marks\b",
    r"^all questions are compulsory\b",
    r"^all sections are compulsory\b",
    r"^this paper has three sections\b",
    r"^term-end examination\b",
    r"^no\. of printed pages\b",
    # Hindi assignment / TEE boilerplate
    r"^नोट\s*[:：]",
    r"^प्रिय\s+छात्र",
    r"^किन्हीं\s+",
    r"^निम्नलिखित\s+में\s+से\s+किन्हीं",
    r"^सत्रीय\s+कार्य",
    r"^समय\s*[:：]",
    r"^अधिकतम\s+अंक",
    r"^ऐच्छिक\s+पाठ्यक्रम",
    r"^अपनी\s+उत्तर\s+पुस्तिका",
    r"उत्तर\s+पुस्तिकाओं\s+के",
    r"उटार\s+पुस्तिका",
    r"अपनी\s+उटार\s+पुस्तिका",
    r"अनुक्रमांक",
    r"कार्यक्रम\s+दर्शिका",
    r"काय़क्रम\s+द[जश]ि़का",
    r"प्रत्येक\s+उत्तर\s+के\s+पहले",
    r"प्रऋयेक\s+उटार\s+के\s+पहले",
    r"अपनी\s+ही\s+लिखावट",
    r"प्रजन\s+संख्या",
    r"फुलस्केप",
    r"अध्ययन\s+केन्द्र|अद्यन\s+केन्द्र",
    r"सत्रीय\s+कार्य\s+पूरा|सत्ीय\s+काय़\s+पूरा",
    r"सबसे\s+पहले\s+सत्ीय|सबसे\s+पहले\s+सत्रीय",
    r"^अद्यन\s*[:：ः]",
    r"^अध्ययन\s*[:：ः]",
    r"^अभ्यास\s*[:：ः]",
    r"^प्रस्तुति\s*[:：ः]",
    r"अभ्यास\s*[:：ः]\s*अपने",
    r"अभ्यास\s*[:：ः]\s*उ",
    r"प्रिय\s+छात्",
    r"पाठ्यक्रम\s+काेड",
    r"जब\s+आप\s+अपने\s+उ[टऋ]",
]

# Loose match for "Answer any three of the following questions (in about 600 words each)"
_INSTRUCTION_LOOSE = [
    r"attempt any \w+ of the following",
    r"answer any \w+ of the following",
    r"write short notes on any \w+ of the following",
    r"explain any \w+ of the following",
    r"explain with reference to context any \w+ of the following",
    r"in about \d+ words each\s*:?\s*$",
    r"किन्हीं\s+\S+\s+प्रश्नों?\s+के\s+उत्तर",
    r"निम्नलिखित\s+में\s+से\s+किन्हीं",
    r"सप्रसंग\s+व्याख्या",
    r"उत्तर\s+पुस्तिका",
    r"पाठ्यक्रम\s+का\s+शीर्षक",
    r"पाठ्यक्रम\s+का\s+षीषा़क",
]

_DEV_DIGIT = "०१२३४५६७८९"
_ASCII_FROM_DEV = str.maketrans(_DEV_DIGIT, "0123456789")
_SUBPART_LABEL = r"[a-zA-Z]|[क-ह]"
_QNUM = r"[0-9०-९]+"


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _normalize_num(num: str) -> str:
    return (num or "?").translate(_ASCII_FROM_DEV)


def clean_exam_text(text: str) -> str:
    """Remove IGNOU PDF boilerplate that breaks parsing."""
    cleaned = text.replace("\r\n", "\n")
    cleaned = re.sub(r"\[\s*\d+\s*\]", " ", cleaned)
    cleaned = re.sub(r"P\.\s*T\.\s*O\.", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"[A-Z]–\d+/BEGC–\d+", " ", cleaned)
    cleaned = re.sub(r"B–\d+/BEGC–\d+", " ", cleaned)
    cleaned = re.sub(r"C–\d+/BEGC–\d+", " ", cleaned)
    cleaned = re.sub(r"D–\d+/BEGC–\d+", " ", cleaned)
    cleaned = re.sub(r"[A-Z]–\d+/BHD[A-Z]*–\d+", " ", cleaned)
    cleaned = re.sub(r"×\s*×\s*×.*", "", cleaned)
    cleaned = re.sub(r"No\. of Printed Pages\s*:\s*\d+", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"कुल\s+मुद्रित\s+पृष्ठों?\s+की\s+संख्या\s*[:：]?\s*\S+", " ", cleaned)
    return cleaned


def is_instruction_question(text: str) -> bool:
    """Return True when text is a section/parent instruction, not a real question."""
    collapsed = _collapse_ws(text).lower()
    if not collapsed or len(collapsed) < 8:
        return True
    if collapsed.endswith(":") and len(collapsed.split()) < 18:
        return True
    if collapsed.endswith("：") and len(collapsed.split()) < 18:
        return True
    for pattern in _INSTRUCTION_PATTERNS:
        if re.search(pattern, collapsed, flags=re.I):
            return True
    for pattern in _INSTRUCTION_LOOSE:
        if re.search(pattern, collapsed, flags=re.I):
            return True
    # Parent stem with no substantive content beyond "any N of the following"
    if re.search(r"any \w+ of the following", collapsed) and len(collapsed.split()) < 22:
        return True
    return False


def _marks_from_context(context: str, default: float = 10.0) -> float:
    match = re.search(r"(\d+)\s*[×xX]\s*(\d+)", context)
    if match:
        return float(match.group(2))
    match = re.search(r"([0-9०-९]+)\s*[×xX×]\s*([0-9०-९]+)", context)
    if match:
        return float(match.group(2).translate(_ASCII_FROM_DEV))
    match = re.search(r"(\d+)\s*marks?", context, re.I)
    if match:
        return float(match.group(1))
    match = re.search(r"([0-9०-९]+)\s*अंक", context)
    if match:
        return float(match.group(1).translate(_ASCII_FROM_DEV))
    return default


def _question_type_for_marks(marks: float) -> str:
    if marks <= 5:
        return "SHORT"
    if marks <= 10:
        return "SHORT"
    return "LONG"


def _extract_subpart_blocks(text: str) -> list[dict]:
    """Pull (a)(b)(c)… / (क)(ख)… items grouped under numbered IGNOU parents."""
    results: list[dict] = []
    normalized = clean_exam_text(text)

    subpart_iter = list(
        re.finditer(
            rf"\(({_SUBPART_LABEL})\)\s*(.+?)"
            rf"(?=\s*\(({_SUBPART_LABEL})\)\s*|\s*{_QNUM}[\.\-–—]\s+|\s*Section[\s—\-]|\s*×\s*×|$)",
            normalized,
            re.IGNORECASE | re.DOTALL,
        )
    )
    if not subpart_iter:
        return results

    for sub_match in subpart_iter:
        label = sub_match.group(1).lower()
        body = _collapse_ws(sub_match.group(2))
        body = re.sub(r"BEGC–\d+\s*$", "", body).strip()
        body = re.sub(r"BHD[A-Z]*–\d+\s*$", "", body).strip()
        if len(body) < 8 or is_instruction_question(body):
            continue

        prefix = normalized[max(0, sub_match.start() - 800) : sub_match.start()]
        parent_match = re.findall(rf"({_QNUM})[\.\-–—]\s*([^\n]+)", prefix)
        parent_num = _normalize_num(parent_match[-1][0]) if parent_match else "?"
        parent_head = parent_match[-1][1] if parent_match else ""
        marks = _marks_from_context(prefix + " " + parent_head + " " + body)

        results.append(
            {
                "question_text": body,
                "question_type": _question_type_for_marks(marks),
                "bloom": "understand",
                "marks": marks,
                "topic": f"Q{parent_num}({label})",
                "options": [],
                "reference_answer": "",
            }
        )
    return results


def _extract_numbered_questions(text: str) -> list[dict]:
    """Pull standalone numbered questions without (a)(b)/(क)(ख) children."""
    results: list[dict] = []
    normalized = clean_exam_text(text)

    for match in re.finditer(
        rf"(?P<num>{_QNUM})[\.\-–—]\s*(?P<body>.+?)"
        rf"(?=\n\s*{_QNUM}[\.\-–—]\s*|\n\s*Section[\s—\-]|\n\s*×\s*×|$)",
        normalized,
        re.DOTALL | re.IGNORECASE,
    ):
        body = match.group("body")
        if re.search(rf"\(({_SUBPART_LABEL})\)\s", body, re.I):
            continue
        collapsed = _collapse_ws(body)
        collapsed = re.sub(r"BEGC–\d+\s*$", "", collapsed).strip()
        collapsed = re.sub(r"BHD[A-Z]*–\d+\s*$", "", collapsed).strip()
        if len(collapsed) < 20 or is_instruction_question(collapsed):
            continue
        marks = _marks_from_context(body)
        results.append(
            {
                "question_text": collapsed,
                "question_type": _question_type_for_marks(marks),
                "bloom": "analyse",
                "marks": marks,
                "topic": f"Q{_normalize_num(match.group('num'))}",
                "options": [],
                "reference_answer": "",
            }
        )
    return results


def extract_ignou_questions(text: str) -> list[dict]:
    """Rule-based IGNOU extraction: sub-parts first, then standalone items."""
    subparts = _extract_subpart_blocks(text)
    standalone = _extract_numbered_questions(text)
    return dedupe_questions(subparts + standalone)


def dedupe_questions(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        key = _collapse_ws(item.get("question_text", "")).lower()[:160]
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def merge_extracted_questions(llm_items: list[dict], rule_items: list[dict]) -> list[dict]:
    """
    Combine rule-based + LLM extractions.
    Rule-based sub-parts are preferred; LLM fills gaps with real questions only.
    """
    merged = []
    for item in rule_items:
        if not is_instruction_question(item.get("question_text", "")):
            merged.append(item)

    rule_keys = {_collapse_ws(q["question_text"]).lower()[:80] for q in merged}
    for item in llm_items:
        text = item.get("question_text", "")
        if is_instruction_question(text):
            continue
        key = _collapse_ws(text).lower()[:80]
        if any(key in rk or rk in key for rk in rule_keys if len(rk) > 20):
            continue
        merged.append(item)
        rule_keys.add(key[:80])

    # If rule-based found structured sub-parts, drop LLM parent stems still present.
    if any(re.search(r"\([a-zक-ह]\)", q.get("topic", ""), re.I) for q in rule_items):
        merged = [
            q for q in merged
            if not is_instruction_question(q.get("question_text", ""))
        ]

    return dedupe_questions(merged)
