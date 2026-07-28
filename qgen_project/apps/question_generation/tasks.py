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

from .council import attach_forced_council_meta, filter_by_council

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
        # Prefer PDF titles + exam axes for retrieval. A nonsense batch topic
        # (e.g. "dasdasd") poisons the query and yields random chunks + invented Qs.
        pdf_names = " ".join(
            run.pdf_contexts.values_list("name", flat=True)[:8]
        )
        topic = (run.topic or "").strip()
        topic_part = ""
        if topic and _topic_looks_usable(topic):
            topic_part = topic
        query = " ".join(p for p in (topic_part, pdf_names, bloom, q_type) if p).strip()
        if not query:
            query = f"{bloom} {q_type}"
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


def _topic_looks_usable(topic: str) -> bool:
    """Heuristic: skip keyboard-mash / opaque labels in RAG queries."""
    t = (topic or "").strip()
    if len(t) < 4:
        return False
    letters = [c for c in t.lower() if c.isalpha()]
    if len(letters) < 4:
        return False
    # Repeated bigrams / alternating patterns (dasdasd, asdfasdf)
    lower = "".join(letters)
    if len(set(lower)) <= 3 and len(lower) >= 6:
        return False
    for size in (2, 3):
        if len(lower) >= size * 2 and lower[:size] * (len(lower) // size) == lower[: size * (len(lower) // size)]:
            if len(lower) >= size * 2:
                return False
    vowels = sum(1 for c in letters if c in "aeiou")
    if vowels / len(letters) < 0.15:
        return False
    return True


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


def item_slot_key(item_or_question) -> tuple:
    return (
        item_or_question.question_type,
        item_or_question.bloom,
        float(item_or_question.marks),
    )


def remaining_slots_for_run(run) -> list[tuple]:
    """
    For each BatchRunItem, how many more questions are still needed.
    Allocates existing generated questions greedily across items that share
    the same type/bloom/marks.
    Returns list of (item, need).
    """
    from collections import defaultdict

    available = defaultdict(int)
    for q in run.questions.filter(is_generated=True).only(
        "question_type", "bloom", "marks"
    ):
        available[item_slot_key(q)] += 1

    rows = []
    for item in run.items.all():
        key = item_slot_key(item)
        claimed = min(available[key], int(item.count or 0))
        available[key] -= claimed
        need = max(int(item.count or 0) - claimed, 0)
        rows.append((item, need))
    return rows


def remaining_question_count(run) -> int:
    return sum(need for _, need in remaining_slots_for_run(run))


def format_council_reject_feedback(rejected_meta: list, *, limit: int = 5) -> str:
    """Short verifier notes to steer a regenerate attempt."""
    lines = []
    for row in (rejected_meta or [])[:limit]:
        text = (row.get("question_text") or "")[:80]
        approvals = row.get("approvals")
        voted = row.get("voted_models") or row.get("total_models")
        threshold = row.get("threshold")
        lines.append(
            f"- Rejected draft ({approvals}/{voted} approvals, need {threshold}): {text!r}"
        )
    if not lines:
        return (
            "- Prior drafts failed Think majority vote "
            "(bloom / correctness / question type / appropriateness)."
        )
    return "\n".join(lines)


def generate_question_pool(
    *,
    run,
    item,
    target_count: int,
    context_text: str,
    pyq_text: str,
    gen_kwargs: dict,
    council_feedback: str = "",
):
    """Generate grounded questions; up to 3 JSON/parse attempts."""
    from .prompts import question_mentions_topic, render_prompt_context

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
            council_feedback=council_feedback,
        )
        response = completion_with_retry(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **gen_kwargs,
        )
        raw = response.choices[0].message.content.strip()
        try:
            parsed = [ensure_mcq_shape(question) for question in parse_llm_json(raw)]
        except Exception as exc:
            last_error = exc
            continue
        filtered = [
            q for q in parsed
            if not question_mentions_topic(q, run.topic)
        ]
        if len(filtered) < len(parsed):
            logger.info(
                "Dropped %s topic-leaking question(s) for item %s",
                len(parsed) - len(filtered),
                item.pk,
            )
        generated = filtered
        if len(generated) >= min(target_count, item.count):
            break
    return generated, last_error


