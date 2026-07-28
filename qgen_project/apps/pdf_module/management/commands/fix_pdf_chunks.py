"""Scan PDF chunks and repair improper text (legacy Hindi, empty scans)."""

from __future__ import annotations

import re

from django.core.management.base import BaseCommand

from apps.pdf_module.legacy_hindi import looks_like_legacy_hindi
from apps.pdf_module.models import PDFChunk, PDFContext
from apps.pdf_module.tasks import index_pdf_context

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


class Command(BaseCommand):
    help = (
        "Scan PDF contexts/chunks: reindex legacy Hindi (KrutiDev) text, "
        "and mark scan-only PDFs with no extractable text as failed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report issues only; do not modify or reindex.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run indexing inline instead of Celery.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        sync = options["sync"]
        reindex = []
        empty = []

        for ctx in PDFContext.objects.all().iterator():
            n = ctx.chunks.count()
            if n == 0:
                if ctx.status in ("ready", "failed", "pending"):
                    empty.append(ctx)
                continue
            if self._needs_legacy_reindex(ctx):
                reindex.append(ctx)

        self.stdout.write(
            f"Found {len(reindex)} legacy-Hindi context(s), "
            f"{len(empty)} empty context(s)."
        )

        if dry:
            for ctx in reindex:
                self.stdout.write(f"  REINDEX {ctx.original_filename} ({ctx.chunk_count} chunks)")
            for ctx in empty:
                self.stdout.write(f"  EMPTY   {ctx.original_filename} [{ctx.status}]")
            return

        for ctx in reindex:
            self.stdout.write(f"Reindexing {ctx.original_filename}…")
            PDFChunk.objects.filter(context=ctx).delete()
            ctx.status = "pending"
            ctx.error_message = ""
            ctx.needs_reindex = True
            ctx.save(update_fields=["status", "error_message", "needs_reindex"])
            if sync:
                index_pdf_context(str(ctx.id))
            else:
                index_pdf_context.delay(str(ctx.id))

        msg = (
            "No extractable text (likely scanned/image PDF). "
            "OCR required for indexing."
        )
        for ctx in empty:
            # Skip contexts we just queued for reindex (chunks wiped).
            if any(c.id == ctx.id for c in reindex):
                continue
            ctx.status = "failed"
            ctx.error_message = msg
            ctx.save(update_fields=["status", "error_message"])
            self.stdout.write(f"Marked failed (no text): {ctx.original_filename}")

        self.stdout.write(self.style.SUCCESS("Done."))

    def _needs_legacy_reindex(self, ctx: PDFContext) -> bool:
        texts = list(ctx.chunks.values_list("text", flat=True)[:30])
        if not texts:
            return False
        legacy_hits = 0
        name = f"{ctx.original_filename or ''} {ctx.name}"
        hindi_named = any(
            x in name.upper() for x in ("BHD", "हिंद", "हिन्दी", "HINDI", "अनुवाद", "कविता")
        )
        for text in texts:
            text = text or ""
            is_leg, _ = looks_like_legacy_hindi(text)
            if is_leg:
                legacy_hits += 1
                continue
            if (
                hindi_named
                and not _DEVANAGARI.search(text)
                and sum(1 for c in text if c.islower()) > 50
            ):
                legacy_hits += 1
        return legacy_hits >= max(3, len(texts) // 4)
