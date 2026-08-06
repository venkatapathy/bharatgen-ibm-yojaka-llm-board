"""Prompt builders for question generation."""

from jinja2 import Template as JinjaTemplate

ANSWER_JSON_HINT = """
Return ONLY a JSON array. Each element must include:
  question_text (string),
  question_type (string),
  bloom (string),
  marks (number),
  topic (string),
  options (array — required for MCQ as ["A) ...", "B) ...", "C) ...", "D) ..."], else []),
  reference_answer (string — REQUIRED correct answer for every question),
  rubrics (object, optional).

For MCQ: reference_answer must be the correct option letter (A/B/C/D) OR the full correct option text.
For SHORT/LONG/FILL/TF/CASE/NUM: reference_answer must be the complete model answer / correct value.
Never leave reference_answer empty.
"""

HINDI_LANGUAGE_HINT = """
LANGUAGE REQUIREMENT (Hindi):
- Write question_text, options (if any), reference_answer, and rubrics in Hindi using Devanagari script.
- Keep JSON keys in English exactly as specified.
- Keep question_type / bloom values as English enum tokens (e.g. SHORT, remember).
- Match IGNOU BA Hindi exam style when PYQ examples are present.
"""

# Appended after every render so DB prompt drift cannot weaken grounding.
RAG_GROUNDING_RULES = """
HARD GROUNDING RULES (mandatory):
1. Base EVERY question ONLY on the reference material provided. If a fact is not in that material, do not use it.
2. The batch/run name or topic label is for filing only. NEVER put it in question_text, options, or answers.
3. Do NOT invent a fictional "subject area", course, or domain around a random/nonsense label.
4. Do NOT ask what a label means, its characteristics, focus, or whether it is a real term.
5. Prefer concepts, names, and facts that appear in the reference material. Questions must be answerable from that material alone.
6. In the JSON "topic" field, use a short theme taken from the reference material — not the batch label.
"""

NO_CONTEXT_RULES = """
HARD RULES (no PDF reference material):
1. Do NOT invent facts about a nonsense or random topic string.
2. If the topic is not a clear real academic subject, return an empty JSON array [].
3. Never write questions of the form "What is characteristic of '<label>'?" for opaque labels.
"""

# App codes → wording expected by the IGNOU unit prompt (prompt.txt).
IGNOU_TYPE_LABELS = {
    "RTC": "reference-to-context",
    "SHORT": "short note",
    "LONG": "long answer",
}
IGNOU_BLOOM_LABELS = {
    "remember": "Remember",
    "understand": "Understand",
    "apply": "Apply",
    "analyse": "Analyze",
    "analyze": "Analyze",
    "evaluate": "Evaluate",
    "create": "Create",
}
IGNOU_LANGUAGE_LABELS = {
    "en": "English",
    "hi": "Hindi",
}

DEFAULT_ANSWER_LENGTHS = {
    "RTC": "100-150 words",
    "SHORT": "100-250 words",
    "LONG": "250-600 words",
}


def default_answer_length(question_type: str) -> str:
    return DEFAULT_ANSWER_LENGTHS.get(str(question_type or "").upper(), "100-250 words")


def question_mentions_topic(question: dict, topic: str) -> bool:
    """True if question text/options leak the batch topic label."""
    label = (topic or "").strip()
    if len(label) < 2:
        return False
    needle = label.lower()
    parts = [
        str(question.get("question_text") or ""),
        str(question.get("reference_answer") or ""),
        str(question.get("topic") or ""),
    ]
    options = question.get("options") or []
    if isinstance(options, dict):
        parts.extend(str(v) for v in options.values())
    else:
        parts.extend(str(o) for o in options)
    blob = " ".join(parts).lower()
    return needle in blob


def _is_ignou_prompt(prompt) -> bool:
    user = getattr(prompt, "user_prompt", "") or ""
    system = getattr(prompt, "system_prompt", "") or ""
    return (
        "complete_unit_content" in user
        or ('"question"' in system and "citation" in system)
    )


def render_prompt_context(*, run, item, context_chunks, pyq_examples, council_feedback=""):
    prompt = run.prompt
    language = getattr(run, "language", "en") or "en"
    has_rag = bool((context_chunks or "").strip())
    unit_content = context_chunks or ""
    ignou_style = _is_ignou_prompt(prompt)

    qtype = item.question_type
    bloom = item.bloom
    lang_for_prompt = language
    answer_length = (getattr(item, "answer_length", None) or "").strip()
    if not answer_length:
        answer_length = default_answer_length(item.question_type)
    if ignou_style:
        qtype = IGNOU_TYPE_LABELS.get(str(qtype).upper(), qtype)
        bloom = IGNOU_BLOOM_LABELS.get(str(bloom).lower(), bloom)
        lang_for_prompt = IGNOU_LANGUAGE_LABELS.get(language, language)

    values = {
        "count": item.count,
        "question_type": qtype,
        "bloom": bloom,
        "marks": item.marks,
        "topic": "",  # Not used by the IGNOU unit prompt.
        "language": lang_for_prompt,
        "answer_length": answer_length,
        "context_chunks": unit_content,
        # Alias used by the IGNOU unit-content prompt (prompt.txt).
        "complete_unit_content": unit_content,
        "pyq_examples": pyq_examples,
        "topic_grounding": getattr(prompt, "topic_grounding", ""),
    }
    system_prompt = JinjaTemplate(prompt.system_prompt).render(**values)
    user_prompt = JinjaTemplate(prompt.user_prompt).render(**values)

    if has_rag:
        if not ignou_style:
            system_prompt = system_prompt.rstrip() + "\n" + RAG_GROUNDING_RULES.strip()
            user_prompt += (
                "\nGenerate from the reference material only. "
                "Do not mention any batch/run name in the questions."
            )
    else:
        system_prompt = system_prompt.rstrip() + "\n" + NO_CONTEXT_RULES.strip()

    if language == "hi" and not ignou_style:
        system_prompt = system_prompt.rstrip() + "\n" + HINDI_LANGUAGE_HINT.strip()
        user_prompt += (
            "\nसभी प्रश्न और उत्तर हिंदी (देवनागरी) में लिखें। "
            "JSON keys अंग्रेज़ी में रखें।"
        )

    if not ignou_style:
        if item.question_type == "MCQ":
            user_prompt += (
                "\nEach MCQ must have exactly 4 labelled options (A, B, C, D) "
                "and set reference_answer to the correct option letter (A/B/C/D)."
            )
        else:
            user_prompt += (
                "\nFor every question, include a complete correct answer in reference_answer "
                "(model answer / key points / exact value)."
            )
        user_prompt += "\n" + ANSWER_JSON_HINT.strip()

    if council_feedback:
        user_prompt += (
            "\n\nPREVIOUS DRAFTS WERE REJECTED BY VERIFIERS. "
            "Generate NEW questions that fix these issues:\n"
            f"{council_feedback.strip()}"
        )

    return system_prompt, user_prompt
