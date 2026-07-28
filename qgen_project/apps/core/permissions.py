"""Access-control helpers and mixins."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .membership import user_may_access_app
from .models import User


class OrgUserRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False):
            return self.handle_no_permission()
        if not user_may_access_app(request.user):
            raise PermissionDenied
        if request.user.role not in (User.Role.SUPERUSER, User.Role.ORGUSER):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class SuperUserRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False):
            return self.handle_no_permission()
        if not user_may_access_app(request.user):
            raise PermissionDenied
        if request.user.role != User.Role.SUPERUSER:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def role_required(*roles):
    """Decorator: require login, active membership, and one of the given roles."""

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not user_may_access_app(request.user):
                raise PermissionDenied
            if request.user.role not in roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


# DRF permissions
class IsActiveMember(BasePermission):
    """Block deactivated Org Admins / Users from API access."""

    message = "Your account is deactivated. Contact your administrator."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return user_may_access_app(user)


class IsSuperUser(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and user_may_access_app(request.user)
            and request.user.role == User.Role.SUPERUSER
        )


class IsOrgUser(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and user_may_access_app(request.user)
            and request.user.role in (User.Role.SUPERUSER, User.Role.ORGUSER)
        )
