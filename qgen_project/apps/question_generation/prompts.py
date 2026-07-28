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

HINDI_SYSTEM_PROMPT = """\
You are an expert IGNOU BA Hindi exam question setter following Bloom's Taxonomy.
Generate exactly {{ count }} questions of type {{ question_type }} at
Bloom's level {{ bloom }}. Each question must be worth {{ marks }} marks.
Write ALL question text, options, and answers in Hindi (Devanagari script).
{% if context_chunks %}
IMPORTANT: Base every question ONLY on the reference material below.
Questions must be answerable from that material alone.
Do NOT mention or invent facts about any batch/run label.

Reference material:
{{ context_chunks }}
{% else %}
Generate questions for the subject area: {{ topic }}.
If the topic is not a clear real academic subject, return [].
{% endif %}
{% if pyq_examples %}
Match the difficulty, style, and format of these example questions (use new content, not copies):
{{ pyq_examples }}
{% endif %}
Return a JSON array with EXACTLY {{ count }} elements. Each element has:
  question_text, reference_answer, rubrics.
For MCQ questions you MUST also include:
  options: an array of exactly 4 objects, each with "label" (A/B/C/D) and "text".
  reference_answer: the correct option label (e.g. "B") or full option text.
Do not prefix question_text with "[MCQ]".
The array length must equal {{ count }} — no more, no less.
JSON keys and enum values (question_type, bloom) must remain in English.
"""

HINDI_USER_PROMPT = """\
{% if context_chunks %}
संदर्भ सामग्री से {{ count }} {{ question_type }} प्रश्न बनाएँ।
Bloom स्तर: {{ bloom }}, प्रत्येक प्रश्न {{ marks }} अंक।
बैच नाम का उल्लेख न करें।
{% else %}
"{{ topic }}" पर {{ count }} {{ question_type }} प्रश्न बनाएँ।
Bloom स्तर: {{ bloom }}, प्रत्येक प्रश्न {{ marks }} अंक।
{% endif %}
सभी प्रश्न और उत्तर हिंदी (देवनागरी) में लिखें।
{% if question_type == 'MCQ' %}
प्रत्येक MCQ में ठीक 4 विकल्प (A, B, C, D) और स्पष्ट सही उत्तर हो।
{% endif %}
"""


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


def render_prompt_context(*, run, item, context_chunks, pyq_examples, council_feedback=""):
    prompt = run.prompt
    language = getattr(run, "language", "en") or "en"
    has_rag = bool((context_chunks or "").strip())
    # When RAG is present, do not feed the batch topic into the template — models
    # otherwise invent "subject area '<topic>'" questions about nonsense labels.
    topic_for_prompt = "" if has_rag else (run.topic or "")
    values = {
        "count": item.count,
        "question_type": item.question_type,
        "bloom": item.bloom,
        "marks": item.marks,
        "topic": topic_for_prompt,
        "language": language,
        "context_chunks": context_chunks,
        "pyq_examples": pyq_examples,
        "topic_grounding": getattr(prompt, "topic_grounding", ""),
    }
    system_prompt = JinjaTemplate(prompt.system_prompt).render(**values)
    user_prompt = JinjaTemplate(prompt.user_prompt).render(**values)

    if has_rag:
        system_prompt = system_prompt.rstrip() + "\n" + RAG_GROUNDING_RULES.strip()
        user_prompt += (
            "\nGenerate from the reference material only. "
            "Do not mention any batch/run name in the questions."
        )
    else:
        system_prompt = system_prompt.rstrip() + "\n" + NO_CONTEXT_RULES.strip()

    if language == "hi":
        system_prompt = system_prompt.rstrip() + "\n" + HINDI_LANGUAGE_HINT.strip()
        user_prompt += (
            "\nसभी प्रश्न और उत्तर हिंदी (देवनागरी) में लिखें। "
            "JSON keys अंग्रेज़ी में रखें।"
        )

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

    if council_feedback:
        user_prompt += (
            "\n\nPREVIOUS DRAFTS WERE REJECTED BY VERIFIERS. "
            "Generate NEW questions that fix these issues:\n"
            f"{council_feedback.strip()}"
        )

    user_prompt += "\n" + ANSWER_JSON_HINT.strip()
    return system_prompt, user_prompt
