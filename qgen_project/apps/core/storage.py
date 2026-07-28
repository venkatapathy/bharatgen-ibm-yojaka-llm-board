"""Storage quota helpers for upload governance."""

from django.db import transaction
from django.db.models import F, Sum

from .limits import is_unlimited_user
from .models import OrganizationProvisioningPolicy, OrganizationSettings, StorageQuota, User

# Matches PDFChunk / sentence-transformers all-mpnet-base-v2 embeddings.
EMBED_DIMENSIONS = 768
BYTES_PER_FLOAT32 = 4


class StorageQuotaExceeded(Exception):
    """Raised when an upload would exceed the user's storage limits."""


class StoragePoolError(Exception):
    """Raised when user storage assignment would exceed the organisation pool."""


def _policy_defaults(user):
    defaults = {
        "max_total_storage_gb": 0.0,
        "max_vector_storage_gb": 0.0,
        "max_saved_pdf_zips": 100,
        "max_saved_pyq_zips": 50,
    }
    if not user.organization_id:
        return defaults
    policy = OrganizationProvisioningPolicy.objects.filter(
        organization_id=user.organization_id
    ).first()
    if not policy:
        return defaults
    # Zip caps apply to Users and Org Admins; GB shares only to Users.
    defaults["max_saved_pdf_zips"] = policy.default_pdf_zip_limit
    defaults["max_saved_pyq_zips"] = policy.default_pyq_zip_limit
    if user.role == User.Role.USER:
        budget = org_storage_budget(user.organization)
        defaults["max_total_storage_gb"] = min(
            float(policy.default_storage_limit_gb),
            budget["storage_remaining"],
        )
    return defaults


def get_storage_quota(user):
    quota, _ = StorageQuota.objects.get_or_create(user=user, defaults=_policy_defaults(user))
    return quota


def _sum_user_storage(organization, field: str, *, exclude_user=None) -> float:
    if organization is None:
        return 0.0
    qs = StorageQuota.objects.filter(
        user__organization=organization,
        user__role=User.Role.USER,
    )
    if exclude_user is not None:
        qs = qs.exclude(user_id=exclude_user.pk)
    return float(qs.aggregate(total=Sum(field))["total"] or 0.0)


def _sum_org_admin_usage(organization, field: str) -> float:
    if organization is None:
        return 0.0
    return float(
        StorageQuota.objects.filter(
            user__organization=organization,
            user__role=User.Role.ORGUSER,
        ).aggregate(total=Sum(field))["total"]
        or 0.0
    )


def _user_storage_assigned(organization, *, exclude_user=None) -> float:
    """Combined file + embedding quota assigned to users."""
    total = _sum_user_storage(
        organization, "max_total_storage_gb", exclude_user=exclude_user
    )
    vector = _sum_user_storage(
        organization, "max_vector_storage_gb", exclude_user=exclude_user
    )
    return total + vector


def org_admin_storage_used(organization) -> float:
    """File + embedding storage used by Org Admins (from unallocated pool)."""
    files = _sum_org_admin_usage(organization, "current_total_storage_gb")
    vectors = _sum_org_admin_usage(organization, "current_vector_storage_gb")
    return files + vectors


def org_storage_budget(organization):
    """Organisation storage pool and how much is assigned to users."""
    from .provisioning import get_org_policy

    policy = get_org_policy(organization) if organization else None
    storage_pool = float(policy.storage_pool_gb) if policy else 0.0
    if policy:
        storage_pool += float(policy.vector_storage_pool_gb or 0)
    storage_assigned = _user_storage_assigned(organization)
    storage_remaining = max(storage_pool - storage_assigned, 0.0)
    admin_used = org_admin_storage_used(organization)
    return {
        "storage_pool": storage_pool,
        "storage_assigned": storage_assigned,
        "storage_remaining": storage_remaining,
        "org_admin_storage_used": admin_used,
        "org_admin_storage_available": max(storage_remaining - admin_used, 0.0),
        "policy": policy,
    }


def assert_storage_fits_pool(organization, *, total_gb, exclude_user=None):
    if organization is None:
        return
    if exclude_user is not None and exclude_user.role != User.Role.USER:
        return
    budget_base = org_storage_budget(organization)
    assigned_others = _user_storage_assigned(organization, exclude_user=exclude_user)
    if assigned_others + float(total_gb) > budget_base["storage_pool"] + 1e-9:
        remaining = max(budget_base["storage_pool"] - assigned_others, 0.0)
        raise StoragePoolError(
            f"Only {remaining:.2f} GB storage left in the organisation pool."
        )
    after_assign = budget_base["storage_pool"] - (assigned_others + float(total_gb))
    if after_assign + 1e-9 < budget_base["org_admin_storage_used"]:
        raise StoragePoolError(
            "Cannot allocate that much storage: Org Admins are already using "
            f"{budget_base['org_admin_storage_used']:.2f} GB from the unallocated pool."
        )


