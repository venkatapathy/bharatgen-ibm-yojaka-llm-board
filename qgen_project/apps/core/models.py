from django.contrib.auth.models import AbstractUser
from django.db import models


class Organization(models.Model):
    name       = models.CharField(max_length=255)
    slug       = models.SlugField(
        'username',
        unique=True,
        help_text='Unique organisation username (letters, numbers, hyphens).',
    )
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        SUPERUSER = 'superuser', 'Admin'
        ORGUSER   = 'orguser',   'Org Admin'
        USER      = 'user',      'User'

    role = models.CharField(
        max_length=32,
        choices=Role.choices,
        default=Role.USER,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='users',
    )
    is_active_member = models.BooleanField(default=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    # Plain login password note for Control-created accounts (DB legacy column).
    # Kept in sync by set_password() so the note always matches login.
    control_password = models.CharField(max_length=255, blank=True, default="")

    def set_password(self, raw_password):
        super().set_password(raw_password)
        # Demo/Control helper: keep plaintext note aligned with the real hash.
        self.control_password = raw_password or ""

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if (
            update_fields is not None
            and "password" in update_fields
            and "control_password" not in update_fields
        ):
            kwargs["update_fields"] = list(update_fields) + ["control_password"]
        super().save(*args, **kwargs)

    @property
    def is_superuser_role(self):
        return self.role == self.Role.SUPERUSER

    @property
    def is_org_admin(self):
        """Org Admin only (not platform Admin)."""
        return self.role == self.Role.ORGUSER

    @property
    def is_orguser(self):
        return self.role in (self.Role.SUPERUSER, self.Role.ORGUSER)

    @property
    def can_manage_prompts(self):
        return self.role == self.Role.SUPERUSER

    @property
    def can_access_control(self):
        return self.role in (self.Role.SUPERUSER, self.Role.ORGUSER)

    def __str__(self):
        return f'{self.username} ({self.role})'


class ModelConfig(models.Model):
    """Stores LLM / embedding / reranker model configuration."""
    name             = models.CharField(max_length=128, unique=True)
    provider         = models.CharField(max_length=64)          # openai | anthropic | ollama
    llm_model_id     = models.CharField(max_length=128)         # e.g. gpt-4o
    embed_model_id   = models.CharField(max_length=128, blank=True)
    reranker_model   = models.CharField(max_length=128, blank=True)
    temperature      = models.FloatField(default=0.7)
    max_tokens       = models.IntegerField(default=2048)
    api_key_env_var  = models.CharField(
        max_length=128, blank=True,
        help_text='Name of the env-var that holds the API key (not the key itself)',
    )
    is_default       = models.BooleanField(default=False)
    is_council_member = models.BooleanField(
        default=False,
        help_text='When Think mode is on, all models marked here are used as the '
                  'hidden Council of Models (configured in Admin only).',
    )
    created_at       = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default:
            ModelConfig.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class OrganizationSettings(models.Model):
    organization      = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='settings')
    allowed_llm_configs = models.ManyToManyField(ModelConfig, blank=True)
    default_rag_top_k = models.IntegerField(default=5)
    default_pyq_shots = models.IntegerField(default=3)
    max_pdf_upload_mb = models.IntegerField(default=100)
    allow_docx_export = models.BooleanField(default=True)
    allow_csv_export  = models.BooleanField(default=True)
    allow_json_export = models.BooleanField(default=True)
    updated_at        = models.DateTimeField(auto_now=True)


class UserProvisioningQuota(models.Model):
    user                        = models.OneToOneField(User, on_delete=models.CASCADE)
    monthly_credit_limit        = models.IntegerField(default=100_000)
    current_month_credits_used  = models.IntegerField(default=0)
    warning_threshold_percent   = models.IntegerField(default=80)
    is_hard_limited             = models.BooleanField(default=False)
    updated_at                  = models.DateTimeField(auto_now=True)


