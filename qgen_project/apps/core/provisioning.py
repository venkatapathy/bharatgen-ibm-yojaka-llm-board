"""Provisioning, credits, and execution quota helpers."""

from contextlib import contextmanager

from django.db import transaction
from django.db.models import F, Sum

from .limits import is_unlimited_user
from .models import (
    ExecutionQuota,
    OrganizationProvisioningPolicy,
    TokenUsageLog,
    User,
    UserProvisioningQuota,
)


class ProvisioningError(Exception):
    """Raised when a run exceeds credits or execution limits."""


class CreditPoolError(Exception):
    """Raised when org credit distribution would exceed the organisation pool."""


def get_org_policy(organization):
    if organization is None:
        return None
    policy, _ = OrganizationProvisioningPolicy.objects.get_or_create(organization=organization)
    return policy


def _sum_credits(organization, *, roles=None, exclude_user=None) -> int:
    if organization is None:
        return 0
    qs = UserProvisioningQuota.objects.filter(user__organization=organization)
    if roles is not None:
        qs = qs.filter(user__role__in=roles)
    else:
        qs = qs.exclude(user__role=User.Role.SUPERUSER)
    if exclude_user is not None:
        qs = qs.exclude(user_id=exclude_user.pk)
    return int(qs.aggregate(total=Sum("monthly_credit_limit"))["total"] or 0)


def org_credits_allocated(organization, *, exclude_user=None) -> int:
    """Credits assigned to regular Users only (org owns the pool, not org admins)."""
    return _sum_credits(organization, roles=[User.Role.USER], exclude_user=exclude_user)


def org_credit_budget(organization):
    """Return org credit pool / assigned-to-users / remaining."""
    policy = get_org_policy(organization)
    pool = int(policy.credit_pool) if policy else 0
    allocated = org_credits_allocated(organization)
    remaining = max(pool - allocated, 0)
    admin_used = org_admin_credits_used(organization)
    return {
        "pool": pool,
        "allocated": allocated,
        "allocated_users": allocated,
        "allocated_org_admins": 0,  # org admins draw from unallocated, not reserved shares
        "remaining": remaining,
        "org_admin_used": admin_used,
        "org_admin_available": max(remaining - admin_used, 0),
        "policy": policy,
    }


def org_admin_credits_used(organization, *, exclude_user=None) -> int:
    """Credits consumed this month by Org Admins (drawn from unallocated pool)."""
    if organization is None:
        return 0
    qs = UserProvisioningQuota.objects.filter(
        user__organization=organization,
        user__role=User.Role.ORGUSER,
    )
    if exclude_user is not None:
        qs = qs.exclude(user_id=exclude_user.pk)
    return int(qs.aggregate(total=Sum("current_month_credits_used"))["total"] or 0)


def effective_credit_limit(user) -> int:
    """Personal limit for Users; unallocated org pool for Org Admins."""
    if user is None:
        return 0
    if is_unlimited_user(user):
        return 10**12
    if user.role == User.Role.ORGUSER and user.organization_id:
        return int(org_credit_budget(user.organization)["remaining"])
    return int(get_credit_quota(user).monthly_credit_limit)


def effective_credits_used(user) -> int:
    """Usage counted against the effective limit."""
    if user is None:
        return 0
    if user.role == User.Role.ORGUSER and user.organization_id:
        return org_admin_credits_used(user.organization)
    return int(get_credit_quota(user).current_month_credits_used)


def credit_quota_display(user):
    """Credit numbers for Generate UI cards."""
    unlimited = is_unlimited_user(user)
    used = 0 if unlimited else effective_credits_used(user)
    limit = 0 if unlimited else effective_credit_limit(user)
    pct = 0
    if not unlimited and limit > 0:
        pct = int(min(100, max(0, round((used / limit) * 100))))
    remaining = 10**12 if unlimited else max(int(limit) - int(used), 0)
    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "pct": pct,
        "unlimited": unlimited,
        "uses_unallocated_pool": bool(
            user and user.role == User.Role.ORGUSER and user.organization_id and not unlimited
        ),
    }


def batch_run_credits_used(batch_run) -> int:
    """Credits logged against a specific generation run (generation + council)."""
    if batch_run is None:
        return 0
    return int(
        TokenUsageLog.objects.filter(batch_run=batch_run).aggregate(
            total=Sum("credits_consumed")
        )["total"]
        or 0
    )


