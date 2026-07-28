from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from apps.core.models import ModelConfig
from apps.core.ownership import browse_list_context, owned_pyq_modules, owned_pyq_questions
from apps.core.soft404 import SoftMissingMixin
from apps.core.storage import (
    StorageQuotaExceeded,
    release_pyq_storage,
    reserve_pyq_storage,
    storage_quota_display,
)

from .forms import PYQModuleUploadForm, QuestionEditForm
from .models import PYQModule, Question


class PYQModuleListView(LoginRequiredMixin, ListView):
    model = PYQModule
    template_name = 'pyq_module/module_list.html'
    context_object_name = 'modules'

    def _browse_params(self):
        org_id = (self.request.GET.get("org") or "").strip() or None
        user_id = (self.request.GET.get("user") or "").strip() or None
        return org_id, user_id

    def get_queryset(self):
        viewer = self.request.user
        org_id, user_id = self._browse_params()
        browse = browse_list_context(
            viewer, organization_id=org_id, user_id=user_id
        )
        if browse["needs_filter"] and browse["target"] is None:
            return PYQModule.objects.none()
        if browse["needs_filter"]:
            return owned_pyq_modules(viewer, owner=browse["target"])
        return owned_pyq_modules(viewer)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        viewer = self.request.user
        org_id, user_id = self._browse_params()
        browse = browse_list_context(
            viewer, organization_id=org_id, user_id=user_id
        )
        ctx.update(browse)
        quota_user = browse["target"] or viewer
        ctx["quota"] = storage_quota_display(quota_user)
        ctx["mine_label"] = "My PYQs"
        return ctx


class PYQModuleUploadView(LoginRequiredMixin, CreateView):
    model = PYQModule
    template_name = 'pyq_module/module_upload.html'
    form_class = PYQModuleUploadForm
    success_url = reverse_lazy('pyq_module:list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by   = self.request.user
        form.instance.organization = self.request.user.organization
        upload = form.cleaned_data["source_file"]
        try:
            with transaction.atomic():
                response = super().form_valid(form)
                reserve_pyq_storage(self.request.user, upload.size)
        except StorageQuotaExceeded as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)
        config     = ModelConfig.objects.filter(is_default=True).first()
        if config:
            from .tasks import extract_pyq_questions
            extract_pyq_questions.delay(self.object.pk, config.pk)
        messages.success(self.request, "PYQ extraction queued.")
        return response


class PYQModuleDetailView(SoftMissingMixin, LoginRequiredMixin, DetailView):
    model = PYQModule
    template_name = 'pyq_module/module_detail.html'
    missing_message = "That PYQ paper is no longer available."
    missing_redirect = "pyq_module:list"

    def get_queryset(self):
        return owned_pyq_modules(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        paginator = Paginator(self.object.questions.all(), 25)
        ctx['questions'] = paginator.get_page(self.request.GET.get("page"))
        return ctx


class PYQModuleDeleteView(SoftMissingMixin, LoginRequiredMixin, DeleteView):
    model = PYQModule
    template_name = "pyq_module/module_confirm_delete.html"
    success_url = reverse_lazy('pyq_module:list')
    missing_message = "That PYQ paper is no longer available."
    missing_redirect = "pyq_module:list"

    def get_queryset(self):
        return owned_pyq_modules(self.request.user)

    def form_valid(self, form):
        owner = self.object.created_by or self.request.user
        if self.object.file_size_bytes:
            release_pyq_storage(owner, self.object.file_size_bytes)
        messages.success(self.request, "PYQ module deleted.")
        return super().form_valid(form)


class QuestionUpdateView(SoftMissingMixin, LoginRequiredMixin, UpdateView):
    model = Question
    form_class = QuestionEditForm
    template_name = 'pyq_module/question_table.html'
    missing_message = (
        "That question is no longer available "
        "(it may have been replaced by a re-extract)."
    )
    missing_redirect = "pyq_module:list"

    def get_queryset(self):
        return owned_pyq_questions(self.request.user)

    def get_success_url(self):
        return reverse_lazy('pyq_module:detail', kwargs={'pk': self.object.pyq_module_id})


class QuestionDeleteView(SoftMissingMixin, LoginRequiredMixin, DeleteView):
    model = Question
    template_name = "pyq_module/question_confirm_delete.html"
    missing_message = (
        "That question is no longer available "
        "(it may have been replaced by a re-extract)."
    )
    missing_redirect = "pyq_module:list"

    def get_queryset(self):
        return owned_pyq_questions(self.request.user)

    def form_valid(self, form):
        module_id = self.object.pyq_module_id
        messages.success(self.request, "Question deleted.")
        response = super().form_valid(form)
        self._module_id = module_id
        return response

    def get_success_url(self):
        module_id = getattr(self, "_module_id", None) or getattr(
            self.object, "pyq_module_id", None
        )
        if module_id:
            return reverse_lazy("pyq_module:detail", kwargs={"pk": module_id})
        return reverse_lazy("pyq_module:list")


def _get_owned_pyq_or_redirect(request, pk):
    mod = owned_pyq_modules(request.user).filter(pk=pk).first()
    if mod is None:
        messages.info(request, "That PYQ paper is no longer available.")
        return None
    return mod


@login_required
def pyq_module_reextract(request, pk):
    mod = _get_owned_pyq_or_redirect(request, pk)
    if mod is None:
        return redirect("pyq_module:list")
    if request.method != "POST":
        return redirect("pyq_module:detail", pk=pk)

    if mod.status in ("extracting", "pending"):
        messages.info(request, "Extraction is already queued or running.")
        return redirect("pyq_module:detail", pk=pk)

    config = ModelConfig.objects.filter(is_default=True).first()
    if not config:
        messages.error(request, "No default model config available for extraction.")
        return redirect("pyq_module:detail", pk=pk)

    Question.objects.filter(pyq_module=mod).delete()
    mod.status = "pending"
    mod.error_msg = ""
    mod.save(update_fields=["status", "error_msg"])

    from .tasks import extract_pyq_questions

    extract_pyq_questions.delay(mod.pk, config.pk)
    messages.success(request, "Re-extraction queued.")
    return redirect("pyq_module:detail", pk=pk)


@login_required
def pyq_module_status(request, pk):
    mod = _get_owned_pyq_or_redirect(request, pk)
    if mod is None:
        if request.headers.get("HX-Request"):
            return render(
                request,
                "pyq_module/partials/module_missing.html",
                {},
            )
        return redirect("pyq_module:list")
    if request.headers.get("HX-Request"):
        return render(
            request,
            "pyq_module/partials/module_status.html",
            {"object": mod},
        )
    return JsonResponse({"status": mod.status, "question_count": mod.questions.count()})
