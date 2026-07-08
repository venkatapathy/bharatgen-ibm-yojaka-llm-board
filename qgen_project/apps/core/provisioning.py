"""Provisioning, credits, and execution quota helpers."""

from contextlib import contextmanager

from django.db import transaction
from django.db.models import F

from .models import ExecutionQuota, OrganizationProvisioningPolicy, TokenUsageLog, UserProvisioningQuota


class ProvisioningError(Exception):
    """Raised when a run exceeds credits or execution limits."""


def _quota_defaults(user):
    credits = 100_000
    if user.organization_id:
        policy = OrganizationProvisioningPolicy.objects.filter(organization_id=user.organization_id).first()
        if policy:
            credits = policy.default_monthly_credits
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


def estimate_credit_cost(*, prompt_tokens=0, completion_tokens=0, embedding_tokens=0):
    return int(prompt_tokens + completion_tokens + (embedding_tokens * 0.5))


@transaction.atomic
def ensure_credit_headroom(user, estimated_cost=0):
    quota = UserProvisioningQuota.objects.select_for_update().get(pk=get_credit_quota(user).pk)
    if quota.current_month_credits_used + estimated_cost > quota.monthly_credit_limit:
        raise ProvisioningError("Monthly credit limit exceeded for this user.")
    return quota


@contextmanager
def execution_slot(user):
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
):
    if user is None:
        return None
    total_tokens = prompt_tokens + completion_tokens + embedding_tokens
    credits = estimate_credit_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        embedding_tokens=embedding_tokens,
    )
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
        metadata=metadata or {},
    )
