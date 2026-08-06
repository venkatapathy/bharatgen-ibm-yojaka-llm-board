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
    """IGNOU demo buckets — keep in sync with the active generation prompt."""
    REFERENCE_TO_CONTEXT = 'RTC', 'Reference to Context'
    SHORT_ANSWER = 'SHORT', 'Short Answer'
    LONG_ANSWER = 'LONG', 'Long Answer'


# Map legacy / free-text labels → the three demo types.
QUESTION_TYPE_ALIASES = {
    'RTC': QuestionType.REFERENCE_TO_CONTEXT,
    'REF': QuestionType.REFERENCE_TO_CONTEXT,
    'REFERENCE': QuestionType.REFERENCE_TO_CONTEXT,
    'REFERENCE-TO-CONTEXT': QuestionType.REFERENCE_TO_CONTEXT,
    'REFERENCE_TO_CONTEXT': QuestionType.REFERENCE_TO_CONTEXT,
    'SHORT': QuestionType.SHORT_ANSWER,
    'SHORT_ANSWER': QuestionType.SHORT_ANSWER,
    'SHORT NOTE': QuestionType.SHORT_ANSWER,
    'DEFINITION': QuestionType.SHORT_ANSWER,
    'IDENTIFICATION': QuestionType.SHORT_ANSWER,
    'LONG': QuestionType.LONG_ANSWER,
    'LONG_ANSWER': QuestionType.LONG_ANSWER,
    'THEMATIC': QuestionType.LONG_ANSWER,
    'ANALYTICAL': QuestionType.LONG_ANSWER,
    'COMPARATIVE': QuestionType.LONG_ANSWER,
    'CRITICAL': QuestionType.LONG_ANSWER,
    'ESSAY': QuestionType.LONG_ANSWER,
    # Legacy app types → nearest bucket
    'MCQ': QuestionType.SHORT_ANSWER,
    'FILL': QuestionType.SHORT_ANSWER,
    'TF': QuestionType.SHORT_ANSWER,
    'NUM': QuestionType.SHORT_ANSWER,
    'CASE': QuestionType.LONG_ANSWER,
}


def normalize_question_type(value, *, marks=None, question_text='') -> str:
    """Resolve any label to RTC / SHORT / LONG."""
    raw = str(value or '').strip()
    key = re.sub(r'\s+', ' ', raw).upper().replace('_', '-')
    key_compact = key.replace(' ', '-').replace('/', '-')
    if key_compact in QUESTION_TYPE_ALIASES:
        return QUESTION_TYPE_ALIASES[key_compact]
    if key in QUESTION_TYPE_ALIASES:
        return QUESTION_TYPE_ALIASES[key]
    text = (question_text or '').lower()
    if any(
        phrase in text
        for phrase in (
            'reference to context',
            'reference to the context',
            'with reference to context',
            'सप्रसंग',
            'सप्रसंग व्याख्या',
        )
    ):
        return QuestionType.REFERENCE_TO_CONTEXT
    try:
        m = float(marks) if marks is not None else None
    except (TypeError, ValueError):
        m = None
    if m is not None and m > 10:
        return QuestionType.LONG_ANSWER
    return QuestionType.SHORT_ANSWER



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
        # Legacy helper — MCQ is no longer a demo type.
        return 0

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


class QuestionQuerySet(models.QuerySet):
    """Ensure review/provenance fields are never NULL on bulk inserts."""

    _NULL_SAFE_DEFAULTS = {
        "rag_chunks": list,
        "pyq_examples": list,
        "rubrics": dict,
        "options": list,
        "user_decision": "pending",
        "user_feedback": "",
        "reference_answer": "",
        "topic": "",
    }

    def bulk_create(self, objs, *args, **kwargs):
        for obj in objs:
            self.model.normalize_null_safe_fields(obj)
        return super().bulk_create(objs, *args, **kwargs)


class QuestionManager(models.Manager.from_queryset(QuestionQuerySet)):
    pass


class Question(models.Model):
    """Shared table: PYQ-extracted (is_generated=False) and AI-generated (is_generated=True)."""

    class UserDecision(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    question_type    = models.CharField(max_length=8, choices=QuestionType.choices)
    bloom            = models.CharField(max_length=16, choices=BloomLevel.choices)
    marks            = models.FloatField()
    is_generated     = models.BooleanField(default=False)
    question_text    = models.TextField()
    reference_answer = models.TextField(blank=True, default="")
    rubrics          = models.JSONField(default=dict)
    topic            = models.CharField(max_length=256, blank=True, default="")
    options          = models.JSONField(default=list)   # MCQ options

    # PhD dataset: RAG / PYQ provenance used when this question was generated.
    rag_chunks = models.JSONField(
        default=list,
        blank=True,
        help_text="Snapshot of PDF chunks retrieved for this generation item.",
    )
    pyq_examples = models.JSONField(
        default=list,
        blank=True,
        help_text="Snapshot of PYQ few-shot examples used for this generation item.",
    )
    # Human review (mandatory after generation completes).
    user_decision = models.CharField(
        max_length=16,
        choices=UserDecision.choices,
        default=UserDecision.PENDING,
        blank=True,
    )
    user_feedback = models.TextField(blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    pyq_module = models.ForeignKey(
        PYQModule, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='questions')
    batch_run  = models.ForeignKey(
        'question_generation.BatchRun', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='questions')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = QuestionManager()

    class Meta:
        ordering = ['pyq_module', 'topic', 'created_at']

    @classmethod
    def normalize_null_safe_fields(cls, obj):
        """Fill NOT NULL JSON/text fields that bulk_create may otherwise omit as NULL."""
        for field, default in QuestionQuerySet._NULL_SAFE_DEFAULTS.items():
            value = getattr(obj, field, None)
            if value is None:
                setattr(obj, field, default() if callable(default) else default)
        return obj

    def save(self, *args, **kwargs):
        self.normalize_null_safe_fields(self)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'[{self.question_type}] {self.question_text[:60]}'

    @property
    def is_mcq(self):
        # Kept for old rows / templates; demo types are RTC/SHORT/LONG only.
        return self.question_type == 'MCQ'

    @property
    def council_opinion(self):
        return (self.rubrics or {}).get("council") or {}

    @property
    def needs_review(self):
        return self.is_generated and self.user_decision == self.UserDecision.PENDING

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
