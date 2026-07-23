from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView

from apps.core.models import ModelConfig
from apps.core.ownership import owned_pdf_contexts
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


class PDFContextListView(LoginRequiredMixin, ListView):
    model = PDFContext
    template_name = 'pdf_module/context_list.html'
    context_object_name = 'contexts'

    def get_queryset(self):
        return owned_pdf_contexts(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Keep vector storage card accurate from live chunk embeddings.
        recompute_vector_storage(self.request.user)
        ctx["quota"] = storage_quota_display(self.request.user)
        user = self.request.user
        ctx["show_owner"] = bool(
            getattr(user, "is_superuser", False)
            or getattr(user, "is_orguser", False)
        )
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
        form.instance.created_by = self.request.user
        form.instance.organization = self.request.user.organization
        try:
            with transaction.atomic():
                response = super().form_valid(form)
                reserve_pdf_storage(self.request.user, uploaded.size)
        except StorageQuotaExceeded as exc:
            if self.object and self.object.zip_path:
                self.object.zip_path.delete(save=False)
            messages.error(self.request, str(exc))
            return self.form_invalid(form)
        index_pdf_context.delay(str(self.object.id))
        messages.success(self.request, "PDF context queued for indexing.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["embed_configs"] = ModelConfig.objects.exclude(embed_model_id="")
        ctx["reranker_configs"] = ModelConfig.objects.exclude(reranker_model="")
        return ctx


class PDFContextDetailView(LoginRequiredMixin, DetailView):
    model = PDFContext
    template_name = 'pdf_module/context_detail.html'

    def get_queryset(self):
        return owned_pdf_contexts(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["chunks"] = self.object.chunks.order_by("source_file", "page_number", "chunk_index")[:50]
        return ctx


class PDFContextDeleteView(LoginRequiredMixin, DeleteView):
    model = PDFContext
    template_name = "pdf_module/context_confirm_delete.html"
    success_url = reverse_lazy('pdf_module:list')

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


@login_required
def pdf_context_status(request, pk):
    ctx = get_object_or_404(owned_pdf_contexts(request.user), pk=pk)
    return render(request, "pdf_module/partials/context_status.html", {"object": ctx})


@login_required
def pdf_context_chunks(request, pk):
    ctx = get_object_or_404(owned_pdf_contexts(request.user), pk=pk)
    chunks = PDFChunk.objects.filter(context=ctx).order_by("source_file", "page_number", "chunk_index")[:100]
    return render(request, "pdf_module/partials/chunk_table.html", {"object": ctx, "chunks": chunks})


@login_required
def pdf_context_reindex(request, pk):
    if request.method == 'POST':
        ctx = get_object_or_404(owned_pdf_contexts(request.user), pk=pk)
        ctx.status = 'pending'
        ctx.needs_reindex = True
        ctx.save(update_fields=['status', 'needs_reindex'])
        index_pdf_context.delay(str(ctx.id))
        messages.info(request, "PDF context reindex queued.")
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
