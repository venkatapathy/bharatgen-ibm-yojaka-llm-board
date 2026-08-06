import csv
import json
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from apps.core.models import GenerationSettings, ModelConfig, User
from apps.core.ownership import (
    browse_list_context,
    owned_batch_runs,
    owned_generated_questions,
    owned_pdf_contexts,
)
from apps.core.soft404 import SoftMissingMixin
from apps.core.provisioning import (
    CREDITS_PER_QUESTION,
    CREDITS_RAG_PER_QUESTION,
    CREDITS_THINK_PER_QUESTION,
    ProvisioningError,
    batch_run_credits_used,
    credit_quota_display,
    ensure_credit_headroom,
    estimate_batch_run_credits,
)
from apps.pyq_module.models import Question, QuestionType, BloomLevel, normalize_question_type

from .export import build_docx
from .models import BatchRun, BatchRunItem
from .prompts import default_answer_length

_COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6}[-\s]?\d{2,4})\b", re.IGNORECASE)

# Intent filters map to one or more BatchRun.status values.
_STATUS_INTENTS = {
    "queue": (BatchRun.Status.PENDING, BatchRun.Status.RUNNING),
    "done": (BatchRun.Status.COMPLETED,),
    "retry": (BatchRun.Status.FAILED, BatchRun.Status.PARTIAL),
    "pending": (BatchRun.Status.PENDING,),
    "running": (BatchRun.Status.RUNNING,),
    "completed": (BatchRun.Status.COMPLETED,),
    "partial": (BatchRun.Status.PARTIAL,),
    "failed": (BatchRun.Status.FAILED,),
}


def _course_code_from_text(text: str) -> str:
    m = _COURSE_CODE_RE.search(text or "")
    if not m:
        return ""
    return re.sub(r"\s+", "-", m.group(1).upper().replace(" ", "-"))


def _is_platform_admin(user) -> bool:
    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "role", "") == User.Role.SUPERUSER
    )


