"""
Test: Multimodal Question Generation from NCERT Diagrams
---------------------------------------------------------
Usage:
    pip install google-generativeai pillow
    python test_diagram_questions.py --image your_diagram.png --api_key YOUR_KEY

Or set GEMINI_API_KEY env variable and run:
    python test_diagram_questions.py --image your_diagram.png

Example with bundled urinary-system figure:
    python test_diagram_questions.py \\
      --image fixtures/ncert_urinary_figure_19_1.png

Model: set GEMINI_TEST_MODEL (default: gemini-2.5-flash). gemini-2.0-flash is deprecated.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import google.generativeai as genai
    from google.api_core import exceptions as google_api_exceptions
    from PIL import Image
except ImportError:
    print("❌ Missing dependencies. Run: pip install google-generativeai pillow")
    sys.exit(1)


def generate_with_retry(model, content, *, max_retries: int = 8, base_delay_s: float = 46.0):
    """Call Gemini with backoff on 429 (free tier is often 5 RPM per model)."""
    delay = base_delay_s
    last_err = None
    for attempt in range(max_retries):
        try:
            return model.generate_content(content)
        except google_api_exceptions.ResourceExhausted as e:
            last_err = e
            if attempt >= max_retries - 1:
                raise
            print(f"   ⏳ Rate limited (429); sleeping {delay:.0f}s then retry ({attempt + 1}/{max_retries})...")
            time.sleep(delay)
            delay = min(delay + 15.0, 90.0)
    raise last_err  # pragma: no cover


# ─────────────────────────────────────────────
# STEP 1: Extract labels from diagram
# ─────────────────────────────────────────────

LABEL_EXTRACTION_PROMPT = """
You are analyzing a scientific diagram. This can be from any subject —
biology, physics, chemistry, geography, or any other field.

Look ONLY at what is visually present in this image.
Do NOT assume or add labels that are not clearly visible.

For every text label you can see in the diagram, return:
  - label: the exact text as written in the image
  - points_to: what structure or part it is pointing/connected to,
               described from what you see (not from prior knowledge)
  - position: approximate location using one of these values only:
               top-left | top-center | top-right |
               center-left | center | center-right |
               bottom-left | bottom-center | bottom-right

Return ONLY a valid JSON array, no extra text, no markdown fences.
Each item must follow this structure exactly:
[
  {"label": "<exact text from image>", "points_to": "<what it points to>", "position": "<location>"}
]

If this is NOT a labeled diagram (e.g. a photo, plain text, graph without labels), return: []
"""


def extract_labels(image_path: str, model) -> list:
    img = Image.open(image_path)
    print("\n📍 Step 1: Extracting labels from diagram...")

    response = generate_with_retry(model, [LABEL_EXTRACTION_PROMPT, img])
    raw = response.text.strip()

    # Clean up if model adds markdown fences anyway
    raw = re.sub(r"```json|```", "", raw).strip()

    try:
        labels = json.loads(raw)
        print(f"   ✅ Found {len(labels)} labels: {[l['label'] for l in labels]}")
        return labels
    except json.JSONDecodeError:
        print(f"   ⚠️  Could not parse JSON. Raw response:\n{raw}")
        return []


# ─────────────────────────────────────────────
# STEP 2: Build answer key (number → label)
# ─────────────────────────────────────────────

def build_answer_key(labels: list) -> dict:
    """Maps 1, 2, 3... → label names"""
    answer_key = {}
    for i, item in enumerate(labels, start=1):
        answer_key[str(i)] = {
            "label": item["label"],
            "points_to": item["points_to"],
            "position": item["position"],
        }
    print("\n🗝️  Step 2: Answer Key built:")
    for num, info in answer_key.items():
        print(f"   {num} → {info['label']} ({info['points_to']})")
    return answer_key


# ─────────────────────────────────────────────
# STEP 3: Generate visual questions
# ─────────────────────────────────────────────

def build_question_prompt(answer_key: dict) -> str:
    key_str = "\n".join(
        [
            f"  Marker {k}: '{v['label']}' — {v['points_to']} (located at {v['position']})"
            for k, v in answer_key.items()
        ]
    )

    return f"""
You are an exam question generator for any subject.

Imagine this diagram has ALL text labels removed and replaced with
numbered markers (1, 2, 3...) at the same positions.

The numbered markers correspond to:
{key_str}

Generate 5 questions a student MUST look at the numbered diagram to answer.
Questions must reference specific marker numbers.

Include these question types:
1. Identify labels (name what markers 1, 2, 3 point to)
2. Spatial/positional (which marker is above/below/connected to which)
3. Role-based (what does the part at marker X do in this system/process)
4. MCQ with 4 options (A, B, C, D)
5. One "match the column" — Column A = marker numbers, Column B = roles (shuffled)

Rules:
- Every question must mention at least one marker number
- A student with no diagram CANNOT answer these from memory alone
- MCQ distractors must be from the same category as the correct answer
- Do NOT assume any subject — base questions only on what the markers show

Return as JSON array:
[
  {{
    "type": "identify / spatial / role / mcq / match",
    "question": "...",
    "options": ["A) ...", "B) ...", "C) ...", "D) ..."],
    "answer": "...",
    "marks": 1
  }}
]

