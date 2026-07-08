import json
import logging
from types import SimpleNamespace

from celery import shared_task
from django.utils import timezone

from apps.core.embeddings import embed_texts
from apps.core.llm import get_litellm_kwargs
from apps.core.provisioning import ProvisioningError, execution_slot, record_token_usage
from apps.core.rerankers import rerank_passages

from .council import filter_by_council

logger = logging.getLogger(__name__)


def format_examples(questions) -> str:
    lines = []
    for i, q in enumerate(questions, 1):
        lines.append(f'{i}. [{q.question_type}] {q.question_text}')
        if q.reference_answer:
            lines.append(f'   Answer: {q.reference_answer}')
    return '\n'.join(lines)


def retrieve_rag_chunks(run, q_type: str, bloom: str, top_k: int) -> str:
    from apps.pdf_module.models import PDFChunk

    try:
        query = f"{run.topic} {bloom} {q_type}"
        q_emb = embed_texts([query], run.model_config.embed_model_id)[0]
        from pgvector.django import L2Distance

        chunks = list(
            PDFChunk.objects.filter(context__in=run.pdf_contexts.all()).order_by(L2Distance("embedding", q_emb))[: top_k * 3]
        )
        if run.model_config.reranker_model and chunks:
            ranked = rerank_passages(
                query,
                [chunk.text for chunk in chunks],
                run.model_config.reranker_model,
                top_k=top_k,
            )
            text_lookup = {chunk.text: chunk for chunk in chunks}
            chunks = [text_lookup[item["text"]] for item in ranked if item["text"] in text_lookup]
        else:
            chunks = chunks[:top_k]
    except Exception as exc:
        logger.warning('RAG retrieval failed (%s), falling back to random chunks', exc)
        chunks = (
            PDFChunk.objects
            .filter(context__in=run.pdf_contexts.all())
            .order_by('?')[:top_k]
        )

    return '\n\n'.join(c.text for c in chunks)


def parse_llm_json(raw_text):
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    payload = json.loads(raw_text)
    if isinstance(payload, dict):
        payload = payload.get("questions", [])
    return payload


def ensure_mcq_shape(question):
    if question.get("question_type") == "MCQ":
        options = question.get("options") or []
        while len(options) < 4:
            options.append(f"Option {len(options) + 1}")
        question["options"] = options[:4]
    return question


@shared_task(bind=True)
def run_batch(self, batch_run_id: int):
    from .models import BatchRun, BatchRunItem
    from apps.pyq_module.models import Question
    import litellm
    from .prompts import render_prompt_context

    run = BatchRun.objects.get(id=batch_run_id)
    run.status        = 'running'
    run.celery_task_id = self.request.id
    run.save(update_fields=['status', 'celery_task_id'])

    errors = []
    run.expected_questions = sum(item.count for item in run.items.all())
    run.save(update_fields=["expected_questions"])

    try:
        with execution_slot(run.created_by):
            for item in run.items.all():
                item.status = 'generating'
                item.save(update_fields=['status'])
                run.active_item = item
                run.save(update_fields=["active_item"])
                try:
                    context_text = ''
                    if run.pdf_contexts.exists():
                        context_text = retrieve_rag_chunks(run, item.question_type, item.bloom, run.rag_top_k)

                    pyq_text = ''
                    if run.pyq_modules.exists():
                        examples = (
                            Question.objects.filter(
                                pyq_module__in=run.pyq_modules.all(),
                                question_type=item.question_type,
                                bloom=item.bloom,
                            )
                            .order_by('?')[:run.pyq_shots]
                        )
                        pyq_text = format_examples(examples)

                    # When council is on, ask for a larger pool so majority filtering can still fill count.
                    target_count = item.count
                    if run.council_enabled and run.council_models.exists():
                        target_count = max(item.count * 2, item.count + 2)

                    generated = []
                    last_error = None
                    for _ in range(3):
                        item_for_prompt = SimpleNamespace(
                            count=target_count,
                            question_type=item.question_type,
                            bloom=item.bloom,
                            marks=item.marks,
                        )
                        system_prompt, user_prompt = render_prompt_context(
                            run=run,
                            item=item_for_prompt,
                            context_chunks=context_text,
                            pyq_examples=pyq_text,
                        )
                        response = litellm.completion(
                            messages=[
                                {'role': 'system', 'content': system_prompt},
                                {'role': 'user', 'content': user_prompt},
                            ],
                            **get_litellm_kwargs(run.model_config),
                        )
                        usage = getattr(response, "usage", None)
                        if usage:
                            record_token_usage(
                                user=run.created_by,
                                batch_run=run,
                                provider=run.model_config.provider,
                                model_name=run.model_config.llm_model_id,
                                request_kind="generation",
                                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                                completion_tokens=getattr(usage, "completion_tokens", 0),
                            )
                        raw = response.choices[0].message.content.strip()
                        try:
                            generated = [ensure_mcq_shape(question) for question in parse_llm_json(raw)]
                        except Exception as exc:
                            last_error = exc
                            continue
                        if len(generated) >= target_count:
                            break
                    if len(generated) < item.count:
                        raise ValueError(last_error or "Model did not return enough questions.")

                    candidates = generated[:target_count] if run.council_enabled else generated[: item.count]
                    rejected_meta = []
                    if run.council_enabled and run.council_models.exists():
                        # Over-generate pool: verify all returned, keep majority-approved,
                        # then refill from extras if needed until we have item.count.
                        pool = list(generated)
                        approved, rejected_meta = filter_by_council(
                            pool,
                            item,
                            run.council_models.all(),
                            user=run.created_by,
                            batch_run=run,
                        )
                        candidates = approved[: item.count]
                        run.council_rejected_count = (
                            run.council_rejected_count or 0
                        ) + len(rejected_meta)
                        run.save(update_fields=["council_rejected_count"])
                        if not candidates:
                            raise ValueError(
                                "Council of models rejected all generated questions "
                                f"(need majority of {run.council_models.count()} models "
                                "passing bloom, correctness, Q-type, and appropriateness)."
                            )

                    Question.objects.bulk_create(
                        [
                            Question(
                                batch_run=run,
                                is_generated=True,
                                question_type=item.question_type,
                                bloom=item.bloom,
                                marks=item.marks,
                                topic=run.topic,
                                question_text=question.get('question_text', ''),
                                reference_answer=question.get('reference_answer', ''),
                                rubrics=question.get('rubrics', {}),
                                options=question.get('options', []),
                            )
                            for question in candidates
                        ]
                    )
                    item.status = 'done'
                    item.save(update_fields=['status'])

                except Exception as exc:
                    logger.exception('BatchRunItem %s failed', item.pk)
                    item.status = 'error'
                    item.error_detail = str(exc)
                    item.save(update_fields=['status', 'error_detail'])
                    errors.append(str(exc))
    except ProvisioningError as exc:
        errors.append(str(exc))

    run.status = 'partial' if errors else 'completed'
    if errors and not run.questions.exists():
        run.status = 'failed'
    run.active_item = None
    run.error_summary = '\n'.join(errors)
    run.completed_at = timezone.now()
    run.save(update_fields=['status', 'active_item', 'error_summary', 'completed_at'])
