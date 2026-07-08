from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (User, Organization, ModelConfig, OrganizationSettings,
                     UserProvisioningQuota, StorageQuota, ExecutionQuota,
                     OrganizationProvisioningPolicy, TokenUsageLog)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'created_at')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'role', 'organization', 'is_active_member')
    list_filter  = ('role', 'organization', 'is_active_member')
    fieldsets    = BaseUserAdmin.fieldsets + (
        ('Platform Role', {'fields': ('role', 'organization', 'is_active_member')}),
    )


@admin.register(ModelConfig)
class ModelConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'llm_model_id', 'is_default')
    list_filter  = ('provider',)


admin.site.register(OrganizationSettings)
admin.site.register(UserProvisioningQuota)
admin.site.register(StorageQuota)
admin.site.register(ExecutionQuota)
admin.site.register(OrganizationProvisioningPolicy)
admin.site.register(TokenUsageLog)
