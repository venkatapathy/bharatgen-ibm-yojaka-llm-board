"""Custom allauth account adapter for demo org assignment."""

from django.utils.text import slugify
from allauth.account.adapter import DefaultAccountAdapter

from .models import Organization


class QGenAccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        if not user.organization_id:
            organization = Organization.objects.filter(is_active=True).first()
            if not organization:
                base_name = form.cleaned_data.get("username") or "Demo Organization"
                organization = Organization.objects.create(
                    name=f"{base_name} Org",
                    slug=slugify(f"{base_name}-org")[:50],
                )
            user.organization = organization
        if commit:
            user.save()
        return user
