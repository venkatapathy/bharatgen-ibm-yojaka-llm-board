from django.db import models
from apps.core.models import User, ModelConfig
from apps.pdf_module.models import PDFContext
from apps.pyq_module.models import PYQModule
from apps.prompt_module.models import PromptTemplate


class BatchRun(models.Model):
    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        RUNNING   = 'running',   'Running'
        COMPLETED = 'completed', 'Completed'
        PARTIAL   = 'partial',   'Partial (some failed)'
        FAILED    = 'failed',    'Failed'

    name          = models.CharField(max_length=256)
    topic         = models.CharField(max_length=512)
    pdf_contexts  = models.ManyToManyField(PDFContext, blank=True, related_name='batch_runs')
    pyq_modules   = models.ManyToManyField(PYQModule,  blank=True, related_name='batch_runs')
    prompt        = models.ForeignKey(PromptTemplate, on_delete=models.SET_NULL, null=True)
    model_config  = models.ForeignKey(ModelConfig,    on_delete=models.SET_NULL, null=True)
    rag_top_k     = models.IntegerField(default=5)
    pyq_shots     = models.IntegerField(default=3)
    council_enabled = models.BooleanField(
        default=False,
        help_text='When enabled, generated questions are verified by selected council models '
                  'on bloom, correctness, question type, and appropriateness. '
                  'Only majority-approved questions are kept.',
    )
    council_models = models.ManyToManyField(
        ModelConfig,
        blank=True,
        related_name='council_batch_runs',
    )
    council_rejected_count = models.IntegerField(
        default=0,
        help_text='Questions discarded because they failed majority council approval.',
    )
    status        = models.CharField(max_length=16, choices=Status.choices,
                                     default=Status.PENDING)
    error_summary = models.TextField(blank=True)
    celery_task_id= models.CharField(max_length=256, blank=True)
    expected_questions = models.IntegerField(default=0)
    active_item = models.ForeignKey(
        'question_generation.BatchRunItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_by    = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                      related_name='batch_runs')
    created_at    = models.DateTimeField(auto_now_add=True)
    completed_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Batch #{self.pk} — {self.name}'

    @property
    def total_questions(self):
        return self.questions.count()

    @property
    def progress(self):
        items = self.items.all()
        if not items:
            return 0
        done = items.filter(status='done').count()
        return int(done / items.count() * 100)


class BatchRunItem(models.Model):
    """One row per question-type/bloom/marks/count specification."""
    batch_run     = models.ForeignKey(BatchRun, on_delete=models.CASCADE, related_name='items')
    question_type = models.CharField(max_length=8)
    bloom         = models.CharField(max_length=16)
    marks         = models.FloatField()
    count         = models.IntegerField()
    status        = models.CharField(max_length=16, default='pending')
    error_detail  = models.TextField(blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.question_type}/{self.bloom} x{self.count}'
