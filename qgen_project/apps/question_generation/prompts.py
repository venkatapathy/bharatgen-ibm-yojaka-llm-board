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

HINDI_SYSTEM_PROMPT = """\
You are an expert IGNOU BA Hindi exam question setter following Bloom's Taxonomy.
Generate exactly {{ count }} questions of type {{ question_type }} at
Bloom's level {{ bloom }}. Each question must be worth {{ marks }} marks.
Write ALL question text, options, and answers in Hindi (Devanagari script).
{% if context_chunks %}
IMPORTANT: Base every question ONLY on the reference material below.
Questions must be answerable from that material alone.
The topic label "{{ topic }}" is for batch organization only — do NOT quote it in questions,
do NOT treat it as a vocabulary term, acronym, or concept to define, and do NOT ask whether
it is a real technical term. Never invent facts about the topic string itself.

Reference material:
{{ context_chunks }}
{% else %}
Generate questions for the subject area: {{ topic }}.
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
{% if topic %}विषय संकेत (वैकल्पिक): {{ topic }}.{% endif %}
{% else %}
"{{ topic }}" पर {{ count }} {{ question_type }} प्रश्न बनाएँ।
Bloom स्तर: {{ bloom }}, प्रत्येक प्रश्न {{ marks }} अंक।
{% endif %}
सभी प्रश्न और उत्तर हिंदी (देवनागरी) में लिखें।
{% if question_type == 'MCQ' %}
प्रत्येक MCQ में ठीक 4 विकल्प (A, B, C, D) और स्पष्ट सही उत्तर हो।
{% endif %}
"""


def render_prompt_context(*, run, item, context_chunks, pyq_examples):
    prompt = run.prompt
    language = getattr(run, "language", "en") or "en"
    values = {
        "count": item.count,
        "question_type": item.question_type,
        "bloom": item.bloom,
        "marks": item.marks,
        "topic": run.topic,
        "language": language,
        "context_chunks": context_chunks,
        "pyq_examples": pyq_examples,
        "topic_grounding": getattr(prompt, "topic_grounding", ""),
    }
    system_prompt = JinjaTemplate(prompt.system_prompt).render(**values)
    user_prompt = JinjaTemplate(prompt.user_prompt).render(**values)

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

    user_prompt += "\n" + ANSWER_JSON_HINT.strip()
    return system_prompt, user_prompt
