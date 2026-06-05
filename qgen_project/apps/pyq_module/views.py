from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, DeleteView, UpdateView
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.http import JsonResponse
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
    fields = ['name', 'description', 'source_file']
    success_url = reverse_lazy('pyq_module:list')

    def form_valid(self, form):
        from apps.core.models import ModelConfig
        form.instance.created_by   = self.request.user
        form.instance.organization = self.request.user.organization
        response   = super().form_valid(form)
        config     = ModelConfig.objects.filter(is_default=True).first()
        if config:
            from .tasks import extract_pyq_questions
            extract_pyq_questions.delay(self.object.pk, config.pk)
        return response


class PYQModuleDetailView(LoginRequiredMixin, DetailView):
    model = PYQModule
    template_name = 'pyq_module/module_detail.html'

    def get_queryset(self):
        return PYQModule.objects.filter(organization=self.request.user.organization)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['questions'] = self.object.questions.all()
        return ctx


class PYQModuleDeleteView(LoginRequiredMixin, DeleteView):
    model = PYQModule
    success_url = reverse_lazy('pyq_module:list')

    def get_queryset(self):
        return PYQModule.objects.filter(created_by=self.request.user)


class QuestionUpdateView(LoginRequiredMixin, UpdateView):
    model = Question
    fields = ['question_text', 'question_type', 'bloom', 'marks', 'topic',
              'reference_answer', 'options', 'rubrics']
    template_name = 'pyq_module/question_table.html'

    def get_success_url(self):
        return reverse_lazy('pyq_module:detail', kwargs={'pk': self.object.pyq_module_id})


def pyq_module_status(request, pk):
    mod = get_object_or_404(PYQModule, pk=pk, organization=request.user.organization)
    return JsonResponse({'status': mod.status, 'question_count': mod.questions.count()})
