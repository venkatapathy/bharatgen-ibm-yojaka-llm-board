from django.db import models
import re

from apps.core.models import User


def _norm_answer_text(value: str) -> str:
    if not value:
        return ''
    text = str(value).strip().upper()
    text = re.sub(r'\s+', ' ', text)
    return text.replace('×', 'X').replace('~', '')


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
    file_size_bytes = models.BigIntegerField(default=0)
    original_filename = models.CharField(max_length=512, blank=True)
    created_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def question_count(self):
        return self.questions.count()

    @property
    def mcq_count(self):
        return self.questions.filter(question_type=QuestionType.MCQ).count()

    @property
    def question_type_breakdown(self):
        """Counts per question type for display in list/detail."""
        from django.db.models import Count

        labels = dict(QuestionType.choices)
        rows = (
            self.questions.values('question_type')
            .annotate(count=Count('id'))
            .order_by('-count', 'question_type')
        )
        return [
            {
                'code': row['question_type'],
                'label': labels.get(row['question_type'], row['question_type']),
                'count': row['count'],
            }
            for row in rows
        ]


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

    @property
    def is_mcq(self):
        return self.question_type == QuestionType.MCQ

    def is_correct_option(self, label: str, text: str = '') -> bool:
        answer = _norm_answer_text(self.reference_answer)
        if not answer:
            return False

        label_norm = label.strip().upper().rstrip('.')
        if answer in 'ABCDEFGH' or (len(answer) == 2 and answer[0] in 'ABCDEFGH' and answer[1] in '.):'):
            return label_norm == answer[0]

        text_norm = _norm_answer_text(text)
        if not text_norm:
            return False

        return (
            answer == text_norm
            or answer in text_norm
            or text_norm in answer
            or (len(answer) >= 8 and answer[:8] in text_norm)
        )

    def get_mcq_options(self):
        """Return MCQ options as {label, text, is_correct} dicts."""
        options = self.options or []
        if not options:
            return []

        labels = 'ABCDEFGH'
        if isinstance(options, dict):
            normalized = [{'label': str(k), 'text': str(v)} for k, v in options.items()]
        else:
            normalized = []
            for i, opt in enumerate(options):
                if isinstance(opt, dict):
                    label = str(
                        opt.get('label') or opt.get('key')
                        or (labels[i] if i < len(labels) else '?')
                    )
                    text = str(opt.get('text') or opt.get('value') or opt.get('option') or '')
                    normalized.append({'label': label.rstrip(')').lstrip('('), 'text': text})
                else:
                    text = str(opt).strip()
                    label = labels[i] if i < len(labels) else '?'
                    if text and text[0] in labels and (len(text) > 2 and text[1] in '.)'):
                        label, text = text[0], text[2:].strip()
                    normalized.append({'label': label, 'text': text})

        for item in normalized:
            item['is_correct'] = self.is_correct_option(item['label'], item['text'])
        return normalized
