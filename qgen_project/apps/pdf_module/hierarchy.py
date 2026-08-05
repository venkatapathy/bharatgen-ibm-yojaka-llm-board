"""Parse unitwise PDF contexts into Degree / Course / Block / Unit hierarchy."""

from __future__ import annotations

import re
from typing import Any

# (BA ENGLISH / BEGC 101_ Indian Classical Literature)
_DESC_RE = re.compile(
    r"\((BA ENGLISH|BA HINDI)\s*/\s*([^)]+)\)",
    re.IGNORECASE,
)
# Block-1_Unit2 | BLOCK 1_Unit1_TITLE | Block1_Unit1_TITLE
_BLOCK_UNIT_RE = re.compile(
    r"(?:Block[-\s]?|BLOCK\s*)(\d+)\s*[_\s-]*Unit\s*(\d+)(?:[_\s-]+(.+))?",
    re.IGNORECASE,
)


def _course_label(course_folder: str) -> str:
    cleaned = re.sub(r"^\d+\.", "", (course_folder or "").strip()).strip()
    if "_" in cleaned:
        code, title = cleaned.split("_", 1)
        return f"{code.strip()} - {title.strip()}"
    return cleaned or "Unknown course"


def _unit_title(rest: str | None, fallback: str) -> str:
    if not rest:
        return fallback
    title = rest.rsplit(".", 1)[0].strip(" _-")
    title = title.replace("_", " ").strip()
    return title or fallback


def parse_pdf_hierarchy(ctx) -> dict[str, str] | None:
    """
    Return degree/course/block/unit labels for a PDFContext, or None if unparseable.
    """
    desc = ctx.description or ""
    m = _DESC_RE.search(desc)
    if not m:
        return None
    degree = m.group(1).strip()
    course = _course_label(m.group(2).strip())

    source = ctx.original_filename or ctx.name or ""
    bu = _BLOCK_UNIT_RE.search(source)
    if not bu:
        bu = _BLOCK_UNIT_RE.search(ctx.name or "")
    if not bu:
        return None

    block_n = bu.group(1)
    unit_n = bu.group(2)
    title = _unit_title(bu.group(3), f"Unit {unit_n}")
    block = f"Block {block_n}"
    unit = f"Unit {unit_n}"
    unit_label = f"{unit} — {title}" if title.lower() != unit.lower() else unit
    return {
        "id": str(ctx.id),
        "name": ctx.name,
        "degree": degree,
        "course": course,
        "block": block,
        "unit": unit,
        "unit_label": unit_label,
    }


def build_pdf_hierarchy_rows(contexts) -> list[dict[str, Any]]:
    """Flat list of parseable contexts for Alpine cascading selects."""
    rows: list[dict[str, Any]] = []
    for ctx in contexts:
        parsed = parse_pdf_hierarchy(ctx)
        if parsed:
            rows.append(parsed)
    rows.sort(key=lambda r: (r["degree"], r["course"], r["block"], r["unit"], r["name"]))
    return rows
