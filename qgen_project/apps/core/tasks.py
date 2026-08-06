"""Periodic housekeeping for jobs that finished work but never updated status."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# Avoid racing a live worker that just wrote OCR / last item and is about to flip status.
_PDF_GRACE_SECONDS = 90
_RUN_GRACE_SECONDS = 90

_TERMINAL_ITEM_STATUSES = frozenset({"done", "error"})


def _finalize_batch_run(run) -> str:
    """Mirror run_batch completion when items are already terminal."""
    from apps.core.models import GenerationSettings
    from apps.question_generation.models import BatchRun

    has_errors = run.items.filter(status="error").exists()
    has_questions = run.questions.filter(is_generated=True).exists()
    if has_errors and not has_questions:
        new_status = BatchRun.Status.FAILED
    elif has_errors:
        new_status = BatchRun.Status.PARTIAL
    else:
        new_status = BatchRun.Status.COMPLETED

    run.status = new_status
    run.active_item = None
    if not run.completed_at:
        run.completed_at = timezone.now()
    if has_questions:
        if GenerationSettings.load().user_feedback_enabled:
            if run.review_status == BatchRun.ReviewStatus.NOT_STARTED:
                run.review_status = BatchRun.ReviewStatus.PENDING
        else:
            run.review_status = BatchRun.ReviewStatus.NOT_STARTED
    else:
        run.review_status = BatchRun.ReviewStatus.NOT_STARTED

    note = (
        "Auto-reconciled: worker finished items but run status was still "
        f"{BatchRun.Status.RUNNING}."
    )
    summary = (run.error_summary or "").strip()
    if note not in summary:
        run.error_summary = f"{summary}\n{note}".strip() if summary else note

    run.save(
        update_fields=[
            "status",
            "active_item",
            "completed_at",
            "review_status",
            "error_summary",
        ]
    )
    return new_status


def reconcile_stuck_batch_runs(*, grace_seconds: int = _RUN_GRACE_SECONDS) -> int:
    from apps.question_generation.models import BatchRun

    fixed = 0
    cutoff = timezone.now() - timedelta(seconds=grace_seconds)
    # Prefer older runs; created_at is a proxy when updated_at is missing.
    candidates = BatchRun.objects.filter(status=BatchRun.Status.RUNNING).filter(
        Q(created_at__lte=cutoff) | Q(completed_at__isnull=False)
    )
    for run in candidates.iterator():
        items = list(run.items.all())
        if not items:
            continue
        if any(i.status not in _TERMINAL_ITEM_STATUSES for i in items):
            continue
        # Items finished recently enough that the worker may still be charging credits.
        # created_at alone is weak; still safe because all items are terminal.
        new_status = _finalize_batch_run(run)
        fixed += 1
        logger.warning(
            "Reconciled stuck BatchRun %s → %s (%s)",
            run.pk,
            new_status,
            (run.name or "")[:80],
        )
    return fixed


def reconcile_stuck_pdf_contexts(*, grace_seconds: int = _PDF_GRACE_SECONDS) -> int:
    from apps.pdf_module.models import PDFContext

    fixed = 0
    cutoff = timezone.now() - timedelta(seconds=grace_seconds)
    qs = PDFContext.objects.filter(status="processing").filter(
        updated_at__lte=cutoff,
    ).exclude(ocr_text="")
    for ctx in qs.iterator():
        if not (ctx.ocr_text or "").strip():
            continue
        ctx.status = "ready"
        ctx.needs_reindex = False
        ctx.error_message = ""
        ctx.save(update_fields=["status", "needs_reindex", "error_message"])
        fixed += 1
        logger.warning(
            "Reconciled stuck PDFContext %s → ready (%s chars, %s)",
            ctx.pk,
            len(ctx.ocr_text or ""),
            (ctx.name or "")[:80],
        )
    return fixed


@shared_task(name="apps.core.tasks.reconcile_stuck_jobs")
def reconcile_stuck_jobs():
    """Flip finished-but-stuck batch runs and PDF OCR jobs to a terminal status."""
    runs = reconcile_stuck_batch_runs()
    pdfs = reconcile_stuck_pdf_contexts()
    if runs or pdfs:
        logger.info("reconcile_stuck_jobs: fixed runs=%s pdfs=%s", runs, pdfs)
    return {"batch_runs": runs, "pdf_contexts": pdfs}
