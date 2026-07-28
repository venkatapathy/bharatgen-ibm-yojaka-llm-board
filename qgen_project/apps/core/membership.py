"""Active-membership checks for Org Admins and Users."""

DEACTIVATED_MESSAGE = (
    "Your account is deactivated. Contact your administrator."
)


def user_may_access_app(user) -> bool:
    """
    Platform Admins always retain access.
    Org Admins and Users need is_active_member=True.
    Their data is kept; access is blocked until reactivated.
    """
    if user is None:
        return False
    if not getattr(user, "is_authenticated", False):
        return True
    if getattr(user, "is_anonymous", False):
        return True
    if getattr(user, "is_superuser", False):
        return True
    from .models import User

    if getattr(user, "role", None) == User.Role.SUPERUSER:
        return True
    return bool(getattr(user, "is_active_member", True))
