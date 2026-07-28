from django.db import models
from apps.core.models import User


DEFAULT_SYSTEM_PROMPT = """\
You are an expert exam question setter following Bloom's Taxonomy.
Generate exactly {{ count }} questions of type {{ question_type }} at
Bloom's level {{ bloom }}. Each question must be worth {{ marks }} marks.
{% if context_chunks %}
IMPORTANT: Base every question ONLY on the reference material below.
Questions must be answerable from that material alone.
Do NOT invent a subject around any batch/run label, and do NOT put such a label in questions.

Reference material:
{{ context_chunks }}
{% else %}
Generate questions for the subject area: {{ topic }}.
If the topic is not a clear real academic subject, return an empty JSON array [].
{% endif %}
{% if pyq_examples %}
Match the difficulty, style, and format of these example questions (use new content, not copies):
{{ pyq_examples }}
{% endif %}
Return a JSON array with EXACTLY {{ count }} elements. Each element has:
  question_text, reference_answer, rubrics.
For MCQ questions you MUST also include:
  options: an array of exactly 4 objects, each with "label" (A/B/C/D) and "text".
  reference_answer: the correct option label (e.g. "B") or full option text.
Do not prefix question_text with "[MCQ]".
The array length must equal {{ count }} — no more, no less.
"""

DEFAULT_USER_PROMPT = """\
{% if context_chunks %}
Generate {{ count }} {{ question_type }} question(s) from the reference material
at Bloom's {{ bloom }} level, {{ marks }} marks each.
Do not mention any batch/run name in the questions.
{% else %}
Generate {{ count }} {{ question_type }} question(s) on "{{ topic }}"
at Bloom's {{ bloom }} level, {{ marks }} marks each.
{% endif %}
{% if question_type == 'MCQ' %}
Each MCQ must have exactly 4 labelled options (A, B, C, D) and a clear correct answer.
{% endif %}
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
