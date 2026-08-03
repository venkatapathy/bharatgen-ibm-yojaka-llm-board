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
            for model in llm_models:
                want = model.pk in selected
                if model.is_council_member != want:
                    model.is_council_member = want
                    model.save(update_fields=["is_council_member"])
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
        },
    )


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
