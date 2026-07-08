"""Storage quota helpers for upload governance."""

from django.db import transaction
from django.db.models import F

from .models import OrganizationProvisioningPolicy, OrganizationSettings, StorageQuota


class StorageQuotaExceeded(Exception):
    """Raised when an upload would exceed the user's storage limits."""


def _policy_defaults(user):
    defaults = {
        "max_total_storage_gb": 5.0,
        "max_vector_storage_gb": 2.0,
        "max_saved_pdf_zips": 100,
        "max_saved_pyq_zips": 50,
    }
    if user.organization_id:
        policy = OrganizationProvisioningPolicy.objects.filter(organization_id=user.organization_id).first()
        if policy:
            defaults.update(
                {
                    "max_total_storage_gb": policy.default_storage_limit_gb,
                    "max_vector_storage_gb": policy.default_vector_storage_gb,
                    "max_saved_pdf_zips": policy.default_pdf_zip_limit,
                    "max_saved_pyq_zips": policy.default_pyq_zip_limit,
                }
            )
    return defaults


def get_storage_quota(user):
    quota, _ = StorageQuota.objects.get_or_create(user=user, defaults=_policy_defaults(user))
    return quota


def _bytes_to_gb(file_size_bytes):
    return max(file_size_bytes, 0) / (1024 ** 3)


def _check_upload(quota, *, file_size_bytes, current_count, max_count):
    size_gb = _bytes_to_gb(file_size_bytes)
    if current_count >= max_count:
        raise StorageQuotaExceeded("Upload limit reached for this user.")
    if quota.current_total_storage_gb + size_gb > quota.max_total_storage_gb:
        raise StorageQuotaExceeded("Total storage quota exceeded.")


def get_max_pdf_upload_mb(user):
    if not user.organization_id:
        return 100
    settings = OrganizationSettings.objects.filter(organization_id=user.organization_id).first()
    return settings.max_pdf_upload_mb if settings else 100


def get_max_pyq_upload_mb(user):
    return get_max_pdf_upload_mb(user)


def check_pdf_upload_allowed(user, file_size_bytes):
    quota = get_storage_quota(user)
    _check_upload(
        quota,
        file_size_bytes=file_size_bytes,
        current_count=quota.current_saved_pdf_zips,
        max_count=quota.max_saved_pdf_zips,
    )
    return True, None


def check_pyq_upload_allowed(user, file_size_bytes):
    quota = get_storage_quota(user)
    _check_upload(
        quota,
        file_size_bytes=file_size_bytes,
        current_count=quota.current_saved_pyq_zips,
        max_count=quota.max_saved_pyq_zips,
    )
    return True, None


@transaction.atomic
def reserve_pdf_storage(user, file_size_bytes):
    check_pdf_upload_allowed(user, file_size_bytes)
    StorageQuota.objects.select_for_update().filter(user=user).update(
        current_total_storage_gb=F("current_total_storage_gb") + _bytes_to_gb(file_size_bytes),
        current_saved_pdf_zips=F("current_saved_pdf_zips") + 1,
    )


@transaction.atomic
def release_pdf_storage(user, file_size_bytes):
    StorageQuota.objects.select_for_update().filter(user=user).update(
        current_total_storage_gb=F("current_total_storage_gb") - _bytes_to_gb(file_size_bytes),
        current_saved_pdf_zips=F("current_saved_pdf_zips") - 1,
    )


@transaction.atomic
def reserve_pyq_storage(user, file_size_bytes):
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
