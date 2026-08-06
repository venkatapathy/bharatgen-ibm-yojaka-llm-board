"""Rebuild full OCR text for PDF contexts (not truncated chunk stitches)."""

from django.core.management.base import BaseCommand

from apps.pdf_module.models import PDFContext
from apps.pdf_module.ocr_full import clean_stored_ocr_text, rebuild_context_ocr


class Command(BaseCommand):
    help = (
        "Rebuild full-document OCR text for PDF contexts "
        "(fixes truncated hierarchical-chunk backfills)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--only", default="", help="Filter by name substring")
        parser.add_argument(
            "--force-vision",
            action="store_true",
            help="Always use Unlimited-OCR (slow); default uses native+legacy when rich",
        )
        parser.add_argument(
            "--short-only",
            action="store_true",
            help="Only rebuild contexts whose ocr_text looks truncated (< 800 words)",
        )
        parser.add_argument(
            "--clean-only",
            action="store_true",
            help="Only collapse OCR loops / strip noise on stored text (no re-OCR)",
        )

    def handle(self, *args, **options):
        qs = PDFContext.objects.all().order_by("name")
        only = (options.get("only") or "").strip()
        if only:
            qs = qs.filter(name__icontains=only)
        force_vision = bool(options.get("force_vision"))
        short_only = bool(options.get("short_only"))
        clean_only = bool(options.get("clean_only"))

        done = 0
        skipped = 0
        for ctx in qs.iterator():
            words = len((ctx.ocr_text or "").split())
            if short_only and words >= 800:
                skipped += 1
                continue
            try:
                before = len(ctx.ocr_text or "")
                if clean_only:
                    n = clean_stored_ocr_text(ctx)
                else:
                    n = rebuild_context_ocr(ctx, force_vision=force_vision)
                ctx.refresh_from_db()
                self.stdout.write(
                    f"OK {ctx.name[:70]}  chars={n} (was {before}) "
                    f"words={len((ctx.ocr_text or '').split())}"
                )
                self.stdout.flush()
                done += 1
            except Exception as exc:
                self.stderr.write(f"FAIL {ctx.name[:70]}: {exc}")
                self.stderr.flush()
        self.stdout.write(self.style.SUCCESS(f"Rebuilt {done}, skipped {skipped}"))
        self.stdout.flush()
