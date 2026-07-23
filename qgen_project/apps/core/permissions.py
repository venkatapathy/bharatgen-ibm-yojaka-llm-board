"""Access-control helpers and mixins."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .models import User


class OrgUserRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False):
            return self.handle_no_permission()
        if request.user.role not in (User.Role.SUPERUSER, User.Role.ORGUSER):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class SuperUserRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False):
            return self.handle_no_permission()
        if request.user.role != User.Role.SUPERUSER:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def role_required(*roles):
    """Decorator: require login and one of the given User.Role values."""

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


# DRF permissions
class IsSuperUser(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.SUPERUSER
        )


class IsOrgUser(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in (User.Role.SUPERUSER, User.Role.ORGUSER)
        )
