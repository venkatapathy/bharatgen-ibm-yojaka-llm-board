"""Visibility helpers for PDF / PYQ / generation content.

Rules:
- Regular users see only what they created.
- Org Admins see content created by Users (not Admins) in their organisation.
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
    from django.db.models import Q

    from apps.core.models import User

    return Q(
        created_by__organization_id=user.organization_id,
        created_by__role=User.Role.USER,
    )


def visible_pdf_contexts(user, *, ready_only=False):
    from apps.pdf_module.models import PDFContext

    if _is_platform_admin(user):
        qs = PDFContext.objects.all()
    elif _is_org_admin(user) and user.organization_id:
        qs = PDFContext.objects.filter(_org_member_content_q(user))
    else:
        qs = PDFContext.objects.filter(created_by=user)
    if ready_only:
        qs = qs.filter(status="ready")
    return qs.select_related("created_by", "organization")


def visible_pyq_modules(user, *, ready_only=False):
    from apps.pyq_module.models import PYQModule

    if _is_platform_admin(user):
        qs = PYQModule.objects.all()
    elif _is_org_admin(user) and user.organization_id:
        qs = PYQModule.objects.filter(_org_member_content_q(user))
    else:
        qs = PYQModule.objects.filter(created_by=user)
    if ready_only:
        qs = qs.filter(status="ready")
    return qs.select_related("created_by", "organization")


def visible_batch_runs(user):
    from apps.question_generation.models import BatchRun

    if _is_platform_admin(user):
        qs = BatchRun.objects.all()
    elif _is_org_admin(user) and user.organization_id:
        qs = BatchRun.objects.filter(_org_member_run_q(user))
    else:
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
            batch_run__created_by__role=User.Role.USER,
        )
    return Question.objects.filter(is_generated=True, batch_run__created_by=user)


def visible_pyq_questions(user):
    from apps.core.models import User
    from apps.pyq_module.models import Question

    if _is_platform_admin(user):
        return Question.objects.filter(pyq_module__isnull=False)
    if _is_org_admin(user) and user.organization_id:
        return Question.objects.filter(
            pyq_module__organization_id=user.organization_id,
            pyq_module__created_by__role=User.Role.USER,
        )
    return Question.objects.filter(pyq_module__created_by=user)


# Backwards-compatible aliases used by existing views.
owned_pdf_contexts = visible_pdf_contexts
owned_pyq_modules = visible_pyq_modules
owned_batch_runs = visible_batch_runs
owned_generated_questions = visible_generated_questions
owned_pyq_questions = visible_pyq_questions
