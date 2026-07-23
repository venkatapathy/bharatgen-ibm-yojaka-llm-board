from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView

from .models import PromptTemplate, PromptVersion
from apps.core.permissions import SuperUserRequiredMixin, role_required
from apps.core.models import User


class PromptListView(SuperUserRequiredMixin, ListView):
    model = PromptTemplate
    template_name = 'prompt_module/prompt_list.html'
    context_object_name = 'prompts'


class PromptDetailView(SuperUserRequiredMixin, UpdateView):
    model = PromptTemplate
    template_name = 'prompt_module/prompt_editor.html'
    fields = ['name', 'description', 'topic_grounding', 'system_prompt', 'user_prompt']

    def form_valid(self, form):
        obj = form.save(commit=False)
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
        ctx['can_edit'] = True
        return ctx


class PromptCreateView(SuperUserRequiredMixin, CreateView):
    model = PromptTemplate
    template_name = 'prompt_module/prompt_editor.html'
    fields = ['name', 'description', 'topic_grounding', 'system_prompt', 'user_prompt']
    success_url = reverse_lazy('prompt_module:list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class PromptDeleteView(SuperUserRequiredMixin, DeleteView):
    model = PromptTemplate
    template_name = "prompt_module/prompt_confirm_delete.html"
    success_url = reverse_lazy('prompt_module:list')


@role_required(User.Role.SUPERUSER)
def prompt_duplicate(request, pk):
    src = get_object_or_404(PromptTemplate, pk=pk)
    src.pk     = None
    src.name   = f'{src.name} (copy)'
    src.version = 1
    src.is_active = False
    src.created_by = request.user
    src.save()
    return redirect('prompt_module:detail', pk=src.pk)


@role_required(User.Role.SUPERUSER)
def prompt_activate(request, pk):
    if request.method == 'POST':
        tmpl = get_object_or_404(PromptTemplate, pk=pk)
        tmpl.is_active = True
        tmpl.save()
        messages.success(request, f'"{tmpl.name}" is now the active template.')
    return redirect('prompt_module:detail', pk=pk)


@role_required(User.Role.SUPERUSER)
def prompt_restore(request, pk, version):
    tmpl    = get_object_or_404(PromptTemplate, pk=pk)
    snap    = get_object_or_404(PromptVersion, template=tmpl, version=version)
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
