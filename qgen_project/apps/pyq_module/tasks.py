import json
import logging

from celery import shared_task
from .models import PYQModule, Question

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


@shared_task(bind=True, max_retries=3)
def extract_pyq_questions(self, pyq_module_id: int, model_config_id: int):
    from apps.core.models import ModelConfig
    import litellm
    import fitz

    mod    = PYQModule.objects.get(id=pyq_module_id)
    config = ModelConfig.objects.get(id=model_config_id)
    mod.status = 'extracting'
    mod.save(update_fields=['status'])

    try:
        doc  = fitz.open(mod.source_file.path)
        text = '\n'.join(page.get_text() for page in doc)

        response = litellm.completion(
            model=config.llm_model_id,
            messages=[
                {'role': 'system', 'content': EXTRACTION_PROMPT},
                {'role': 'user',   'content': text[:12000]},
            ],
            temperature=0.1,
        )
        raw       = response.choices[0].message.content.strip()
        extracted = json.loads(raw)

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
