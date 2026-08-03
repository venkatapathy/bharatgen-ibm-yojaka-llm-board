from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (User, Organization, ModelConfig, OrganizationSettings,
                     UserProvisioningQuota, StorageQuota, ExecutionQuota,
                     OrganizationProvisioningPolicy, PDFIndexingSettings,
                     GenerationSettings, TokenUsageLog)



@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'created_at')
    list_display_links = ('name',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    # slug field is shown as "username" via model verbose_name


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'role', 'organization', 'is_active_member')
    list_filter  = ('role', 'organization', 'is_active_member')
    fieldsets    = BaseUserAdmin.fieldsets + (
        ('Platform Role', {'fields': ('role', 'organization', 'is_active_member')}),
    )


@admin.register(ModelConfig)
class ModelConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'llm_model_id', 'is_default', 'is_council_member')
    list_filter  = ('provider', 'is_default', 'is_council_member')
    list_editable = ('is_council_member',)
    search_fields = ('name', 'llm_model_id')
    fieldsets = (
        (None, {
            'fields': (
                'name', 'provider', 'llm_model_id', 'embed_model_id',
                'reranker_model', 'temperature', 'max_tokens', 'api_key_env_var',
                'is_default',
            ),
        }),
        ('Council of Models (Think mode)', {
            'description': (
                'Users only see a Think toggle on New Run. Mark models here to '
                'include them in the hidden council when Think is enabled.'
            ),
            'fields': ('is_council_member',),
        }),
    )


@admin.register(GenerationSettings)
class GenerationSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "prompt",
        "hindi_prompt",
        "model_config",
        "rag_top_k",
        "pyq_shots",
        "user_feedback_enabled",
        "updated_at",
    )
    list_editable = ("user_feedback_enabled",)
    fieldsets = (
        (
            "Prompts & model",
            {"fields": ("prompt", "hindi_prompt", "model_config", "rag_top_k", "pyq_shots")},
        ),
        (
            "User feedback",
            {
                "description": (
                    "When enabled, users must approve or reject each generated "
                    "question before the full results open."
                ),
                "fields": ("user_feedback_enabled",),
            },
        ),
    )


admin.site.register(OrganizationSettings)


@admin.register(PDFIndexingSettings)
class PDFIndexingSettingsAdmin(admin.ModelAdmin):
    # Reranker is query-time (Generation settings); hide leftover DB field.
    fields = ("strategy", "chunk_size", "chunk_overlap", "embed_config")


admin.site.register(UserProvisioningQuota)
admin.site.register(StorageQuota)
admin.site.register(ExecutionQuota)
admin.site.register(OrganizationProvisioningPolicy)
admin.site.register(TokenUsageLog)
