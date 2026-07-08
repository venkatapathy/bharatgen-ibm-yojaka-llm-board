from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from apps.core.models import ModelConfig
from apps.core.storage import (
    StorageQuotaExceeded,
    release_pyq_storage,
    reserve_pyq_storage,
)

from .forms import PYQModuleUploadForm, QuestionEditForm
from .models import PYQModule, Question


class PYQModuleListView(LoginRequiredMixin, ListView):
    model = PYQModule
    template_name = 'pyq_module/module_list.html'
    context_object_name = 'modules'

    def get_queryset(self):
        return PYQModule.objects.filter(organization=self.request.user.organization)


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


class PYQModuleDetailView(LoginRequiredMixin, DetailView):
    model = PYQModule
    template_name = 'pyq_module/module_detail.html'

    def get_queryset(self):
        return PYQModule.objects.filter(organization=self.request.user.organization)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        paginator = Paginator(self.object.questions.all(), 25)
        ctx['questions'] = paginator.get_page(self.request.GET.get("page"))
        return ctx


class PYQModuleDeleteView(LoginRequiredMixin, DeleteView):
    model = PYQModule
    template_name = "pyq_module/module_confirm_delete.html"
    success_url = reverse_lazy('pyq_module:list')

    def get_queryset(self):
        return PYQModule.objects.filter(organization=self.request.user.organization)

    def form_valid(self, form):
        if self.object.file_size_bytes:
            release_pyq_storage(self.request.user, self.object.file_size_bytes)
        messages.success(self.request, "PYQ module deleted.")
        return super().form_valid(form)


class QuestionUpdateView(LoginRequiredMixin, UpdateView):
    model = Question
    form_class = QuestionEditForm
    template_name = 'pyq_module/question_table.html'

    def get_queryset(self):
        return Question.objects.filter(pyq_module__organization=self.request.user.organization)

    def get_success_url(self):
        return reverse_lazy('pyq_module:detail', kwargs={'pk': self.object.pyq_module_id})


class QuestionDeleteView(LoginRequiredMixin, DeleteView):
    model = Question
    template_name = "pyq_module/question_confirm_delete.html"

    def get_queryset(self):
        return Question.objects.filter(pyq_module__organization=self.request.user.organization)

    def get_success_url(self):
        return reverse_lazy("pyq_module:detail", kwargs={"pk": self.object.pyq_module_id})


@login_required
def pyq_module_status(request, pk):
    mod = get_object_or_404(PYQModule, pk=pk, organization=request.user.organization)
    return JsonResponse({'status': mod.status, 'question_count': mod.questions.count()})
