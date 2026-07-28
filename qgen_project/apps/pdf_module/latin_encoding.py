"""Repair Latin PDF text extracted with a shifted / broken ToUnicode cmap.

Some IGNOU English PDFs (e.g. BEGC 102) extract as Caesar-shifted letters
(A→D) with punctuation/digits collapsed into C0 control bytes (codepoint = ASCII-29).
"""

from __future__ import annotations

import re

_COMMON_EN = {
    "the", "and", "for", "that", "with", "this", "from", "are", "was", "were",
    "have", "been", "his", "her", "their", "which", "while", "about", "into",
    "over", "after", "before", "upon", "than", "then", "when", "where", "what",
    "who", "how", "not", "but", "can", "may", "will", "would", "should", "could",
    "must", "also", "only", "more", "most", "some", "such", "each", "both",
    "between", "through", "during", "without", "within", "english", "literature",
    "unit", "block", "page", "chapter", "student", "course", "university",
    "national", "open", "school", "humanities", "hero", "story", "personal",
}

_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _english_score(text: str) -> float:
    words = _WORD_RE.findall(text or "")
    if len(words) < 12:
        return 0.0
    hits = sum(1 for w in words if w.lower() in _COMMON_EN)
    return hits / len(words)


def _shift_latin_minus_3(text: str) -> str:
    out: list[str] = []
    for ch in text:
        o = ord(ch)
        if ch in "\t\n\r":
            out.append(" " if ch == "\t" else ch)
        elif 1 <= o <= 31:
            restored = o + 29
            out.append(chr(restored) if 32 <= restored <= 126 else ch)
        elif 65 <= o <= 90:
            out.append(chr((o - 65 - 3) % 26 + 65))
        elif 97 <= o <= 122:
            out.append(chr((o - 97 - 3) % 26 + 97))
        elif 91 <= o <= 96:
            # Y/Z overflow: Y+3='\\', Z+3=']'
            out.append(chr(o - 3))
        elif 33 <= o <= 64:
            # Encoded letters/punct landed in !"#$%&'()*+...@
            restored = o + 29
            out.append(chr(restored) if restored <= 126 else ch)
        else:
            out.append(ch)
    return "".join(out)


def looks_like_shifted_latin(text: str) -> bool:
    """True when Latin text looks English-garbled (low common-word rate)."""
    if not text or len(text) < 80:
        return False
    latin = len(_LATIN_RE.findall(text))
    if latin < 40 or latin / len(text) < 0.35:
        return False
    # Control bytes used as spaces/punct are a strong signal.
    controls = sum(1 for ch in text if 1 <= ord(ch) <= 31)
    score = _english_score(text)
    if controls >= 8 and score < 0.12:
        return True
    return score < 0.08 and latin >= 80


def repair_shifted_latin_text(text: str) -> str:
    """Decode shifted Latin extraction when it clearly improves English score."""
    if not text or not looks_like_shifted_latin(text):
        return text
    repaired = _shift_latin_minus_3(text)
    if _english_score(repaired) >= max(_english_score(text) + 0.08, 0.12):
        return repaired
    return text
