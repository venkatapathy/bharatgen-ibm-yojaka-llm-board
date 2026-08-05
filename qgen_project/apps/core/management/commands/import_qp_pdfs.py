"""Import all BharatGen TEE QP PDFs into the demo PYQ bank."""

from __future__ import annotations

import re
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import ModelConfig, Organization, StorageQuota, User
from apps.core.storage import StorageQuotaExceeded, reserve_pyq_storage
from apps.pyq_module.models import PYQModule
from apps.pyq_module.tasks import extract_pyq_questions

DEFAULT_ROOT = Path("/app/BharatGen")


def _pyq_name(pdf: Path, course_folder: str) -> str:
    """BEGC-101_DEC24.pdf under BEGC 101_ … → BEGC-101 DEC24."""
    stem = pdf.stem.replace("_", " ").strip()
    return stem[:256]


def _course_code(course_folder: str) -> str:
    cleaned = re.sub(r"^\d+\.", "", course_folder).strip()
    if "_" in cleaned:
        return cleaned.split("_", 1)[0].strip()
    return cleaned


class Command(BaseCommand):
    help = (
        "Import ALL BharatGen **/QP/*.pdf into PYQ modules and queue "
        "Unlimited-OCR + LLM question extraction."
    )

    def add_arguments(self, parser):
        parser.add_argument("--root", default=str(DEFAULT_ROOT))
        parser.add_argument("--org", default="IGNOV Demo")
        parser.add_argument("--user", default="admin")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-import / re-extract even if a module with the same name exists",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run extraction synchronously (slow; default uses Celery)",
        )
        parser.add_argument(
            "--only",
            default="",
            help="Filter by filename substring (e.g. BHDC-110 or DEC24)",
        )

    def handle(self, *args, **options):
        root = Path(options["root"])
        if not root.is_dir():
            raise CommandError(f"BharatGen root not found: {root}")

        org = Organization.objects.filter(name=options["org"]).first()
        if not org:
            raise CommandError(f"Organization not found: {options['org']}")
        user = User.objects.filter(username=options["user"], organization=org).first()
        if not user:
            raise CommandError(f"User not found: {options['user']} in {org.name}")

        config = ModelConfig.objects.filter(is_default=True).first()
        if not config:
            raise CommandError("No default ModelConfig for PYQ LLM extraction")

        self._ensure_quota(user)

        qps = sorted(root.rglob("QP/*.pdf"))
        if options["only"]:
            qps = [p for p in qps if options["only"] in p.name]

        self.stdout.write(f"Found {len(qps)} QP PDFs under {root}")
        if options["dry_run"]:
            for p in qps:
                rel = p.relative_to(root)
                self.stdout.write(f"  {rel}")
            return

        created = 0
        skipped = 0
        failed = 0
        for pdf in qps:
            rel = pdf.relative_to(root)
            # BA ENGLISH / Course / QP / file.pdf
            parts = rel.parts
            course_folder = parts[1] if len(parts) >= 4 else ""
            code = _course_code(course_folder)
            name = _pyq_name(pdf, course_folder)
            desc = f"TEE QP · {code} · {pdf.name}"

            existing = PYQModule.objects.filter(organization=org, name=name).first()
            if existing and not options["force"]:
                skipped += 1
                self.stdout.write(f"  skip existing: {name}")
                continue
            if existing and options["force"]:
                existing.delete()

            try:
                payload = pdf.read_bytes()
                with transaction.atomic():
                    mod = PYQModule(
                        organization=org,
                        created_by=user,
                        name=name,
                        description=desc,
                        file_size_bytes=len(payload),
                        original_filename=pdf.name,
                        status="pending",
                    )
                    mod.source_file.save(pdf.name, ContentFile(payload), save=False)
                    mod.save()
                    try:
                        reserve_pyq_storage(user, len(payload))
                    except StorageQuotaExceeded as exc:
                        mod.source_file.delete(save=False)
                        mod.delete()
                        raise CommandError(str(exc)) from exc

                if options["sync"]:
                    extract_pyq_questions(mod.pk, config.pk)
                else:
                    extract_pyq_questions.delay(mod.pk, config.pk)
                created += 1
                self.stdout.write(f"  queued: {name}")
            except Exception as exc:
                failed += 1
                self.stderr.write(f"  FAIL {pdf.name}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: created={created} skipped={skipped} failed={failed} "
                f"total_qp={len(qps)}"
            )
        )

    def _ensure_quota(self, user: User) -> None:
        sq, _ = StorageQuota.objects.get_or_create(user=user)
        changed = False
        for field, value in (
            ("max_total_storage_gb", 50.0),
            ("max_saved_pyq_zips", 200),
        ):
            if float(getattr(sq, field) or 0) < float(value):
                setattr(sq, field, value)
                changed = True
        if changed:
            sq.save()
            self.stdout.write("Raised admin PYQ storage quota")
