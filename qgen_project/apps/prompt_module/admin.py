from django.contrib import admin
from .models import PromptTemplate, PromptVersion


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display  = ('name', 'version', 'is_active', 'created_by', 'updated_at')
    list_filter   = ('is_active',)
    search_fields = ('name',)


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    list_display = ('template', 'version', 'saved_by', 'saved_at')
    list_filter  = ('template',)
