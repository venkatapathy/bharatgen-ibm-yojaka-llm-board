"""Visibility helpers for PDF / PYQ / generation content.

Rules:
- PDF contexts are a shared library: every signed-in user can see them.
  Only platform Admins may upload / edit OCR / delete (enforced in views).
- Regular users see only their own generation runs (user integrity).
- Org Admins see generation runs/questions from Users and other Org Admins
  in their organisation (peer org-admin visibility).
- Platform Admins (superuser) see everything.
"""


def _is_platform_admin(user) -> bool:
    from apps.core.models import User

    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "role", None) == User.Role.SUPERUSER
    )


def _is_org_admin(user) -> bool:
    from apps.core.models import User

    return getattr(user, "role", None) == User.Role.ORGUSER


def _org_member_content_q(user):
    """Filter: same org, owned by a regular User (exclude Admin / Org Admin)."""
    from django.db.models import Q

    from apps.core.models import User

    return Q(
        organization_id=user.organization_id,
        created_by__role=User.Role.USER,
    )


def _org_member_run_q(user):
    """Runs owned by Users or Org Admins in the viewer's organisation."""
    from django.db.models import Q

    from apps.core.models import User

    return Q(
        created_by__organization_id=user.organization_id,
        created_by__role__in=(User.Role.USER, User.Role.ORGUSER),
    )


SELF_BROWSE_VALUE = "me"
ORG_ALL_BROWSE_VALUE = "all"


def selectable_organizations(viewer):
    """Orgs an Admin can filter by. Empty for non-admins."""
    from apps.core.models import Organization

    if not _is_platform_admin(viewer):
        return Organization.objects.none()
    return Organization.objects.filter(is_active=True).order_by("name")


def selectable_content_users(viewer, *, organization_id=None):
    """
    Users whose content Org Admin / Admin can browse.

    - Org Admin: Users and Org Admins in their organisation.
    - Admin: Users and Org Admins in the selected organisation.
    """
    from apps.core.models import User

    if _is_platform_admin(viewer):
        if not organization_id:
            return User.objects.none()
        return (
            User.objects.filter(
                role__in=(User.Role.USER, User.Role.ORGUSER),
                organization_id=organization_id,
            )
            .select_related("organization")
            .order_by("role", "username")
        )
    if _is_org_admin(viewer) and viewer.organization_id:
        return (
            User.objects.filter(
                role__in=(User.Role.USER, User.Role.ORGUSER),
                organization_id=viewer.organization_id,
            )
            .select_related("organization")
            .order_by("role", "username")
        )
    return User.objects.none()


def resolve_browse_target_user(viewer, *, organization_id=None, user_id=None):
    """
    For Admin / Org Admin list pages: scope content to a selected user.

    Default (no ``user`` query param): viewer's own content ("My …").
    Supports ``user=me`` (or the viewer's own pk) explicitly.
    Supports ``user=all`` for every User and Org Admin in the organisation.

    Returns (target_user_or_None, needs_user_filter, browse_all).
    Regular users always browse as themselves (needs_filter=False).
    """
    if not (_is_platform_admin(viewer) or _is_org_admin(viewer)):
        return viewer, False, False

    # Default landing: My PDFs / My PYQs / My runs.
    if not user_id:
        return viewer, True, False

    # "My …" — Admin / Org Admin viewing their own content.
    if str(user_id) in {SELF_BROWSE_VALUE, str(viewer.pk)}:
        return viewer, True, False

    # All regular Users in the org (shared verification packs).
    if str(user_id) == ORG_ALL_BROWSE_VALUE:
        if _is_platform_admin(viewer) and not organization_id:
            return None, True, False
        return None, True, True

    qs = selectable_content_users(viewer, organization_id=organization_id)
    target = qs.filter(pk=user_id).first()
    return target, True, False