def assert_credits_fit_pool(organization, new_limit, *, exclude_user=None):
    """Ensure assigning new_limit credits to a User still fits inside the org pool."""
    if organization is None:
        return
    if exclude_user is not None and exclude_user.role != User.Role.USER:
        return
    policy = get_org_policy(organization)
    pool = int(policy.credit_pool) if policy else 0
    allocated = org_credits_allocated(organization, exclude_user=exclude_user)
    if allocated + int(new_limit) > pool:
        remaining = max(pool - allocated, 0)
        raise CreditPoolError(
            f"Only {remaining} credits left in the organisation pool "
            f"({allocated} already assigned to users of {pool})."
        )
    # Keep enough unallocated headroom for Org Admin usage already spent this month.
    admin_used = org_admin_credits_used(organization)
    if pool - (allocated + int(new_limit)) < admin_used:
        raise CreditPoolError(
            f"Cannot allocate that many credits: Org Admins have already used "
            f"{admin_used} from the unallocated pool this month."
        )


def _quota_defaults(user):
    credits = 0
    if user.organization_id and user.role == User.Role.USER:
        policy = OrganizationProvisioningPolicy.objects.filter(
            organization_id=user.organization_id
        ).first()
        if policy:
            budget = org_credit_budget(user.organization)
            credits = min(int(policy.default_monthly_credits), budget["remaining"])
    return {"monthly_credit_limit": credits}


def _execution_defaults(user):
    defaults = {"max_concurrent_runs": 2, "max_generation_runs_per_day": 20}
    if user.organization_id:
        policy = OrganizationProvisioningPolicy.objects.filter(organization_id=user.organization_id).first()
        if policy:
            defaults["max_concurrent_runs"] = policy.default_concurrent_run_limit
            defaults["max_generation_runs_per_day"] = policy.default_daily_run_limit
    return defaults


def get_credit_quota(user):
    return UserProvisioningQuota.objects.get_or_create(user=user, defaults=_quota_defaults(user))[0]


def get_execution_quota(user):
    return ExecutionQuota.objects.get_or_create(user=user, defaults=_execution_defaults(user))[0]


# ── Fixed credit tariff (generation only; PYQ extraction is free) ───────────
# Demo-friendly rates so a typical 5-question run fits a 6k user quota.
CREDITS_PER_QUESTION = 200
CREDITS_RAG_PER_QUESTION = 50
CREDITS_PYQ_PER_QUESTION = 25
CREDITS_THINK_PER_QUESTION = 100


def rule_credits_per_question(
    *,
    has_rag: bool = False,
    has_pyq: bool = False,
    think_enabled: bool = False,
) -> int:
    cost = CREDITS_PER_QUESTION
    if has_rag:
        cost += CREDITS_RAG_PER_QUESTION
    if has_pyq:
        cost += CREDITS_PYQ_PER_QUESTION
    if think_enabled:
        cost += CREDITS_THINK_PER_QUESTION
    return cost


def estimate_batch_run_credits(
    batch_run=None,
    *,
    question_count=None,
    has_rag=None,
    has_pyq=None,
    think_enabled=None,
) -> int:
    """Predictable credits for a generation run from fixed rules."""
    if batch_run is not None:
        if question_count is None:
            question_count = int(
                getattr(batch_run, "expected_questions", 0)
                or sum(item.count for item in batch_run.items.all())
            )
        if has_rag is None:
            has_rag = batch_run.pdf_contexts.exists()
        if has_pyq is None:
            has_pyq = batch_run.pyq_modules.exists()
        if think_enabled is None:
            think_enabled = bool(batch_run.council_enabled)
    n = max(int(question_count or 0), 0)
    if n <= 0:
        return 0
    return n * rule_credits_per_question(
        has_rag=bool(has_rag),
        has_pyq=bool(has_pyq),
        think_enabled=bool(think_enabled),
    )


def estimate_credit_cost(*, prompt_tokens=0, completion_tokens=0, embedding_tokens=0):
    """Legacy token formula — kept for logs; generation charging uses rules instead."""
    return int(prompt_tokens + completion_tokens + (embedding_tokens * 0.5))


