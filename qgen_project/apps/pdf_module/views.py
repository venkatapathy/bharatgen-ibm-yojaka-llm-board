import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView

from apps.core.ownership import owned_pdf_contexts
from apps.core.permissions import SuperUserRequiredMixin, role_required
from apps.core.soft404 import SoftMissingMixin
from apps.core.storage import (
    StorageQuotaExceeded,
    recompute_vector_storage,
    release_pdf_storage,
    reserve_pdf_storage,
    storage_quota_display,
)
from apps.core.models import User

from .forms import PDFContextUploadForm
from .models import PDFChunk, PDFContext
from .tasks import index_pdf_context
from .uploads import iter_upload_pdf_members


def _ocr_and_ready(ctx: PDFContext, *, force_vision: bool = False) -> bool:
    """Fill full-document OCR and mark ready. No chunk/embed indexing.

    Upload path uses native text layer only (fast). Vision OCR is Celery-only.
    """
    from apps.pdf_module.legacy_hindi import looks_like_legacy_hindi, normalize_legacy_hindi
    from apps.pdf_module.ocr_full import rebuild_context_ocr

    native_only = not force_vision
    try:
        rebuild_context_ocr(
            ctx, force_vision=force_vision, native_only=native_only
        )
        ctx.refresh_from_db(fields=["ocr_text"])
    except Exception as exc:
        ctx.status = "error"
        ctx.error_message = str(exc)[:500]
        ctx.save(update_fields=["status", "error_message"])
        return False

    ocr = (ctx.ocr_text or "").strip()
    if ocr:
        is_leg, ft = looks_like_legacy_hindi(ocr)
        if is_leg:
            ocr = normalize_legacy_hindi(ocr, force=True, font_type=ft or "krutidev")
            ctx.ocr_text = ocr
        ctx.status = "ready"
        ctx.needs_reindex = False
        ctx.error_message = ""
        ctx.embedded_chunk_count = 0
        ctx.save(
            update_fields=[
                "ocr_text",
                "status",
                "needs_reindex",
                "error_message",
                "embedded_chunk_count",
            ]
        )
        return True

    ctx.status = "pending"
    ctx.error_message = "No text extracted; queued for vision OCR."
    ctx.save(update_fields=["status", "error_message"])
    return False

_COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6}[-\s]?\d{2,4})\b", re.IGNORECASE)


def _course_code_from_name(name: str) -> str:
    m = _COURSE_CODE_RE.search(name or "")
    if not m:
        return ""
    return re.sub(r"\s+", "-", m.group(1).upper().replace(" ", "-"))


def _is_platform_admin(user) -> bool:
    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "role", "") == User.Role.SUPERUSER
        or getattr(user, "is_superuser_role", False)
    )


def _pdf_file_url(ctx: PDFContext) -> str:
    """Same-origin stream URL (works behind ngrok; avoids /media iframe blocks)."""
    return reverse("pdf_module:file", kwargs={"pk": ctx.pk})


def _mark_ready_if_ocr(ctx: PDFContext) -> None:
    """If OCR is present but Celery never flipped status, mark ready."""
    if ctx.status in ("pending", "processing") and (ctx.ocr_text or "").strip():
        ctx.status = "ready"
        ctx.needs_reindex = False
        ctx.error_message = ""
        ctx.save(update_fields=["status", "needs_reindex", "error_message"])


def _ensure_ocr_text(ctx: PDFContext) -> str:
    """Return full OCR text; rebuild from PDF if stored text looks truncated."""
    from apps.pdf_module.legacy_hindi import looks_like_legacy_hindi, normalize_legacy_hindi
    from apps.pdf_module.ocr_full import rebuild_context_ocr

    def _normalize(text: str) -> str:
        if not text:
            return ""
        is_leg, ft = looks_like_legacy_hindi(text)
        if is_leg:
            return normalize_legacy_hindi(text, force=True, font_type=ft or "krutidev")
        return text

    stored = (ctx.ocr_text or "").strip()
    stored_words = len(stored.split())
    # Hierarchical-chunk backfill was often <300 words for multi-page units.
    if stored and stored_words >= 800:
        fixed = _normalize(stored)
        if fixed != ctx.ocr_text:
            ctx.ocr_text = fixed
            ctx.save(update_fields=["ocr_text"])
        _mark_ready_if_ocr(ctx)
        return fixed

    try:
        rebuild_context_ocr(ctx, force_vision=False)
        ctx.refresh_from_db()
        if (ctx.ocr_text or "").strip():
            _mark_ready_if_ocr(ctx)
            return ctx.ocr_text
    except Exception:
        pass

    if stored:
        fixed = _normalize(stored)
        _mark_ready_if_ocr(ctx)
        return fixed
    return ""


