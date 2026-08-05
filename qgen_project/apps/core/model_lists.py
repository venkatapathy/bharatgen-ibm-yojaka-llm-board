"""Split Generation vs Think (council) model catalogues."""

from apps.core.models import ModelConfig

# Generation-only models (large). Think/council uses <8B roster separately.
GENERATION_LLM_IDS = (
    "ollama/deepseek-r1:32b",
    "ollama/gemma4:31b",
    "ollama/granite4.1:30b",
    "ollama/granite4.1:8b",
)


def generation_model_queryset():
    return (
        ModelConfig.objects.filter(llm_model_id__in=GENERATION_LLM_IDS)
        .exclude(llm_model_id="")
        .order_by("name")
    )