@transaction.atomic
def ensure_credit_headroom(user, estimated_cost=0):
    if is_unlimited_user(user):
        return get_credit_quota(user)
    quota = UserProvisioningQuota.objects.select_for_update().get(pk=get_credit_quota(user).pk)
    if user.role == User.Role.ORGUSER and user.organization_id:
        # Lock all org-admin quota rows so concurrent admins share one unallocated pool.
        list(
            UserProvisioningQuota.objects.select_for_update().filter(
                user__organization_id=user.organization_id,
                user__role=User.Role.ORGUSER,
            )
        )
        budget = org_credit_budget(user.organization)
        unallocated = int(budget["remaining"])
        admin_used = org_admin_credits_used(user.organization)
        if admin_used + int(estimated_cost) > unallocated:
            raise ProvisioningError(
                "Organisation unallocated credit pool exhausted "
                f"({admin_used} used of {unallocated} unallocated)."
            )
        return quota
    if quota.current_month_credits_used + estimated_cost > quota.monthly_credit_limit:
        raise ProvisioningError("Monthly credit limit exceeded for this user.")
    return quota


@contextmanager
def execution_slot(user):
    if is_unlimited_user(user):
        yield
        return
    quota = get_execution_quota(user)
    with transaction.atomic():
        locked = ExecutionQuota.objects.select_for_update().get(pk=quota.pk)
        if locked.current_active_runs >= locked.max_concurrent_runs:
            raise ProvisioningError("Concurrent generation limit reached.")
        if locked.today_generation_runs >= locked.max_generation_runs_per_day:
            raise ProvisioningError("Daily generation limit reached.")
        locked.current_active_runs += 1
        locked.today_generation_runs += 1
        locked.save(update_fields=["current_active_runs", "today_generation_runs"])
    try:
        yield
    finally:
        ExecutionQuota.objects.filter(pk=quota.pk).update(
            current_active_runs=F("current_active_runs") - 1,
        )


@transaction.atomic
def charge_rule_credits(
    *,
    user,
    credits,
    batch_run=None,
    provider="",
    model_name="",
    request_kind="generation",
    metadata=None,
):
    """Debit a fixed rule-based credit amount (ignores raw token counts)."""
    if user is None:
        return None
    credits = max(int(credits or 0), 0)
    if credits <= 0:
        return None
    if not is_unlimited_user(user):
        ensure_credit_headroom(user, credits)
        quota = UserProvisioningQuota.objects.select_for_update().get(pk=get_credit_quota(user).pk)
        quota.current_month_credits_used += credits
        quota.save(update_fields=["current_month_credits_used", "updated_at"])
    return TokenUsageLog.objects.create(
        user=user,
        batch_run=batch_run,
        provider=provider,
        model_name=model_name,
        request_kind=request_kind,
        prompt_tokens=0,
        completion_tokens=0,
        embedding_tokens=0,
        total_tokens=0,
        credits_consumed=credits,
        metadata={**(metadata or {}), "billing": "rule"},
    )


@transaction.atomic
def record_token_usage(
    *,
    user,
    batch_run=None,
    provider="",
    model_name="",
    request_kind="generation",
    prompt_tokens=0,
    completion_tokens=0,
    embedding_tokens=0,
    metadata=None,
    charge_credits=True,
):
    """Log token usage. Generation should prefer charge_rule_credits instead."""
    if user is None:
        return None
    total_tokens = prompt_tokens + completion_tokens + embedding_tokens
    credits = (
        estimate_credit_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            embedding_tokens=embedding_tokens,
        )
        if charge_credits
        else 0
    )
    if charge_credits and credits and not is_unlimited_user(user):
        ensure_credit_headroom(user, credits)
        quota = UserProvisioningQuota.objects.select_for_update().get(pk=get_credit_quota(user).pk)
        quota.current_month_credits_used += credits
        quota.save(update_fields=["current_month_credits_used", "updated_at"])
    return TokenUsageLog.objects.create(
        user=user,
        batch_run=batch_run,
        provider=provider,
        model_name=model_name,
        request_kind=request_kind,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        embedding_tokens=embedding_tokens,
        total_tokens=total_tokens,
        credits_consumed=credits,
        metadata={**(metadata or {}), "billing": "tokens" if charge_credits else "log_only"},
    )
