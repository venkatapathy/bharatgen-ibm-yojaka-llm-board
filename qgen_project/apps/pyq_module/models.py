from django.db import models
from apps.core.models import User


class QuestionType(models.TextChoices):
    MCQ          = 'MCQ',   'Multiple Choice'
    SHORT_ANSWER = 'SHORT', 'Short Answer'
    LONG_ANSWER  = 'LONG',  'Long Answer'
    FILL_BLANK   = 'FILL',  'Fill in the Blank'
    TRUE_FALSE   = 'TF',    'True / False'
    CASE_STUDY   = 'CASE',  'Case Study'
    NUMERICAL    = 'NUM',   'Numerical'


class BloomLevel(models.TextChoices):
    REMEMBER   = 'remember',   'Remember'
    UNDERSTAND = 'understand', 'Understand'
    APPLY      = 'apply',      'Apply'
    ANALYSE    = 'analyse',    'Analyse'
    EVALUATE   = 'evaluate',   'Evaluate'
    CREATE     = 'create',     'Create'


class PYQModule(models.Model):
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, null=True)
    name         = models.CharField(max_length=256)
    description  = models.TextField(blank=True)
    source_file  = models.FileField(upload_to='pyq_uploads/%Y/%m/', null=True, blank=True)
    status       = models.CharField(max_length=32, default='pending')
    error_msg    = models.TextField(blank=True)
    created_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Question(models.Model):
    """Shared table: PYQ-extracted (is_generated=False) and AI-generated (is_generated=True)."""
    question_type    = models.CharField(max_length=8, choices=QuestionType.choices)
    bloom            = models.CharField(max_length=16, choices=BloomLevel.choices)
    marks            = models.FloatField()
    is_generated     = models.BooleanField(default=False)
    question_text    = models.TextField()
    reference_answer = models.TextField(blank=True)
    rubrics          = models.JSONField(default=dict)
    topic            = models.CharField(max_length=256, blank=True)
    options          = models.JSONField(default=list)   # MCQ options

    pyq_module = models.ForeignKey(
        PYQModule, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='questions')
    batch_run  = models.ForeignKey(
        'question_generation.BatchRun', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='questions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pyq_module', 'topic', 'created_at']

    def __str__(self):
        return f'[{self.question_type}] {self.question_text[:60]}'
