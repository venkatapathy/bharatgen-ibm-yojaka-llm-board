import csv
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from apps.core.models import ModelConfig, User
from apps.core.ownership import (
    owned_batch_runs,
    owned_generated_questions,
    owned_pdf_contexts,
    owned_pyq_modules,
)
from apps.core.provisioning import (
    CREDITS_PER_QUESTION,
    CREDITS_PYQ_PER_QUESTION,
    CREDITS_RAG_PER_QUESTION,
    CREDITS_THINK_PER_QUESTION,
    ProvisioningError,
    batch_run_credits_used,
    credit_quota_display,
    ensure_credit_headroom,
    estimate_batch_run_credits,
)
from apps.prompt_module.models import PromptTemplate
from apps.pyq_module.models import Question, QuestionType, BloomLevel

from .export import build_docx
from .models import BatchRun, BatchRunItem


def _is_platform_admin(user) -> bool:
    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "role", "") == User.Role.SUPERUSER
    )


def _batch_run_queryset(user):
    return owned_batch_runs(user)


def _generated_question_queryset(user):
    return owned_generated_questions(user)


class BatchRunListView(LoginRequiredMixin, ListView):
    model = BatchRun
    template_name = 'question_generation/run_list.html'
    context_object_name = 'runs'

    def get_queryset(self):
        return _batch_run_queryset(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["credits"] = credit_quota_display(user)
        ctx["show_owner"] = bool(
            getattr(user, "is_superuser", False)
            or getattr(user, "is_orguser", False)
        )
        return ctx


class BatchRunNewView(LoginRequiredMixin, CreateView):
    model = BatchRun
    template_name = 'question_generation/run_new.html'
    fields = ['name', 'topic', 'pdf_contexts', 'pyq_modules',
              'prompt', 'model_config', 'rag_top_k', 'pyq_shots']
    success_url = reverse_lazy('question_generation:list')

    def get_context_data(self, **kwargs):
        from .council import get_active_council_models

        ctx = super().get_context_data(**kwargs)
        ctx['credits'] = credit_quota_display(self.request.user)
        ctx['credit_tariff'] = {
            "per_question": CREDITS_PER_QUESTION,
            "rag": CREDITS_RAG_PER_QUESTION,
            "pyq": CREDITS_PYQ_PER_QUESTION,
            "think": CREDITS_THINK_PER_QUESTION,
        }
        ctx['pdf_contexts']  = owned_pdf_contexts(self.request.user, ready_only=True)
        ctx['pyq_modules']   = owned_pyq_modules(self.request.user, ready_only=True)
        ctx['prompts']       = PromptTemplate.objects.all()
        ctx['model_configs'] = ModelConfig.objects.all()
        ctx['think_available'] = bool(get_active_council_models())
        ctx['hindi_prompt_id'] = (
            PromptTemplate.objects.filter(name__iexact='Hindi Generator')
            .values_list('id', flat=True)
            .first()
        )
        ctx['english_prompt_id'] = (
            PromptTemplate.objects.filter(is_active=True)
            .values_list('id', flat=True)
            .first()
            or PromptTemplate.objects.filter(name__iexact='Default Generator')
            .values_list('id', flat=True)
            .first()
        )
        ctx['question_types'] = QuestionType.choices
        ctx['bloom_levels']   = BloomLevel.choices
        ctx['show_advanced_settings'] = _is_platform_admin(self.request.user)
        return ctx

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['pdf_contexts'].queryset = owned_pdf_contexts(
            self.request.user, ready_only=True
        )
        form.fields['pyq_modules'].queryset = owned_pyq_modules(
            self.request.user, ready_only=True
        )
        # Users/Org Admins never submit these; Admin sets platform defaults.
        if not _is_platform_admin(self.request.user):
            for name in ("prompt", "model_config", "rag_top_k", "pyq_shots"):
                form.fields[name].required = False
        return form

    def form_valid(self, form):
        from .tasks import run_batch
        from .council import get_active_council_models

        form.instance.created_by = self.request.user
        data = self.request.POST
        language = data.get('language', BatchRun.Language.ENGLISH)
        if language not in {BatchRun.Language.ENGLISH, BatchRun.Language.HINDI}:
            language = BatchRun.Language.ENGLISH
        form.instance.language = language

        hindi_prompt = PromptTemplate.objects.filter(name__iexact='Hindi Generator').first()
        english_prompt = (
            PromptTemplate.objects.filter(is_active=True).first()
            or PromptTemplate.objects.filter(name__iexact='Default Generator').first()
        )
        can_edit_advanced = _is_platform_admin(self.request.user)

        if can_edit_advanced:
            posted_prompt = data.get('prompt')
            # If user left Advanced at the default that matches language, keep auto; if they picked another id, keep it.
            if language == BatchRun.Language.HINDI and hindi_prompt:
                if not posted_prompt or str(posted_prompt) in {
                    str(english_prompt.id) if english_prompt else '',
                    str(hindi_prompt.id),
                }:
                    form.instance.prompt = hindi_prompt
            elif language == BatchRun.Language.ENGLISH and english_prompt:
                if not posted_prompt or (hindi_prompt and str(posted_prompt) == str(hindi_prompt.id)):
                    form.instance.prompt = english_prompt
        else:
            # Locked platform defaults for Users / Org Admins.
            if language == BatchRun.Language.HINDI and hindi_prompt:
                form.instance.prompt = hindi_prompt
            elif english_prompt:
                form.instance.prompt = english_prompt
            form.instance.model_config = (
                ModelConfig.objects.filter(is_default=True).first()
                or ModelConfig.objects.first()
            )
            form.instance.rag_top_k = 5
            form.instance.pyq_shots = 3

        think_enabled = data.get('think') in ('on', 'true', '1', 'yes')
        council_models = get_active_council_models() if think_enabled else []
        form.instance.council_enabled = bool(think_enabled and council_models)

        # Block submit if rule-based cost exceeds remaining credits.
        question_count = 0
        for key in data:
            if key.startswith('items-') and key.endswith('-count'):
                try:
                    question_count += max(int(data.get(key) or 0), 0)
                except (TypeError, ValueError):
                    pass
        if question_count <= 0:
            question_count = 5
        estimated = estimate_batch_run_credits(
            None,
            question_count=question_count,
            has_rag=bool(data.getlist('pdf_contexts')),
            has_pyq=bool(data.getlist('pyq_modules')),
            think_enabled=form.instance.council_enabled,
        )
        try:
            ensure_credit_headroom(self.request.user, estimated)
        except ProvisioningError:
            messages.error(
                self.request,
                "You don't have enough credits for this run.",
            )
            return self.form_invalid(form)

        response = super().form_valid(form)

        if form.instance.council_enabled:
            self.object.council_models.set(council_models)

        # Parse the dynamic item rows submitted with the form
        indices = set()
        for key in data:
            if key.startswith('items-') and '-question_type' in key:
                indices.add(key.split('-')[1])

        for idx in sorted(indices):
            BatchRunItem.objects.create(
                batch_run=self.object,
                question_type=data.get(f'items-{idx}-question_type', 'SHORT'),
                bloom=data.get(f'items-{idx}-bloom', 'remember'),
                marks=float(data.get(f'items-{idx}-marks', 1)),
                count=int(data.get(f'items-{idx}-count', 5)),
            )

        self.object.expected_questions = sum(item.count for item in self.object.items.all())
        self.object.save(update_fields=["expected_questions", "language"])
        run_batch.delay(self.object.pk)
        if form.instance.council_enabled:
            messages.success(
                self.request,
                'Batch run queued with Think mode (multi-model verification).',
            )
        elif think_enabled and not council_models:
            messages.warning(
                self.request,
                'Think was enabled but no council models are configured in Admin — '
                'run queued without verification.',
            )
        else:
            messages.success(self.request, 'Batch run queued successfully.')
        return redirect('question_generation:detail', pk=self.object.pk)


class BatchRunDetailView(LoginRequiredMixin, DetailView):
    model = BatchRun
    template_name = 'question_generation/run_detail.html'

    def get_queryset(self):
        return _batch_run_queryset(self.request.user)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        # After generation finishes, force one-by-one human review first.
        if self.object.needs_human_review:
            return redirect("question_generation:review", pk=self.object.pk)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        # Only admin/superuser may see which council models were used.
        ctx['show_council_models'] = bool(
            getattr(user, 'is_superuser', False)
            or getattr(user, 'role', '') == User.Role.SUPERUSER
        )
        ctx['questions'] = self.object.questions.filter(is_generated=True).order_by('created_at', 'id')
        ctx['run_credits'] = batch_run_credits_used(self.object)
        return ctx


@login_required
def batch_run_review(request, pk):
    """Mandatory sequential approve/reject of each generated question."""
    run = get_object_or_404(_batch_run_queryset(request.user), pk=pk)

    if run.status in {BatchRun.Status.PENDING, BatchRun.Status.RUNNING}:
        return redirect("question_generation:detail", pk=run.pk)

    pending = (
        run.questions.filter(
            is_generated=True,
            user_decision=Question.UserDecision.PENDING,
        )
        .order_by("created_at", "id")
    )
    total = run.questions.filter(is_generated=True).count()
    reviewed = total - pending.count()

    if not pending.exists():
        if total and run.review_status != BatchRun.ReviewStatus.COMPLETE:
            run.review_status = BatchRun.ReviewStatus.COMPLETE
            run.save(update_fields=["review_status"])
        return redirect("question_generation:detail", pk=run.pk)

    question = pending.first()

    if request.method == "POST":
        decision = (request.POST.get("decision") or "").strip().lower()
        feedback = (request.POST.get("feedback") or "").strip()
        if decision not in {
            Question.UserDecision.APPROVED,
            Question.UserDecision.REJECTED,
        }:
            messages.error(request, "Please approve or reject this question.")
        elif decision == Question.UserDecision.REJECTED and not feedback:
            messages.error(request, "Please add a short comment when rejecting.")
        else:
            question.user_decision = decision
            question.user_feedback = feedback
            question.reviewed_at = timezone.now()
            question.save(
                update_fields=["user_decision", "user_feedback", "reviewed_at"]
            )
            still_pending = run.questions.filter(
                is_generated=True,
                user_decision=Question.UserDecision.PENDING,
            ).exists()
            if not still_pending:
                run.review_status = BatchRun.ReviewStatus.COMPLETE
                run.save(update_fields=["review_status"])
                messages.success(request, "Review complete. Full results are ready.")
                return redirect("question_generation:detail", pk=run.pk)
            return redirect("question_generation:review", pk=run.pk)

    show_council_models = _is_platform_admin(request.user)
    return render(
        request,
        "question_generation/run_review.html",
        {
            "object": run,
            "question": question,
            "reviewed_count": reviewed,
            "total_count": total,
            "position": reviewed + 1,
            "show_council_models": show_council_models,
        },
    )


class BatchRunDeleteView(LoginRequiredMixin, DeleteView):
    model = BatchRun
    template_name = "question_generation/batchrun_confirm_delete.html"
    success_url = reverse_lazy('question_generation:list')

    def get_queryset(self):
        return _batch_run_queryset(self.request.user)


class GeneratedQuestionUpdateView(LoginRequiredMixin, UpdateView):
    model = Question
    fields = ['question_text', 'question_type', 'bloom', 'marks', 'topic', 'reference_answer', 'options', 'rubrics']
    template_name = 'question_generation/generated_question_form.html'

    def get_queryset(self):
        return _generated_question_queryset(self.request.user)

    def get_success_url(self):
        return reverse_lazy('question_generation:detail', kwargs={'pk': self.object.batch_run_id})


class GeneratedQuestionDeleteView(LoginRequiredMixin, DeleteView):
    model = Question
    template_name = 'question_generation/question_confirm_delete.html'

    def get_queryset(self):
        return _generated_question_queryset(self.request.user)

    def get_success_url(self):
        return reverse_lazy('question_generation:detail', kwargs={'pk': self.object.batch_run_id})


@login_required
def batch_run_status(request, pk):
    from django.urls import reverse

    run = get_object_or_404(_batch_run_queryset(request.user), pk=pk)
    response = render(
        request,
        'question_generation/partials/progress_panel.html',
        {
            'object': run,
            'run_credits': batch_run_credits_used(run),
        },
    )
    if run.needs_human_review:
        response["HX-Redirect"] = reverse("question_generation:review", kwargs={"pk": run.pk})
    return response


@login_required
def batch_run_export(request, pk):
    run       = get_object_or_404(_batch_run_queryset(request.user), pk=pk)
    fmt       = request.GET.get('format', 'csv')
    questions = run.questions.filter(is_generated=True).order_by('created_at', 'id')

    if fmt == 'docx':
        stream = build_docx(run)
        response = HttpResponse(
            stream.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        response['Content-Disposition'] = f'attachment; filename="batch_{pk}.docx"'
        return response

    if fmt == 'dataset':
        rows = []
        for q in questions:
            rows.append(
                {
                    "run_id": run.pk,
                    "run_name": run.name,
                    "topic": run.topic,
                    "language": run.language,
                    "question_id": q.pk,
                    "question_type": q.question_type,
                    "bloom": q.bloom,
                    "marks": q.marks,
                    "question_text": q.question_text,
                    "reference_answer": q.reference_answer,
                    "options": q.options,
                    "rag_chunks": q.rag_chunks or [],
                    "pyq_examples": q.pyq_examples or [],
                    "council_opinion": q.council_opinion,
                    "user_decision": q.user_decision,
                    "user_feedback": q.user_feedback,
                    "reviewed_at": q.reviewed_at.isoformat() if q.reviewed_at else None,
                    "created_at": q.created_at.isoformat() if q.created_at else None,
                }
            )
        payload = {
            "run": {
                "id": run.pk,
                "name": run.name,
                "topic": run.topic,
                "status": run.status,
                "review_status": run.review_status,
                "council_enabled": run.council_enabled,
            },
            "questions": rows,
        }
        response = HttpResponse(
            json.dumps(payload, indent=2, ensure_ascii=False),
            content_type="application/json",
        )
        response["Content-Disposition"] = f'attachment; filename="dataset_batch_{pk}.json"'
        return response

    if fmt == 'json':
        data = list(questions.values(
            'id', 'question_type', 'bloom', 'marks', 'topic',
            'question_text', 'reference_answer', 'rubrics',
            'user_decision', 'user_feedback', 'rag_chunks', 'pyq_examples'))
        return HttpResponse(json.dumps(data, indent=2, ensure_ascii=False),
                            content_type='application/json')

    # CSV default
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="batch_{pk}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'ID', 'Type', 'Bloom', 'Marks', 'Topic', 'Question', 'Reference Answer',
        'User decision', 'User feedback',
    ])
    for q in questions:
        writer.writerow([
            q.id, q.question_type, q.bloom, q.marks, q.topic,
            q.question_text, q.reference_answer,
            q.user_decision, q.user_feedback,
        ])
    return response


@login_required
def batch_run_rerun(request, pk):
    """Re-queue failed / incomplete items for another attempt."""
    if request.method == 'POST':
        from .tasks import run_batch
        run = get_object_or_404(_batch_run_queryset(request.user), pk=pk)
        # Retry anything that did not finish successfully.
        to_retry = run.items.exclude(status='done')
        if not to_retry.exists():
            # Full re-run of all items if nothing is marked failed but run failed.
            to_retry = run.items.all()
        to_retry.update(status='pending', error_detail='')
        run.status = 'pending'
        run.review_status = BatchRun.ReviewStatus.NOT_STARTED
        run.error_summary = ''
        run.active_item = None
        run.save(update_fields=['status', 'review_status', 'error_summary', 'active_item'])
        run_batch.delay(run.pk)
        messages.info(request, 'Generation re-queued. Retrying failed items…')
    return redirect('question_generation:detail', pk=pk)