def _order_generated_questions(qs):
    """Approved/pending first; user-rejected questions last."""
    from django.db.models import Case, IntegerField, Value, When

    return qs.annotate(
        _review_sort=Case(
            When(user_decision=Question.UserDecision.REJECTED, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by("_review_sort", "created_at", "id")


def _batch_run_queryset(user, *, owner=None, org_all=False, organization_id=None):
    return owned_batch_runs(
        user, owner=owner, org_all=org_all, organization_id=organization_id
    )


def _generated_question_queryset(user):
    return owned_generated_questions(user)


class BatchRunListView(LoginRequiredMixin, ListView):
    model = BatchRun
    template_name = 'question_generation/run_list.html'
    context_object_name = 'runs'
    paginate_by = 25

    def _browse_params(self):
        org_id = (self.request.GET.get("org") or "").strip() or None
        user_id = (self.request.GET.get("user") or "").strip() or None
        return org_id, user_id

    def _filter_params(self):
        q = (self.request.GET.get("q") or "").strip()
        status = (self.request.GET.get("status") or "").strip().lower()
        course = (self.request.GET.get("course") or "").strip().upper()
        lang = (self.request.GET.get("lang") or "").strip().lower()
        outcome = (self.request.GET.get("outcome") or "").strip().lower()
        review = (self.request.GET.get("review") or "").strip().lower()
        sort = (self.request.GET.get("sort") or "newest").strip().lower()
        if status and status not in _STATUS_INTENTS:
            status = ""
        if lang not in {"", "en", "hi"}:
            lang = ""
        if outcome not in {"", "with_q", "empty"}:
            outcome = ""
        if review not in {"", "needs_review", "reviewed"}:
            review = ""
        if sort not in {"newest", "oldest", "name", "questions"}:
            sort = "newest"
        return {
            "q": q,
            "status": status,
            "course": course,
            "lang": lang,
            "outcome": outcome,
            "review": review,
            "sort": sort,
        }

    def _base_queryset(self):
        viewer = self.request.user
        org_id, user_id = self._browse_params()
        browse = browse_list_context(
            viewer, organization_id=org_id, user_id=user_id
        )
        if browse.get("browse_all"):
            return (
                _batch_run_queryset(
                    viewer, org_all=True, organization_id=org_id
                ),
                browse,
            )
        if browse["needs_filter"] and browse["target"] is None:
            return BatchRun.objects.none(), browse
        if browse["needs_filter"]:
            return _batch_run_queryset(viewer, owner=browse["target"]), browse
        return _batch_run_queryset(viewer), browse

    def _apply_filters(self, qs, params, *, feedback_on):
        q = params["q"]
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(topic__icontains=q)
                | Q(error_summary__icontains=q)
                | Q(pdf_contexts__name__icontains=q)
                | Q(pdf_contexts__original_filename__icontains=q)
            ).distinct()

        statuses = _STATUS_INTENTS.get(params["status"])
        if statuses:
            qs = qs.filter(status__in=statuses)

        course = params["course"]
        if course:
            loose = course.replace("-", " ")
            qs = qs.filter(
                Q(name__icontains=course)
                | Q(name__icontains=loose)
                | Q(pdf_contexts__name__icontains=course)
                | Q(pdf_contexts__name__icontains=loose)
                | Q(pdf_contexts__original_filename__icontains=course)
                | Q(pdf_contexts__original_filename__icontains=loose)
            ).distinct()

        if params["lang"]:
            qs = qs.filter(language=params["lang"])

        if params["outcome"] == "with_q":
            qs = qs.filter(questions__is_generated=True).distinct()
        elif params["outcome"] == "empty":
            qs = qs.exclude(questions__is_generated=True)

        review = params["review"]
        if feedback_on and review == "needs_review":
            qs = qs.filter(
                status__in=[BatchRun.Status.COMPLETED, BatchRun.Status.PARTIAL],
                questions__is_generated=True,
                questions__user_decision=Question.UserDecision.PENDING,
            ).distinct()
        elif feedback_on and review == "reviewed":
            qs = (
                qs.filter(
                    status__in=[BatchRun.Status.COMPLETED, BatchRun.Status.PARTIAL],
                    questions__is_generated=True,
                )
                .exclude(
                    questions__is_generated=True,
                    questions__user_decision=Question.UserDecision.PENDING,
                )
                .distinct()
            )

        qs = qs.annotate(
            generated_count=Count(
                "questions",
                filter=Q(questions__is_generated=True),
                distinct=True,
            )
        )

        sort = params["sort"]
        if sort == "oldest":
            return qs.order_by("created_at", "id")
        if sort == "name":
            return qs.order_by("name", "-created_at")
        if sort == "questions":
            return qs.order_by("-generated_count", "-created_at", "-id")
        return qs.order_by("-created_at", "-id")

    def _courses_from_queryset(self, qs):
        codes = set()
        for name in qs.values_list("name", flat=True).distinct():
            code = _course_code_from_text(name)
            if code:
                codes.add(code)
        for name in qs.values_list("pdf_contexts__name", flat=True).distinct():
            code = _course_code_from_text(name or "")
            if code:
                codes.add(code)
        return sorted(codes)

    def _filter_query(self, params, *, extra=None, omit=None):
        org_id, user_id = self._browse_params()
        data = {}
        if org_id:
            data["org"] = org_id
        if user_id:
            data["user"] = user_id
        for key in ("q", "status", "course", "lang", "outcome", "review", "sort"):
            if omit and key in omit:
                continue
            val = params.get(key) or ""
            if key == "sort" and val == "newest":
                continue
            if val:
                data[key] = val
        if extra:
            for key, val in extra.items():
                if val in (None, ""):
                    data.pop(key, None)
                else:
                    data[key] = val
        return urlencode(data)

    def get_queryset(self):
        qs, _browse = self._base_queryset()
        params = self._filter_params()
        feedback_on = GenerationSettings.load().user_feedback_enabled
        if not feedback_on:
            params["review"] = ""
        return self._apply_filters(qs, params, feedback_on=feedback_on)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        viewer = self.request.user
        org_id, user_id = self._browse_params()
        browse = browse_list_context(
            viewer, organization_id=org_id, user_id=user_id
        )
        ctx.update(browse)
        quota_user = browse["target"] or viewer
        ctx["credits"] = credit_quota_display(quota_user)
        ctx["mine_label"] = "My runs"
        ctx["show_admin_exports"] = _is_platform_admin(viewer)

        params = self._filter_params()
        feedback_on = GenerationSettings.load().user_feedback_enabled
        if not feedback_on:
            params["review"] = ""

        base_qs, _ = self._base_queryset()
        page_obj = ctx.get("page_obj")
        result_count = page_obj.paginator.count if page_obj else len(ctx["object_list"])

        chip_counts = {
            "all": base_qs.count(),
            "queue": base_qs.filter(
                status__in=[BatchRun.Status.PENDING, BatchRun.Status.RUNNING]
            ).count(),
            "done": base_qs.filter(status=BatchRun.Status.COMPLETED).count(),
            "retry": base_qs.filter(
                status__in=[BatchRun.Status.FAILED, BatchRun.Status.PARTIAL]
            ).count(),
            "with_q": base_qs.filter(questions__is_generated=True).distinct().count(),
        }

        filter_active = bool(
            params["q"]
            or params["status"]
            or params["course"]
            or params["lang"]
            or params["outcome"]
            or params["review"]
            or (params["sort"] and params["sort"] != "newest")
        )

        ctx.update(
            {
                "filter_q": params["q"],
                "filter_status": params["status"],
                "filter_course": params["course"],
                "filter_lang": params["lang"],
                "filter_outcome": params["outcome"],
                "filter_review": params["review"],
                "filter_sort": params["sort"],
                "filter_courses": self._courses_from_queryset(base_qs),
                "filter_feedback_on": feedback_on,
                "filter_active": filter_active,
                "filter_query": self._filter_query(params),
                "chip_counts": chip_counts,
                "chip_href": {
                    "all": self._filter_query(
                        params, omit={"status", "outcome", "review"}
                    ),
                    "queue": self._filter_query(
                        params, extra={"status": "queue"}, omit={"outcome"}
                    ),
                    "done": self._filter_query(
                        params, extra={"status": "done"}, omit={"outcome"}
                    ),
                    "retry": self._filter_query(
                        params, extra={"status": "retry"}, omit={"outcome"}
                    ),
                    "with_q": self._filter_query(
                        params, extra={"outcome": "with_q"}, omit={"status"}
                    ),
                },
                "result_count": result_count,
            }
        )
        return ctx


class BatchRunNewView(LoginRequiredMixin, CreateView):
    model = BatchRun
    template_name = 'question_generation/run_new.html'
    fields = ['name', 'pdf_contexts']
    success_url = reverse_lazy('question_generation:list')

    def get_context_data(self, **kwargs):
        from .council import get_active_council_models
        from apps.pdf_module.hierarchy import build_pdf_hierarchy_rows

        ctx = super().get_context_data(**kwargs)
        ctx['credits'] = credit_quota_display(self.request.user)
        ctx['credit_tariff'] = {
            "per_question": CREDITS_PER_QUESTION,
            "rag": CREDITS_RAG_PER_QUESTION,
            "think": CREDITS_THINK_PER_QUESTION,
        }
        pdf_qs = owned_pdf_contexts(self.request.user, ready_only=True)
        ctx['pdf_contexts'] = pdf_qs
        ctx['pdf_hierarchy'] = build_pdf_hierarchy_rows(pdf_qs)
        ctx['pdf_hierarchy_json'] = json.dumps(ctx['pdf_hierarchy'], ensure_ascii=False)
        ctx['think_available'] = bool(get_active_council_models())
        ctx['question_types'] = QuestionType.choices
        ctx['bloom_levels']   = BloomLevel.choices
        return ctx

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['pdf_contexts'].queryset = owned_pdf_contexts(
            self.request.user, ready_only=True
        )
        form.fields['pdf_contexts'].required = True
        form.fields['pdf_contexts'].error_messages = {
            "required": "Select one PDF unit so questions are grounded in its OCR text.",
        }
        return form

    def form_valid(self, form):
        from .tasks import run_batch
        from .council import get_active_council_models

        pdfs = list(form.cleaned_data.get("pdf_contexts") or [])
        if len(pdfs) != 1:
            form.add_error(
                "pdf_contexts",
                "Select exactly one PDF unit. Its full OCR text is used as unit content.",
            )
            return self.form_invalid(form)

        form.instance.created_by = self.request.user
        form.instance.topic = ""
        data = self.request.POST
        language = data.get('language', BatchRun.Language.ENGLISH)
        if language not in {BatchRun.Language.ENGLISH, BatchRun.Language.HINDI}:
            language = BatchRun.Language.ENGLISH
        form.instance.language = language

        # Platform defaults from Control → Technical settings (all roles).
        gen = GenerationSettings.load()
        prompt = gen.resolve_prompt(language)
        model_config = (
            gen.model_config
            or ModelConfig.objects.filter(is_default=True).first()
            or ModelConfig.objects.exclude(llm_model_id="").first()
        )
        if prompt is None:
            messages.error(
                self.request,
                "No prompt template is configured. Ask a platform admin to set one in Control → Technical settings.",
            )
            return self.form_invalid(form)
        if model_config is None:
            messages.error(
                self.request,
                "No generation model is configured. Ask a platform admin to set one in Control → Technical settings.",
            )
            return self.form_invalid(form)
        form.instance.prompt = prompt
        form.instance.model_config = model_config
        form.instance.rag_top_k = int(gen.rag_top_k or 5)
        # PYQ few-shot disabled for this demo — unit OCR only.
        form.instance.pyq_shots = 0

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
            has_rag=True,
            has_pyq=False,
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
        # Ensure no PYQ modules stick on the run.
        self.object.pyq_modules.clear()

        # Parse the dynamic item rows submitted with the form
        indices = set()
        for key in data:
            if key.startswith('items-') and '-question_type' in key:
                indices.add(key.split('-')[1])

        for idx in sorted(indices):
            qtype = normalize_question_type(
                data.get(f'items-{idx}-question_type', 'SHORT'),
                marks=data.get(f'items-{idx}-marks', 5),
            )
            answer_length = (data.get(f'items-{idx}-answer_length') or '').strip()
            if not answer_length:
                answer_length = default_answer_length(qtype)
            BatchRunItem.objects.create(
                batch_run=self.object,
                question_type=qtype,
                bloom=data.get(f'items-{idx}-bloom', 'understand'),
                marks=float(data.get(f'items-{idx}-marks', 5)),
                count=int(data.get(f'items-{idx}-count', 2)),
                answer_length=answer_length[:64],
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


def _get_owned_run_or_redirect(request, pk):
    run = _batch_run_queryset(request.user).filter(pk=pk).first()
    if run is None:
        messages.info(request, "That generation run is no longer available.")
        return None
    return run


class BatchRunDetailView(SoftMissingMixin, LoginRequiredMixin, DetailView):
    model = BatchRun
    template_name = 'question_generation/run_detail.html'
    missing_message = "That generation run is no longer available."
    missing_redirect = "question_generation:list"

    def get_queryset(self):
        return _batch_run_queryset(self.request.user)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object is None:
            return redirect(self.get_missing_redirect())
        # After generation finishes, force one-by-one human review first.
        if self.object.needs_human_review:
            return redirect("question_generation:review", pk=self.object.pk)
        return super(DetailView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        is_platform_admin = bool(
            getattr(user, 'is_superuser', False)
            or getattr(user, 'role', '') == User.Role.SUPERUSER
        )
        is_org_admin = getattr(user, 'role', '') == User.Role.ORGUSER
        # Only admin/superuser may see which council models were used.
        ctx['show_council_models'] = is_platform_admin
        ctx['show_dataset_meta'] = ctx['show_council_models']
        ctx['show_admin_exports'] = ctx['show_council_models']
        ctx['questions'] = _order_generated_questions(
            self.object.questions.filter(is_generated=True)
        )
        ctx['run_credits'] = batch_run_credits_used(self.object)
        from .tasks import remaining_question_count
        ctx['remaining_questions'] = remaining_question_count(self.object)
        # On finished runs, always allow flipping approve/reject on the results page.
        # (Technical "user feedback" only controls the mandatory first-pass review.)
        ctx['allow_decision_edit'] = bool(
            self.object.status
            not in {BatchRun.Status.PENDING, BatchRun.Status.RUNNING}
            and not self.object.needs_human_review
        )
        # Testing layout: PDF left + questions right (admins / org admins only).
        pdf_ctx = self.object.pdf_contexts.order_by("id").first()
        ctx['review_split_layout'] = bool(is_platform_admin or is_org_admin)
        ctx['run_pdf'] = pdf_ctx
        ctx['run_pdf_search_text'] = pdf_ctx.ocr_text if pdf_ctx else ""
        ctx['run_pdf_url'] = (
            reverse("pdf_module:file", kwargs={"pk": pdf_ctx.pk}) if pdf_ctx else ""
        )
        return ctx


@login_required
def batch_run_review(request, pk):
    """Mandatory sequential approve/reject of each generated question."""
    run = _get_owned_run_or_redirect(request, pk)
    if run is None:
        return redirect("question_generation:list")

    from apps.core.models import GenerationSettings

    if not GenerationSettings.load().user_feedback_enabled:
        return redirect("question_generation:detail", pk=run.pk)

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
    is_org_admin = getattr(request.user, "role", "") == User.Role.ORGUSER
    pdf_ctx = run.pdf_contexts.order_by("id").first()
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
            "show_dataset_meta": show_council_models,
            "review_split_layout": bool(show_council_models or is_org_admin),
            "run_pdf": pdf_ctx,
            "run_pdf_search_text": pdf_ctx.ocr_text if pdf_ctx else "",
            "run_pdf_url": (
                reverse("pdf_module:file", kwargs={"pk": pdf_ctx.pk}) if pdf_ctx else ""
            ),
        },
    )


@login_required
def generated_question_decision(request, pk):
    """Allow changing approve/reject after the initial review pass."""
    if request.method != "POST":
        return redirect("question_generation:list")

    question = _generated_question_queryset(request.user).filter(pk=pk).first()
    if question is None or not question.batch_run_id:
        messages.error(request, "That generated question is no longer available.")
        return redirect("question_generation:list")

    run = question.batch_run
    if run.status in {BatchRun.Status.PENDING, BatchRun.Status.RUNNING}:
        messages.error(request, "Wait for the run to finish before changing decisions.")
        return redirect("question_generation:detail", pk=run.pk)

    # Initial one-by-one review must finish first (when feedback mode is on).
    if run.needs_human_review:
        return redirect("question_generation:review", pk=run.pk)

    decision = (request.POST.get("decision") or "").strip().lower()
    feedback = (request.POST.get("feedback") or "").strip()
    if decision not in {
        Question.UserDecision.APPROVED,
        Question.UserDecision.REJECTED,
    }:
        messages.error(request, "Please choose Approve or Reject.")
    elif decision == Question.UserDecision.REJECTED and not feedback:
        messages.error(request, "Please add a short comment when rejecting.")
    else:
        question.user_decision = decision
        question.user_feedback = feedback
        question.reviewed_at = timezone.now()
        question.save(
            update_fields=["user_decision", "user_feedback", "reviewed_at"]
        )
        # Keep run marked complete even if someone flips decisions later.
        if run.review_status != BatchRun.ReviewStatus.COMPLETE:
            run.review_status = BatchRun.ReviewStatus.COMPLETE
            run.save(update_fields=["review_status"])
        messages.success(
            request,
            f"Decision updated to {question.get_user_decision_display()}.",
        )

    return redirect(f"{reverse('question_generation:detail', kwargs={'pk': run.pk})}#q-{question.pk}")


class BatchRunDeleteView(SoftMissingMixin, LoginRequiredMixin, DeleteView):
    model = BatchRun
    template_name = "question_generation/batchrun_confirm_delete.html"
    success_url = reverse_lazy('question_generation:list')
    missing_message = "That generation run is no longer available."
    missing_redirect = "question_generation:list"

    def get_queryset(self):
        return _batch_run_queryset(self.request.user)


class GeneratedQuestionUpdateView(SoftMissingMixin, LoginRequiredMixin, UpdateView):
    model = Question
    fields = ['question_text', 'question_type', 'bloom', 'marks', 'topic', 'reference_answer', 'options', 'rubrics']
    template_name = 'question_generation/generated_question_form.html'
    missing_message = "That generated question is no longer available."
    missing_redirect = "question_generation:list"

    def get_queryset(self):
        return _generated_question_queryset(self.request.user)

    def get_success_url(self):
        return reverse_lazy('question_generation:detail', kwargs={'pk': self.object.batch_run_id})


class GeneratedQuestionDeleteView(SoftMissingMixin, LoginRequiredMixin, DeleteView):
    model = Question
    template_name = 'question_generation/question_confirm_delete.html'
    missing_message = "That generated question is no longer available."
    missing_redirect = "question_generation:list"

    def get_queryset(self):
        return _generated_question_queryset(self.request.user)

    def get_success_url(self):
        return reverse_lazy('question_generation:detail', kwargs={'pk': self.object.batch_run_id})


@login_required
def batch_run_status(request, pk):
    from django.urls import reverse
    from .tasks import remaining_question_count

    run = _get_owned_run_or_redirect(request, pk)
    if run is None:
        return redirect("question_generation:list")
    response = render(
        request,
        'question_generation/partials/progress_panel.html',
        {
            'object': run,
            'run_credits': batch_run_credits_used(run),
            'remaining_questions': remaining_question_count(run),
        },
    )
    if run.needs_human_review:
        response["HX-Redirect"] = reverse("question_generation:review", kwargs={"pk": run.pk})
    return response


@login_required
def batch_run_export(request, pk):
    run = _get_owned_run_or_redirect(request, pk)
    if run is None:
        return redirect("question_generation:list")
    fmt       = request.GET.get('format', 'csv')
    questions = _order_generated_questions(
        run.questions.filter(is_generated=True)
    )

    # Users / org admins: DOCX + CSV only. Dataset JSON / JSON are platform admin.
    if fmt in ('dataset', 'json') and not _is_platform_admin(request.user):
        messages.error(request, "That export format is available to platform admins only.")
        return redirect("question_generation:detail", pk=pk)

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
        run = _get_owned_run_or_redirect(request, pk)
        if run is None:
            return redirect("question_generation:list")
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
    run = _get_owned_run_or_redirect(request, pk)
    if run is None:
        return redirect("question_generation:list")
    return redirect('question_generation:detail', pk=pk)


@login_required
def batch_run_regenerate(request, pk):
    """Generate only the remaining (missing) questions for this run."""
    if request.method != "POST":
        return redirect("question_generation:detail", pk=pk)

    from .tasks import remaining_question_count, remaining_slots_for_run, run_batch

    run = _get_owned_run_or_redirect(request, pk)
    if run is None:
        return redirect("question_generation:list")
    if run.status in {BatchRun.Status.PENDING, BatchRun.Status.RUNNING}:
        messages.warning(request, "This run is already generating.")
        return redirect("question_generation:detail", pk=pk)
    if not run.pdf_contexts.exists():
        messages.error(request, "Add at least one PDF context before regenerating.")
        return redirect("question_generation:detail", pk=pk)

    remaining = remaining_question_count(run)
    if remaining <= 0:
        messages.info(
            request,
            "All expected questions are already generated for this run.",
        )
        return redirect("question_generation:detail", pk=pk)

    estimated = estimate_batch_run_credits(run, question_count=remaining)
    try:
        ensure_credit_headroom(request.user, estimated)
    except ProvisioningError:
        messages.error(
            request,
            f"You don't have enough credits to generate the remaining {remaining} question(s).",
        )
        return redirect("question_generation:detail", pk=pk)

    for item, need in remaining_slots_for_run(run):
        if need > 0:
            item.status = "pending"
            item.error_detail = ""
        else:
            item.status = "done"
            item.error_detail = ""
        item.save(update_fields=["status", "error_detail"])

    run.status = BatchRun.Status.PENDING
    run.error_summary = ""
    run.active_item = None
    run.completed_at = None
    run.save(
        update_fields=[
            "status",
            "error_summary",
            "active_item",
            "completed_at",
        ]
    )
    run_batch.delay(run.pk, fill_remaining=True)
    messages.info(
        request,
        f"Generating {remaining} remaining question(s). Existing questions are kept.",
    )
    return redirect("question_generation:detail", pk=pk)
