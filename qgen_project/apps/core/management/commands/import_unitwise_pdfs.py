"""Import every unitwise PDF under BharatGen/Bharatgen- unitwise as its own PDFContext."""

from __future__ import annotations

import re
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.embeddings import DEFAULT_EMBED_MODEL
from apps.core.models import (
    ModelConfig,
    Organization,
    OrganizationSettings,
    StorageQuota,
    User,
    UserProvisioningQuota,
)
from apps.core.storage import StorageQuotaExceeded, reserve_pdf_storage
from apps.pdf_module.models import ChunkingStrategy, PDFContext
from apps.pdf_module.tasks import index_pdf_context

DEFAULT_ROOT = Path(
    "/app/BharatGen/Bharatgen- unitwise"
)


def _course_code(course_folder: str) -> str:
    """Normalize course folder to a short code label."""
    cleaned = re.sub(r"^\d+\.", "", course_folder).strip()
    if "_" in cleaned:
        return cleaned.split("_", 1)[0].strip()
    return cleaned.strip()


class Command(BaseCommand):
    help = (
        "Import ALL unitwise PDFs from Bharatgen- unitwise "
        "(one PDFContext per PDF; queues Celery indexing)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--root",
            default=str(DEFAULT_ROOT),
            help="Path to Bharatgen- unitwise directory",
        )
        parser.add_argument("--org", default="IGNOV Demo")
        parser.add_argument("--user", default="admin")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Index synchronously (slow; default uses Celery)",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            default=True,
            help="Skip PDFContexts that already exist by name (default)",
        )
        parser.add_argument(
            "--force-reimport",
            action="store_true",
            help="Import even if a context with the same name exists",
        )

    def handle(self, *args, **options):
        root = Path(options["root"])
        if not root.is_dir():
            raise CommandError(f"Unitwise root not found: {root}")

        org = Organization.objects.filter(name=options["org"]).first()
        if not org:
            raise CommandError(f"Organization not found: {options['org']}")

        user = User.objects.filter(username=options["user"], organization=org).first()
        if not user:
            raise CommandError(f"User not found: {options['user']} in {org.name}")

        self._ensure_bootstrap(org, user)

        pdfs = sorted(root.rglob("*.pdf"))
        self.stdout.write(f"Found {len(pdfs)} unitwise PDFs under {root}")

        planned = []
        for pdf in pdfs:
            rel = pdf.relative_to(root)
            parts = rel.parts
            if len(parts) < 3:
                # BA ENGLISH / Course / file.pdf  OR BA HINDI / Course / file.pdf
                self.stderr.write(f"  Unexpected path (skip): {rel}")
                continue
            stream, course_folder, filename = parts[0], parts[1], parts[-1]
            code = _course_code(course_folder)
            stem = Path(filename).stem
            name = f"{code} — {stem}"[:256]
            planned.append((pdf, name, stream, course_folder))

        if options["dry_run"]:
            for pdf, name, stream, course in planned:
                self.stdout.write(f"  {stream}/{course}: {name}")
            self.stdout.write(self.style.WARNING(f"Dry run: {len(planned)} PDFs"))
            return

        created = 0
        skipped = 0
        failed = 0
        skip_existing = not options["force_reimport"]

        for pdf, name, stream, course in planned:
            if skip_existing and PDFContext.objects.filter(
                organization=org, name=name
            ).exists():
                skipped += 1
                continue
            try:
                payload = pdf.read_bytes()
                with transaction.atomic():
                    ctx = PDFContext(
                        organization=org,
                        created_by=user,
                        name=name,
                        description=(
                            f"Unitwise PDF from Bharatgen- unitwise "
                            f"({stream} / {course})"
                        ),
                        strategy=ChunkingStrategy.HIERARCHICAL,
                        chunk_size=512,
                        chunk_overlap=64,
                        embed_model=DEFAULT_EMBED_MODEL,
                        file_size_bytes=len(payload),
                        original_filename=pdf.name,
                    )
                    ctx.zip_path.save(pdf.name, ContentFile(payload), save=False)
                    ctx.save()
                    try:
                        reserve_pdf_storage(user, len(payload), count_delta=1)
                    except StorageQuotaExceeded as exc:
                        ctx.zip_path.delete(save=False)
                        ctx.delete()
                        raise CommandError(str(exc)) from exc

                if options["sync"]:
                    index_pdf_context(str(ctx.id))
                else:
                    index_pdf_context.delay(str(ctx.id))
                created += 1
                if created % 20 == 0:
                    self.stdout.write(f"  … queued {created}/{len(planned)}")
            except Exception as exc:
                failed += 1
                self.stderr.write(f"  FAIL {name}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: created={created} skipped={skipped} failed={failed} "
                f"total_on_disk={len(planned)}"
            )
        )

    def _ensure_bootstrap(self, org: Organization, user: User) -> None:
        """Ensure ModelConfig + generous quotas exist on a fresh demo DB."""
        if not ModelConfig.objects.filter(is_default=True).exists():
            ModelConfig.objects.create(
                name="Demo Default",
                provider="ollama",
                llm_model_id="qwen3:8b",
                embed_model_id=DEFAULT_EMBED_MODEL,
                temperature=0.3,
                max_tokens=2048,
                is_default=True,
            )
            self.stdout.write("Created default ModelConfig")

        OrganizationSettings.objects.get_or_create(organization=org)

        sq, _ = StorageQuota.objects.get_or_create(user=user)
        # Large enough for all unitwise PDFs + embeddings
        changed = False
        for field, value in (
            ("max_total_storage_gb", 50.0),
            ("max_vector_storage_gb", 50.0),
            ("max_saved_pdf_zips", 500),
            ("max_saved_pyq_zips", 200),
        ):
            if hasattr(sq, field) and float(getattr(sq, field) or 0) < float(value):
                setattr(sq, field, value)
                changed = True
        if changed:
            sq.save()
            self.stdout.write("Raised admin StorageQuota for bulk import")

        uq, _ = UserProvisioningQuota.objects.get_or_create(user=user)
        if hasattr(uq, "monthly_credit_limit"):
            if (uq.monthly_credit_limit or 0) < 500_000:
                uq.monthly_credit_limit = 500_000
                uq.save(update_fields=["monthly_credit_limit"])
