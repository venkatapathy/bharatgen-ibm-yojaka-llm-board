import csv
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, DeleteView
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from .models import BatchRun, BatchRunItem
from apps.pyq_module.models import Question, QuestionType, BloomLevel
from apps.pdf_module.models import PDFContext
from apps.pyq_module.models import PYQModule
from apps.prompt_module.models import PromptTemplate
from apps.core.models import ModelConfig


class BatchRunListView(LoginRequiredMixin, ListView):
    model = BatchRun
    template_name = 'question_generation/run_list.html'
    context_object_name = 'runs'

    def get_queryset(self):
        return BatchRun.objects.filter(created_by=self.request.user)


class BatchRunNewView(LoginRequiredMixin, CreateView):
    model = BatchRun
    template_name = 'question_generation/run_new.html'
    fields = ['name', 'topic', 'pdf_contexts', 'pyq_modules',
              'prompt', 'model_config', 'rag_top_k', 'pyq_shots']
    success_url = reverse_lazy('question_generation:list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        org = self.request.user.organization
        ctx['pdf_contexts']  = PDFContext.objects.filter(organization=org, status='ready')
        ctx['pyq_modules']   = PYQModule.objects.filter(organization=org, status='ready')
        ctx['prompts']       = PromptTemplate.objects.all()
        ctx['model_configs'] = ModelConfig.objects.all()
        ctx['question_types'] = QuestionType.choices
        ctx['bloom_levels']   = BloomLevel.choices
        return ctx

    def form_valid(self, form):
        from .tasks import run_batch
        form.instance.created_by = self.request.user
        response = super().form_valid(form)

        # Parse the dynamic item rows submitted with the form
        data = self.request.POST
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

        run_batch.delay(self.object.pk)
        messages.success(self.request, 'Batch run queued successfully.')
        return redirect('question_generation:detail', pk=self.object.pk)


class BatchRunDetailView(LoginRequiredMixin, DetailView):
    model = BatchRun
    template_name = 'question_generation/run_detail.html'

    def get_queryset(self):
        return BatchRun.objects.filter(created_by=self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['questions'] = self.object.questions.all()
        return ctx


class BatchRunDeleteView(LoginRequiredMixin, DeleteView):
    model = BatchRun
    success_url = reverse_lazy('question_generation:list')

    def get_queryset(self):
        return BatchRun.objects.filter(created_by=self.request.user)


def batch_run_status(request, pk):
    run = get_object_or_404(BatchRun, pk=pk, created_by=request.user)
    return JsonResponse({
        'status':   run.status,
        'progress': run.progress,
        'total':    run.total_questions,
        'items':    list(run.items.values('id', 'question_type', 'bloom', 'status')),
    })


def batch_run_export(request, pk):
    run       = get_object_or_404(BatchRun, pk=pk, created_by=request.user)
    fmt       = request.GET.get('format', 'csv')
    questions = run.questions.all()

    if fmt == 'json':
        data = list(questions.values(
            'id', 'question_type', 'bloom', 'marks', 'topic',
            'question_text', 'reference_answer', 'rubrics'))
        return HttpResponse(json.dumps(data, indent=2),
                            content_type='application/json')

    # CSV default
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="batch_{pk}.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'Type', 'Bloom', 'Marks', 'Topic',
                     'Question', 'Reference Answer'])
    for q in questions:
        writer.writerow([q.id, q.question_type, q.bloom, q.marks, q.topic,
                         q.question_text, q.reference_answer])
    return response


def batch_run_rerun(request, pk):
    """Re-queue only failed items."""
    if request.method == 'POST':
        from .tasks import run_batch
        run = get_object_or_404(BatchRun, pk=pk, created_by=request.user)
        run.items.filter(status='error').update(status='pending')
        run.status = 'pending'
        run.save(update_fields=['status'])
        run_batch.delay(run.pk)
        messages.info(request, 'Re-queued failed items.')
    return redirect('question_generation:detail', pk=pk)
