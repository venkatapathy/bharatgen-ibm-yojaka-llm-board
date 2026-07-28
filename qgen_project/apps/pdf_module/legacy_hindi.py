"""Normalize legacy Hindi font encodings (KrutiDev / Chanakya) to Unicode Devanagari."""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Protect Latin/English spans so KrutiDev remap does not scramble them.
# Require 3+ letters so IGNOU sub-labels like (d)/(x) still convert to (क)/(ग).
_LATIN_PAREN = re.compile(
    r"(?:¼|\()([A-Za-z]{3,}[A-Za-z0-9 ./\-&,']{0,77})(?:½|\))"
)
_COURSE_CODE = re.compile(r"\b(?:BHD(?:C|LA|S)|BEGC|BHDS)[-\s]?\d+\b", re.I)
_URL_OR_EMAIL = re.compile(
    r"\b(?:https?://|www\.)[^\s]+|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
# Private-use placeholders so KrutiDev remapping cannot touch index digits.
_PH_START = "\ue000"
_PH_END = "\ue001"
_PH_BASE = 0xE010

# Common IGNOU / eGyanKosh embedded font names for KrutiDev-family glyphs.
_LEGACY_FONT_NAMES = re.compile(
    r"(?i)^(k|krutidev|kruti|chanakya|devlys|walkman|mangal|shusha|dv-tt|kokila)"
)

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _protect_latin_spans(text: str) -> Tuple[str, list[str]]:
    holders: list[str] = []

    def _store(match: re.Match) -> str:
        idx = len(holders)
        holders.append(match.group(0))
        return f"{_PH_START}{chr(_PH_BASE + idx)}{_PH_END}"

    protected = _URL_OR_EMAIL.sub(_store, text)
    protected = _COURSE_CODE.sub(_store, protected)
    protected = _LATIN_PAREN.sub(_store, protected)
    return protected, holders


def _restore_latin_spans(text: str, holders: list[str]) -> str:
    for idx, original in enumerate(holders):
        token = f"{_PH_START}{chr(_PH_BASE + idx)}{_PH_END}"
        restored = original.replace("¼", "(").replace("½", ")")
        text = text.replace(token, restored)
    text = text.replace("¼", "(").replace("½", ")")
    return text


def page_uses_legacy_hindi_font(page) -> bool:
    """True when a PyMuPDF page mostly uses legacy Hindi glyph fonts."""
    try:
        data = page.get_text("dict")
    except Exception:
        return False
    legacy_chars = 0
    total_chars = 0
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_text = span.get("text") or ""
                if not span_text.strip():
                    continue
                n = len(span_text)
                total_chars += n
                font = span.get("font") or ""
                if _LEGACY_FONT_NAMES.search(font.split("+")[-1].strip()):
                    legacy_chars += n
    if total_chars < 40:
        return False
    return (legacy_chars / total_chars) >= 0.35


def looks_like_legacy_hindi(text: str) -> Tuple[bool, str]:
    """Heuristic: returns (is_legacy, font_type)."""
    if not text or not text.strip():
        return False, "unknown"
    if len(_DEVANAGARI_RE.findall(text)) / max(len(text), 1) > 0.25:
        return False, "unknown"

    # Clear English must never be remapped (lipi false-positives on BA English PDFs).
    try:
        from .latin_encoding import _english_score

        if _english_score(text) >= 0.12:
            return False, "unknown"
    except Exception:
        pass

    try:
        from lipi import HindiPreprocessor

        has_legacy, font_type = HindiPreprocessor.detect_encoding(text)
        if has_legacy and font_type != "unknown":
            # Re-check after lipi says yes — protect English body text.
            try:
                from .latin_encoding import _english_score

                if _english_score(text) >= 0.10:
                    return False, "unknown"
            except Exception:
                pass
            return True, font_type
    except Exception as exc:
        logger.debug("lipi detect_encoding failed: %s", exc)

    # Fallback fingerprints for shorter page extracts that fail lipi's threshold.
    fingerprints = ("kk", "kh", "gS", "gSa", "fd", "esa", "dks", "vkSj", "Hkh", "'k", "/k", "Hk")
    score = sum(text.count(p) for p in fingerprints)
    latin_letters = sum(1 for ch in text if "A" <= ch <= "Z" or "a" <= ch <= "z")
    if score >= 3 and latin_letters > 40 and len(_DEVANAGARI_RE.findall(text)) < 5:
        try:
            from .latin_encoding import _english_score

            if _english_score(text) >= 0.10:
                return False, "unknown"
        except Exception:
            pass
        return True, "krutidev"
    return False, "unknown"


def normalize_legacy_hindi(
    text: str,
    *,
    force: bool = False,
    font_type: Optional[str] = None,
) -> str:
    """
    Convert KrutiDev/Chanakya glyph text to Unicode Devanagari when detected.

    English spans in ¼…½ / (…) and course codes are preserved.
    """
    if not text:
        return text

    detected_font = font_type
    if not force:
        is_legacy, detected = looks_like_legacy_hindi(text)
        if not is_legacy:
            return text
        detected_font = detected_font or detected
    detected_font = detected_font or "krutidev"
    if detected_font in ("unknown", "none", "scrambled_devanagari"):
        detected_font = "krutidev"

    try:
        from lipi import HindiPreprocessor
    except ImportError:
        logger.warning("lipi-aparsoft not installed; skipping Hindi font remap")
        return text

    protected, holders = _protect_latin_spans(text)
    try:
        converted = HindiPreprocessor.convert(protected, font_type=detected_font)
        converted = HindiPreprocessor.post_process(
            converted,
            repair_doubled_consonant_imatra=detected_font
            in ("krutidev", "chanakya", "walkman_chanakya"),
        )
    except Exception as exc:
        logger.warning("Legacy Hindi conversion failed: %s", exc)
        return text
    return _restore_latin_spans(converted, holders)
