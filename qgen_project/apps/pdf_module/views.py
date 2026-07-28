from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
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


class PDFContextListView(LoginRequiredMixin, ListView):
    model = PDFContext
    template_name = 'pdf_module/context_list.html'
    context_object_name = 'contexts'

    def _browse_params(self):
        org_id = (self.request.GET.get("org") or "").strip() or None
        user_id = (self.request.GET.get("user") or "").strip() or None
        return org_id, user_id

    def get_queryset(self):
        viewer = self.request.user
        org_id, user_id = self._browse_params()
        browse = browse_list_context(
            viewer, organization_id=org_id, user_id=user_id
        )
        if browse["needs_filter"] and browse["target"] is None:
            return PDFContext.objects.none()
        if browse["needs_filter"]:
            return owned_pdf_contexts(viewer, owner=browse["target"])
        return owned_pdf_contexts(viewer)

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
        ctx["show_chunks"] = bool(getattr(viewer, "is_superuser_role", False))
        ctx["mine_label"] = "My PDFs"
        return ctx


class PDFContextUploadView(LoginRequiredMixin, CreateView):
    model = PDFContext
    template_name = 'pdf_module/context_upload.html'
    form_class = PDFContextUploadForm
    success_url = reverse_lazy('pdf_module:list')

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
        description = form.cleaned_data.get("description") or ""
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
                        description=description,
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
                    # Store the individual PDF — never keep the parent ZIP.
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
            messages.success(self.request, "PDF context queued for indexing.")
        else:
            messages.success(
                self.request,
                f"{n} PDFs from ZIP queued for indexing (listed individually).",
            )
        return redirect(self.success_url)


class PDFContextDetailView(SoftMissingMixin, LoginRequiredMixin, DetailView):
    model = PDFContext
    template_name = 'pdf_module/context_detail.html'
    missing_message = "That PDF context is no longer available."
    missing_redirect = "pdf_module:list"

    def get_queryset(self):
        return owned_pdf_contexts(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        show_chunks = bool(getattr(self.request.user, "is_superuser_role", False))
        ctx["show_chunks"] = show_chunks
        ctx["chunks"] = (
            self.object.chunks.order_by("source_file", "page_number", "chunk_index")[:50]
            if show_chunks
            else []
        )
        return ctx


class PDFContextDeleteView(SoftMissingMixin, LoginRequiredMixin, DeleteView):
    model = PDFContext
    template_name = "pdf_module/context_confirm_delete.html"
    success_url = reverse_lazy('pdf_module:list')
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
    chunks = PDFChunk.objects.filter(context=ctx).order_by("source_file", "page_number", "chunk_index")[:100]
    return render(request, "pdf_module/partials/chunk_table.html", {"object": ctx, "chunks": chunks})


@login_required
def pdf_context_reindex(request, pk):
    if request.method == 'POST':
        ctx = _get_owned_pdf_or_redirect(request, pk)
        if ctx is None:
            return redirect("pdf_module:list")
        ctx.status = 'pending'
        ctx.needs_reindex = True
        ctx.save(update_fields=['status', 'needs_reindex'])
        index_pdf_context.delay(str(ctx.id))
        messages.info(request, "PDF context reindex queued.")
        return redirect('pdf_module:detail', pk=pk)
    ctx = _get_owned_pdf_or_redirect(request, pk)
    if ctx is None:
        return redirect("pdf_module:list")
    return redirect('pdf_module:detail', pk=pk)


@login_required
def reindex_stale_pdfs(request):
    if request.method == "POST":
        stale = owned_pdf_contexts(request.user).filter(needs_reindex=True)
        count = stale.count()
        for context in stale:
            index_pdf_context.delay(str(context.id))
        messages.info(request, f"Queued {count} stale PDF contexts for reindexing.")
    return redirect("pdf_module:list")
