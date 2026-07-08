import json
import logging

from celery import shared_task

from apps.core.llm import get_litellm_kwargs
from apps.core.provisioning import record_token_usage

from .models import PYQModule, Question
from .pdf_text import chunk_text, extract_text

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """
You are a question extraction assistant. Given exam paper text, extract ALL
questions and return a JSON array. Each element must have:
  question_text, question_type (MCQ|SHORT|LONG|FILL|TF|CASE|NUM),
  bloom (remember|understand|apply|analyse|evaluate|create),
  marks (number), topic (string), options (array, only for MCQ),
  reference_answer (string, if answer key present, else "").
Return ONLY the JSON array, no extra text.
"""


def parse_llm_json(raw_text):
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw_text)


def looks_like_instruction_block(text: str):
    lowered = text.lower()
    markers = ["instruction", "attempt any", "all questions are compulsory", "section a", "note:"]
    return any(marker in lowered for marker in markers) and len(text.split()) < 80


@shared_task(bind=True, max_retries=3)
def extract_pyq_questions(self, pyq_module_id: int, model_config_id: int):
    from apps.core.models import ModelConfig
    import litellm

    mod    = PYQModule.objects.get(id=pyq_module_id)
    config = ModelConfig.objects.get(id=model_config_id)
    mod.status = 'extracting'
    mod.save(update_fields=['status'])

    try:
        pages = extract_text(mod.source_file.path)
        filtered_pages = [page["text"] for page in pages if not looks_like_instruction_block(page["text"])]
        text_chunks = chunk_text("\n\n".join(filtered_pages))
        extracted = []

        for text_chunk in text_chunks[:6]:
            response = litellm.completion(
                messages=[
                    {"role": "system", "content": EXTRACTION_PROMPT},
                    {"role": "user", "content": text_chunk[:12000]},
                ],
                **get_litellm_kwargs(config, temperature=0.1, max_tokens=1500),
            )
            usage = getattr(response, "usage", None)
            if usage:
                record_token_usage(
                    user=mod.created_by,
                    provider=config.provider,
                    model_name=config.llm_model_id,
                    request_kind="extraction",
                    prompt_tokens=getattr(usage, "prompt_tokens", 0),
                    completion_tokens=getattr(usage, "completion_tokens", 0),
                )
            raw = response.choices[0].message.content.strip()
            extracted.extend(parse_llm_json(raw))

        questions = [
            Question(
                pyq_module=mod,
                is_generated=False,
                question_text=q.get('question_text', ''),
                question_type=q.get('question_type', 'SHORT'),
                bloom=q.get('bloom', 'remember'),
                marks=float(q.get('marks', 1)),
                topic=q.get('topic', ''),
                options=q.get('options', []),
                reference_answer=q.get('reference_answer', ''),
            )
            for q in extracted if q.get('question_text')
        ]
        Question.objects.filter(pyq_module=mod).delete()
        Question.objects.bulk_create(questions)
        mod.status = 'ready'
        mod.save(update_fields=['status'])

    except Exception as exc:
        mod.status  = 'error'
        mod.error_msg = str(exc)
        mod.save(update_fields=['status', 'error_msg'])
        raise self.retry(exc=exc, countdown=30)
