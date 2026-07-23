"""Bulk-import BA Hindi BharatGen PDFs into PDF Context and PYQ modules."""

import io
import re
import zipfile
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.embeddings import DEFAULT_EMBED_MODEL
from apps.core.models import ModelConfig, Organization, User
from apps.core.storage import StorageQuotaExceeded, reserve_pdf_storage, reserve_pyq_storage
from apps.pdf_module.models import ChunkingStrategy, PDFContext
from apps.pdf_module.tasks import index_pdf_context
from apps.pyq_module.models import PYQModule
from apps.pyq_module.tasks import extract_pyq_questions

BA_HINDI_PREFIX = "BharatGen/BA HINDI/"
ASSIGNMENT_PREFIX = "BharatGen/ASSIGNMNET QUESTIONS/BA HINDI/"
DEFAULT_ZIP = "BharatGen-20260425T162924Z-3-001.zip"


def _course_label(folder_name: str) -> str:
    """1.BHDC-110_ हिंदी कहानी -> BHDC-110 - हिंदी कहानी"""
    cleaned = re.sub(r"^\d+\.", "", folder_name).strip()
    if "_" in cleaned:
        code, title = cleaned.split("_", 1)
        return f"{code.strip()} - {title.strip()}"
    return cleaned


def _pyq_name(filename: str) -> str:
    stem = PurePosixPath(filename).stem
    return stem.replace("_", " ")


