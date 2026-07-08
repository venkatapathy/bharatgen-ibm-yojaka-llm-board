"""LiteLLM request helpers."""

import os


def get_litellm_kwargs(model_config, *, temperature=None, max_tokens=None):
    provider = (model_config.provider or "").lower()
    model_name = model_config.llm_model_id
    kwargs = {
        "temperature": model_config.temperature if temperature is None else temperature,
        "max_tokens": model_config.max_tokens if max_tokens is None else max_tokens,
    }

    if provider == "groq":
        kwargs["model"] = model_name if model_name.startswith("groq/") else f"groq/{model_name}"
        api_key = os.environ.get(model_config.api_key_env_var or "GROQ_API_KEY")
        if api_key:
            kwargs["api_key"] = api_key
    elif provider == "ollama":
        kwargs["model"] = model_name if model_name.startswith("ollama/") else f"ollama/{model_name}"
        kwargs["api_base"] = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    else:
        kwargs["model"] = model_name
        api_key = os.environ.get(model_config.api_key_env_var or "")
        if api_key:
            kwargs["api_key"] = api_key

    return kwargs
