import uuid
from django.db import models
from apps.core.models import User

try:
    from pgvector.django import VectorField
except ImportError:
    # Fallback when pgvector not installed (CI / dev without PG)
    from django.db.models import TextField as VectorField  # type: ignore


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


class PDFChunk(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    context     = models.ForeignKey(PDFContext, on_delete=models.CASCADE, related_name='chunks')
    source_file = models.CharField(max_length=512)
    page_number = models.IntegerField(null=True, blank=True)
    chunk_index = models.IntegerField()
    text        = models.TextField()
    # VectorField falls back to TextField when pgvector unavailable
    embedding   = VectorField(dimensions=1536, null=True) if hasattr(VectorField, 'dimensions') \
                  else models.TextField(null=True, blank=True)
    token_count = models.IntegerField(default=0)
    metadata    = models.JSONField(default=dict)

    class Meta:
        ordering = ['context', 'source_file', 'chunk_index']
        indexes  = [models.Index(fields=['context'])]

    def __str__(self):
        return f'{self.source_file}[{self.chunk_index}]'
