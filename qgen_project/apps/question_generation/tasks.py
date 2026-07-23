import json
import logging
import time
from types import SimpleNamespace

from celery import shared_task
from django.utils import timezone

from apps.core.embeddings import embed_texts
from apps.core.llm import completion_with_retry, get_litellm_kwargs
from apps.core.provisioning import (
    ProvisioningError,
    charge_rule_credits,
    ensure_credit_headroom,
    estimate_batch_run_credits,
    execution_slot,
)
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


def snapshot_pyq_examples(questions) -> list[dict]:
    rows = []
    for q in questions:
        rows.append(
            {
                "id": q.pk,
                "question_type": q.question_type,
                "bloom": q.bloom,
                "marks": q.marks,
                "question_text": q.question_text,
                "reference_answer": q.reference_answer or "",
                "pyq_module_id": q.pyq_module_id,
                "pyq_module_name": getattr(q.pyq_module, "name", "") if q.pyq_module_id else "",
            }
        )
    return rows


def snapshot_rag_chunks(chunks) -> list[dict]:
    rows = []
    for chunk in chunks:
        context = getattr(chunk, "context", None)
        rows.append(
            {
                "id": str(chunk.pk),
                "text": chunk.text,
                "source_file": chunk.source_file or "",
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "context_id": str(getattr(context, "pk", "") or ""),
                "context_name": getattr(context, "name", "") or "",
            }
        )
    return rows


