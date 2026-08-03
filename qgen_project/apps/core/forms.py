"""Forms for the Control panel (Admin / Org Admin)."""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from allauth.account.forms import LoginForm as AllauthLoginForm

from .membership import DEACTIVATED_MESSAGE, user_may_access_app
from .models import (
    ModelConfig,
    Organization,
    OrganizationProvisioningPolicy,
    PDFIndexingSettings,
    GenerationSettings,
    User,
)


class QGenLoginForm(AllauthLoginForm):
    """Show a clear message when a deactivated Org Admin / User tries to sign in."""

    def clean(self):
        login = (self.data.get("login") or "").strip()
        if login:
            candidate = (
                User.objects.filter(Q(username__iexact=login) | Q(email__iexact=login))
                .only("id", "role", "is_active_member", "is_superuser")
                .first()
            )
            if candidate is not None and not user_may_access_app(candidate):
                raise forms.ValidationError(DEACTIVATED_MESSAGE)
        return super().clean()



class PDFIndexingSettingsForm(forms.ModelForm):
    class Meta:
        model = PDFIndexingSettings
        fields = [
            "strategy",
            "chunk_size",
            "chunk_overlap",
            "embed_config",
        ]
        labels = {
            "strategy": "Chunking strategy",
            "chunk_size": "Chunk size",
            "chunk_overlap": "Chunk overlap",
            "embed_config": "Embedding model",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["embed_config"].queryset = ModelConfig.objects.exclude(
            embed_model_id=""
        )
        self.fields["embed_config"].required = False
        self.fields["embed_config"].empty_label = "Platform default"
        self.fields["chunk_size"].widget.attrs.setdefault("min", 64)
        self.fields["chunk_overlap"].widget.attrs.setdefault("min", 0)
        for field in self.fields.values():
            field.help_text = ""
            field.widget.attrs.setdefault("class", "tech-input")


class GenerationSettingsForm(forms.ModelForm):
    class Meta:
        model = GenerationSettings
        fields = [
            "prompt",
            "hindi_prompt",
            "model_config",
            "rag_top_k",
            "rag_reranker_model",
            "pyq_shots",
            "user_feedback_enabled",
        ]
        labels = {
            "prompt": "Prompt (English)",
            "hindi_prompt": "Prompt (Hindi)",
            "model_config": "Generation model",
            "rag_top_k": "RAG Top-K",
            "rag_reranker_model": "RAG reranker",
            "pyq_shots": "PYQ n-shot",
            "user_feedback_enabled": "User feedback on questions",
        }
        help_texts = {
            "prompt": "",
            "hindi_prompt": "",
            "model_config": "",
            "rag_top_k": "",
            "rag_reranker_model": "",
            "pyq_shots": "",
            "user_feedback_enabled": "",
        }
        widgets = {
            "model_config": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        from apps.prompt_module.models import PromptTemplate
        from apps.core.rerankers import RERANKER_CHOICES

        super().__init__(*args, **kwargs)
        prompts = PromptTemplate.objects.order_by("name")
        self.fields["prompt"].queryset = prompts
        self.fields["prompt"].required = False
        self.fields["prompt"].empty_label = "— select —"
        self.fields["hindi_prompt"].queryset = prompts
        self.fields["hindi_prompt"].required = False
        self.fields["hindi_prompt"].empty_label = "— select —"
        llm_qs = ModelConfig.objects.exclude(llm_model_id="").order_by("name")
        self.fields["model_config"].queryset = llm_qs
        self.fields["model_config"].required = True
        self.fields["model_config"].empty_label = None
        self.fields["rag_top_k"].widget.attrs.update({"min": 1, "max": 20})
        self.fields["rag_reranker_model"].choices = RERANKER_CHOICES
        self.fields["rag_reranker_model"].required = False
        self.fields["pyq_shots"].widget.attrs.update({"min": 0, "max": 10})
        self.fields["user_feedback_enabled"].required = False
        for name, field in self.fields.items():
            field.help_text = ""
            if name not in {"model_config", "user_feedback_enabled"}:
                field.widget.attrs["class"] = "tech-input"

    def clean_rag_top_k(self):
        value = int(self.cleaned_data.get("rag_top_k") or 5)
        return max(1, min(value, 20))

    def clean_pyq_shots(self):
        value = int(self.cleaned_data.get("pyq_shots") or 0)
        return max(0, min(value, 10))


class OrganizationPolicyForm(forms.ModelForm):
    class Meta:
        model = OrganizationProvisioningPolicy
        fields = [
            "credit_pool",
            "storage_pool_gb",
            "default_monthly_credits",
            "default_storage_limit_gb",
            "default_pdf_zip_limit",
            "default_pyq_zip_limit",
            "default_daily_run_limit",
            "default_concurrent_run_limit",
        ]
        labels = {
            "credit_pool": "Organisation credit pool",
            "storage_pool_gb": "Organisation storage pool (GB)",
            "default_monthly_credits": "Suggested credits per new user",
            "default_storage_limit_gb": "Suggested storage per new user (GB)",
            "default_pdf_zip_limit": "Default PDF context limit",
            "default_pyq_zip_limit": "Default PYQ module limit",
            "default_daily_run_limit": "Default daily generation runs",
            "default_concurrent_run_limit": "Default concurrent runs",
        }
        help_texts = {
            "credit_pool": "Organisation-owned credits. Org Admin distributes to users only.",
            "storage_pool_gb": "Organisation-owned storage (files + embeddings). Distributed to users only.",
            "default_monthly_credits": "Starting suggestion when creating a user (must fit remaining pool).",
        }
        widgets = {
            field: forms.NumberInput(attrs={"class": "form-control"})
            for field in [
                "credit_pool",
                "storage_pool_gb",
                "default_monthly_credits",
                "default_storage_limit_gb",
                "default_pdf_zip_limit",
                "default_pyq_zip_limit",
                "default_daily_run_limit",
                "default_concurrent_run_limit",
            ]
        }


class OrganizationCreateForm(forms.ModelForm):
    is_active = forms.TypedChoiceField(
        label="Status",
        coerce=lambda v: v == "True",
        choices=((True, "Active"), (False, "Inactive")),
        widget=forms.RadioSelect,
        initial=True,
    )

    class Meta:
        model = Organization
        fields = ["name", "slug", "is_active"]
        labels = {"slug": "Username"}
        help_texts = {
            "slug": "Unique organisation username (letters, numbers, hyphens).",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. eduqgen-demo-lab",
                    "autocomplete": "off",
                }
            ),
        }


