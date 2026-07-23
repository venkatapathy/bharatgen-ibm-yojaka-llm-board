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
    control_password = models.CharField(max_length=255, blank=True, default="")

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
    max_vector_storage_gb    = models.FloatField(default=2.0)
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
        default=20.0,
        help_text='Total vector/embedding storage (GB) for this organisation. Distributed to users only.',
    )
    default_monthly_credits = models.IntegerField(
        default=1_000,
        help_text='Suggested starting credits when creating a new user (must fit in remaining pool).',
    )
    default_storage_limit_gb = models.FloatField(default=5.0)
    default_vector_storage_gb = models.FloatField(default=2.0)
    default_pdf_zip_limit = models.IntegerField(default=20)
    default_pyq_zip_limit = models.IntegerField(default=10)
    default_daily_run_limit = models.IntegerField(default=20)
    default_concurrent_run_limit = models.IntegerField(default=2)


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
