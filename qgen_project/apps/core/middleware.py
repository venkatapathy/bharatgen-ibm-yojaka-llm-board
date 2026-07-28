"""Kick deactivated members out of authenticated sessions."""

from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.shortcuts import redirect

from .membership import DEACTIVATED_MESSAGE, user_may_access_app

User = get_user_model()

# Paths a deactivated user may still hit (login / logout / static / API).
_ALLOWED_PREFIXES = (
    "/accounts/",
    "/static/",
    "/media/",
    "/api/",
)


class ActiveMemberMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and not self._path_allowed(request.path)
        ):
            if not self._fresh_membership_ok(user):
                logout(request)
                messages.error(request, DEACTIVATED_MESSAGE)
                return redirect("account_login")
        return self.get_response(request)

    @staticmethod
    def _path_allowed(path: str) -> bool:
        return any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES)

    @staticmethod
    def _fresh_membership_ok(user) -> bool:
        try:
            fresh = User.objects.only(
                "is_active_member", "role", "is_superuser"
            ).get(pk=user.pk)
        except User.DoesNotExist:
            return False
        return user_may_access_app(fresh)