def effective_storage_limit_gb(user) -> float:
    """Personal limit for Users; unallocated org pool for Org Admins."""
    if user is None:
        return 0.0
    if is_unlimited_user(user):
        return 10**6
    if user.role == User.Role.ORGUSER and user.organization_id:
        return float(org_storage_budget(user.organization)["storage_remaining"])
    quota = get_storage_quota(user)
    return float(quota.max_total_storage_gb) + float(quota.max_vector_storage_gb)


def effective_storage_used_gb(user) -> float:
    if user is None:
        return 0.0
    if user.role == User.Role.ORGUSER and user.organization_id:
        return org_admin_storage_used(user.organization)
    quota = get_storage_quota(user)
    return float(quota.current_total_storage_gb) + float(quota.current_vector_storage_gb)


def _format_storage_amount(gb: float) -> str:
    """Human-readable size for quota cards (avoid 0.00 GB for small PDFs)."""
    gb = float(gb or 0)
    if gb <= 0:
        return "0 MB"
    mb = gb * 1024
    if mb < 1024:
        if mb < 0.01:
            return f"{mb * 1024:.0f} KB"
        return f"{mb:.2f} MB"
    return f"{gb:.2f} GB"


def _usage_pct(used, limit):
    """Ring fill (0–100) and center label; keep a visible arc when usage is non-zero."""
    if not limit or float(limit) <= 0:
        return 0, "0%"
    used_f = float(used or 0)
    limit_f = float(limit)
    if used_f <= 0:
        return 0, "0%"
    pct_exact = (used_f / limit_f) * 100
    if pct_exact < 1:
        return 1, "<1%"
    rounded = int(min(100, round(pct_exact)))
    return rounded, f"{rounded}%"


def storage_usage_summary(user):
    """Combined file + embedding usage for Control tables and quota headers."""
    used_gb = effective_storage_used_gb(user)
    max_gb = effective_storage_limit_gb(user)
    pct, pct_label = _usage_pct(used_gb, max_gb)
    return {
        "used_gb": used_gb,
        "max_gb": max_gb,
        "used_label": _format_storage_amount(used_gb),
        "max_label": _format_storage_amount(max_gb),
        "pct": pct,
        "pct_label": pct_label,
    }


def storage_quota_display(user):
    """Quota numbers for UI cards (org admins see unallocated pool usage)."""
    quota = get_storage_quota(user)
    used_gb = effective_storage_used_gb(user)
    max_gb = effective_storage_limit_gb(user)
    pdf_used = int(quota.current_saved_pdf_zips)
    pdf_max = int(quota.max_saved_pdf_zips) or 0
    pyq_used = int(quota.current_saved_pyq_zips)
    pyq_max = int(quota.max_saved_pyq_zips) or 0

    storage_pct, storage_pct_label = _usage_pct(used_gb, max_gb)
    pdf_pct, pdf_pct_label = _usage_pct(pdf_used, pdf_max)
    pyq_pct, pyq_pct_label = _usage_pct(pyq_used, pyq_max)

    return {
        "current_total_storage_gb": used_gb,
        "max_total_storage_gb": max_gb,
        "storage_used_label": _format_storage_amount(used_gb),
        "storage_max_label": _format_storage_amount(max_gb),
        "storage_pct": storage_pct,
        "storage_pct_label": storage_pct_label,
        "current_saved_pdf_zips": pdf_used,
        "max_saved_pdf_zips": pdf_max,
        "pdf_pct": pdf_pct,
        "pdf_pct_label": pdf_pct_label,
        "current_saved_pyq_zips": pyq_used,
        "max_saved_pyq_zips": pyq_max,
        "pyq_pct": pyq_pct,
        "pyq_pct_label": pyq_pct_label,
        "uses_unallocated_pool": bool(
            user and user.role == User.Role.ORGUSER and user.organization_id
        ),
    }

def _bytes_to_gb(file_size_bytes):
    return max(file_size_bytes, 0) / (1024 ** 3)


def estimate_embedding_bytes(chunk_count: int, dimensions: int = EMBED_DIMENSIONS) -> int:
    """Approximate pgvector size for float32 embeddings."""
    return max(int(chunk_count), 0) * dimensions * BYTES_PER_FLOAT32


def measure_user_vector_bytes(user) -> int:
    """Measure embedding storage for PDF contexts owned by this user."""
    from apps.pdf_module.models import PDFChunk

    if user is None:
        return 0
    chunk_count = (
        PDFChunk.objects.filter(context__created_by=user)
        .exclude(embedding__isnull=True)
        .count()
    )
    return estimate_embedding_bytes(chunk_count)


@transaction.atomic
def recompute_vector_storage(user) -> float:
    """Recompute and persist current_vector_storage_gb for a user. Returns GB used."""
    if user is None:
        return 0.0
    quota = get_storage_quota(user)
    used_gb = _bytes_to_gb(measure_user_vector_bytes(user))
    StorageQuota.objects.filter(pk=quota.pk).update(current_vector_storage_gb=used_gb)
    quota.current_vector_storage_gb = used_gb
    return used_gb