@shared_task(bind=True)
def run_batch(self, batch_run_id: int, fill_remaining: bool = False):
    from .models import BatchRun
    from apps.pyq_module.models import Question

    run = BatchRun.objects.get(id=batch_run_id)
    run.status = 'running'
    run.celery_task_id = self.request.id
    run.error_summary = ''
    update_fields = ['status', 'celery_task_id', 'error_summary']
    if not fill_remaining:
        run.council_rejected_count = 0
        update_fields.append('council_rejected_count')
    run.save(update_fields=update_fields)

    errors = []
    run.expected_questions = sum(item.count for item in run.items.all())
    run.save(update_fields=["expected_questions"])

    if fill_remaining:
        slot_plan = remaining_slots_for_run(run)
    else:
        slot_plan = [(item, int(item.count or 0)) for item in run.items.all()]

    to_generate = sum(need for _, need in slot_plan)
    questions_before = run.questions.filter(is_generated=True).count()

    try:
        reserved = estimate_batch_run_credits(run, question_count=to_generate)
        ensure_credit_headroom(run.created_by, reserved)
        with execution_slot(run.created_by):
            for item, want in slot_plan:
                if want <= 0:
                    item.status = 'done'
                    item.error_detail = ''
                    item.save(update_fields=['status', 'error_detail'])
                    continue

                item.status = 'generating'
                item.save(update_fields=['status'])
                run.active_item = item
                run.save(update_fields=["active_item"])

                # Generate only the shortfall for this row.
                gen_item = SimpleNamespace(
                    pk=item.pk,
                    count=want,
                    question_type=item.question_type,
                    bloom=item.bloom,
                    marks=item.marks,
                )
                try:
                    if not run.pdf_contexts.exists():
                        raise ValueError(
                            "At least one PDF context is required so questions "
                            "are grounded in course material."
                        )
                    context_text, rag_snapshot = retrieve_rag_chunks(
                        run, gen_item.question_type, gen_item.bloom, run.rag_top_k
                    )
                    if not (context_text or "").strip():
                        raise ValueError(
                            "No PDF chunks available for the selected contexts. "
                            "Re-index the PDFs or pick another context."
                        )

                    pyq_text = ''
                    pyq_snapshot: list[dict] = []
                    if run.pyq_modules.exists():
                        examples = list(
                            Question.objects.filter(
                                pyq_module__in=run.pyq_modules.all(),
                                question_type=gen_item.question_type,
                                bloom=gen_item.bloom,
                            )
                            .select_related("pyq_module")
                            .order_by('?')[:run.pyq_shots]
                        )
                        pyq_text = format_examples(examples)
                        pyq_snapshot = snapshot_pyq_examples(examples)

                    target_count = want
                    if run.council_enabled and run.council_models.exists():
                        provider = (run.model_config.provider or "").lower()
                        if provider == "groq":
                            target_count = want + 1
                        else:
                            target_count = max(want * 2, want + 2)

                    gen_kwargs = get_litellm_kwargs(run.model_config)
                    if (run.model_config.provider or "").lower() == "groq":
                        gen_kwargs["max_tokens"] = min(int(gen_kwargs.get("max_tokens") or 2048), 1800)

                    use_council = bool(
                        run.council_enabled and run.council_models.exists()
                    )
                    council_rounds = 3 if use_council else 1
                    council_feedback = ""
                    candidates = []
                    last_error = None
                    last_pool = []
                    seen_texts = set()

                    for round_idx in range(council_rounds):
                        if round_idx:
                            time.sleep(3)
                        generated, last_error = generate_question_pool(
                            run=run,
                            item=gen_item,
                            target_count=target_count,
                            context_text=context_text,
                            pyq_text=pyq_text,
                            gen_kwargs=gen_kwargs,
                            council_feedback=council_feedback,
                        )
                        if len(generated) < 1:
                            continue
                        last_pool = list(generated)

                        if not use_council:
                            candidates = generated[: want]
                            break

                        council_qs = list(run.council_models.all())
                        approved, rejected_meta = filter_by_council(
                            list(generated),
                            gen_item,
                            council_qs,
                            user=run.created_by,
                            batch_run=run,
                        )
                        run.council_rejected_count = (
                            run.council_rejected_count or 0
                        ) + len(rejected_meta)
                        run.save(update_fields=["council_rejected_count"])

                        for q in approved:
                            key = (q.get("question_text") or "").strip()
                            if not key or key in seen_texts:
                                continue
                            seen_texts.add(key)
                            candidates.append(q)

                        if len(candidates) >= want:
                            candidates = candidates[: want]
                            break

                        council_feedback = format_council_reject_feedback(rejected_meta)
                        logger.info(
                            "Think round %s for item %s: %s approved so far / need %s — regenerating",
                            round_idx + 1,
                            item.pk,
                            len(candidates),
                            want,
                        )

                    if use_council and 0 < len(candidates) < want and last_pool:
                        approved_n = len(candidates)
                        need_fill = want - approved_n
                        fillers = []
                        for q in last_pool:
                            key = (q.get("question_text") or "").strip()
                            if not key or key in seen_texts:
                                continue
                            seen_texts.add(key)
                            fillers.append(q)
                            if len(fillers) >= need_fill:
                                break
                        if fillers:
                            candidates.extend(
                                attach_forced_council_meta(
                                    fillers,
                                    reason=(
                                        f"Think approved {approved_n} of {want}; "
                                        f"filled remaining after {council_rounds} "
                                        "regenerations for human review."
                                    ),
                                )
                            )

                    if use_council and not candidates and last_pool:
                        n_council = run.council_models.count()
                        logger.warning(
                            "Think rejected all after %s rounds for item %s; "
                            "soft-keeping %s for delivery.",
                            council_rounds,
                            item.pk,
                            min(want, len(last_pool)),
                        )
                        candidates = attach_forced_council_meta(
                            last_pool[: want],
                            reason=(
                                f"Think rejected all drafts after {council_rounds} "
                                f"generate+verify rounds (need majority of {n_council} "
                                "models). Kept for human review."
                            ),
                        )
                        run.error_summary = (
                            (run.error_summary or "")
                            + (
                                f"\nThink soft-keep after {council_rounds} regenerations "
                                f"({len(candidates)} question(s))."
                            )
                        ).strip()
                        run.save(update_fields=["error_summary"])

                    if not candidates:
                        raise ValueError(
                            last_error
                            or (
                                "Could not produce grounded questions"
                                + (
                                    " after Think regenerations."
                                    if use_council
                                    else ". Retry with clearer PDF material."
                                )
                            )
                        )
                    candidates = candidates[: want]

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

    produced_new = max(
        run.questions.filter(is_generated=True).count() - questions_before,
        0,
    )
    if produced_new:
        try:
            charge_rule_credits(
                user=run.created_by,
                credits=estimate_batch_run_credits(run, question_count=produced_new),
                batch_run=run,
                provider=getattr(run.model_config, "provider", "") or "",
                model_name=getattr(run.model_config, "llm_model_id", "") or "",
                request_kind="generation",
                metadata={
                    "questions": produced_new,
                    "expected": run.expected_questions,
                    "fill_remaining": bool(fill_remaining),
                    "has_rag": run.pdf_contexts.exists(),
                    "has_pyq": run.pyq_modules.exists(),
                    "think": bool(run.council_enabled),
                },
            )
        except ProvisioningError as exc:
            errors.append(str(exc))

    run.status = 'partial' if errors else 'completed'
    if errors and not run.questions.filter(is_generated=True).exists():
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