class PDFContextListView(LoginRequiredMixin, ListView):
    model = PDFContext
    template_name = "pdf_module/context_list.html"
    context_object_name = "contexts"

    def _filter_params(self):
        q = (self.request.GET.get("q") or "").strip()
        status = (self.request.GET.get("status") or "").strip().lower()
        course = (self.request.GET.get("course") or "").strip().upper()
        return q, status, course

    def get_queryset(self):
        qs = owned_pdf_contexts(self.request.user)
        q, status, course = self._filter_params()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(original_filename__icontains=q)
                | Q(description__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)
        if course:
            course_loose = course.replace("-", " ")
            qs = qs.filter(
                Q(name__icontains=course)
                | Q(name__icontains=course_loose)
                | Q(original_filename__icontains=course)
                | Q(original_filename__icontains=course_loose)
            )
        return qs.order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        viewer = self.request.user
        is_admin = _is_platform_admin(viewer)
        ctx["is_platform_admin"] = is_admin
        ctx["can_manage_pdfs"] = is_admin
        if is_admin:
            recompute_vector_storage(viewer)
            ctx["quota"] = storage_quota_display(viewer)
        else:
            ctx["quota"] = None

        q, status, course = self._filter_params()
        base_qs = owned_pdf_contexts(viewer)
        courses = sorted(
            {
                code
                for name in base_qs.values_list("name", flat=True)
                if (code := _course_code_from_name(name))
            }
        )
        statuses = sorted(
            {s for s in base_qs.values_list("status", flat=True).distinct() if s}
        )
        ctx.update(
            {
                "filter_q": q,
                "filter_status": status,
                "filter_course": course,
                "filter_courses": courses,
                "filter_statuses": statuses,
                "filter_active": bool(q or status or course),
                "result_count": ctx["object_list"].count()
                if hasattr(ctx["object_list"], "count")
                else len(ctx["object_list"]),
                # Shared library: no per-user browse gate.
                "needs_user_filter": False,
                "browse_user": None,
            }
        )
        return ctx