def retrieve_rag_chunks(run, q_type: str, bloom: str, top_k: int) -> tuple[str, list[dict]]:
    from apps.pdf_module.models import PDFChunk
    from apps.core.embeddings import DEFAULT_EMBED_MODEL
    from pgvector.django import CosineDistance

    try:
        query = f"{run.topic} {bloom} {q_type}"
        embed_model = (run.model_config.embed_model_id or "").strip() or DEFAULT_EMBED_MODEL
        q_emb = embed_texts([query], embed_model)[0]
        if not q_emb:
            raise ValueError("Empty query embedding")

        chunks = list(
            PDFChunk.objects.filter(
                context__in=run.pdf_contexts.all(),
                embedding__isnull=False,
            )
            .select_related("context")
            .order_by(CosineDistance("embedding", q_emb))[: top_k * 3]
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
        chunks = list(
            PDFChunk.objects
            .filter(context__in=run.pdf_contexts.all())
            .select_related("context")
            .order_by('?')[:top_k]
        )

    return '\n\n'.join(c.text for c in chunks), snapshot_rag_chunks(chunks)


def parse_llm_json(raw_text):
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    payload = json.loads(raw_text)
    if isinstance(payload, dict):
        payload = payload.get("questions", [])
    return payload


def ensure_mcq_shape(question):
    """Normalize options and ensure a usable reference_answer for display."""
    q_type = (question.get("question_type") or "").upper()
    if q_type == "MCQ":
        options = question.get("options") or []
        if isinstance(options, dict):
            options = [f"{k}) {v}" for k, v in options.items()]
        while len(options) < 4:
            options.append(f"Option {len(options) + 1}")
        question["options"] = options[:4]

        answer = str(question.get("reference_answer") or "").strip()
        if not answer:
            # Fall back to first option letter if model omitted answer.
            question["reference_answer"] = "A"
        elif answer.upper() not in "ABCD" and len(answer) > 1:
            # Keep full-text answers — UI matching handles them.
            question["reference_answer"] = answer
        else:
            question["reference_answer"] = answer.upper()[:1]
    else:
        # Non-MCQ: keep whatever answer text we got; mark empty for regeneration awareness.
        if not str(question.get("reference_answer") or "").strip():
            question["reference_answer"] = question.get("answer") or question.get("correct_answer") or ""

    return question


@shared_task(bind=True)
def run_batch(self, batch_run_id: int):
    from .models import BatchRun, BatchRunItem
    from apps.pyq_module.models import Question
    from .prompts import render_prompt_context

    run = BatchRun.objects.get(id=batch_run_id)
    run.status        = 'running'
    run.celery_task_id = self.request.id
    run.error_summary = ''
    run.council_rejected_count = 0
    run.save(update_fields=['status', 'celery_task_id', 'error_summary', 'council_rejected_count'])

    errors = []
    run.expected_questions = sum(item.count for item in run.items.all())
    run.save(update_fields=["expected_questions"])

    try:
        reserved = estimate_batch_run_credits(run)
        ensure_credit_headroom(run.created_by, reserved)
        with execution_slot(run.created_by):
            for item in run.items.all():
                item.status = 'generating'
                item.save(update_fields=['status'])
                run.active_item = item
                run.save(update_fields=["active_item"])
                try:
                    context_text = ''
                    rag_snapshot: list[dict] = []
                    if run.pdf_contexts.exists():
                        context_text, rag_snapshot = retrieve_rag_chunks(
                            run, item.question_type, item.bloom, run.rag_top_k
                        )

                    pyq_text = ''
                    pyq_snapshot: list[dict] = []
                    if run.pyq_modules.exists():
                        examples = list(
                            Question.objects.filter(
                                pyq_module__in=run.pyq_modules.all(),
                                question_type=item.question_type,
                                bloom=item.bloom,
                            )
                            .select_related("pyq_module")
                            .order_by('?')[:run.pyq_shots]
                        )
                        pyq_text = format_examples(examples)
                        pyq_snapshot = snapshot_pyq_examples(examples)

                    # When council is on, ask for a modest over-pool. Keep it small on
                    # Groq to stay under TPM (6000 tokens/min on free/on_demand).
                    target_count = item.count
                    if run.council_enabled and run.council_models.exists():
                        provider = (run.model_config.provider or "").lower()
                        if provider == "groq":
                            target_count = item.count + 1
                        else:
                            target_count = max(item.count * 2, item.count + 2)

                    # Cap completion tokens for generation to reduce TPM spikes.
                    gen_kwargs = get_litellm_kwargs(run.model_config)
                    if (run.model_config.provider or "").lower() == "groq":
                        gen_kwargs["max_tokens"] = min(int(gen_kwargs.get("max_tokens") or 2048), 1800)

                    generated = []
                    last_error = None
                    for attempt_idx in range(3):
                        if attempt_idx:
                            time.sleep(2)
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
                        response = completion_with_retry(
                            messages=[
                                {'role': 'system', 'content': system_prompt},
                                {'role': 'user', 'content': user_prompt},
                            ],
                            **gen_kwargs,
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
                        from .council import attach_forced_council_meta

                        council_qs = list(run.council_models.all())
                        n_council = len(council_qs)
                        pool = list(generated)
                        approved, rejected_meta = filter_by_council(
                            pool,
                            item,
                            council_qs,
                            user=run.created_by,
                            batch_run=run,
                        )
                        candidates = approved[: item.count]
                        run.council_rejected_count = (
                            run.council_rejected_count or 0
                        ) + len(rejected_meta)
                        run.save(update_fields=["council_rejected_count"])
                        if not candidates:
                            # Soft fallback: keep generated questions rather than empty run.
                            logger.warning(
                                "Council rejected all %s candidates for item %s "
                                "(%s models). Keeping first %s with forced_keep flag.",
                                len(pool),
                                item.pk,
                                n_council,
                                item.count,
                            )
                            candidates = attach_forced_council_meta(
                                pool[: item.count],
                                reason=(
                                    f"Council rejected all candidates "
                                    f"(need ≥ half of {n_council} voting models). "
                                    "Kept for delivery with degraded council flag."
                                ),
                            )
                            run.error_summary = (
                                (run.error_summary or "")
                                + (
                                    f"\nCouncil soft-keep: rejected all then kept "
                                    f"{len(candidates)} question(s) (models={n_council})."
                                )
                            ).strip()
                            run.save(update_fields=["error_summary"])

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
                                rubrics=question.get('rubrics', {}) or {},
                                options=question.get('options', []) or [],
                                rag_chunks=rag_snapshot or [],
                                pyq_examples=pyq_snapshot or [],
                                user_decision=Question.UserDecision.PENDING,
                                user_feedback="",
                            )
                            for question in candidates
                        ]
                    )
                    item.status = 'done'
                    item.save(update_fields=['status'])
                    # Brief pause between batch items so Groq TPM can recover.
                    if (run.model_config.provider or "").lower() == "groq":
                        time.sleep(8)

                except Exception as exc:
                    logger.exception('BatchRunItem %s failed', item.pk)
                    item.status = 'error'
                    item.error_detail = str(exc)
                    item.save(update_fields=['status', 'error_detail'])
                    errors.append(str(exc))
    except ProvisioningError as exc:
        errors.append(str(exc))

    produced = run.questions.count()
    if produced:
        try:
            charge_rule_credits(
                user=run.created_by,
                credits=estimate_batch_run_credits(run, question_count=produced),
                batch_run=run,
                provider=getattr(run.model_config, "provider", "") or "",
                model_name=getattr(run.model_config, "llm_model_id", "") or "",
                request_kind="generation",
                metadata={
                    "questions": produced,
                    "expected": run.expected_questions,
                    "has_rag": run.pdf_contexts.exists(),
                    "has_pyq": run.pyq_modules.exists(),
                    "think": bool(run.council_enabled),
                },
            )
        except ProvisioningError as exc:
            errors.append(str(exc))

    run.status = 'partial' if errors else 'completed'
    if errors and not run.questions.exists():
        run.status = 'failed'
    run.active_item = None
    run.error_summary = '\n'.join(errors)
    run.completed_at = timezone.now()
    if run.questions.filter(is_generated=True).exists():
        run.review_status = BatchRun.ReviewStatus.PENDING
    else:
        run.review_status = BatchRun.ReviewStatus.COMPLETE
    run.save(
        update_fields=[
            'status',
            'active_item',
            'error_summary',
            'completed_at',
            'review_status',
        ]
    )