def browse_list_context(viewer, *, organization_id=None, user_id=None):
    """Shared template/query context for Admin·Org Admin user-scoped list pages."""
    target, needs_filter, browse_all = resolve_browse_target_user(
        viewer, organization_id=organization_id, user_id=user_id
    )
    if browse_all:
        selected_user = ORG_ALL_BROWSE_VALUE
    elif target and target.pk == viewer.pk and (
        not user_id
        or user_id == SELF_BROWSE_VALUE
        or str(user_id) == str(viewer.pk)
    ):
        selected_user = SELF_BROWSE_VALUE
    else:
        selected_user = str(target.pk) if target else ""
    return {
        "target": target,
        "needs_filter": needs_filter,
        "needs_user_filter": needs_filter,
        "browse_all": browse_all,
        "selected_org": organization_id or "",
        "selected_user": selected_user,
        "self_browse_value": SELF_BROWSE_VALUE,
        "org_all_browse_value": ORG_ALL_BROWSE_VALUE,
        "filter_orgs": selectable_organizations(viewer),
        "filter_users": selectable_content_users(
            viewer, organization_id=organization_id
        ),
        "browse_user": target if needs_filter and not browse_all else None,
        "browsing_own": bool(target and target.pk == viewer.pk and not browse_all),
        "browsing_all_org": browse_all,
        "show_owner": False,
    }


def visible_pdf_contexts(user, *, ready_only=False, owner=None):
    """Shared PDF library: every signed-in user can see all contexts.

    Upload / OCR edit / delete remain admin-only in the views layer.
    ``owner`` is kept for rare admin browse tooling.
    """
    from apps.pdf_module.models import PDFContext

    if owner is not None:
        qs = PDFContext.objects.filter(created_by=owner)
    else:
        qs = PDFContext.objects.all()
    if ready_only:
        qs = qs.filter(status="ready")
    return qs.select_related("created_by", "organization")


def visible_pyq_modules(user, *, ready_only=False, owner=None):
    from apps.pyq_module.models import PYQModule
    from django.db.models import Q

    if owner is not None:
        qs = PYQModule.objects.filter(created_by=owner)
    elif _is_platform_admin(user):
        qs = PYQModule.objects.all()
    elif _is_org_admin(user) and user.organization_id:
        qs = PYQModule.objects.filter(
            Q(created_by=user) | _org_member_content_q(user)
        )
    else:
        qs = PYQModule.objects.filter(created_by=user)
    if ready_only:
        qs = qs.filter(status="ready")
    return qs.select_related("created_by", "organization")


def visible_batch_runs(user, *, owner=None, org_all=False, organization_id=None):
    from apps.core.models import User
    from apps.question_generation.models import BatchRun

    if owner is not None:
        qs = BatchRun.objects.filter(created_by=owner)
    elif org_all:
        if _is_platform_admin(user):
            qs = BatchRun.objects.filter(
                created_by__role__in=(User.Role.USER, User.Role.ORGUSER)
            )
            if organization_id:
                qs = qs.filter(created_by__organization_id=organization_id)
        elif _is_org_admin(user) and user.organization_id:
            qs = BatchRun.objects.filter(_org_member_run_q(user))
        else:
            qs = BatchRun.objects.none()
    elif _is_platform_admin(user):
        qs = BatchRun.objects.all()
    elif _is_org_admin(user) and user.organization_id:
        # Peer org-admins + regular users in the same organisation.
        qs = BatchRun.objects.filter(_org_member_run_q(user))
    else:
        # Regular users: own runs only (integrity).
        qs = BatchRun.objects.filter(created_by=user)
    return qs.select_related("created_by")


def visible_generated_questions(user):
    from apps.core.models import User
    from apps.pyq_module.models import Question

    if _is_platform_admin(user):
        return Question.objects.filter(is_generated=True)
    if _is_org_admin(user) and user.organization_id:
        return Question.objects.filter(
            is_generated=True,
            batch_run__created_by__organization_id=user.organization_id,
            batch_run__created_by__role__in=(User.Role.USER, User.Role.ORGUSER),
        )
    return Question.objects.filter(is_generated=True, batch_run__created_by=user)


def visible_pyq_questions(user):
    from django.db.models import Q

    from apps.core.models import User
    from apps.pyq_module.models import Question

    if _is_platform_admin(user):
        return Question.objects.filter(pyq_module__isnull=False)
    if _is_org_admin(user) and user.organization_id:
        # Org Admin: own modules plus regular Users in the organisation.
        return Question.objects.filter(
            Q(pyq_module__created_by=user)
            | Q(
                pyq_module__organization_id=user.organization_id,
                pyq_module__created_by__role=User.Role.USER,
            )
        )
    return Question.objects.filter(pyq_module__created_by=user)


# Backwards-compatible aliases used by existing views.
owned_pdf_contexts = visible_pdf_contexts
owned_pyq_modules = visible_pyq_modules
owned_batch_runs = visible_batch_runs
owned_generated_questions = visible_generated_questions
owned_pyq_questions = visible_pyq_questions
