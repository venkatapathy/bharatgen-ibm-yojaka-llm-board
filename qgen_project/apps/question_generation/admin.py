from django.contrib import admin
from .models import BatchRun, BatchRunItem


class BatchRunItemInline(admin.TabularInline):
    model = BatchRunItem
    extra = 0


@admin.register(BatchRun)
class BatchRunAdmin(admin.ModelAdmin):
    list_display = ('name', 'topic', 'status', 'created_by', 'created_at', 'completed_at')
    list_filter  = ('status',)
    inlines      = [BatchRunItemInline]
