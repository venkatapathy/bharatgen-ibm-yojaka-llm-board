from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from .models import PromptTemplate, PromptVersion
from apps.core.permissions import OrgUserRequiredMixin


class PromptListView(LoginRequiredMixin, ListView):
    model = PromptTemplate
    template_name = 'prompt_module/prompt_list.html'
    context_object_name = 'prompts'


class PromptDetailView(OrgUserRequiredMixin, UpdateView):
    model = PromptTemplate
    template_name = 'prompt_module/prompt_editor.html'
    fields = ['name', 'description', 'system_prompt', 'user_prompt']

    def form_valid(self, form):
        obj = form.save(commit=False)
        # Save current version to history before bumping
        PromptVersion.objects.create(
            template=obj,
            version=obj.version,
            system_prompt=obj.system_prompt,
            user_prompt=obj.user_prompt,
            saved_by=self.request.user,
        )
        obj.version += 1
        obj.save()
        messages.success(self.request, f'Saved as version {obj.version}.')
        return redirect('prompt_module:detail', pk=obj.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['history'] = self.object.history.all()
        return ctx


class PromptCreateView(OrgUserRequiredMixin, CreateView):
    model = PromptTemplate
    template_name = 'prompt_module/prompt_editor.html'
    fields = ['name', 'description', 'system_prompt', 'user_prompt']
    success_url = reverse_lazy('prompt_module:list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class PromptDeleteView(OrgUserRequiredMixin, DeleteView):
    model = PromptTemplate
    success_url = reverse_lazy('prompt_module:list')


def prompt_duplicate(request, pk):
    src = get_object_or_404(PromptTemplate, pk=pk)
    src.pk     = None
    src.name   = f'{src.name} (copy)'
    src.version = 1
    src.is_active = False
    src.created_by = request.user
    src.save()
    return redirect('prompt_module:detail', pk=src.pk)


def prompt_activate(request, pk):
    if request.method == 'POST':
        tmpl = get_object_or_404(PromptTemplate, pk=pk)
        tmpl.is_active = True
        tmpl.save()
        messages.success(request, f'"{tmpl.name}" is now the active template.')
    return redirect('prompt_module:detail', pk=pk)


def prompt_restore(request, pk, version):
    tmpl    = get_object_or_404(PromptTemplate, pk=pk)
    snap    = get_object_or_404(PromptVersion, template=tmpl, version=version)
    # Save current as history
    PromptVersion.objects.create(
        template=tmpl, version=tmpl.version,
        system_prompt=tmpl.system_prompt, user_prompt=tmpl.user_prompt,
        saved_by=request.user,
    )
    tmpl.system_prompt = snap.system_prompt
    tmpl.user_prompt   = snap.user_prompt
    tmpl.version      += 1
    tmpl.save()
    messages.success(request, f'Restored to v{version}.')
    return redirect('prompt_module:detail', pk=pk)