class OrgUserCreateForm(UserCreationForm):
    """Create Org Admin (platform Admin) or User (Org Admin)."""

    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    credits_to_assign = forms.IntegerField(
        min_value=0,
        required=False,
        label="Credits to assign",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    storage_gb = forms.FloatField(
        min_value=0,
        required=False,
        label="Storage (GB)",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
    )

    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]
        widgets = {
            "username": forms.TextInput(
                attrs={"class": "form-control", "autocomplete": "username"}
            ),
        }

    def __init__(
        self,
        *args,
        create_org_admin=False,
        max_credits=0,
        suggested_credits=0,
        max_storage=0,
        suggested_storage=0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.create_org_admin = create_org_admin
        self.max_credits = max(int(max_credits), 0)
        self.max_storage = max(float(max_storage), 0.0)
        self.fields["password1"].widget.attrs["class"] = "form-control"
        self.fields["password2"].widget.attrs["class"] = "form-control"
        # Interactive checks live in the template (pw-rules); drop Django's static help.
        self.fields["password1"].help_text = ""
        self.fields["password2"].help_text = ""
        self.fields["username"].help_text = (
            "Login username for this Org Admin."
            if create_org_admin
            else "Login username for this user."
        )
        if create_org_admin:
            # Org Admins manage the org; they do not consume org pools.
            self.fields["email"].widget = forms.HiddenInput()
            for name in ("credits_to_assign", "storage_gb"):
                self.fields[name].widget = forms.HiddenInput()
                self.fields[name].required = False
                self.fields[name].initial = 0
        else:
            self.fields["credits_to_assign"].required = True
            self.fields["storage_gb"].required = True
            self.fields["credits_to_assign"].initial = min(
                int(suggested_credits or 0), self.max_credits
            )
            self.fields["storage_gb"].initial = min(
                float(suggested_storage or 0), self.max_storage
            )
            self.fields["credits_to_assign"].help_text = (
                f"Organisation credit pool remaining: {self.max_credits}."
            )
            self.fields["storage_gb"].help_text = (
                f"Organisation storage pool remaining: {self.max_storage:.2f} GB "
                "(includes PDF files and embeddings)."
            )

    def clean_credits_to_assign(self):
        value = int(self.cleaned_data.get("credits_to_assign") or 0)
        if self.create_org_admin:
            return 0
        if value > self.max_credits:
            raise forms.ValidationError(
                f"Only {self.max_credits} credits remain in the organisation pool."
            )
        return value

    def clean_storage_gb(self):
        value = float(self.cleaned_data.get("storage_gb") or 0)
        if self.create_org_admin:
            return 0.0
        if value > self.max_storage + 1e-9:
            raise forms.ValidationError(
                f"Only {self.max_storage:.2f} GB remain in the organisation storage pool."
            )
        return value


class UserQuotaForm(forms.Form):
    monthly_credit_limit = forms.IntegerField(
        min_value=0,
        label="Monthly credits",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    max_total_storage_gb = forms.FloatField(
        min_value=0,
        label="Storage (GB)",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
    )
    max_saved_pdf_zips = forms.IntegerField(
        min_value=0,
        label="PDF context limit",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    max_saved_pyq_zips = forms.IntegerField(
        min_value=0,
        label="PYQ module limit",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    max_generation_runs_per_day = forms.IntegerField(
        min_value=0,
        label="Daily generation runs",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    max_concurrent_runs = forms.IntegerField(
        min_value=0,
        label="Concurrent runs",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    is_hard_limited = forms.BooleanField(
        required=False,
        label="Hard-limit credits (block when exceeded)",
    )
    is_active_member = forms.BooleanField(required=False, label="Account active")

    def __init__(
        self,
        *args,
        max_credits=None,
        max_storage=None,
        for_org_admin=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_credits = max_credits
        self.max_storage = max_storage
        self.for_org_admin = for_org_admin
        if for_org_admin:
            for name in (
                "monthly_credit_limit",
                "max_total_storage_gb",
                "is_hard_limited",
            ):
                self.fields.pop(name, None)
        else:
            if max_credits is not None:
                self.fields["monthly_credit_limit"].widget.attrs["max"] = max_credits
                self.fields["monthly_credit_limit"].help_text = (
                    f"Max from org credit pool (incl. this user's current share): {max_credits}."
                )
            if max_storage is not None:
                self.fields["max_total_storage_gb"].help_text = (
                    f"Max from org storage pool (incl. current share): {max_storage:.2f} GB "
                    "(files + embeddings)."
                )

    def clean_monthly_credit_limit(self):
        value = int(self.cleaned_data.get("monthly_credit_limit") or 0)
        if self.max_credits is not None and value > self.max_credits:
            raise forms.ValidationError(
                f"Only {self.max_credits} credits are available from the organisation pool."
            )
        return value

    def clean_max_total_storage_gb(self):
        value = float(self.cleaned_data.get("max_total_storage_gb") or 0)
        if self.max_storage is not None and value > self.max_storage + 1e-9:
            raise forms.ValidationError(
                f"Only {self.max_storage:.2f} GB remain in the organisation storage pool."
            )
        return value

    @classmethod
    def from_user(cls, user, data=None):
        from .provisioning import get_credit_quota, get_execution_quota, org_credit_budget
        from .storage import get_storage_quota, org_storage_budget

        credits = get_credit_quota(user)
        storage = get_storage_quota(user)
        execution = get_execution_quota(user)
        credit_budget = org_credit_budget(user.organization)
        storage_budget = org_storage_budget(user.organization)

        for_org_admin = user.role == User.Role.ORGUSER
        if user.role == User.Role.USER:
            max_credits = credit_budget["remaining"] + int(credits.monthly_credit_limit)
            max_storage = storage_budget["storage_remaining"] + float(
                storage.max_total_storage_gb
            ) + float(storage.max_vector_storage_gb)
        else:
            # Org Admins are outside org pools.
            max_credits = max_storage = None

        initial = {
            "max_saved_pdf_zips": storage.max_saved_pdf_zips,
            "max_saved_pyq_zips": storage.max_saved_pyq_zips,
            "max_generation_runs_per_day": execution.max_generation_runs_per_day,
            "max_concurrent_runs": execution.max_concurrent_runs,
            "is_active_member": user.is_active_member,
        }
        if not for_org_admin:
            initial.update(
                {
                    "monthly_credit_limit": credits.monthly_credit_limit,
                    "max_total_storage_gb": float(storage.max_total_storage_gb)
                    + float(storage.max_vector_storage_gb),
                    "is_hard_limited": credits.is_hard_limited,
                }
            )
        kwargs = {
            "initial": initial,
            "max_credits": max_credits,
            "max_storage": max_storage,
            "for_org_admin": for_org_admin,
        }
        return cls(data, **kwargs) if data is not None else cls(**kwargs)

    def save_to_user(self, user):
        from .provisioning import (
            assert_credits_fit_pool,
            get_credit_quota,
            get_execution_quota,
        )
        from .storage import (
            assert_storage_fits_pool,
            get_storage_quota,
        )

        cleaned = self.cleaned_data
        is_org_admin = user.role == User.Role.ORGUSER
        if user.role == User.Role.USER:
            assert_credits_fit_pool(
                user.organization,
                cleaned["monthly_credit_limit"],
                exclude_user=user,
            )
            assert_storage_fits_pool(
                user.organization,
                total_gb=cleaned["max_total_storage_gb"],
                exclude_user=user,
            )

        credits = get_credit_quota(user)
        credits.monthly_credit_limit = (
            0 if is_org_admin else cleaned["monthly_credit_limit"]
        )
        credits.is_hard_limited = (
            False if is_org_admin else cleaned.get("is_hard_limited", False)
        )
        credits.save(
            update_fields=["monthly_credit_limit", "is_hard_limited", "updated_at"]
        )

        storage = get_storage_quota(user)
        storage.max_total_storage_gb = (
            0 if is_org_admin else cleaned["max_total_storage_gb"]
        )
        storage.max_vector_storage_gb = 0
        storage.max_saved_pdf_zips = cleaned["max_saved_pdf_zips"]
        storage.max_saved_pyq_zips = cleaned["max_saved_pyq_zips"]
        storage.save(
            update_fields=[
                "max_total_storage_gb",
                "max_vector_storage_gb",
                "max_saved_pdf_zips",
                "max_saved_pyq_zips",
                "updated_at",
            ]
        )

        execution = get_execution_quota(user)
        execution.max_generation_runs_per_day = cleaned["max_generation_runs_per_day"]
        execution.max_concurrent_runs = cleaned["max_concurrent_runs"]
        execution.save(
            update_fields=["max_generation_runs_per_day", "max_concurrent_runs"]
        )

        user.is_active_member = cleaned["is_active_member"]
        user.save(update_fields=["is_active_member"])