class Command(BaseCommand):
    help = "Import BA Hindi egyankosh PDFs (PDF Context) and assignment/QP PDFs (PYQ)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--zip-path",
            default=DEFAULT_ZIP,
            help=f"Path to BharatGen zip (default: {DEFAULT_ZIP})",
        )
        parser.add_argument("--org", default="EduQGen Demo Lab")
        parser.add_argument("--user", default="admin")
        parser.add_argument("--skip-pdf", action="store_true")
        parser.add_argument("--skip-pyq", action="store_true")
        parser.add_argument(
            "--skip-qp",
            action="store_true",
            help="Skip term-end QP PDFs (default: import QP + assignments)",
        )
        parser.add_argument(
            "--include-qp",
            action="store_true",
            help="Deprecated; QP PDFs are imported by default. Kept for compatibility.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run indexing/extraction synchronously (no Celery)",
        )

    def handle(self, *args, **options):
        zip_path = options["zip_path"]
        try:
            archive = zipfile.ZipFile(zip_path, "r")
        except FileNotFoundError as exc:
            raise CommandError(f"Zip not found: {zip_path}") from exc

        org = Organization.objects.filter(name=options["org"]).first()
        if not org:
            raise CommandError(f"Organization not found: {options['org']}")

        user = User.objects.filter(username=options["user"], organization=org).first()
        if not user:
            raise CommandError(f"User not found: {options['user']} in {org.name}")

        default_config = ModelConfig.objects.filter(is_default=True).first()
        if not options["skip_pyq"] and not default_config:
            raise CommandError("No default ModelConfig; PYQ extraction requires one.")

        courses = self._discover_courses(archive)
        include_qp = not options["skip_qp"]
        qp_files = self._discover_qp_files(archive) if include_qp else []
        assignment_files = self._discover_assignment_files(archive)

        self.stdout.write(f"Found {len(courses)} BA Hindi courses")
        self.stdout.write(f"Found {len(qp_files)} QP PDFs")
        self.stdout.write(f"Found {len(assignment_files)} assignment PDFs")

        if options["dry_run"]:
            for course, pdfs in sorted(courses.items()):
                self.stdout.write(
                    f"  PDF Context: {_course_label(course)} ({len(pdfs)} blocks)"
                )
            for path in qp_files + assignment_files:
                self.stdout.write(f"  PYQ: {PurePosixPath(path).name}")
            return

        pdf_created = 0
        pyq_created = 0

        if not options["skip_pdf"]:
            pdf_created = self._import_pdf_contexts(
                archive, courses, org, user, options["sync"]
            )

        if not options["skip_pyq"]:
            pyq_created = self._import_pyq_modules(
                archive,
                qp_files + assignment_files,
                org,
                user,
                default_config,
                options["sync"],
            )

        archive.close()
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued {pdf_created} PDF context(s) and {pyq_created} PYQ module(s)."
            )
        )

    def _discover_courses(self, archive: zipfile.ZipFile) -> dict[str, list[str]]:
        courses: dict[str, list[str]] = {}
        for name in archive.namelist():
            if not name.startswith(BA_HINDI_PREFIX) or "/egyankosh/" not in name:
                continue
            if not name.lower().endswith(".pdf"):
                continue
            rel = name[len(BA_HINDI_PREFIX) :]
            course_folder = rel.split("/", 1)[0]
            courses.setdefault(course_folder, []).append(name)
        return courses

    def _discover_qp_files(self, archive: zipfile.ZipFile) -> list[str]:
        return sorted(
            name
            for name in archive.namelist()
            if name.startswith(BA_HINDI_PREFIX)
            and "/QP/" in name
            and name.lower().endswith(".pdf")
        )

    def _discover_assignment_files(self, archive: zipfile.ZipFile) -> list[str]:
        return sorted(
            name
            for name in archive.namelist()
            if name.startswith(ASSIGNMENT_PREFIX) and name.lower().endswith(".pdf")
        )

    def _import_pdf_contexts(self, archive, courses, org, user, sync: bool) -> int:
        created = 0
        for course_folder, pdf_paths in sorted(courses.items()):
            label = _course_label(course_folder)
            if PDFContext.objects.filter(organization=org, name=label).exists():
                self.stdout.write(f"  Skip existing PDF context: {label}")
                continue

            zip_bytes = io.BytesIO()
            with zipfile.ZipFile(zip_bytes, "w", zipfile.ZIP_DEFLATED) as out_zip:
                for path in sorted(pdf_paths):
                    out_zip.writestr(PurePosixPath(path).name, archive.read(path))
            payload = zip_bytes.getvalue()
            zip_name = re.sub(r"[^\w\-]+", "_", label)[:80] + ".zip"

            with transaction.atomic():
                ctx = PDFContext(
                    organization=org,
                    created_by=user,
                    name=label,
                    description="BharatGen BA Hindi eGyanKosh course material",
                    strategy=ChunkingStrategy.FIXED_SIZE,
                    chunk_size=512,
                    chunk_overlap=64,
                    embed_model=DEFAULT_EMBED_MODEL,
                    file_size_bytes=len(payload),
                    original_filename=zip_name,
                )
                ctx.zip_path.save(zip_name, ContentFile(payload), save=False)
                ctx.save()
                try:
                    reserve_pdf_storage(user, len(payload))
                except StorageQuotaExceeded as exc:
                    ctx.zip_path.delete(save=False)
                    ctx.delete()
                    raise CommandError(str(exc)) from exc

            if sync:
                index_pdf_context(str(ctx.id))
            else:
                index_pdf_context.delay(str(ctx.id))

            created += 1
            self.stdout.write(f"  PDF context queued: {label} ({len(pdf_paths)} PDFs)")
        return created

    def _import_pyq_modules(
        self, archive, paths, org, user, default_config, sync: bool
    ) -> int:
        created = 0
        for path in paths:
            filename = PurePosixPath(path).name
            name = _pyq_name(filename)
            if PYQModule.objects.filter(organization=org, name=name).exists():
                self.stdout.write(f"  Skip existing PYQ: {name}")
                continue

            payload = archive.read(path)
            with transaction.atomic():
                mod = PYQModule(
                    organization=org,
                    created_by=user,
                    name=name,
                    description="BharatGen BA Hindi question paper",
                    file_size_bytes=len(payload),
                    original_filename=filename,
                )
                mod.source_file.save(filename, ContentFile(payload), save=False)
                mod.save()
                try:
                    reserve_pyq_storage(user, len(payload))
                except StorageQuotaExceeded as exc:
                    mod.source_file.delete(save=False)
                    mod.delete()
                    raise CommandError(str(exc)) from exc

            if sync:
                extract_pyq_questions(mod.pk, default_config.pk)
            else:
                extract_pyq_questions.delay(mod.pk, default_config.pk)

            created += 1
            self.stdout.write(f"  PYQ queued: {name}")
        return created
