import base64
import io
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from PIL import Image

from mm_siglip import fix_image_path
from retrieve_mm import retrieve

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
FIGURES_DIR = BASE_DIR / "outputs" / "ncert_figures_v6"
ALL_BOOKS_FIGURES_DIR = BASE_DIR / "outputs" / "all_books"

for env_path in (
    BASE_DIR.parent.parent / ".env",
    BASE_DIR.parent / ".env",
    BASE_DIR / ".env",
):
    if env_path.is_file():
        load_dotenv(env_path)
        break
else:
    load_dotenv()

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

_figures_lookup: dict[str, dict] | None = None

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

# Keywords that indicate user wants diagram-based questions
DIAGRAM_KEYWORDS = {
    "diagram", "figure", "label", "structure", "draw", "show",
    "mark", "indicate", "arrow", "part", "identify", "name the part"
}

# Max images per vision call to avoid attention dilution
MAX_IMAGES_PER_CALL = 2


# ─────────────────────────────────────────────
# HELPERS (unchanged from original)
# ─────────────────────────────────────────────

def _resolve_image_file(raw: str) -> Path | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    repo_root = BASE_DIR.parent.parent
    cleaned = raw.replace("\\", "/").lstrip("/")
    if cleaned.startswith("backend/mm_rag/"):
        cleaned = cleaned[len("backend/mm_rag/"):]

    candidates = [
        Path(raw),
        BASE_DIR / cleaned,
        repo_root / raw,
        repo_root / cleaned,
        Path(fix_image_path(raw)),
    ]
    for c in candidates:
        try:
            rc = c.resolve()
        except Exception:
            continue
        if rc.is_file():
            return rc
    return None


def _groq_client():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    from groq import Groq
    return Groq(api_key=key)


def _get_figures_lookup() -> dict[str, dict]:
    global _figures_lookup
    if _figures_lookup is None:
        _figures_lookup = {}
        path = BASE_DIR / "outputs" / "figures.json"
        if path.is_file():
            with open(path, encoding="utf-8") as f:
                for fig in json.load(f):
                    ip = fig.get("image_path") or ""
                    bn = os.path.basename(ip)
                    if bn:
                        _figures_lookup[bn] = fig
    return _figures_lookup


def _enrich_figure_rows(results: list[dict]) -> None:
    lookup = _get_figures_lookup()
    for r in results:
        if r.get("type") != "figure":
            continue
        bn = os.path.basename(r.get("image_path") or "")
        meta = lookup.get(bn)
        if meta:
            r["fig_id"]      = meta.get("fig_id")
            r["label"]       = meta.get("label")
            # FIX 6: carry over diagram_type and label_map if stored during ingestion
            r["diagram_type"] = meta.get("diagram_type")
            r["label_map"]    = meta.get("label_map")


def _encode_image_data_url(path: str, max_bytes: int = 3_200_000) -> str | None:
    try:
        im = Image.open(path).convert("RGB")
    except OSError:
        return None
    max_side = int(os.getenv("MM_RAG_VISION_MAX_SIDE", "1400"))
    w, h = im.size
    if max(w, h) > max_side:
        s = max_side / max(w, h)
        im = im.resize((int(w * s), int(h * s)), Image.Resampling.LANCZOS)
    for quality in (88, 78, 68, 58, 48):
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        raw = buf.getvalue()
        if len(raw) <= max_bytes:
            b64 = base64.standard_b64encode(raw).decode()
            return f"data:image/jpeg;base64,{b64}"
    return None


def _figure_image_data_urls(figure_rows: list[dict], limit: int = MAX_IMAGES_PER_CALL) -> list[str]:
    """
    FIX 5: Cap at MAX_IMAGES_PER_CALL (2) to avoid vision model attention dilution.
    Original was limit=5.
    """
    out: list[str] = []
    for r in figure_rows[:limit]:
        resolved = _resolve_image_file(r.get("image_path") or "")
        if resolved is not None:
            url = _encode_image_data_url(str(resolved))
            if url:
                out.append(url)
    return out


