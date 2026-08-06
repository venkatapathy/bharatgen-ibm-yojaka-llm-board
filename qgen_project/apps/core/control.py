"""Control panel: Admin org quotas + Org Admin user management."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.generic import TemplateView

from .forms import (
    GenerationSettingsForm,
    OrganizationCreateForm,
    OrganizationPolicyForm,
    OrgUserCreateForm,
    PDFIndexingSettingsForm,
    UserQuotaForm,
)
from .models import (
    GenerationSettings,
    Organization,
    OrganizationProvisioningPolicy,
    PDFIndexingSettings,
    User,
)
from .permissions import OrgUserRequiredMixin, role_required
from .provisioning import (
    CreditPoolError,
    assert_credits_fit_pool,
    get_credit_quota,
    get_execution_quota,
    org_credit_budget,
)
from .storage import (
    StoragePoolError,
    assert_storage_fits_pool,
    get_storage_quota,
    org_storage_budget,
    storage_usage_summary,
)


class ControlHubView(OrgUserRequiredMixin, TemplateView):
    template_name = "core/control_hub.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["is_admin"] = user.role == User.Role.SUPERUSER
        ctx["is_org_admin"] = user.role == User.Role.ORGUSER
        if user.role == User.Role.SUPERUSER:
            ctx["org_count"] = Organization.objects.count()
            ctx["org_admin_count"] = User.objects.filter(role=User.Role.ORGUSER).count()
        elif user.organization_id:
            ctx["member_count"] = User.objects.filter(
                organization=user.organization
            ).count()
            ctx["budget"] = org_credit_budget(user.organization)
        return ctx


@role_required(User.Role.SUPERUSER)
def technical_settings(request):
    from .models import ModelConfig

    pdf_settings = PDFIndexingSettings.load()
    gen_settings = GenerationSettings.load()
    from apps.question_generation.council import ensure_council_models, COUNCIL_MODEL_SPECS
    from apps.core.model_lists import generation_model_queryset, generation_models_by_source

    ensure_council_models()
    generation_models = generation_model_queryset()
    ollama_models, api_models = generation_models_by_source()
    openai_key_set = bool((gen_settings.openai_api_key or "").strip())
    gemini_key_set = bool((gen_settings.gemini_api_key or "").strip())
    from apps.core.model_status import annotate_model_availability

    annotate_model_availability(
        list(ollama_models) + list(api_models),
        openai_key_set=openai_key_set,
        gemini_key_set=gemini_key_set,
    )
    current_mc = gen_settings.model_config
    model_source = "api"
    if current_mc is None or (current_mc.provider or "").lower() == "ollama":
        model_source = "ollama"
    import json

    api_provider_map_json = json.dumps(
        {str(m.id): (m.provider or "").lower() for m in api_models}
    )
    think_names = [spec["name"] for spec in COUNCIL_MODEL_SPECS]
    think_models = list(
        ModelConfig.objects.filter(name__in=think_names)
        .exclude(llm_model_id="")
        .order_by("name")
    )
    annotate_model_availability(
        think_models,
        openai_key_set=openai_key_set,
        gemini_key_set=gemini_key_set,
    )
    # Legacy template var: full list (unused by split sections).
    llm_models = ModelConfig.objects.exclude(llm_model_id="").order_by("name")

    if request.method == "POST":
        pdf_form = PDFIndexingSettingsForm(
            request.POST, instance=pdf_settings, prefix="pdf"
        )
        gen_form = GenerationSettingsForm(
            request.POST, instance=gen_settings, prefix="gen"
        )
        pdf_ok = pdf_form.is_valid()
        gen_ok = gen_form.is_valid()
        if pdf_ok:
            pdf_form.save()
        if gen_ok:
            gen_settings = gen_form.save()
            # Keep generation ModelConfig.reranker_model in sync for legacy paths.
            mc = gen_settings.model_config
            if mc is not None:
                want = (gen_settings.rag_reranker_model or "").strip()
                if (mc.reranker_model or "") != want:
                    mc.reranker_model = want
                    mc.save(update_fields=["reranker_model"])
            selected = {
                int(pk) for pk in request.POST.getlist("council_models") if pk.isdigit()
            }
            # Toggle Think roster only (snapshot before save); never touch generation models.
            # Skip unavailable (red) models — they cannot be selected.
            for model in think_models:
                want = model.pk in selected and bool(
                    getattr(model, "is_available", False)
                )
                if model.is_council_member != want:
                    model.is_council_member = want
                    model.save(update_fields=["is_council_member"])
            skipped_think = selected - {
                m.pk for m in think_models if getattr(m, "is_available", False)
            }
            if skipped_think:
                messages.warning(
                    request,
                    "Unavailable Think models were not saved. Only green models can be selected.",
                )
        if pdf_ok and gen_ok:
            messages.success(request, "Technical settings saved.")
            return redirect("core:control_technical")
        if pdf_ok and not gen_ok:
            messages.warning(
                request, "PDF indexing saved. Fix generation settings and save again."
            )
        elif gen_ok and not pdf_ok:
            messages.warning(
                request, "Generation settings saved. Fix PDF indexing and save again."
            )
        generation_models = generation_model_queryset()
        ollama_models, api_models = generation_models_by_source()
        openai_key_set = bool((gen_settings.openai_api_key or "").strip())
        gemini_key_set = bool((gen_settings.gemini_api_key or "").strip())
        annotate_model_availability(
            list(ollama_models) + list(api_models),
            openai_key_set=openai_key_set,
            gemini_key_set=gemini_key_set,
        )
        mc = None
        if gen_ok:
            mc = gen_settings.model_config
        else:
            raw = (request.POST.get(gen_form.add_prefix("model_config")) or "").strip()
            if raw.isdigit():
                mc = ModelConfig.objects.filter(pk=int(raw)).first()
        model_source = "api"
        if mc is None or (mc.provider or "").lower() == "ollama":
            model_source = "ollama"
        think_models = list(
            ModelConfig.objects.filter(name__in=think_names)
            .exclude(llm_model_id="")
            .order_by("name")
        )
        annotate_model_availability(
            think_models,
            openai_key_set=openai_key_set,
            gemini_key_set=gemini_key_set,
        )
    else:
        pdf_form = PDFIndexingSettingsForm(instance=pdf_settings, prefix="pdf")
        gen_form = GenerationSettingsForm(instance=gen_settings, prefix="gen")
    return render(
        request,
        "core/control_technical.html",
        {
            "form": pdf_form,
            "generation_form": gen_form,
            "settings": pdf_settings,
            "generation_settings": gen_settings,
            "llm_models": llm_models,
            "generation_models": generation_models,
            "ollama_models": ollama_models,
            "api_models": api_models,
            "api_provider_map_json": api_provider_map_json,
            "model_source": model_source,
            "think_models": think_models,
            "openai_key_set": bool((gen_settings.openai_api_key or "").strip()),
            "gemini_key_set": bool((gen_settings.gemini_api_key or "").strip()),
            "model_status_url": "core:control_model_status",
        },
    )


@role_required(User.Role.SUPERUSER)
def technical_model_status(request):
    """Live availability probe for Technical settings refresh button."""
    from django.http import JsonResponse
    from apps.core.model_status import model_status_snapshot

    return JsonResponse(model_status_snapshot())


@role_required(User.Role.SUPERUSER)
def org_list(request):
    orgs = Organization.objects.all().order_by("name")
    rows = []
    for org in orgs:
        budget = org_credit_budget(org)
        storage = org_storage_budget(org)
        rows.append(
            {
                "org": org,
                "policy": budget["policy"],
                "budget": budget,
                "storage_budget": storage,
                "admins": org.users.filter(role=User.Role.ORGUSER).count(),
                "members": org.users.filter(role=User.Role.USER).count(),
            }
        )
    return render(
        request,
        "core/control_org_list.html",
        {"rows": rows, "create_form": OrganizationCreateForm()},
    )


@role_required(User.Role.SUPERUSER)
def org_create(request):
    if request.method != "POST":
        return redirect("core:control_orgs")
    form = OrganizationCreateForm(request.POST)
    if form.is_valid():
        org = form.save(commit=False)
        if not org.slug:
            org.slug = slugify(org.name)[:50]
        org.save()
        OrganizationProvisioningPolicy.objects.get_or_create(organization=org)
        messages.success(request, f'Organization "{org.name}" created.')
        return redirect("core:control_org_policy", org_id=org.id)
    messages.error(request, "Could not create organization. Check name/username.")
    return redirect("core:control_orgs")


@role_required(User.Role.SUPERUSER)
def org_policy_edit(request, org_id):
    org = get_object_or_404(Organization, pk=org_id)
    policy, _ = OrganizationProvisioningPolicy.objects.get_or_create(organization=org)
    if request.method == "POST":
        form = OrganizationPolicyForm(request.POST, instance=policy)
        if form.is_valid():
            form.save()
            messages.success(request, f'Credit pool and defaults updated for "{org.name}".')
            return redirect("core:control_orgs")
    else:
        form = OrganizationPolicyForm(instance=policy)
    org_admins = org.users.filter(role=User.Role.ORGUSER).order_by("username")
    budget = org_credit_budget(org)
    storage_budget = org_storage_budget(org)
    return render(
        request,
        "core/control_org_policy.html",
        {
            "org": org,
            "form": form,
            "org_admins": org_admins,
            "budget": budget,
            "storage_budget": storage_budget,
        },
    )


def _managed_users_queryset(request):
    user = request.user
    if user.role == User.Role.SUPERUSER:
        org_id = request.GET.get("org") or request.POST.get("organization")
        qs = (
            User.objects.filter(role=User.Role.ORGUSER)
            .select_related("organization")
            .order_by("username")
        )
        if org_id:
            qs = qs.filter(organization_id=org_id)
        return qs
    if user.role == User.Role.ORGUSER and user.organization_id:
        return User.objects.filter(
            organization=user.organization,
            role=User.Role.USER,
        ).order_by("username")
    raise PermissionDenied


@role_required(User.Role.SUPERUSER, User.Role.ORGUSER)
def user_list(request):
    users = _managed_users_queryset(request)
    orgs = None
    selected_org = None
    is_admin = request.user.role == User.Role.SUPERUSER
    budget = None
    storage_budget = None
    if is_admin:
        orgs = Organization.objects.filter(is_active=True).order_by("name")
        selected_org = request.GET.get("org")
        if selected_org:
            org = Organization.objects.filter(pk=selected_org).first()
            if org:
                budget = org_credit_budget(org)
                storage_budget = org_storage_budget(org)
    elif request.user.organization_id:
        budget = org_credit_budget(request.user.organization)
        storage_budget = org_storage_budget(request.user.organization)

    rows = []
    for member in users:
        storage = get_storage_quota(member)
        credits = get_credit_quota(member)
        execution = get_execution_quota(member)
        rows.append(
            {
                "user": member,
                "credits": credits,
                "storage": storage,
                "storage_summary": storage_usage_summary(member),
                "execution": execution,
            }
        )
    return render(
        request,
        "core/control_user_list.html",
        {
            "rows": rows,
            "orgs": orgs,
            "selected_org": selected_org,
            "is_admin": is_admin,
            "budget": budget,
            "storage_budget": storage_budget,
            "list_title": "Org Admins" if is_admin else "Users",
            "create_label": "+ New Org Admin" if is_admin else "+ New user",
        },
    )


def _budget_for_create(request, create_org_admin):
    if create_org_admin:
        org_id = request.POST.get("organization") or request.GET.get("org")
        org = Organization.objects.filter(pk=org_id).first() if org_id else None
        if org is None:
            org = Organization.objects.filter(is_active=True).order_by("name").first()
    else:
        org = request.user.organization
    credit_budget = (
        org_credit_budget(org)
        if org
        else {"pool": 0, "allocated": 0, "remaining": 0, "policy": None}
    )
    storage_budget = (
        org_storage_budget(org)
        if org
        else {
            "storage_pool": 0,
            "storage_assigned": 0,
            "storage_remaining": 0,
            "policy": None,
        }
    )
    policy = credit_budget.get("policy") or storage_budget.get("policy")
    suggested_credits = int(getattr(policy, "default_monthly_credits", 0) or 0)
    suggested_storage = float(getattr(policy, "default_storage_limit_gb", 0) or 0)
    return org, credit_budget, storage_budget, suggested_credits, suggested_storage


@role_required(User.Role.SUPERUSER, User.Role.ORGUSER)
def user_create(request):
    create_org_admin = request.user.role == User.Role.SUPERUSER
    (
        org,
        credit_budget,
        storage_budget,
        suggested_credits,
        suggested_storage,
    ) = _budget_for_create(request, create_org_admin)

    form_kwargs = dict(
        create_org_admin=create_org_admin,
        max_credits=credit_budget["remaining"],
        suggested_credits=suggested_credits,
        max_storage=storage_budget["storage_remaining"],
        suggested_storage=suggested_storage,
    )

    if request.method == "POST":
        if create_org_admin:
            org = get_object_or_404(Organization, pk=request.POST.get("organization"))
            credit_budget = org_credit_budget(org)
            storage_budget = org_storage_budget(org)
            form_kwargs.update(
                max_credits=0,
                suggested_credits=0,
                max_storage=0,
                suggested_storage=0,
            )
        else:
            form_kwargs.update(
                max_credits=credit_budget["remaining"],
                max_storage=storage_budget["storage_remaining"],
            )

        form = OrgUserCreateForm(request.POST, **form_kwargs)
        if form.is_valid():
            credits_to_assign = int(form.cleaned_data.get("credits_to_assign") or 0)
            storage_gb = float(form.cleaned_data.get("storage_gb") or 0)
            errors = False
            if not create_org_admin:
                try:
                    assert_credits_fit_pool(org, credits_to_assign)
                except CreditPoolError as exc:
                    form.add_error("credits_to_assign", str(exc))
                    errors = True
                try:
                    assert_storage_fits_pool(org, total_gb=storage_gb)
                except StoragePoolError as exc:
                    form.add_error("storage_gb", str(exc))
                    errors = True
            if not errors:
                member = form.save(commit=False)
                member.organization = org
                member.role = User.Role.ORGUSER if create_org_admin else User.Role.USER
                member.is_active_member = True
                member.is_staff = False
                member.is_superuser = False
                # Legacy NOT NULL column: keep the chosen login password for Control notes.
                member.control_password = form.cleaned_data.get("password1") or ""
                member.save()
                storage_quota = get_storage_quota(member)
                storage_quota.max_total_storage_gb = 0 if create_org_admin else storage_gb
                storage_quota.max_vector_storage_gb = 0
                storage_quota.save(
                    update_fields=[
                        "max_total_storage_gb",
                        "max_vector_storage_gb",
                        "updated_at",
                    ]
                )
                get_execution_quota(member)
                credit_quota = get_credit_quota(member)
                credit_quota.monthly_credit_limit = 0 if create_org_admin else credits_to_assign
                credit_quota.save(update_fields=["monthly_credit_limit", "updated_at"])
                label = "Org Admin" if create_org_admin else "User"
                if create_org_admin:
                    messages.success(
                        request,
                        f'{label} "{member.username}" created '
                        "(uses unallocated org credit/storage; no reserved personal share).",
                    )
                else:
                    messages.success(
                        request,
                        f'{label} "{member.username}" created with {credits_to_assign} credits, '
                        f"{storage_gb:g} GB storage.",
                    )
                return redirect("core:control_users")
    else:
        form = OrgUserCreateForm(**form_kwargs)

    orgs = None
    if create_org_admin:
        orgs = Organization.objects.filter(is_active=True).order_by("name")
    return render(
        request,
        "core/control_user_create.html",
        {
            "form": form,
            "orgs": orgs,
            "create_org_admin": create_org_admin,
            "page_title": "Create Org Admin" if create_org_admin else "Create user",
            "budget": credit_budget,
            "storage_budget": storage_budget,
            "selected_org_id": getattr(org, "id", None),
        },
    )


def _can_manage_user(actor, target):
    if actor.role == User.Role.SUPERUSER:
        return target.role == User.Role.ORGUSER
    if actor.role == User.Role.ORGUSER:
        return (
            target.role == User.Role.USER
            and actor.organization_id
            and target.organization_id == actor.organization_id
        )
    return False


@role_required(User.Role.SUPERUSER, User.Role.ORGUSER)
def user_quota_edit(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    if not _can_manage_user(request.user, target):
        raise PermissionDenied

    budget = org_credit_budget(target.organization)
    storage_budget = org_storage_budget(target.organization)
    if request.method == "POST":
        form = UserQuotaForm.from_user(target, data=request.POST)
        if form.is_valid():
            try:
                form.save_to_user(target)
            except CreditPoolError as exc:
                form.add_error("monthly_credit_limit", str(exc))
            except StoragePoolError as exc:
                form.add_error("max_total_storage_gb", str(exc))
            else:
                messages.success(request, f'Quotas updated for "{target.username}".')
                return redirect("core:control_users")
    else:
        form = UserQuotaForm.from_user(target)

    return render(
        request,
        "core/control_user_quota.html",
        {
            "target": target,
            "form": form,
            "storage": get_storage_quota(target),
            "storage_summary": storage_usage_summary(target),
            "credits": get_credit_quota(target),
            "execution": get_execution_quota(target),
            "budget": budget,
            "storage_budget": storage_budget,
        },
    )


@role_required(User.Role.SUPERUSER, User.Role.ORGUSER)
def statistics_dashboard(request):
    """Per-user generated-question review stats (Admin + Org Admin)."""
    import re
    from collections import defaultdict

    from django.db.models import Count, Exists, OuterRef, Q

    from apps.pdf_module.models import PDFContext
    from apps.pyq_module.models import BloomLevel, Question, QuestionType
    from apps.question_generation.models import BatchRun

    viewer = request.user
    is_admin = viewer.role == User.Role.SUPERUSER

    if is_admin:
        # Admin sees own stats + org admins + regular users.
        users = (
            User.objects.filter(is_active=True)
            .select_related("organization")
            .order_by("username")
        )
        questions = Question.objects.filter(
            is_generated=True,
            batch_run__isnull=False,
            batch_run__created_by__isnull=False,
        )
        pdfs = PDFContext.objects.all()
    else:
        if not viewer.organization_id:
            raise PermissionDenied
        users = (
            User.objects.filter(
                organization_id=viewer.organization_id,
                is_active=True,
            )
            .select_related("organization")
            .order_by("username")
        )
        questions = Question.objects.filter(
            is_generated=True,
            batch_run__isnull=False,
            batch_run__created_by__organization_id=viewer.organization_id,
        )
        pdfs = PDFContext.objects.filter(organization_id=viewer.organization_id)

    aggregates = {
        row["batch_run__created_by_id"]: row
        for row in questions.values("batch_run__created_by_id").annotate(
            generated=Count("id"),
            approved=Count(
                "id", filter=Q(user_decision=Question.UserDecision.APPROVED)
            ),
            rejected=Count(
                "id", filter=Q(user_decision=Question.UserDecision.REJECTED)
            ),
            pending=Count(
                "id",
                filter=Q(user_decision=Question.UserDecision.PENDING)
                | Q(user_decision="")
                | Q(user_decision__isnull=True),
            ),
        )
    }

    role_rank = {
        User.Role.SUPERUSER: 0,
        User.Role.ORGUSER: 1,
        User.Role.USER: 2,
    }

    rows = []
    totals = {"generated": 0, "approved": 0, "rejected": 0, "pending": 0}
    for user in users:
        stats = aggregates.get(user.pk) or {}
        generated = int(stats.get("generated") or 0)
        approved = int(stats.get("approved") or 0)
        rejected = int(stats.get("rejected") or 0)
        pending = int(stats.get("pending") or 0)
        # Prefer residual so odd/legacy decision values still count as unverified.
        residual = generated - approved - rejected
        pending = max(pending, residual, 0)
        reviewed = approved + rejected
        approval_rate = int(round((approved / generated) * 100)) if generated else 0
        reviewed_rate = int(round((reviewed / generated) * 100)) if generated else 0
        # Stacked bar segments (percent of generated).
        pct_approved = int(round((approved / generated) * 100)) if generated else 0
        pct_rejected = int(round((rejected / generated) * 100)) if generated else 0
        pct_pending = max(0, 100 - pct_approved - pct_rejected)
        initials = (user.username or "?")[:2].upper()
        row = {
            "user": user,
            "initials": initials,
            "generated": generated,
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
            "approval_rate": approval_rate,
            "reviewed_rate": reviewed_rate,
            "pct_approved": pct_approved,
            "pct_rejected": pct_rejected,
            "pct_pending": pct_pending,
        }
        rows.append(row)
        totals["generated"] += generated
        totals["approved"] += approved
        totals["rejected"] += rejected
        totals["pending"] += pending

    # Admins / org admins first, then by activity.
    rows.sort(
        key=lambda r: (
            role_rank.get(r["user"].role, 9),
            -r["generated"],
            r["user"].username.lower(),
        )
    )

    total_gen = totals["generated"] or 0
    totals["approval_rate"] = (
        int(round((totals["approved"] / total_gen) * 100)) if total_gen else 0
    )
    totals["rejection_rate"] = (
        int(round((totals["rejected"] / total_gen) * 100)) if total_gen else 0
    )
    totals["pct_pending"] = (
        int(round((totals["pending"] / total_gen) * 100)) if total_gen else 0
    )
    # Keep bar segments summing near 100.
    if total_gen:
        totals["pct_pending"] = max(
            0,
            100 - totals["approval_rate"] - totals["rejection_rate"],
        )
    totals["reviewed_rate"] = (
        int(
            round(
                ((totals["approved"] + totals["rejected"]) / total_gen) * 100
            )
        )
        if total_gen
        else 0
    )
    active_users = sum(1 for r in rows if r["generated"] > 0)

    # --- Content mix (type / bloom / marks / language) ---
    def _mix_rows(field, *, label_map=None, format_key=None):
        out = []
        for item in (
            questions.values(field)
            .annotate(count=Count("id"))
            .order_by("-count", field)
        ):
            raw = item[field]
            key = format_key(raw) if format_key else (raw if raw not in (None, "") else "—")
            count = int(item["count"] or 0)
            out.append(
                {
                    "key": key,
                    "label": (label_map or {}).get(raw, key) if raw not in (None, "") else "—",
                    "count": count,
                    "pct": int(round((count / total_gen) * 100)) if total_gen else 0,
                }
            )
        return out

    def _fmt_marks(value):
        if value is None:
            return "—"
        try:
            num = float(value)
        except (TypeError, ValueError):
            return str(value)
        if num.is_integer():
            return str(int(num))
        return f"{num:g}"

    type_labels = {c.value: c.label for c in QuestionType}
    bloom_labels = {c.value: c.label for c in BloomLevel}
    lang_labels = {c.value: c.label for c in BatchRun.Language}

    content_mix = {
        "by_type": _mix_rows("question_type", label_map=type_labels),
        "by_bloom": _mix_rows("bloom", label_map=bloom_labels),
        "by_marks": _mix_rows(
            "marks",
            format_key=_fmt_marks,
            label_map=None,
        ),
        "by_language": _mix_rows(
            "batch_run__language",
            label_map=lang_labels,
        ),
    }
    # Marks rows: label = formatted marks + " marks"
    for item in content_mix["by_marks"]:
        if item["key"] != "—":
            item["label"] = f"{item['key']} marks"

    # --- PDF / unit coverage ---
    course_re = re.compile(r"\b([A-Z]{2,6}[-\s]?\d{2,4})\b", re.IGNORECASE)

    def _course_code(name: str) -> str:
        m = course_re.search(name or "")
        if not m:
            return "Other"
        return re.sub(r"\s+", "-", m.group(1).upper().replace(" ", "-"))

    has_generated = Exists(
        Question.objects.filter(
            is_generated=True,
            batch_run__pdf_contexts=OuterRef("pk"),
        )
    )
    pdf_base = pdfs.annotate(has_generated=has_generated)
    ocr_ready_qs = pdf_base.filter(status="ready").exclude(ocr_text="")
    ocr_ready = ocr_ready_qs.count()
    with_generation = pdf_base.filter(has_generated=True).count()
    never_generated = ocr_ready_qs.filter(has_generated=False).count()
    indexing = pdf_base.exclude(status="ready").count()
    total_units = pdf_base.count()

    # Per-PDF approved / generated counts (one PDF per run in practice).
    pdf_q_stats = {
        row["batch_run__pdf_contexts"]: row
        for row in questions.values("batch_run__pdf_contexts")
        .annotate(
            generated=Count("id"),
            approved=Count(
                "id", filter=Q(user_decision=Question.UserDecision.APPROVED)
            ),
        )
        if row["batch_run__pdf_contexts"]
    }

    course_bucket = defaultdict(
        lambda: {
            "units": 0,
            "ocr_ready": 0,
            "with_generation": 0,
            "never_generated": 0,
            "generated": 0,
            "approved": 0,
        }
    )
    for pdf in pdf_base.only("id", "name", "status", "ocr_text"):
        code = _course_code(pdf.name)
        bucket = course_bucket[code]
        bucket["units"] += 1
        ready = pdf.status == "ready" and bool((pdf.ocr_text or "").strip())
        stats = pdf_q_stats.get(pdf.pk) or {}
        generated = int(stats.get("generated") or 0)
        approved = int(stats.get("approved") or 0)
        if ready:
            bucket["ocr_ready"] += 1
        if generated:
            bucket["with_generation"] += 1
        elif ready:
            bucket["never_generated"] += 1
        bucket["generated"] += generated
        bucket["approved"] += approved

    course_rows = []
    for code, bucket in course_bucket.items():
        gen = bucket["generated"]
        approved = bucket["approved"]
        course_rows.append(
            {
                "course": code,
                "units": bucket["units"],
                "ocr_ready": bucket["ocr_ready"],
                "with_generation": bucket["with_generation"],
                "never_generated": bucket["never_generated"],
                "generated": gen,
                "approved": approved,
                "approval_rate": (
                    int(round((approved / gen) * 100)) if gen else 0
                ),
                "coverage_pct": (
                    int(
                        round(
                            (bucket["with_generation"] / bucket["ocr_ready"])
                            * 100
                        )
                    )
                    if bucket["ocr_ready"]
                    else 0
                ),
            }
        )
    course_rows.sort(key=lambda r: (-r["approved"], -r["generated"], r["course"]))

    coverage = {
        "total_units": total_units,
        "ocr_ready": ocr_ready,
        "with_generation": with_generation,
        "never_generated": never_generated,
        "indexing": indexing,
        "coverage_pct": (
            int(round((with_generation / ocr_ready) * 100)) if ocr_ready else 0
        ),
        "courses": course_rows,
        "top_courses": course_rows[:5],
        "thin_courses": sorted(
            [c for c in course_rows if c["ocr_ready"]],
            key=lambda r: (r["approved"], r["with_generation"], r["course"]),
        )[:5],
    }

    return render(
        request,
        "core/statistics.html",
        {
            "rows": rows,
            "totals": totals,
            "content_mix": content_mix,
            "coverage": coverage,
            "is_admin": is_admin,
            "org_name": (
                None
                if is_admin
                else (viewer.organization.name if viewer.organization_id else "")
            ),
            "user_count": len(rows),
            "active_users": active_users,
        },
    )
