"""Prompt builders for question generation."""

from jinja2 import Template as JinjaTemplate


def render_prompt_context(*, run, item, context_chunks, pyq_examples):
    prompt = run.prompt
    values = {
        "count": item.count,
        "question_type": item.question_type,
        "bloom": item.bloom,
        "marks": item.marks,
        "topic": run.topic,
        "context_chunks": context_chunks,
        "pyq_examples": pyq_examples,
        "topic_grounding": getattr(prompt, "topic_grounding", ""),
    }
    system_prompt = JinjaTemplate(prompt.system_prompt).render(**values)
    user_prompt = JinjaTemplate(prompt.user_prompt).render(**values)
    if item.question_type == "MCQ":
        user_prompt += "\nReturn four answer options and mark the correct answer in reference_answer."
    user_prompt += "\nReturn ONLY valid JSON."
    return system_prompt, user_prompt