# ─────────────────────────────────────────────
# FIX 4: Detect if query is diagram-focused
# ─────────────────────────────────────────────

def _is_diagram_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in DIAGRAM_KEYWORDS)


# ─────────────────────────────────────────────
# FIX 3: Removed "Identify" and "Determine"
# from forbidden words — needed for visual Q stems
# ─────────────────────────────────────────────

def _strict_output_format(qtype: str, num_questions: int) -> str:
    header = """OUTPUT — FOLLOW EXACTLY:
- Reply with NOTHING except the questions in the format below. No other words.
- Do not use markdown bullets; keep plain text only.
- Forbidden: titles, markdown headers (#), "Step", numbered analysis, reasoning,
  summaries, preambles, "Correct option", "The best answer is", "Compile",
  "According to".
- Do not explain answers. One brief answer line per question only where shown.
- The very first character of your reply must be "1." """

    if qtype == "mcq":
        body = """
Repeat exactly {n} times:
1. <question stem>
A) ...
B) ...
C) ...
D) ...
Answer: <A/B/C/D only>
""".format(n=num_questions)
    elif qtype == "fill_blanks":
        body = """
Repeat exactly {n} times:
1. <sentence with one ______ blank>
Answer: <word or short phrase>
""".format(n=num_questions)
    elif qtype == "true_false":
        body = """
Repeat exactly {n} times:
1. <statement>
Answer: True or False
""".format(n=num_questions)
    else:
        body = """
Repeat exactly {n} times:
1. <question>
Answer: <expected reply in 1–2 sentences>
""".format(n=num_questions)
    return header + body


# ─────────────────────────────────────────────
# FIX 1: Visual prompt — inject label map,
# ask only what image can support
# ─────────────────────────────────────────────

def _build_visual_prompt(
    figure_rows: list[dict],
    query: str,
    qtype: str,
    num_images: int,
    num_questions: int,
) -> str:
    """
    FIX 1: Inject label_map from figure metadata so model knows
    exactly what labels are visible — no guessing.

    FIX: num_visual_questions = num_images * 2 (max 1-2 genuine
    visual questions per image, not forced 3 from 1 image).
    """
    num_visual_q = max(1, min(num_images * 2, num_questions))

    # Build figure metadata block
    fig_lines = []
    for r in figure_rows[:num_images]:
        fid   = r.get("fig_id") or "unknown"
        cap   = (r.get("caption") or "").strip()
        chap  = r.get("chapter") or ""
        pg    = r.get("page") or ""
        dtype = r.get("diagram_type") or "diagram"
        lmap  = r.get("label_map")  # dict like {"1": "Cortex", "2": "Medulla"}

        fig_lines.append(f"Figure {fid} | {chap} | page {pg} | type: {dtype}")
        fig_lines.append(f"  Caption: {cap}")

        if lmap:
            fig_lines.append(f"  Known labels in this figure: {json.dumps(lmap)}")
        else:
            fig_lines.append("  Labels: extract from the image directly")

    fig_block = "\n".join(fig_lines)

    return f"""You are an NCERT exam question generator.

A diagram image is attached. Look at it carefully.

FIGURE METADATA:
{fig_block}

Student topic: {query}

Task:
- First, list every labeled part you can see in the diagram (use metadata labels if provided).
- Then generate exactly {num_visual_q} question(s) that require a student to LOOK at the diagram.
- Every question must reference a specific visible label, marker number, or structural position.
- A student who has not seen the diagram must NOT be able to answer from memory alone.
- Question type: {qtype}

{_strict_output_format(qtype, num_visual_q)}
"""


# ─────────────────────────────────────────────
# Text-only prompt (clean, no diagram confusion)
# ─────────────────────────────────────────────

