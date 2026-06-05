from django.contrib import admin
from .models import PDFContext, PDFChunk


@admin.register(PDFContext)
class PDFContextAdmin(admin.ModelAdmin):
    list_display  = ('name', 'strategy', 'status', 'created_by', 'created_at')
    list_filter   = ('strategy', 'status')
    search_fields = ('name',)


@admin.register(PDFChunk)
class PDFChunkAdmin(admin.ModelAdmin):
    list_display = ('context', 'source_file', 'chunk_index', 'token_count')
    list_filter  = ('context',)