class PDFContextUploadView(SuperUserRequiredMixin, CreateView):
    model = PDFContext
    template_name = "pdf_module/context_upload.html"
    form_class = PDFContextUploadForm
    success_url = reverse_lazy("pdf_module:list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        uploaded = form.cleaned_data["zip_path"]
        members = list(iter_upload_pdf_members(uploaded))
        if not members:
            form.add_error("zip_path", "No valid PDF files found in the upload.")
            return self.form_invalid(form)

        draft = form.save(commit=False)
        base_name = (form.cleaned_data.get("name") or "").strip() or "PDF Context"
        total_bytes = sum(len(payload) for _, payload in members)
        created_ids = []

        try:
            with transaction.atomic():
                reserve_pdf_storage(
                    self.request.user,
                    total_bytes,
                    count_delta=len(members),
                )
                for filename, payload in members:
                    if len(members) == 1:
                        ctx_name = base_name
                    else:
                        stem = filename.rsplit(".", 1)[0]
                        ctx_name = f"{base_name} — {stem}"[:256]
                    ctx = PDFContext(
                        name=ctx_name,
                        description="",
                        strategy=draft.strategy,
                        chunk_size=draft.chunk_size,
                        chunk_overlap=draft.chunk_overlap,
                        embed_model=draft.embed_model or "",
                        reranker_model=draft.reranker_model or "",
                        created_by=self.request.user,
                        organization=self.request.user.organization,
                        file_size_bytes=len(payload),
                        original_filename=filename,
                    )
                    ctx.zip_path.save(filename, ContentFile(payload), save=False)
                    ctx.save()
                    created_ids.append(ctx.id)
        except StorageQuotaExceeded as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        for context_id in created_ids:
            ctx = PDFContext.objects.get(id=context_id)
            # Full-document OCR only (no chunk/embed indexing). Text-layer first.
            if not _ocr_and_ready(ctx, force_vision=False):
                # Scanned / empty text layer → vision OCR in Celery background.
                index_pdf_context.delay(str(context_id))

        n = len(created_ids)
        ready_n = PDFContext.objects.filter(
            id__in=created_ids, status="ready"
        ).count()
        if n == 1:
            if ready_n:
                messages.success(self.request, "PDF uploaded — OCR ready.")
            else:
                messages.info(
                    self.request,
                    "PDF uploaded — text layer empty; vision OCR queued.",
                )
        else:
            messages.success(
                self.request,
                f"{n} PDFs uploaded ({ready_n} OCR-ready"
                f"{f', {n - ready_n} queued for vision OCR' if n - ready_n else ''}).",
            )
        return redirect(self.success_url)


class PDFContextDetailView(SoftMissingMixin, LoginRequiredMixin, DetailView):
    model = PDFContext
    template_name = "pdf_module/context_detail.html"
    missing_message = "That PDF context is no longer available."
    missing_redirect = "pdf_module:list"

    def get_queryset(self):
        return owned_pdf_contexts(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        is_admin = _is_platform_admin(self.request.user)
        ctx["pdf_url"] = _pdf_file_url(self.object)
        ctx["can_manage_pdfs"] = is_admin
        ctx["can_view_ocr"] = is_admin
        searchable_ocr_text = _ensure_ocr_text(self.object)
        ctx["searchable_ocr_text"] = searchable_ocr_text
        # Never load / rebuild OCR for org admins or users.
        ctx["ocr_text"] = searchable_ocr_text if is_admin else ""
        return ctx


class PDFContextDeleteView(SoftMissingMixin, SuperUserRequiredMixin, DeleteView):
    model = PDFContext
    template_name = "pdf_module/context_confirm_delete.html"
    success_url = reverse_lazy("pdf_module:list")
    missing_message = "That PDF context is no longer available."
    missing_redirect = "pdf_module:list"

    def get_queryset(self):
        return owned_pdf_contexts(self.request.user)

    def form_valid(self, form):
        owner = self.object.created_by or self.request.user
        if self.object.file_size_bytes:
            release_pdf_storage(owner, self.object.file_size_bytes)
        messages.success(self.request, "PDF context deleted.")
        response = super().form_valid(form)
        recompute_vector_storage(owner)
        return response


def _get_owned_pdf_or_redirect(request, pk):
    ctx = owned_pdf_contexts(request.user).filter(pk=pk).first()
    if ctx is None:
        messages.info(request, "That PDF context is no longer available.")
        return None
    return ctx


@login_required
@xframe_options_exempt
def pdf_context_file(request, pk):
    """Stream the PDF inline for the split viewer (blob-fetch or iframe)."""
    ctx = owned_pdf_contexts(request.user).filter(pk=pk).first()
    if ctx is None or not ctx.zip_path:
        raise Http404("PDF not found")
    try:
        fh = ctx.zip_path.open("rb")
    except FileNotFoundError as exc:
        raise Http404("PDF file missing on disk") from exc
    filename = ctx.original_filename or ctx.zip_path.name.split("/")[-1] or "document.pdf"
    safe_name = filename.replace('"', "").replace("\n", " ")[:180]
    response = FileResponse(fh, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{safe_name}"'
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, max-age=120"
    return response


@login_required
def pdf_context_status(request, pk):
    ctx = _get_owned_pdf_or_redirect(request, pk)
    if ctx is None:
        return redirect("pdf_module:list")
    return render(request, "pdf_module/partials/context_status.html", {"object": ctx})


@login_required
def pdf_context_chunks(request, pk):
    if not _is_platform_admin(request.user):
        raise PermissionDenied
    ctx = _get_owned_pdf_or_redirect(request, pk)
    if ctx is None:
        return redirect("pdf_module:list")
    chunks = PDFChunk.objects.filter(context=ctx).order_by(
        "source_file", "page_number", "chunk_index"
    )[:100]
    return render(
        request,
        "pdf_module/partials/chunk_table.html",
        {"object": ctx, "chunks": chunks},
    )


@require_POST
@role_required(User.Role.SUPERUSER)
def pdf_context_save_ocr(request, pk):
    from apps.pdf_module.legacy_hindi import looks_like_legacy_hindi, normalize_legacy_hindi

    ctx = _get_owned_pdf_or_redirect(request, pk)
    if ctx is None:
        return redirect("pdf_module:list")
    text = request.POST.get("ocr_text", "")
    is_leg, ft = looks_like_legacy_hindi(text)
    if is_leg:
        text = normalize_legacy_hindi(text, force=True, font_type=ft or "krutidev")
    ctx.ocr_text = text
    ctx.status = "ready"
    ctx.needs_reindex = False
    ctx.error_message = ""
    ctx.save(update_fields=["ocr_text", "status", "needs_reindex", "error_message"])
    messages.success(request, "OCR text saved.")
    return redirect("pdf_module:detail", pk=pk)


@role_required(User.Role.SUPERUSER)
def pdf_context_reindex(request, pk):
    if request.method == "POST":
        ctx = _get_owned_pdf_or_redirect(request, pk)
        if ctx is None:
            return redirect("pdf_module:list")
        ctx.status = "pending"
        ctx.needs_reindex = True
        ctx.save(update_fields=["status", "needs_reindex"])
        index_pdf_context.delay(str(ctx.id))
        messages.info(request, "PDF re-OCR queued.")
        return redirect("pdf_module:detail", pk=pk)
    ctx = _get_owned_pdf_or_redirect(request, pk)
    if ctx is None:
        return redirect("pdf_module:list")
    return redirect("pdf_module:detail", pk=pk)


@role_required(User.Role.SUPERUSER)
def reindex_stale_pdfs(request):
    if request.method == "POST":
        stale = owned_pdf_contexts(request.user).filter(needs_reindex=True)
        count = stale.count()
        for context in stale:
            index_pdf_context.delay(str(context.id))
        messages.info(request, f"Queued {count} stale PDF contexts for reindexing.")
    return redirect("pdf_module:list")