def _build_text_prompt(
    text_rows: list[dict],
    query: str,
    qtype: str,
    num_questions: int,
) -> str:
    text_context = "\n\n---\n\n".join(
        (r.get("content") or "").strip() for r in text_rows
        if (r.get("content") or "").strip()
    ) or "(no text context)"

    return f"""You are an NCERT exam question generator.

TEXT CONTEXT (from textbook):
{text_context}

Student topic: {query}

Task: Generate exactly {num_questions} {qtype} question(s) based ONLY on the text above.
Do NOT reference any diagram, figure, or image.
Ground the question in specific facts from the text context.

{_strict_output_format(qtype, num_questions)}
"""


# ─────────────────────────────────────────────
# FIX 2: Split generation — separate visual
# and text calls, fix fallback prompt rebuild
# ─────────────────────────────────────────────

def _generate_split(
    results: list[dict],
    query: str,
    qtype: str,
    num_questions: int,
    client,
    text_model: str,
    vision_model: str,
) -> dict:
    """
    FIX 2: Two focused calls instead of one mixed prompt.
    - Call 1: vision model   → visual questions only (from diagram)
    - Call 2: text model     → 1 text question only (from text chunks)

    On vision failure: rebuild prompt with has_diagram_images=False
    so model stops hallucinating visual references.
    """
    figure_rows = [r for r in results if r.get("type") == "figure"]
    text_rows   = [r for r in results if r.get("type") == "text"]
    image_urls  = _figure_image_data_urls(figure_rows)

    output = {
        "visual": None,
        "text":   None,
        "mode":   "text",
        "errors": [],
    }

    # ── Call 1: Visual questions ──────────────────
    if image_urls:
        visual_q = num_questions if not text_rows else max(1, min(num_questions - 1, len(image_urls) * 2))
        visual_prompt = _build_visual_prompt(
            figure_rows, query, qtype, num_images=len(image_urls), num_questions=visual_q
        )
        content: list[dict] = [{"type": "text", "text": visual_prompt}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})

        try:
            resp = client.chat.completions.create(
                model=vision_model,
                messages=[{"role": "user", "content": content}],
                temperature=0.2,
                max_tokens=800,
            )
            output["visual"] = resp.choices[0].message.content
            output["mode"]   = "vision"

        except Exception as vision_err:
            # FIX 2: On failure, fall back to text model
            # but use a text-only prompt (no diagram references)
            output["errors"].append(f"vision_failed: {vision_err}")
            try:
                # Rebuild as text-only — don't keep "diagram attached" language
                fallback_prompt = _build_text_prompt(text_rows, query, qtype, num_questions=num_questions)
                resp = client.chat.completions.create(
                    model=text_model,
                    messages=[{"role": "user", "content": fallback_prompt}],
                    temperature=0.2,
                    max_tokens=400,
                )
                output["visual"] = resp.choices[0].message.content
                output["mode"]   = f"vision_fallback_as_text"
            except Exception as fallback_err:
                output["errors"].append(f"fallback_failed: {fallback_err}")

    # ── Call 2: Text question ─────────────────────
    if text_rows:
        if image_urls:
            visual_q = num_questions if not text_rows else max(1, min(num_questions - 1, len(image_urls) * 2))
            text_q = max(0, num_questions - visual_q)
        else:
            text_q = num_questions
        if text_q < 1:
            return output
        text_prompt = _build_text_prompt(text_rows, query, qtype, num_questions=text_q)
        try:
            resp = client.chat.completions.create(
                model=text_model,
                messages=[{"role": "user", "content": text_prompt}],
                temperature=0.2,
                max_tokens=400,
            )
            output["text"] = resp.choices[0].message.content
            if output["mode"] == "text":
                output["mode"] = "text_only"
        except Exception as text_err:
            output["errors"].append(f"text_failed: {text_err}")

    return output


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def serve_ui():
    return send_from_directory(str(FRONTEND_DIR), "index.html")