Only return valid JSON, no extra text.
"""


def generate_questions(image_path: str, answer_key: dict, model) -> list:
    img = Image.open(image_path)
    prompt = build_question_prompt(answer_key)

    print("\n✏️  Step 3: Generating visual questions...")
    response = generate_with_retry(model, [prompt, img])
    raw = response.text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()

    try:
        questions = json.loads(raw)
        print(f"   ✅ Generated {len(questions)} questions")
        return questions
    except json.JSONDecodeError:
        print(f"   ⚠️  Could not parse questions JSON. Raw response:\n{raw}")
        return []


# ─────────────────────────────────────────────
# STEP 4: Validate questions are truly visual
# ─────────────────────────────────────────────

def validate_visual_dependency(questions: list, model) -> list:
    """
    One batched API call (not N calls). Free tier is often 5 RPM for gemini-2.5-flash;
    separate per-question calls exceed that after label + question generation.
    """
    print("\n🔍 Step 4: Validating visual dependency (single batched call)...")
    if not questions:
        return []

    numbered = "\n".join(
        f"{i + 1}. [{q.get('type', '?')}] {q['question']}" for i, q in enumerate(questions)
    )
    check_prompt = f"""
For EACH numbered question below, decide:
  - "textbook_only": true  → a student could answer correctly WITHOUT any diagram
                          (generic recall / vocabulary is enough)
  - "textbook_only": false → the question depends on THIS diagram (markers, layout,
                          or relations not fixed in generic memory)

Questions:
{numbered}

Return ONLY a JSON array of objects, one per question IN ORDER, same length as {len(questions)}:
[
  {{"textbook_only": false}},
  {{"textbook_only": true}}
]
No markdown fences.
"""
    result = generate_with_retry(model, check_prompt)
    raw = (result.text or "").strip()
    raw = re.sub(r"```json|```", "", raw).strip()

    flags: list[bool] = []
    try:
        parsed = json.loads(raw)
        for item in parsed:
            if isinstance(item, dict):
                flags.append(bool(item.get("textbook_only")))
            elif isinstance(item, bool):
                flags.append(item)
            else:
                flags.append(False)
    except json.JSONDecodeError:
        print(f"   ⚠️  Could not parse validation JSON; keeping all questions.\n{raw[:500]}")
        return list(questions)

    if len(flags) != len(questions):
        print(
            f"   ⚠️  Validation length {len(flags)} != {len(questions)}; keeping all questions."
        )
        return list(questions)

    valid = []
    for i, q in enumerate(questions):
        textbook_only = flags[i]
        needs_image = not textbook_only
        status = "✅ Visual" if needs_image else "❌ Text-answerable (filtered out)"
        print(f"   Q{i + 1} ({q.get('type', '?')}): {status}")
        if needs_image:
            valid.append(q)

    print(f"\n   Kept {len(valid)}/{len(questions)} truly visual questions")
    return valid


# ─────────────────────────────────────────────
# STEP 5: Print final output
# ─────────────────────────────────────────────

def print_results(answer_key: dict, questions: list, image_path: str):
    print("\n" + "=" * 60)
    print("📋 FINAL OUTPUT")
    print("=" * 60)

    print(f"\n📌 Image: {image_path}")
    print(
        f"   Show to student: Same image with labels replaced by numbers 1-{len(answer_key)}"
    )

    print("\n🗝️  ANSWER KEY (keep hidden from student):")
    for num, info in answer_key.items():
        print(f"   Marker {num}: {info['label']}")

    print(f"\n❓ QUESTIONS ({len(questions)} visual questions):\n")
    for i, q in enumerate(questions, 1):
        print(f"Q{i}. [{q['type'].upper()}] {q['question']}")
        if q.get("options"):
            for opt in q["options"]:
                print(f"     {opt}")
        print(f"     ✔ Answer: {q['answer']}  [{q.get('marks', 1)} mark(s)]")
        print()

    # Save to JSON next to image
    output = {"image": image_path, "answer_key": answer_key, "questions": questions}
    out_path = Path(image_path).resolve().parent / (Path(image_path).stem + "_questions.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"💾 Saved to: {out_path}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate visual questions from NCERT diagram"
    )
    parser.add_argument("--image", required=True, help="Path to diagram image (PNG/JPG)")
    parser.add_argument(
        "--api_key", default=None, help="Gemini API key (or set GEMINI_API_KEY env var)"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Gemini model id (default: GEMINI_TEST_MODEL or gemini-2.5-flash)",
    )
    parser.add_argument(
        "--skip_validation",
        action="store_true",
        help="Skip visual dependency validation (faster)",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ No API key. Use --api_key or set GEMINI_API_KEY environment variable")
        sys.exit(1)

    if not Path(args.image).exists():
        print(f"❌ Image not found: {args.image}")
        sys.exit(1)

    model_name = (
        args.model
        or os.environ.get("GEMINI_TEST_MODEL")
        or "gemini-2.5-flash"
    )
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)
    print(f"🚀 Model: {model_name}")
    print(f"🖼️  Image: {args.image}")

    labels = extract_labels(args.image, model)

    if not labels:
        print("\n⚠️  No labels found. This may not be a labeled diagram.")
        print("   Try with a clearer NCERT diagram image.")
        sys.exit(0)

    answer_key = build_answer_key(labels)
    questions = generate_questions(args.image, answer_key, model)

    if not args.skip_validation:
        questions = validate_visual_dependency(questions, model)

    print_results(answer_key, questions, args.image)


if __name__ == "__main__":
    main()
