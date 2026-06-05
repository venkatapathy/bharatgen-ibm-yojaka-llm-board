import json
import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def format_examples(questions) -> str:
    lines = []
    for i, q in enumerate(questions, 1):
        lines.append(f'{i}. [{q.question_type}] {q.question_text}')
        if q.reference_answer:
            lines.append(f'   Answer: {q.reference_answer}')
    return '\n'.join(lines)


def retrieve_rag_chunks(run, q_type: str, bloom: str, top_k: int) -> str:
    """Embed the query and return the top-k most relevant PDF chunks."""
    from apps.pdf_module.models import PDFChunk

    try:
        from pgvector.django import L2Distance
        from apps.pdf_module.tasks import get_embed_fn

        embed_fn = get_embed_fn(run.model_config.embed_model_id)
        query    = f'{run.topic} {bloom} {q_type}'
        q_emb    = embed_fn([query])[0]
        if q_emb is None:
            raise ValueError('Embedding returned None')

        chunks = (
            PDFChunk.objects
            .filter(context__in=run.pdf_contexts.all())
            .order_by(L2Distance('embedding', q_emb))[:top_k]
        )
    except Exception as exc:
        logger.warning('RAG retrieval failed (%s), falling back to random chunks', exc)
        chunks = (
            PDFChunk.objects
            .filter(context__in=run.pdf_contexts.all())
            .order_by('?')[:top_k]
        )

    return '\n\n'.join(c.text for c in chunks)


@shared_task(bind=True)
def run_batch(self, batch_run_id: int):
    from .models import BatchRun, BatchRunItem
    from apps.pyq_module.models import Question
    import litellm
    from jinja2 import Template as JinjaTemplate

    run = BatchRun.objects.get(id=batch_run_id)
    run.status        = 'running'
    run.celery_task_id = self.request.id
    run.save(update_fields=['status', 'celery_task_id'])

    errors = []

    for item in run.items.all():
        item.status = 'generating'
        item.save(update_fields=['status'])
        try:
            # 1. RAG retrieval
            context_text = ''
            if run.pdf_contexts.exists():
                context_text = retrieve_rag_chunks(
                    run, item.question_type, item.bloom, run.rag_top_k)

            # 2. PYQ n-shot examples
            pyq_text = ''
            if run.pyq_modules.exists():
                examples = (
                    Question.objects
                    .filter(pyq_module__in=run.pyq_modules.all(),
                            question_type=item.question_type,
                            bloom=item.bloom)
                    .order_by('?')[:run.pyq_shots]
                )
                pyq_text = format_examples(examples)

            # 3. Render prompt
            ctx = dict(
                count=item.count,
                question_type=item.question_type,
                bloom=item.bloom,
                marks=item.marks,
                topic=run.topic,
                context_chunks=context_text,
                pyq_examples=pyq_text,
            )
            sys_tmpl = JinjaTemplate(run.prompt.system_prompt)
            usr_tmpl = JinjaTemplate(run.prompt.user_prompt)

            # 4. LLM call
            response = litellm.completion(
                model=run.model_config.llm_model_id,
                messages=[
                    {'role': 'system', 'content': sys_tmpl.render(**ctx)},
                    {'role': 'user',   'content': usr_tmpl.render(**ctx)},
                ],
                temperature=run.model_config.temperature,
                max_tokens=run.model_config.max_tokens,
            )
            raw       = response.choices[0].message.content.strip()
            generated = json.loads(raw)

            # 5. Persist
            Question.objects.bulk_create([
                Question(
                    batch_run=run,
                    is_generated=True,
                    question_type=item.question_type,
                    bloom=item.bloom,
                    marks=item.marks,
                    topic=run.topic,
                    question_text=q.get('question_text', ''),
                    reference_answer=q.get('reference_answer', ''),
                    rubrics=q.get('rubrics', {}),
                )
                for q in generated
            ])
            item.status = 'done'
            item.save(update_fields=['status'])

        except Exception as exc:
            logger.exception('BatchRunItem %s failed', item.pk)
            item.status      = 'error'
            item.error_detail = str(exc)
            item.save(update_fields=['status', 'error_detail'])
            errors.append(str(exc))

    run.status       = 'partial' if errors else 'completed'
    run.error_summary = '\n'.join(errors)
    run.completed_at  = timezone.now()
    run.save(update_fields=['status', 'error_summary', 'completed_at'])
