import csv
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .models import BatchRun, BatchRunItem
from .export import build_docx
from apps.pyq_module.models import Question, QuestionType, BloomLevel
from apps.pdf_module.models import PDFContext
from apps.pyq_module.models import PYQModule
from apps.prompt_module.models import PromptTemplate
from apps.core.models import ModelConfig, User


def _batch_run_queryset(user):
    if user.role == User.Role.SUPERUSER:
        return BatchRun.objects.all()
    if user.organization_id:
        return BatchRun.objects.filter(created_by__organization=user.organization)
    return BatchRun.objects.filter(created_by=user)


def _generated_question_queryset(user):
    if user.role == User.Role.SUPERUSER:
        return Question.objects.filter(is_generated=True)
    if user.organization_id:
        return Question.objects.filter(
            is_generated=True,
            batch_run__created_by__organization=user.organization,
        )
    return Question.objects.filter(is_generated=True, batch_run__created_by=user)


class BatchRunListView(LoginRequiredMixin, ListView):
    model = BatchRun
    template_name = 'question_generation/run_list.html'
    context_object_name = 'runs'

    def get_queryset(self):
        return _batch_run_queryset(self.request.user)


class BatchRunNewView(LoginRequiredMixin, CreateView):
    model = BatchRun
    template_name = 'question_generation/run_new.html'
    fields = ['name', 'topic', 'pdf_contexts', 'pyq_modules',
              'prompt', 'model_config', 'rag_top_k', 'pyq_shots']
    success_url = reverse_lazy('question_generation:list')

    def get_context_data(self, **kwargs):
        from .council import ensure_council_models

        ctx = super().get_context_data(**kwargs)
        org = self.request.user.organization
        ctx['pdf_contexts']  = PDFContext.objects.filter(organization=org, status='ready')
        ctx['pyq_modules']   = PYQModule.objects.filter(organization=org, status='ready')
        ctx['prompts']       = PromptTemplate.objects.all()
        ctx['model_configs'] = ModelConfig.objects.all()
        ctx['council_models'] = ensure_council_models()
        ctx['question_types'] = QuestionType.choices
        ctx['bloom_levels']   = BloomLevel.choices
        return ctx

    def form_valid(self, form):
        from .tasks import run_batch
        form.instance.created_by = self.request.user
        data = self.request.POST
        council_ids = data.getlist('council_models')
        form.instance.council_enabled = bool(council_ids)
        response = super().form_valid(form)

        if council_ids:
            self.object.council_models.set(
                ModelConfig.objects.filter(pk__in=council_ids)
            )

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
        self.object.save(update_fields=["expected_questions"])
        run_batch.delay(self.object.pk)
        if council_ids:
            messages.success(
                self.request,
                f'Batch run queued with council of {len(council_ids)} models '
                '(bloom · correctness · Q-type · appropriate).',
            )
        else:
            messages.success(self.request, 'Batch run queued successfully.')
        return redirect('question_generation:detail', pk=self.object.pk)


class BatchRunDetailView(LoginRequiredMixin, DetailView):
    model = BatchRun
    template_name = 'question_generation/run_detail.html'

    def get_queryset(self):
        return _batch_run_queryset(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['questions'] = self.object.questions.all()
        return ctx


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
    run = get_object_or_404(_batch_run_queryset(request.user), pk=pk)
    return render(request, 'question_generation/partials/progress_panel.html', {'object': run})


@login_required
def batch_run_export(request, pk):
    run       = get_object_or_404(_batch_run_queryset(request.user), pk=pk)
    fmt       = request.GET.get('format', 'csv')
    questions = run.questions.all()

    if fmt == 'docx':
        stream = build_docx(run)
        response = HttpResponse(
            stream.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        response['Content-Disposition'] = f'attachment; filename="batch_{pk}.docx"'
        return response

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


@login_required
def batch_run_rerun(request, pk):
    """Re-queue only failed items."""
    if request.method == 'POST':
        from .tasks import run_batch
        run = get_object_or_404(_batch_run_queryset(request.user), pk=pk)
        run.items.filter(status='error').update(status='pending')
        run.status = 'pending'
        run.save(update_fields=['status'])
        run_batch.delay(run.pk)
        messages.info(request, 'Re-queued failed items.')
    return redirect('question_generation:detail', pk=pk)