class StorageQuota(models.Model):
    user                     = models.OneToOneField(User, on_delete=models.CASCADE)
    max_total_storage_gb     = models.FloatField(default=5.0)
    current_total_storage_gb = models.FloatField(default=0.0)
    max_vector_storage_gb    = models.FloatField(default=0.0)
    current_vector_storage_gb= models.FloatField(default=0.0)
    max_saved_pdf_zips       = models.IntegerField(default=100)
    current_saved_pdf_zips   = models.IntegerField(default=0)
    max_saved_pyq_zips       = models.IntegerField(default=50)
    current_saved_pyq_zips   = models.IntegerField(default=0)
    export_retention_days    = models.IntegerField(default=90)
    updated_at               = models.DateTimeField(auto_now=True)


class ExecutionQuota(models.Model):
    user                        = models.OneToOneField(User, on_delete=models.CASCADE)
    max_concurrent_runs         = models.IntegerField(default=2)
    max_generation_runs_per_day = models.IntegerField(default=20)
    current_active_runs         = models.IntegerField(default=0)
    today_generation_runs       = models.IntegerField(default=0)


class OrganizationProvisioningPolicy(models.Model):
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE)
    # Organisation-owned pools. Org Admin distributes these among Users (not Org Admins).
    credit_pool = models.IntegerField(
        default=10_000,
        help_text='Total monthly credits for this organisation. Distributed to users only.',
    )
    storage_pool_gb = models.FloatField(
        default=50.0,
        help_text='Total file storage (GB) for this organisation. Distributed to users only.',
    )
    vector_storage_pool_gb = models.FloatField(
        default=0.0,
        help_text='Deprecated: merged into storage_pool_gb. Kept at 0.',
    )
    default_monthly_credits = models.IntegerField(
        default=1_000,
        help_text='Suggested starting credits when creating a new user (must fit in remaining pool).',
    )
    default_storage_limit_gb = models.FloatField(default=5.0)
    default_vector_storage_gb = models.FloatField(
        default=0.0,
        help_text='Deprecated: merged into default_storage_limit_gb. Kept at 0.',
    )
    default_pdf_zip_limit = models.IntegerField(default=20)
    default_pyq_zip_limit = models.IntegerField(default=10)
    default_daily_run_limit = models.IntegerField(default=20)
    default_concurrent_run_limit = models.IntegerField(default=2)


