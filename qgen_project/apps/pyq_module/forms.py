from django import forms

from apps.core.storage import get_max_pyq_upload_mb

from .models import PYQModule, Question
from .validators import validate_pyq_upload


class PYQModuleUploadForm(forms.ModelForm):
    class Meta:
        model = PYQModule
        fields = ["name", "description", "source_file"]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_source_file(self):
        uploaded = self.cleaned_data["source_file"]
        max_size = get_max_pyq_upload_mb(self.user) * 1024 * 1024 if self.user else None
        validate_pyq_upload(uploaded, max_size_bytes=max_size)
        return uploaded

    def save(self, commit=True):
        instance = super().save(commit=False)
        upload = self.cleaned_data.get("source_file")
        if upload:
            instance.file_size_bytes = upload.size
            instance.original_filename = upload.name
        if commit:
            instance.save()
        return instance


class QuestionEditForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = [
            "question_text",
            "question_type",
            "bloom",
            "marks",
            "topic",
            "reference_answer",
            "options",
            "rubrics",
        ]
