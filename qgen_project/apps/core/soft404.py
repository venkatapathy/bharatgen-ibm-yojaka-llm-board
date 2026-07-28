"""Helpers to avoid scary DEBUG 404 pages for stale/missing content."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect


class SoftMissingMixin:
    """
    If the target object is gone or not visible, show a friendly message and
    redirect instead of raising Http404 (which DEBUG turns into a scary page).
    """

    missing_message = "That item is no longer available."
    missing_redirect = "/"

    def get_missing_redirect(self):
        return self.missing_redirect

    def get_missing_message(self):
        return self.missing_message

    def get_object(self, queryset=None):
        try:
            return super().get_object(queryset)
        except Http404:
            messages.info(self.request, self.get_missing_message())
            return None

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except Http404:
            messages.info(request, self.get_missing_message())
            return redirect(self.get_missing_redirect())

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object is None:
            return redirect(self.get_missing_redirect())
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object is None:
            return redirect(self.get_missing_redirect())
        return super().post(request, *args, **kwargs)


def soft_get_or_redirect(request, queryset, *, message, redirect_to):
    """Function-view helper: return object or (None) after messaging + caller redirects."""
    obj = queryset.first() if hasattr(queryset, "first") else None
    # Prefer filter(pk=...) callers that pass a narrowed queryset of 0/1 rows.
    if obj is None and hasattr(queryset, "exists") and not queryset.exists():
        messages.info(request, message)
        return None
    if obj is None:
        messages.info(request, message)
        return None
    return obj