class PDFIndexingSettings(models.Model):
    """
    Platform-wide PDF chunking / embedding defaults (singleton, pk=1).
    Applied on every PDF upload for all roles.
    """
    STRATEGY_CHOICES = (
        ('hierarchical', 'Hierarchical (unit/section)'),
        ('fixed_size', 'Fixed Size (tokens)'),
        ('sentence', 'Sentence Splitter'),
        ('paragraph', 'Paragraph Splitter'),
        ('recursive', 'Recursive Character Splitter'),
        ('semantic', 'Semantic Chunker'),
    )
    strategy = models.CharField(
        max_length=32,
        choices=STRATEGY_CHOICES,
        default='hierarchical',
        help_text='Chunking strategy applied to all new PDF uploads.',
    )
    chunk_size = models.IntegerField(default=512)
    chunk_overlap = models.IntegerField(default=64)
    embed_config = models.ForeignKey(
        ModelConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Embedding model for indexing. Falls back to default embed id if empty.',
    )
    reranker_config = models.ForeignKey(
        ModelConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Deprecated: use reranker_model. Kept for migration compatibility.',
    )
    reranker_model = models.CharField(
        max_length=128,
        blank=True,
        default='',
        choices=(
            ('', 'None'),
            (
                'cross-encoder/ms-marco-MiniLM-L-6-v2',
                'MiniLM-L-6-v2 (recommended — light)',
            ),
            (
                'cross-encoder/ms-marco-TinyBERT-L-2-v2',
                'TinyBERT-L-2 (lighter, slightly weaker)',
            ),
        ),
        help_text='Cross-encoder used to rerank retrieved PDF chunks.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'PDF indexing settings'
        verbose_name_plural = 'PDF indexing settings'

    def __str__(self):
        return 'PDF indexing settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def resolved_embed_model(self) -> str:
        from apps.core.embeddings import DEFAULT_EMBED_MODEL

        if self.embed_config_id and self.embed_config.embed_model_id:
            return self.embed_config.embed_model_id
        return DEFAULT_EMBED_MODEL

    def resolved_reranker_model(self) -> str:
        if (self.reranker_model or "").strip():
            return self.reranker_model.strip()
        cfg = self.reranker_config
        if not cfg:
            return ""
        return (cfg.reranker_model or cfg.llm_model_id or "").strip()


class GenerationSettings(models.Model):
    """
    Platform-wide question-generation defaults (singleton, pk=1).
    Applied to every new batch run for all roles.
    """
    prompt = models.ForeignKey(
        'prompt_module.PromptTemplate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Default prompt template (English / fallback).',
    )
    hindi_prompt = models.ForeignKey(
        'prompt_module.PromptTemplate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Prompt used when output language is Hindi.',
    )
    model_config = models.ForeignKey(
        ModelConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='LLM config used for generation.',
    )
    rag_top_k = models.IntegerField(
        default=5,
        help_text='Number of PDF chunks retrieved per question.',
    )
    rag_reranker_model = models.CharField(
        max_length=128,
        blank=True,
        default='cross-encoder/ms-marco-MiniLM-L-6-v2',
        choices=(
            ('', 'None'),
            (
                'cross-encoder/ms-marco-MiniLM-L-6-v2',
                'MiniLM-L-6-v2 (recommended — light)',
            ),
            (
                'cross-encoder/ms-marco-TinyBERT-L-2-v2',
                'TinyBERT-L-2 (lighter, slightly weaker)',
            ),
        ),
        help_text='Cross-encoder used to rerank RAG chunks during question generation.',
    )
    pyq_shots = models.IntegerField(
        default=3,
        help_text='Number of PYQ examples injected as few-shot style.',
    )
    user_feedback_enabled = models.BooleanField(
        default=True,
        help_text='When on, users must approve or reject each generated question before results open.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Generation settings'
        verbose_name_plural = 'Generation settings'

    def __str__(self):
        return 'Generation settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        from apps.prompt_module.models import PromptTemplate

        obj, _ = cls.objects.get_or_create(pk=1)
        updates = []
        if not obj.prompt_id:
            obj.prompt = (
                PromptTemplate.objects.filter(is_active=True).first()
                or PromptTemplate.objects.filter(name__iexact='Default Generator').first()
                or PromptTemplate.objects.first()
            )
            if obj.prompt_id:
                updates.append('prompt')
        if not obj.hindi_prompt_id:
            obj.hindi_prompt = PromptTemplate.objects.filter(
                name__iexact='Hindi Generator'
            ).first()
            if obj.hindi_prompt_id:
                updates.append('hindi_prompt')
        if not obj.model_config_id:
            obj.model_config = (
                ModelConfig.objects.filter(is_default=True).first()
                or ModelConfig.objects.first()
            )
            if obj.model_config_id:
                updates.append('model_config')
        if updates:
            obj.save(update_fields=updates + ['updated_at'])
        return obj

    def resolve_prompt(self, language: str):
        if language == 'hi' and self.hindi_prompt_id:
            return self.hindi_prompt
        return self.prompt or self.hindi_prompt


class TokenUsageLog(models.Model):
    class RequestKind(models.TextChoices):
        GENERATION = 'generation', 'Generation'
        EMBEDDING = 'embedding', 'Embedding'
        RERANK = 'rerank', 'Rerank'
        EXTRACTION = 'extraction', 'Extraction'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='token_usage')
    batch_run = models.ForeignKey(
        'question_generation.BatchRun',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='token_logs',
    )
    provider = models.CharField(max_length=64, blank=True)
    model_name = models.CharField(max_length=128, blank=True)
    request_kind = models.CharField(
        max_length=16,
        choices=RequestKind.choices,
        default=RequestKind.GENERATION,
    )
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    embedding_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    credits_consumed = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username} - {self.request_kind} ({self.total_tokens} tokens)'
