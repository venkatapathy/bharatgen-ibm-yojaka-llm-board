"""Probe whether generation / Think models are currently usable."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def _ollama_base_url() -> str:
    return (os.environ.get("OLLAMA_BASE_URL") or "http://10.129.7.47:11434").rstrip("/")


def fetch_ollama_model_tags(*, timeout: float = 3.0) -> set[str]:
    """Return installed Ollama model names (e.g. deepseek-r1:32b). Empty on failure."""
    url = f"{_ollama_base_url()}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("Ollama tags probe failed (%s): %s", url, exc)
        return set()

    tags: set[str] = set()
    for item in data.get("models") or []:
        name = (item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        tags.add(name)
        # Also accept tag without :latest
        if name.endswith(":latest"):
            tags.add(name[: -len(":latest")])
    return tags


def ollama_tag_from_llm_id(llm_model_id: str) -> str:
    tag = (llm_model_id or "").strip()
    if tag.startswith("ollama/"):
        tag = tag[len("ollama/") :]
    return tag


def is_ollama_model_available(llm_model_id: str, installed: set[str]) -> bool:
    if not installed:
        return False
    tag = ollama_tag_from_llm_id(llm_model_id)
    if not tag:
        return False
    if tag in installed:
        return True
    # Match base name if either side omits a tag
    base = tag.split(":", 1)[0]
    for name in installed:
        if name == tag or name.split(":", 1)[0] == base:
            return True
    return False


def annotate_model_availability(models, *, openai_key_set: bool, gemini_key_set: bool):
    """
    Attach .is_available / .availability_label on each ModelConfig instance
    for the Technical settings UI (not persisted).
    """
    ollama_tags = fetch_ollama_model_tags()
    ollama_reachable = bool(ollama_tags) or _ollama_ping()

    for model in models:
        provider = (model.provider or "").lower()
        if provider == "ollama":
            ok = is_ollama_model_available(model.llm_model_id, ollama_tags)
            model.is_available = ok
            if not ollama_reachable and not ollama_tags:
                model.availability_label = "Ollama unreachable"
            elif ok:
                model.availability_label = "Available"
            else:
                model.availability_label = "Not on Ollama"
        elif provider == "openai":
            model.is_available = bool(openai_key_set)
            model.availability_label = "Key set" if openai_key_set else "No API key"
        elif provider in {"gemini", "google"}:
            model.is_available = bool(gemini_key_set)
            model.availability_label = "Key set" if gemini_key_set else "No API key"
        else:
            model.is_available = True
            model.availability_label = "Unknown"
    return models


def _ollama_ping(*, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{_ollama_base_url()}/api/version", timeout=timeout):
            return True
    except Exception:
        return False


def model_status_snapshot():
    """JSON-ready availability for Technical settings refresh button."""
    from apps.core.model_lists import generation_models_by_source
    from apps.core.models import GenerationSettings, ModelConfig
    from apps.question_generation.council import COUNCIL_MODEL_SPECS, ensure_council_models

    ensure_council_models()
    gs = GenerationSettings.load()
    openai_key_set = bool((gs.openai_api_key or "").strip())
    gemini_key_set = bool((gs.gemini_api_key or "").strip())
    ollama_models, api_models = generation_models_by_source()
    think_names = [spec["name"] for spec in COUNCIL_MODEL_SPECS]
    think_models = list(
        ModelConfig.objects.filter(name__in=think_names)
        .exclude(llm_model_id="")
        .order_by("name")
    )
    all_models = list(ollama_models) + list(api_models) + think_models
    annotate_model_availability(
        all_models,
        openai_key_set=openai_key_set,
        gemini_key_set=gemini_key_set,
    )
    return {
        "ollama_reachable": _ollama_ping(),
        "openai_key_set": openai_key_set,
        "gemini_key_set": gemini_key_set,
        "models": {
            str(m.id): {
                "available": bool(getattr(m, "is_available", False)),
                "label": getattr(m, "availability_label", ""),
                "provider": (m.provider or "").lower(),
            }
            for m in all_models
        },
    }
