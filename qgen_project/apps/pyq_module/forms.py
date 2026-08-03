from django import forms

from apps.core.storage import get_max_pyq_upload_mb

from .models import PYQModule, Question
from .validators import validate_pyq_upload


class PYQModuleUploadForm(forms.ModelForm):
    class Meta:
        model = PYQModule
        fields = ["name", "source_file"]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["source_file"].widget.attrs.update(
            {
                "accept": ".pdf,application/pdf",
                "x-ref": "fileInput",
            }
        )

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
    options_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Options (MCQ)",
        help_text="One option per line. Leave blank for non-MCQ questions.",
    )

    class Meta:
        model = Question
        fields = [
            "question_text",
            "question_type",
            "bloom",
            "marks",
            "reference_answer",
        ]
        widgets = {
            "question_text": forms.Textarea(attrs={"rows": 6}),
            "reference_answer": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reference_answer"].required = False
        self.fields["marks"].required = True

        options = []
        if self.instance and self.instance.pk:
            raw = self.instance.options or []
            if isinstance(raw, dict):
                options = [f"{k}. {v}" for k, v in raw.items()]
            elif isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        label = item.get("label") or item.get("key") or ""
                        text = item.get("text") or item.get("value") or item.get("option") or ""
                        options.append(f"{label}. {text}".strip(". ").strip() if label else str(text))
                    else:
                        options.append(str(item))
        self.fields["options_text"].initial = "\n".join(options)

        # Only show options editor for MCQ by default; still editable if switching type.
        if self.instance and self.instance.question_type != "MCQ" and not options:
            self.fields["options_text"].widget.attrs["placeholder"] = "Optional — for MCQ only"

    def clean_options_text(self):
        text = (self.cleaned_data.get("options_text") or "").strip()
        if not text:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    def save(self, commit=True):
        instance = super().save(commit=False)
        options = self.cleaned_data.get("options_text") or []
        qtype = instance.question_type
        if qtype == "MCQ":
            instance.options = options
        else:
            instance.options = options if options else []
        if instance.rubrics is None:
            instance.rubrics = {}
        if commit:
            instance.save()
        return instance
