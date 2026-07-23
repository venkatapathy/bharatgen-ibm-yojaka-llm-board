"""Helpers for users who bypass demo quotas (admin / superuser)."""


def is_unlimited_user(user) -> bool:
    if user is None:
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "username", "") == "admin":
        return True
    return getattr(user, "role", "") == "superuser"
