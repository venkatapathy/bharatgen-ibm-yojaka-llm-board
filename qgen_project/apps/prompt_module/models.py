from django.db import models
from apps.core.models import User

DEFAULT_SYSTEM_PROMPT = """\
You are an expert exam question setter following Bloom's Taxonomy.
Generate exactly {{ count }} questions of type {{ question_type }} at
Bloom's level {{ bloom }} for the topic: {{ topic }}.
Each question must be worth {{ marks }} marks.
{% if context_chunks %}
Use the following reference material:
{{ context_chunks }}
{% endif %}
{% if pyq_examples %}
Model your style on these example questions:
{{ pyq_examples }}
{% endif %}
Return a JSON array where each element has:
  question_text, reference_answer, rubrics.
"""

DEFAULT_USER_PROMPT = """\
Generate {{ count }} {{ question_type }} questions on "{{ topic }}" at
Bloom's {{ bloom }} level, {{ marks }} marks each.
"""


class PromptTemplate(models.Model):
    name          = models.CharField(max_length=256, unique=True)
    description   = models.TextField(blank=True)
    topic_grounding = models.TextField(blank=True)
    system_prompt = models.TextField(default=DEFAULT_SYSTEM_PROMPT)
    user_prompt   = models.TextField(default=DEFAULT_USER_PROMPT)
    version       = models.IntegerField(default=1)
    is_active     = models.BooleanField(default=False)
    created_by    = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                      related_name='prompts')
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.name} (v{self.version})'

    def save(self, *args, **kwargs):
        if self.is_active:
            PromptTemplate.objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)


class PromptVersion(models.Model):
    """Immutable snapshot of a PromptTemplate at a specific version."""
    template      = models.ForeignKey(PromptTemplate, on_delete=models.CASCADE,
                                      related_name='history')
    version       = models.IntegerField()
    system_prompt = models.TextField()
    user_prompt   = models.TextField()
    saved_by      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    saved_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('template', 'version')
        ordering        = ['-version']

    def __str__(self):
        return f'{self.template.name} v{self.version}'
