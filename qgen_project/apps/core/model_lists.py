"""Split Generation vs Think (council) model catalogues."""

from apps.core.models import ModelConfig

# Generation-only models (large local + a few cloud defaults).
GENERATION_LLM_IDS = (
    "ollama/deepseek-r1:32b",
    "ollama/gemma4:31b",
    "ollama/granite4.1:30b",
    "ollama/granite4.1:8b",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "gemini/gemini-2.0-flash",
    "gemini/gemini-2.5-flash",
)

CLOUD_GENERATION_SPECS = (
    {
        "name": "GPT-4o",
        "provider": "openai",
        "llm_model_id": "openai/gpt-4o",
        "max_tokens": 4096,
    },
    {
        "name": "GPT-4o Mini",
        "provider": "openai",
        "llm_model_id": "openai/gpt-4o-mini",
        "max_tokens": 4096,
    },
    {
        "name": "Gemini 2.0 Flash",
        "provider": "gemini",
        "llm_model_id": "gemini/gemini-2.0-flash",
        "max_tokens": 4096,
    },
    {
        "name": "Gemini 2.5 Flash",
        "provider": "gemini",
        "llm_model_id": "gemini/gemini-2.5-flash",
        "max_tokens": 4096,
    },
)


def ensure_cloud_generation_models():
    """Create GPT / Gemini ModelConfig rows used by Technical settings."""
    for spec in CLOUD_GENERATION_SPECS:
        obj, created = ModelConfig.objects.get_or_create(
            name=spec["name"],
            defaults={
                "provider": spec["provider"],
                "llm_model_id": spec["llm_model_id"],
                "temperature": 0.7,
                "max_tokens": spec["max_tokens"],
                "is_default": False,
                "is_council_member": False,
            },
        )
        updates = []
        if obj.provider != spec["provider"]:
            obj.provider = spec["provider"]
            updates.append("provider")
        if obj.llm_model_id != spec["llm_model_id"]:
            obj.llm_model_id = spec["llm_model_id"]
            updates.append("llm_model_id")
        if obj.is_council_member:
            obj.is_council_member = False
            updates.append("is_council_member")
        if updates:
            obj.save(update_fields=updates)


def generation_model_queryset():
    ensure_cloud_generation_models()
    return (
        ModelConfig.objects.filter(llm_model_id__in=GENERATION_LLM_IDS)
        .exclude(llm_model_id="")
        .order_by("provider", "name")
    )


def generation_models_by_source():
    """Split generation catalogue into local Ollama vs cloud API models."""
    qs = list(generation_model_queryset())
    ollama = [m for m in qs if (m.provider or "").lower() == "ollama"]
    api = [
        m
        for m in qs
        if (m.provider or "").lower() in {"openai", "gemini", "google", "groq"}
    ]
    return ollama, api
