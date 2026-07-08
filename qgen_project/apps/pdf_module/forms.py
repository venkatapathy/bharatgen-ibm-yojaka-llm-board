from django import forms

from apps.core.embeddings import DEFAULT_EMBED_MODEL
from apps.core.models import ModelConfig
from apps.core.storage import get_max_pdf_upload_mb

from .models import PDFContext
from .validators import validate_pdf_upload


class PDFContextUploadForm(forms.ModelForm):
    embed_config = forms.ModelChoiceField(queryset=ModelConfig.objects.none(), required=False)
    reranker_config = forms.ModelChoiceField(queryset=ModelConfig.objects.none(), required=False)

    class Meta:
        model = PDFContext
        fields = [
            "name",
            "description",
            "zip_path",
            "strategy",
            "chunk_size",
            "chunk_overlap",
            "embed_config",
            "reranker_config",
        ]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["embed_config"].queryset = ModelConfig.objects.exclude(embed_model_id="")
        self.fields["reranker_config"].queryset = ModelConfig.objects.exclude(reranker_model="")

    def clean_zip_path(self):
        uploaded = self.cleaned_data["zip_path"]
        max_size = get_max_pdf_upload_mb(self.user) * 1024 * 1024 if self.user else None
        validate_pdf_upload(uploaded, max_size_bytes=max_size)
        return uploaded

    def save(self, commit=True):
        instance = super().save(commit=False)
        embed_config = self.cleaned_data.get("embed_config")
        reranker_config = self.cleaned_data.get("reranker_config")
        if embed_config:
            instance.embed_model = embed_config.embed_model_id or instance.embed_model
        if reranker_config:
            instance.reranker_model = reranker_config.reranker_model or reranker_config.llm_model_id
        if not instance.embed_model:
            instance.embed_model = DEFAULT_EMBED_MODEL
        upload = self.cleaned_data.get("zip_path")
        if upload:
            instance.file_size_bytes = upload.size
            instance.original_filename = upload.name
        if commit:
            instance.save()
            self.save_m2m()
        return instance
