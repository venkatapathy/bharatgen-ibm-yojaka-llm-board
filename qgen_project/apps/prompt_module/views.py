from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView

from .models import PromptTemplate, PromptVersion
from apps.core.permissions import SuperUserRequiredMixin, role_required
from apps.core.models import User
from apps.core.soft404 import SoftMissingMixin


class PromptListView(SuperUserRequiredMixin, ListView):
    model = PromptTemplate
    template_name = 'prompt_module/prompt_list.html'
    context_object_name = 'prompts'


class PromptDetailView(SoftMissingMixin, SuperUserRequiredMixin, UpdateView):
    model = PromptTemplate
    template_name = 'prompt_module/prompt_editor.html'
    fields = ['name', 'description', 'topic_grounding', 'system_prompt', 'user_prompt']
    missing_message = "That prompt template is no longer available."
    missing_redirect = "prompt_module:list"

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


class PromptDeleteView(SoftMissingMixin, SuperUserRequiredMixin, DeleteView):
    model = PromptTemplate
    template_name = "prompt_module/prompt_confirm_delete.html"
    success_url = reverse_lazy('prompt_module:list')
    missing_message = "That prompt template is no longer available."
    missing_redirect = "prompt_module:list"


def _get_prompt_or_redirect(request, pk):
    tmpl = PromptTemplate.objects.filter(pk=pk).first()
    if tmpl is None:
        messages.info(request, "That prompt template is no longer available.")
        return None
    return tmpl


@role_required(User.Role.SUPERUSER)
def prompt_duplicate(request, pk):
    src = _get_prompt_or_redirect(request, pk)
    if src is None:
        return redirect("prompt_module:list")
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
        tmpl = _get_prompt_or_redirect(request, pk)
        if tmpl is None:
            return redirect("prompt_module:list")
        tmpl.is_active = True
        tmpl.save()
        messages.success(request, f'"{tmpl.name}" is now the active template.')
        return redirect('prompt_module:detail', pk=pk)
    tmpl = _get_prompt_or_redirect(request, pk)
    if tmpl is None:
        return redirect("prompt_module:list")
    return redirect('prompt_module:detail', pk=pk)


@role_required(User.Role.SUPERUSER)
def prompt_restore(request, pk, version):
    tmpl = _get_prompt_or_redirect(request, pk)
    if tmpl is None:
        return redirect("prompt_module:list")
    snap = PromptVersion.objects.filter(template=tmpl, version=version).first()
    if snap is None:
        messages.info(request, "That prompt version is no longer available.")
        return redirect("prompt_module:detail", pk=pk)
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
