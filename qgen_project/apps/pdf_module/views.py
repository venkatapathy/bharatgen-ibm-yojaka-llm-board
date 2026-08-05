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

from apps.core.ownership import (
    browse_list_context,
    owned_pdf_contexts,
)
from apps.core.soft404 import SoftMissingMixin
from apps.core.storage import (
    StorageQuotaExceeded,
    recompute_vector_storage,
    release_pdf_storage,
    reserve_pdf_storage,
    storage_quota_display,
)

from .forms import PDFContextUploadForm
from .models import PDFChunk, PDFContext
from .tasks import index_pdf_context
from .uploads import iter_upload_pdf_members

_COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6}[-\s]?\d{2,4})\b", re.IGNORECASE)


def _course_code_from_name(name: str) -> str:
    m = _COURSE_CODE_RE.search(name or "")
    if not m:
        return ""
    return re.sub(r"\s+", "-", m.group(1).upper().replace(" ", "-"))


def _pdf_file_url(ctx: PDFContext) -> str:
    """Same-origin stream URL (works behind ngrok; avoids /media iframe blocks)."""
    return reverse("pdf_module:file", kwargs={"pk": ctx.pk})


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
        return fixed

    try:
        rebuild_context_ocr(ctx, force_vision=False)
        ctx.refresh_from_db()
        if (ctx.ocr_text or "").strip():
            return ctx.ocr_text
    except Exception:
        pass

    if stored:
        return _normalize(stored)
    return ""


class PDFContextListView(LoginRequiredMixin, ListView):
    model = PDFContext
    template_name = "pdf_module/context_list.html"
    context_object_name = "contexts"

    def _browse_params(self):
        org_id = (self.request.GET.get("org") or "").strip() or None
        user_id = (self.request.GET.get("user") or "").strip() or None
        return org_id, user_id

    def _filter_params(self):
        q = (self.request.GET.get("q") or "").strip()
        status = (self.request.GET.get("status") or "").strip().lower()
        course = (self.request.GET.get("course") or "").strip().upper()
        return q, status, course

    def _base_queryset(self):
        viewer = self.request.user
        org_id, user_id = self._browse_params()
        browse = browse_list_context(
            viewer, organization_id=org_id, user_id=user_id
        )
        if browse["needs_filter"] and browse["target"] is None:
            return PDFContext.objects.none(), browse
        if browse["needs_filter"]:
            return owned_pdf_contexts(viewer, owner=browse["target"]), browse
        return owned_pdf_contexts(viewer), browse

    def get_queryset(self):
        qs, _browse = self._base_queryset()
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
            # Match "BHDC-133", "BHDC 133", "bhdc-133" in name/filename.
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
        org_id, user_id = self._browse_params()
        browse = browse_list_context(
            viewer, organization_id=org_id, user_id=user_id
        )
        ctx.update(browse)

        quota_user = browse["target"] or viewer
        recompute_vector_storage(quota_user)
        ctx["quota"] = storage_quota_display(quota_user)
        ctx["mine_label"] = "My PDFs"

        q, status, course = self._filter_params()
        base_qs, _ = self._base_queryset()
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
            }
        )
        return ctx


class PDFContextUploadView(LoginRequiredMixin, CreateView):
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
            index_pdf_context.delay(str(context_id))

        n = len(created_ids)
        if n == 1:
            messages.success(self.request, "PDF context queued for OCR indexing.")
        else:
            messages.success(
                self.request,
                f"{n} PDFs from ZIP queued for OCR indexing (listed individually).",
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
        ctx["pdf_url"] = _pdf_file_url(self.object)
        ctx["ocr_text"] = _ensure_ocr_text(self.object)
        return ctx


class PDFContextDeleteView(SoftMissingMixin, LoginRequiredMixin, DeleteView):
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
    # Sanitize filename for header
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
    if not getattr(request.user, "is_superuser_role", False):
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


@login_required
@require_POST
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
    ctx.save(update_fields=["ocr_text"])
    messages.success(request, "OCR text saved.")
    return redirect("pdf_module:detail", pk=pk)


@login_required
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


@login_required
def reindex_stale_pdfs(request):
    if request.method == "POST":
        stale = owned_pdf_contexts(request.user).filter(needs_reindex=True)
        count = stale.count()
        for context in stale:
            index_pdf_context.delay(str(context.id))
        messages.info(request, f"Queued {count} stale PDF contexts for reindexing.")
    return redirect("pdf_module:list")