def _check_upload(quota, *, file_size_bytes, current_count, max_count, user=None, count_delta=1):
    if user and is_unlimited_user(user):
        return
    size_gb = _bytes_to_gb(file_size_bytes)
    count_delta = max(int(count_delta), 1)
    if current_count + count_delta > max_count:
        raise StorageQuotaExceeded(
            f"Upload limit reached for this user "
            f"(need {count_delta} slot(s), {max_count - current_count} remaining)."
        )
    if user and user.role == User.Role.ORGUSER and user.organization_id:
        budget = org_storage_budget(user.organization)
        unallocated = float(budget["storage_remaining"])
        admin_used = org_admin_storage_used(user.organization)
        if admin_used + size_gb > unallocated + 1e-9:
            raise StorageQuotaExceeded(
                "Organisation unallocated storage pool exhausted "
                f"({admin_used:.2f} used of {unallocated:.2f} GB unallocated)."
            )
        return
    used_gb = float(quota.current_total_storage_gb) + float(quota.current_vector_storage_gb)
    limit_gb = float(quota.max_total_storage_gb) + float(quota.max_vector_storage_gb)
    if used_gb + size_gb > limit_gb:
        raise StorageQuotaExceeded("Storage quota exceeded.")


def get_max_pdf_upload_mb(user):
    if user and is_unlimited_user(user):
        return 500
    if not user.organization_id:
        return 100
    settings = OrganizationSettings.objects.filter(organization_id=user.organization_id).first()
    return settings.max_pdf_upload_mb if settings else 100


def get_max_pyq_upload_mb(user):
    return get_max_pdf_upload_mb(user)


def check_pdf_upload_allowed(user, file_size_bytes, *, count_delta=1):
    quota = get_storage_quota(user)
    # Org admins keep zip-count caps from policy defaults even with 0 personal GB share.
    if user.role == User.Role.ORGUSER and user.organization_id and quota.max_saved_pdf_zips <= 0:
        policy = OrganizationProvisioningPolicy.objects.filter(
            organization_id=user.organization_id
        ).first()
        max_count = policy.default_pdf_zip_limit if policy else 100
    else:
        max_count = quota.max_saved_pdf_zips
    _check_upload(
        quota,
        file_size_bytes=file_size_bytes,
        current_count=quota.current_saved_pdf_zips,
        max_count=max_count,
        user=user,
        count_delta=count_delta,
    )
    return True, None


def check_pyq_upload_allowed(user, file_size_bytes):
    quota = get_storage_quota(user)
    if user.role == User.Role.ORGUSER and user.organization_id and quota.max_saved_pyq_zips <= 0:
        policy = OrganizationProvisioningPolicy.objects.filter(
            organization_id=user.organization_id
        ).first()
        max_count = policy.default_pyq_zip_limit if policy else 50
    else:
        max_count = quota.max_saved_pyq_zips
    _check_upload(
        quota,
        file_size_bytes=file_size_bytes,
        current_count=quota.current_saved_pyq_zips,
        max_count=max_count,
        user=user,
    )
    return True, None


@transaction.atomic
def reserve_pdf_storage(user, file_size_bytes, *, count_delta=1):
    if user.role == User.Role.ORGUSER and user.organization_id:
        list(
            StorageQuota.objects.select_for_update().filter(
                user__organization_id=user.organization_id,
                user__role=User.Role.ORGUSER,
            )
        )
    count_delta = max(int(count_delta), 1)
    check_pdf_upload_allowed(user, file_size_bytes, count_delta=count_delta)
    StorageQuota.objects.select_for_update().filter(user=user).update(
        current_total_storage_gb=F("current_total_storage_gb") + _bytes_to_gb(file_size_bytes),
        current_saved_pdf_zips=F("current_saved_pdf_zips") + count_delta,
    )


@transaction.atomic
def release_pdf_storage(user, file_size_bytes):
    StorageQuota.objects.select_for_update().filter(user=user).update(
        current_total_storage_gb=F("current_total_storage_gb") - _bytes_to_gb(file_size_bytes),
        current_saved_pdf_zips=F("current_saved_pdf_zips") - 1,
    )


@transaction.atomic
def reserve_pyq_storage(user, file_size_bytes):
    if user.role == User.Role.ORGUSER and user.organization_id:
        list(
            StorageQuota.objects.select_for_update().filter(
                user__organization_id=user.organization_id,
                user__role=User.Role.ORGUSER,
            )
        )
    check_pyq_upload_allowed(user, file_size_bytes)
    StorageQuota.objects.select_for_update().filter(user=user).update(
        current_total_storage_gb=F("current_total_storage_gb") + _bytes_to_gb(file_size_bytes),
        current_saved_pyq_zips=F("current_saved_pyq_zips") + 1,
    )


@transaction.atomic
def release_pyq_storage(user, file_size_bytes):
    StorageQuota.objects.select_for_update().filter(user=user).update(
        current_total_storage_gb=F("current_total_storage_gb") - _bytes_to_gb(file_size_bytes),
        current_saved_pyq_zips=F("current_saved_pyq_zips") - 1,
    )
