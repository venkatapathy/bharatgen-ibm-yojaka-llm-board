"""Seed per-user demo PDF/PYQ/run data with ownership isolation."""

from __future__ import annotations

import random
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.models import User
from apps.core.provisioning import get_credit_quota, get_org_policy
from apps.core.storage import get_storage_quota, org_storage_budget, recompute_vector_storage
from apps.pdf_module.models import PDFChunk, PDFContext
from apps.pyq_module.models import PYQModule, Question
from apps.question_generation.models import BatchRun, BatchRunItem

# Realistic monthly lab quotas (rule-based: ~5.75k–8.25k credits per question).
DEMO_ORG_CREDIT_POOL = 1_000_000
DEMO_USER_CREDIT_LIMIT = 150_000


class Command(BaseCommand):
    help = (
        "Clone admin PDF/PYQ assets to regular users (with overlap) and "
        "create demo generation runs based on existing completed runs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument(
            "--pdfs-per-user",
            type=int,
            default=4,
            help="How many PDF contexts to assign to each user (with replacement across users).",
        )
        parser.add_argument(
            "--pyqs-per-user",
            type=int,
            default=3,
            help="How many PYQ modules to assign to each user.",
        )
        parser.add_argument(
            "--runs-per-user",
            type=int,
            default=2,
            help="How many demo generation runs to create per user.",
        )
        parser.add_argument(
            "--clear-user-content",
            action="store_true",
            help="Delete existing PDF/PYQ/runs owned by target users before seeding.",
        )

    def handle(self, *args, **options):
        rng = random.Random(options["seed"])
        users = list(
            User.objects.filter(role=User.Role.USER, is_active_member=True).order_by(
                "username"
            )
        )
        if not users:
            self.stderr.write("No regular users found.")
            return

        admin = User.objects.filter(role=User.Role.SUPERUSER).order_by("id").first()
        source_pdfs = list(
            PDFContext.objects.filter(status="ready")
            .order_by("name")
        )
        if admin:
            admin_pdfs = list(
                PDFContext.objects.filter(created_by=admin, status="ready").order_by("name")
            )
            if admin_pdfs:
                source_pdfs = admin_pdfs

        source_pyqs = list(PYQModule.objects.filter(status="ready").order_by("name"))
        if admin:
            admin_pyqs = list(
                PYQModule.objects.filter(created_by=admin, status="ready").order_by("name")
            )
            if admin_pyqs:
                source_pyqs = admin_pyqs

        source_runs = list(
            BatchRun.objects.filter(status=BatchRun.Status.COMPLETED)
            .prefetch_related("items", "pdf_contexts", "pyq_modules", "questions")
            .order_by("-id")[:12]
        )
        # Prefer reference runs that already have generated questions.
        source_runs = sorted(
            source_runs,
            key=lambda r: r.questions.filter(is_generated=True).count(),
            reverse=True,
        )

        if not source_pdfs:
            self.stderr.write("No ready PDF contexts to clone.")
            return

        self.stdout.write(
            f"Users={len(users)} source_pdfs={len(source_pdfs)} "
            f"source_pyqs={len(source_pyqs)} source_runs={len(source_runs)}"
        )

        if options["clear_user_content"]:
            for user in users:
                BatchRun.objects.filter(created_by=user).delete()
                PYQModule.objects.filter(created_by=user).delete()
                PDFContext.objects.filter(created_by=user).delete()
                self.stdout.write(f"  Cleared prior content for {user.username}")

        for user in users:
            self._ensure_quota(user)

        self._ensure_org_pools(users)

        # Map source PDF name -> list of cloned contexts per user
        user_pdf_map: dict[int, dict[str, PDFContext]] = {u.id: {} for u in users}
        user_pyq_map: dict[int, dict[str, PYQModule]] = {u.id: {} for u in users}

        for user in users:
            picks = self._pick_with_overlap(
                source_pdfs, options["pdfs_per_user"], rng
            )
            for src in picks:
                if src.name in user_pdf_map[user.id]:
                    continue
                clone = self._clone_pdf(src, user)
                user_pdf_map[user.id][src.name] = clone
                self.stdout.write(f"  PDF  {user.username}: {clone.name}")

            if source_pyqs:
                pyq_picks = self._pick_with_overlap(
                    source_pyqs, options["pyqs_per_user"], rng
                )
                for src in pyq_picks:
                    if src.name in user_pyq_map[user.id]:
                        continue
                    clone = self._clone_pyq(src, user)
                    user_pyq_map[user.id][src.name] = clone
                    self.stdout.write(f"  PYQ  {user.username}: {clone.name}")

            recompute_vector_storage(user)

        # Demo runs from reference completed runs
        if not source_runs:
            self.stdout.write(self.style.WARNING("No completed runs to clone as demos."))
        else:
            for user in users:
                refs = list(source_runs)
                rng.shuffle(refs)
                created = 0
                for ref in refs:
                    if created >= options["runs_per_user"]:
                        break
                    run = self._clone_run(
                        ref,
                        user,
                        user_pdf_map[user.id],
                        user_pyq_map[user.id],
                        rng,
                    )
                    if run is None:
                        continue
                    created += 1
                    self.stdout.write(
                        f"  RUN  {user.username}: #{run.id} {run.name} "
                        f"({run.questions.count()} qs)"
                    )

        self._sync_credit_usage_from_runs(users)
        self.stdout.write(self.style.SUCCESS("Demo seed complete."))

    def _ensure_quota(self, user):
        quota = get_storage_quota(user)
        changed = False
        if float(quota.max_total_storage_gb) < 25:
            quota.max_total_storage_gb = 25.0
            changed = True
        if float(quota.max_vector_storage_gb) < 15:
            quota.max_vector_storage_gb = 15.0
            changed = True
        if quota.max_saved_pdf_zips < 50:
            quota.max_saved_pdf_zips = 50
            changed = True
        if quota.max_saved_pyq_zips < 50:
            quota.max_saved_pyq_zips = 50
            changed = True
        if changed:
            quota.save()

        credits = get_credit_quota(user)
        if user.role == User.Role.USER:
            credits.monthly_credit_limit = DEMO_USER_CREDIT_LIMIT
            credits.save(update_fields=["monthly_credit_limit", "updated_at"])

    def _credits_from_runs(self, user) -> int:
        from apps.core.provisioning import estimate_batch_run_credits

        total = 0
        for run in BatchRun.objects.filter(created_by=user).exclude(status="failed"):
            n = run.questions.count()
            if n <= 0:
                continue
            total += estimate_batch_run_credits(run, question_count=n)
        return total

    def _ensure_org_pools(self, users):
        """Keep org credit/storage pools demo-ready."""
        org = next((u.organization for u in users if u.organization_id), None)
        if org is None:
            return
        policy = get_org_policy(org)
        policy.credit_pool = DEMO_ORG_CREDIT_POOL
        policy.default_monthly_credits = DEMO_USER_CREDIT_LIMIT
        policy.save(update_fields=["credit_pool", "default_monthly_credits"])

        budget = org_storage_budget(org)
        storage_need = max(budget["storage_assigned"] + 20.0, 120.0)
        vector_need = max(budget["vector_assigned"] + 20.0, 80.0)
        if float(policy.storage_pool_gb) < storage_need:
            policy.storage_pool_gb = storage_need
        if float(policy.vector_storage_pool_gb) < vector_need:
            policy.vector_storage_pool_gb = vector_need
        policy.save(update_fields=["storage_pool_gb", "vector_storage_pool_gb"])
        self.stdout.write(
            f"  Org pools: credits={DEMO_ORG_CREDIT_POOL:,} "
            f"default/user={DEMO_USER_CREDIT_LIMIT:,}"
        )

    def _sync_credit_usage_from_runs(self, users):
        """Set monthly usage from rule-based cost of each member's generation runs."""
        org = next((u.organization for u in users if u.organization_id), None)
        if org is None:
            return
        members = list(
            User.objects.filter(organization=org).exclude(role=User.Role.SUPERUSER)
        )
        for member in members:
            credits = get_credit_quota(member)
            if member.role == User.Role.USER:
                credits.monthly_credit_limit = DEMO_USER_CREDIT_LIMIT
            else:
                credits.monthly_credit_limit = 0
            credits.current_month_credits_used = self._credits_from_runs(member)
            credits.save(
                update_fields=[
                    "monthly_credit_limit",
                    "current_month_credits_used",
                    "updated_at",
                ]
            )
        self.stdout.write(f"  Credits usage synced from runs for {len(members)} members")

    def _pick_with_overlap(self, items, count, rng):
        if not items:
            return []
        count = max(1, min(count, len(items)))
        # Prefer sample without replacement per user; overlap comes across users.
        return rng.sample(items, k=count)

    def _clone_pdf(self, src: PDFContext, user: User) -> PDFContext:
        existing = PDFContext.objects.filter(
            created_by=user, name=src.name, status="ready"
        ).first()
        if existing:
            return existing

        with transaction.atomic():
            clone = PDFContext(
                organization=user.organization,
                created_by=user,
                name=src.name,
                description=src.description,
                strategy=src.strategy,
                chunk_size=src.chunk_size,
                chunk_overlap=src.chunk_overlap,
                embed_model=src.embed_model,
                reranker_model=src.reranker_model,
                status="ready",
                error_message="",
                file_size_bytes=src.file_size_bytes,
                original_filename=src.original_filename,
                has_embedding=src.has_embedding,
                needs_reindex=False,
                embedded_chunk_count=src.embedded_chunk_count,
            )
            if src.zip_path:
                src.zip_path.open("rb")
                try:
                    data = src.zip_path.read()
                finally:
                    src.zip_path.close()
                filename = Path(src.original_filename or src.zip_path.name).name
                clone.zip_path.save(filename, ContentFile(data), save=False)
            clone.save()

            chunks = []
            for ch in src.chunks.all().iterator(chunk_size=200):
                chunks.append(
                    PDFChunk(
                        context=clone,
                        source_file=ch.source_file,
                        page_number=ch.page_number,
                        chunk_index=ch.chunk_index,
                        text=ch.text,
                        embedding=ch.embedding,
                        token_count=ch.token_count,
                        metadata=ch.metadata or {},
                    )
                )
                if len(chunks) >= 200:
                    PDFChunk.objects.bulk_create(chunks)
                    chunks = []
            if chunks:
                PDFChunk.objects.bulk_create(chunks)

            quota = get_storage_quota(user)
            quota.current_saved_pdf_zips += 1
            quota.current_total_storage_gb = float(quota.current_total_storage_gb) + (
                max(src.file_size_bytes, 0) / (1024**3)
            )
            quota.save(
                update_fields=[
                    "current_saved_pdf_zips",
                    "current_total_storage_gb",
                    "updated_at",
                ]
            )
        return clone

    def _clone_pyq(self, src: PYQModule, user: User) -> PYQModule:
        existing = PYQModule.objects.filter(
            created_by=user, name=src.name, status="ready"
        ).first()
        if existing:
            return existing

        with transaction.atomic():
            clone = PYQModule(
                organization=user.organization,
                created_by=user,
                name=src.name,
                description=src.description,
                status="ready",
                error_msg="",
                file_size_bytes=src.file_size_bytes,
                original_filename=src.original_filename,
            )
            if src.source_file:
                src.source_file.open("rb")
                try:
                    data = src.source_file.read()
                finally:
                    src.source_file.close()
                filename = Path(src.original_filename or src.source_file.name).name
                clone.source_file.save(filename, ContentFile(data), save=False)
            clone.save()

            questions = [
                Question(
                    pyq_module=clone,
                    is_generated=False,
                    question_type=q.question_type,
                    bloom=q.bloom,
                    marks=q.marks,
                    question_text=q.question_text,
                    reference_answer=q.reference_answer,
                    rubrics=q.rubrics or {},
                    topic=q.topic,
                    options=q.options or [],
                    rag_chunks=[],
                    pyq_examples=[],
                    user_decision=Question.UserDecision.PENDING,
                    user_feedback="",
                )
                for q in src.questions.filter(is_generated=False)
            ]
            if questions:
                Question.objects.bulk_create(questions, batch_size=200)

            quota = get_storage_quota(user)
            quota.current_saved_pyq_zips += 1
            quota.current_total_storage_gb = float(quota.current_total_storage_gb) + (
                max(src.file_size_bytes, 0) / (1024**3)
            )
            quota.save(
                update_fields=[
                    "current_saved_pyq_zips",
                    "current_total_storage_gb",
                    "updated_at",
                ]
            )
        return clone

    def _clone_run(
        self,
        ref: BatchRun,
        user: User,
        pdf_by_name: dict[str, PDFContext],
        pyq_by_name: dict[str, PYQModule],
        rng: random.Random,
    ):
        # Prefer PDFs/PYQs that match the reference; otherwise fall back to any owned assets.
        pdfs = [
            pdf_by_name[name]
            for name in ref.pdf_contexts.values_list("name", flat=True)
            if name in pdf_by_name
        ]
        if not pdfs and pdf_by_name:
            pdfs = rng.sample(list(pdf_by_name.values()), k=min(2, len(pdf_by_name)))
        pyqs = [
            pyq_by_name[name]
            for name in ref.pyq_modules.values_list("name", flat=True)
            if name in pyq_by_name
        ]
        if not pyqs and pyq_by_name:
            pyqs = rng.sample(list(pyq_by_name.values()), k=min(2, len(pyq_by_name)))

        if not pdfs and not pyqs:
            return None

        name = f"Demo — {ref.name}"
        if BatchRun.objects.filter(created_by=user, name=name).exists():
            name = f"Demo — {ref.name} ({user.username})"

        with transaction.atomic():
            run = BatchRun.objects.create(
                name=name,
                topic=ref.topic or "Demo topic",
                language=ref.language or BatchRun.Language.ENGLISH,
                prompt=ref.prompt,
                model_config=ref.model_config,
                rag_top_k=ref.rag_top_k,
                pyq_shots=ref.pyq_shots,
                council_enabled=False,
                status=BatchRun.Status.COMPLETED,
                expected_questions=ref.expected_questions
                or ref.questions.filter(is_generated=True).count(),
                created_by=user,
                completed_at=timezone.now(),
            )
            if pdfs:
                run.pdf_contexts.set(pdfs)
            if pyqs:
                run.pyq_modules.set(pyqs)

            for item in ref.items.all():
                BatchRunItem.objects.create(
                    batch_run=run,
                    question_type=item.question_type,
                    bloom=item.bloom,
                    marks=item.marks,
                    count=item.count,
                    status="done",
                )

            gen_qs = list(ref.questions.filter(is_generated=True)[:20])
            if gen_qs:
                Question.objects.bulk_create(
                    [
                        Question(
                            batch_run=run,
                            is_generated=True,
                            question_type=q.question_type,
                            bloom=q.bloom,
                            marks=q.marks,
                            question_text=q.question_text,
                            reference_answer=q.reference_answer,
                            rubrics=q.rubrics or {},
                            topic=q.topic,
                            options=q.options or [],
                            rag_chunks=q.rag_chunks or [],
                            pyq_examples=q.pyq_examples or [],
                            user_decision=Question.UserDecision.PENDING,
                            user_feedback="",
                        )
                        for q in gen_qs
                    ]
                )
        return run
