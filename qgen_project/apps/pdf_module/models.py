import uuid
from django.db import models
from apps.core.models import User
from pgvector.django import VectorField

DEFAULT_EMBED_DIMENSIONS = 768


class ChunkingStrategy(models.TextChoices):
    FIXED_SIZE = 'fixed_size', 'Fixed Size (tokens)'
    SENTENCE   = 'sentence',   'Sentence Splitter'
    PARAGRAPH  = 'paragraph',  'Paragraph Splitter'
    RECURSIVE  = 'recursive',  'Recursive Character Splitter'
    SEMANTIC   = 'semantic',   'Semantic Chunker'


class PDFContext(models.Model):
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization  = models.ForeignKey('core.Organization', on_delete=models.CASCADE, null=True)
    name          = models.CharField(max_length=256)
    description   = models.TextField(blank=True)
    zip_path      = models.FileField(upload_to='pdf_uploads/%Y/%m/')
    strategy      = models.CharField(max_length=32, choices=ChunkingStrategy.choices,
                                     default=ChunkingStrategy.FIXED_SIZE)
    chunk_size    = models.IntegerField(default=512)
    chunk_overlap = models.IntegerField(default=64)
    embed_model   = models.CharField(max_length=128)
    reranker_model= models.CharField(max_length=128, blank=True)
    status        = models.CharField(max_length=32, default='pending')
    error_message = models.TextField(blank=True)
    file_size_bytes = models.BigIntegerField(default=0)
    original_filename = models.CharField(max_length=512, blank=True)
    has_embedding = models.BooleanField(default=True)
    needs_reindex = models.BooleanField(default=False)
    embedded_chunk_count = models.IntegerField(default=0)
    created_by    = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                      related_name='pdf_contexts')
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def chunk_count(self):
        return self.chunks.count()

    @property
    def file_size_label(self):
        from apps.core.storage import _bytes_to_gb, _format_storage_amount

        return _format_storage_amount(_bytes_to_gb(self.file_size_bytes))


class PDFChunk(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    context     = models.ForeignKey(PDFContext, on_delete=models.CASCADE, related_name='chunks')
    source_file = models.CharField(max_length=512)
    page_number = models.IntegerField(null=True, blank=True)
    chunk_index = models.IntegerField()
    text        = models.TextField()
    embedding   = VectorField(dimensions=DEFAULT_EMBED_DIMENSIONS, null=True, blank=True)
    token_count = models.IntegerField(default=0)
    metadata    = models.JSONField(default=dict)

    class Meta:
        ordering = ['context', 'source_file', 'chunk_index']
        indexes  = [models.Index(fields=['context'])]

    def __str__(self):
        return f'{self.source_file}[{self.chunk_index}]'
