from django.contrib import admin
from .models import PYQModule, Question


@admin.register(PYQModule)
class PYQModuleAdmin(admin.ModelAdmin):
    list_display  = ('name', 'status', 'created_by', 'created_at')
    list_filter   = ('status',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display  = ('question_type', 'bloom', 'marks', 'is_generated', 'topic', 'created_at')
    list_filter   = ('question_type', 'bloom', 'is_generated')
    search_fields = ('question_text', 'topic')
