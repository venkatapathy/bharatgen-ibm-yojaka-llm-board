from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, DeleteView
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.http import JsonResponse
from .models import PDFContext
from .tasks import index_pdf_context


class PDFContextListView(LoginRequiredMixin, ListView):
    model = PDFContext
    template_name = 'pdf_module/context_list.html'
    context_object_name = 'contexts'

    def get_queryset(self):
        return PDFContext.objects.filter(organization=self.request.user.organization)


class PDFContextUploadView(LoginRequiredMixin, CreateView):
    model = PDFContext
    template_name = 'pdf_module/context_upload.html'
    fields = ['name', 'description', 'zip_path', 'strategy', 'chunk_size',
              'chunk_overlap', 'embed_model', 'reranker_model']
    success_url = reverse_lazy('pdf_module:list')

    def form_valid(self, form):
        form.instance.created_by   = self.request.user
        form.instance.organization = self.request.user.organization
        response = super().form_valid(form)
        index_pdf_context.delay(str(self.object.id))
        return response


class PDFContextDetailView(LoginRequiredMixin, DetailView):
    model = PDFContext
    template_name = 'pdf_module/context_detail.html'

    def get_queryset(self):
        return PDFContext.objects.filter(organization=self.request.user.organization)


class PDFContextDeleteView(LoginRequiredMixin, DeleteView):
    model = PDFContext
    success_url = reverse_lazy('pdf_module:list')

    def get_queryset(self):
        return PDFContext.objects.filter(created_by=self.request.user)


def pdf_context_status(request, pk):
    ctx = get_object_or_404(PDFContext, pk=pk, organization=request.user.organization)
    return JsonResponse({'status': ctx.status, 'chunk_count': ctx.chunk_count})


def pdf_context_reindex(request, pk):
    if request.method == 'POST':
        ctx = get_object_or_404(PDFContext, pk=pk, created_by=request.user)
        ctx.status = 'pending'
        ctx.save(update_fields=['status'])
        index_pdf_context.delay(str(ctx.id))
    return redirect('pdf_module:detail', pk=pk)
