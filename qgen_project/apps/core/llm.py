"""LiteLLM request helpers with rate-limit backoff."""

from __future__ import annotations

import logging
import os
import re
import time

import litellm

logger = logging.getLogger(__name__)

_RETRY_SECONDS_RE = re.compile(
    r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*s",
    re.IGNORECASE,
)

# Models that dump chain-of-thought into `thinking` and leave `content` empty
# unless think/reasoning is disabled.
_REASONING_MODEL_MARKERS = (
    "qwen3",
    "qwen3.5",
    "qwen3.6",
    "deepseek-r1",
)


def _api_key_from_generation_settings(provider: str) -> str:
    """Admin-entered keys on GenerationSettings (Technical settings)."""
    provider = (provider or "").lower()
    try:
        from apps.core.models import GenerationSettings

        gs = GenerationSettings.load()
    except Exception:
        return ""
    if provider == "openai":
        return (gs.openai_api_key or "").strip()
    if provider in {"gemini", "google"}:
        return (gs.gemini_api_key or "").strip()
    if provider == "groq":
        return ""
    return ""


def get_litellm_kwargs(model_config, *, temperature=None, max_tokens=None, force_json=False):
    provider = (model_config.provider or "").lower()
    model_name = model_config.llm_model_id
    kwargs = {
        "temperature": model_config.temperature if temperature is None else temperature,
        "max_tokens": model_config.max_tokens if max_tokens is None else max_tokens,
        # Long generations through the host Ollama proxy can exceed defaults.
        "timeout": int(os.environ.get("LITELLM_TIMEOUT", "300")),
    }

    if provider == "groq":
        kwargs["model"] = model_name if model_name.startswith("groq/") else f"groq/{model_name}"
        api_key = (
            _api_key_from_generation_settings("groq")
            or os.environ.get(model_config.api_key_env_var or "GROQ_API_KEY")
        )
        if api_key:
            kwargs["api_key"] = api_key
    elif provider == "ollama":
        kwargs["model"] = model_name if model_name.startswith("ollama/") else f"ollama/{model_name}"
        kwargs["api_base"] = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
        # Keep one request at a time stable: disable thinking + optional JSON mode.
        extra_body = {}
        if _is_reasoning_model(model_name):
            extra_body["think"] = False
        if force_json:
            extra_body["format"] = "json"
        # Soft per-request options so Ollama doesn't hang forever under load.
        options = {
            "num_predict": kwargs["max_tokens"],
            "temperature": kwargs["temperature"],
        }
        extra_body["options"] = options
        if extra_body:
            kwargs["extra_body"] = extra_body
    elif provider == "openai":
        kwargs["model"] = (
            model_name if model_name.startswith("openai/") else f"openai/{model_name}"
        )
        api_key = (
            _api_key_from_generation_settings("openai")
            or os.environ.get(model_config.api_key_env_var or "OPENAI_API_KEY")
        )
        if api_key:
            kwargs["api_key"] = api_key
        if force_json:
            kwargs["response_format"] = {"type": "json_object"}
    elif provider in {"gemini", "google"}:
        # LiteLLM uses gemini/<model> with Google AI Studio keys.
        if model_name.startswith("gemini/") or model_name.startswith("google/"):
            kwargs["model"] = model_name
        else:
            kwargs["model"] = f"gemini/{model_name}"
        api_key = (
            _api_key_from_generation_settings("gemini")
            or os.environ.get(model_config.api_key_env_var or "GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if api_key:
            kwargs["api_key"] = api_key
    else:
        kwargs["model"] = model_name
        api_key = (
            _api_key_from_generation_settings(provider)
            or os.environ.get(model_config.api_key_env_var or "")
        )
        if api_key:
            kwargs["api_key"] = api_key

    return kwargs


def _is_reasoning_model(model_name: str) -> bool:
    lower = (model_name or "").lower()
    return any(marker in lower for marker in _REASONING_MODEL_MARKERS)


def is_rate_limit_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        "ratelimit" in name
        or "rate_limit" in text
        or "rate limit" in text
        or "tokens per minute" in text
        or "tpm" in text and "limit" in text
    )


def parse_retry_after_seconds(exc: BaseException, default: float = 45.0) -> float:
    match = _RETRY_SECONDS_RE.search(str(exc))
    if match:
        return min(float(match.group(1)) + 2.0, 120.0)
    return default


def is_transient_llm_error(exc: BaseException) -> bool:
    if is_rate_limit_error(exc):
        return True
    text = str(exc).lower()
    return (
        "server disconnected" in text
        or "connection reset" in text
        or "connection refused" in text
        or "eof" in text
        or "cuda error" in text
        or "busy or unavailable" in text
        or "timeout" in text
        or "timed out" in text
    )


def completion_with_retry(
    *,
    messages,
    max_retries: int = 6,
    base_wait: float = 5.0,
    **kwargs,
):
    """
    Call litellm.completion with backoff on Groq TPM / Ollama disconnects.
    Also recovers content from reasoning models that fill `thinking` and leave
    `message.content` empty (e.g. Qwen3 via Ollama when think isn't disabled).
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = litellm.completion(messages=messages, **kwargs)
            return normalize_completion_response(response)
        except Exception as exc:
            last_exc = exc
            if not is_transient_llm_error(exc) or attempt >= max_retries - 1:
                raise
            if is_rate_limit_error(exc):
                wait = parse_retry_after_seconds(exc, default=base_wait * (2 ** attempt))
            else:
                # Cap Ollama backoff — long sleeps stack badly with council verification.
                wait = min(base_wait * (2 ** attempt), 20.0)
            wait = max(wait, base_wait)
            logger.warning(
                "Transient LLM error (attempt %s/%s). Sleeping %.1fs. Error: %s",
                attempt + 1,
                max_retries,
                wait,
                str(exc)[:240],
            )
            time.sleep(wait)
    raise last_exc


def normalize_completion_response(response):
    """Ensure choices[0].message.content is usable for reasoning models."""
    try:
        choice = response.choices[0]
        message = choice.message
        content = (getattr(message, "content", None) or "").strip()
        if content:
            return response

        thinking = (
            getattr(message, "thinking", None)
            or getattr(message, "reasoning_content", None)
            or ""
        )
        if isinstance(thinking, str) and thinking.strip():
            lines = [ln.strip() for ln in thinking.strip().splitlines() if ln.strip()]
            recovered = lines[-1] if lines else thinking.strip()
            if "{" in thinking and "}" in thinking:
                recovered = thinking.strip()
            message.content = recovered
            logger.info(
                "Recovered empty content from reasoning/thinking field (%s chars)",
                len(recovered),
            )
    except Exception as exc:
        logger.debug("normalize_completion_response skipped: %s", exc)
    return response
