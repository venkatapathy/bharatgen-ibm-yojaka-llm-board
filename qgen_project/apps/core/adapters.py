"""Custom allauth account adapter for demo org assignment."""

from django.utils.text import slugify
from allauth.account.adapter import DefaultAccountAdapter

from .membership import DEACTIVATED_MESSAGE, user_may_access_app
from .models import Organization


class QGenAccountAdapter(DefaultAccountAdapter):
    error_messages = {
        **DefaultAccountAdapter.error_messages,
        "username_password_mismatch": "Wrong password. Please try again.",
        "email_password_mismatch": "Wrong password. Please try again.",
        "incorrect_password": "Wrong password. Please try again.",
        "account_inactive": DEACTIVATED_MESSAGE,
    }

    def is_open_for_signup(self, request):
        return False

    def pre_login(
        self,
        request,
        user,
        *,
        email_verification,
        signal_kwargs,
        email,
        signup,
        redirect_url,
    ):
        if not user_may_access_app(user):
            return self.respond_user_inactive(request, user)
        return super().pre_login(
            request,
            user,
            email_verification=email_verification,
            signal_kwargs=signal_kwargs,
            email=email,
            signup=signup,
            redirect_url=redirect_url,
        )

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
