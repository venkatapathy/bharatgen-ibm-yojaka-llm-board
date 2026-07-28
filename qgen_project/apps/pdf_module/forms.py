from django import forms

from apps.core.models import PDFIndexingSettings
from apps.core.storage import get_max_pdf_upload_mb

from .models import PDFContext
from .validators import validate_pdf_upload


class PDFContextUploadForm(forms.ModelForm):
    """Same upload fields for every role — indexing defaults come from Technical settings."""

    class Meta:
        model = PDFContext
        fields = [
            "name",
            "description",
            "zip_path",
        ]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["zip_path"].widget.attrs.update(
            {
                "accept": ".pdf,.zip,application/pdf,application/zip,application/x-zip-compressed",
                "x-ref": "fileInput",
            }
        )

    def clean_zip_path(self):
        uploaded = self.cleaned_data["zip_path"]
        max_size = get_max_pdf_upload_mb(self.user) * 1024 * 1024 if self.user else None
        validate_pdf_upload(uploaded, max_size_bytes=max_size)
        return uploaded

    def apply_indexing_defaults(self, instance):
        settings = PDFIndexingSettings.load()
        instance.strategy = settings.strategy
        instance.chunk_size = settings.chunk_size
        instance.chunk_overlap = settings.chunk_overlap
        instance.embed_model = settings.resolved_embed_model()
        instance.reranker_model = settings.resolved_reranker_model()
        return instance

    def save(self, commit=True):
        instance = super().save(commit=False)
        self.apply_indexing_defaults(instance)
        if commit:
            instance.save()
            self.save_m2m()
        return instance