@app.route("/search", methods=["POST"])
def search():
    if not request.is_json:
        return jsonify({"error": "Expected JSON body"}), 400

    data        = request.get_json(silent=True) or {}
    query       = (data.get("query") or "").strip()
    qtype       = data.get("qtype", "short_answer")
    subject     = (data.get("subject") or "").strip().lower() or None
    class_level = (str(data.get("class_level") or "").strip()) or None
    part        = "all"
    language    = (data.get("language") or "english").strip().lower()

    try:
        num_text    = int(data.get("num_text", 4))
        num_figures = int(data.get("num_figures", 1))
        num_questions = int(data.get("num_questions", 3))
    except (TypeError, ValueError):
        num_text, num_figures, num_questions = 4, 1, 3

    num_text    = max(0, min(num_text, 20))
    num_figures = max(0, min(num_figures, 10))
    num_questions = max(1, min(num_questions, 10))

    if not query:
        return jsonify({"error": "Empty query"}), 400

    # FIX 4: Boost figure retrieval for diagram-focused queries
    if _is_diagram_query(query):
        num_figures = max(num_figures, 2)
        num_text    = min(num_text, 2)

    client = _groq_client()
    if client is None:
        return jsonify({"error": "GROQ_API_KEY is not set"}), 503

    try:
        results = retrieve(
            query,
            num_text=num_text,
            num_figures=num_figures,
            subject=subject,
            class_level=class_level,
            part=part,
            language=language,
        )
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": f"Retrieval failed: {e}"}), 500

    _enrich_figure_rows(results)

    text_model   = os.getenv("MM_RAG_GROQ_TEXT_MODEL",   "llama-3.3-70b-versatile")
    vision_model = os.getenv("MM_RAG_GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

    try:
        gen = _generate_split(results, query, qtype, num_questions, client, text_model, vision_model)
    except Exception as e:
        return jsonify({"error": f"Generation failed: {e}", "results": results}), 502

    # Combine visual + text answers for frontend
    parts = [p for p in [gen["visual"], gen["text"]] if p]
    answer = "\n\n".join(parts) if parts else ""

    if not answer:
        return jsonify({"error": "No output generated", "details": gen["errors"]}), 502

    return jsonify({
        "answer":          answer,
        "visual_answer":   gen["visual"],
        "text_answer":     gen["text"],
        "results":         results,
        "generation_mode": gen["mode"],
        "generation_errors": gen["errors"],
        "num_questions": num_questions,
        "filters": {
            "language":    language,
            "subject":     subject,
            "class_level": class_level,
        },
    })


@app.route("/images/<path:filename>")
def serve_image(filename):
    if ".." in filename or filename.startswith("/"):
        return jsonify({"error": "Invalid path"}), 400
    if FIGURES_DIR.is_dir():
        maybe = FIGURES_DIR / filename
        if maybe.is_file():
            return send_from_directory(str(FIGURES_DIR), filename)
    if ALL_BOOKS_FIGURES_DIR.is_dir():
        for p in ALL_BOOKS_FIGURES_DIR.rglob(filename):
            if p.is_file():
                return send_from_directory(str(p.parent), p.name)
    return jsonify({"error": "Image not found"}), 404


@app.route("/image")
def serve_image_by_path():
    raw = request.args.get("path", "").strip()
    if not raw:
        return jsonify({"error": "Missing path parameter"}), 400

    resolved = _resolve_image_file(raw)
    if resolved is None:
        return jsonify({"error": "Image not found"}), 404

    repo_root    = BASE_DIR.parent.parent
    allowed_roots = [
        (BASE_DIR / "outputs").resolve(),
        BASE_DIR.resolve(),
        repo_root.resolve(),
    ]
    if not any(root == resolved or root in resolved.parents for root in allowed_roots):
        return jsonify({"error": "Path outside allowed directories"}), 400
    return send_from_directory(str(resolved.parent), resolved.name)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("MM_RAG_PORT", "5050")),
        debug=True,
    )