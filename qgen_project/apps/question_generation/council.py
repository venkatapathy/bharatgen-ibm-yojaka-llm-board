"""Council-of-models verification for generated questions.

Each selected council model scores four dimensions:
  bloom, correctness, question_type, appropriate

A model *approves* only when all four dimensions pass.
A question is *kept* only when a strict majority of council models approve.
"""

from __future__ import annotations

import json
import logging
import re

import litellm

from apps.core.llm import get_litellm_kwargs
from apps.core.models import ModelConfig
from apps.core.provisioning import record_token_usage

logger = logging.getLogger(__name__)

COUNCIL_DIMENSIONS = ("bloom", "correctness", "question_type", "appropriate")

# Display name → default Ollama / LiteLLM id (demo council roster from screenshot)
COUNCIL_MODEL_SPECS = [
    {"name": "Gemma-E4B", "llm_model_id": "ollama/gemma2:4b", "provider": "ollama"},
    {"name": "Qwen3.5-4B", "llm_model_id": "ollama/qwen3.5:4b", "provider": "ollama"},
    {"name": "Phi4-mini-3.8B", "llm_model_id": "ollama/phi4-mini", "provider": "ollama"},
    {"name": "DeepSeek-R1-1.5B", "llm_model_id": "ollama/deepseek-r1:1.5b", "provider": "ollama"},
    {"name": "DeepSeek-R1-7B", "llm_model_id": "ollama/deepseek-r1:7b", "provider": "ollama"},
    {"name": "Granite4.1-8B", "llm_model_id": "ollama/granite3.1:8b", "provider": "ollama"},
]

VERIFY_SYSTEM = """You are an exam-question quality verifier.
Return ONLY valid JSON (no markdown, no thinking tags) with this exact shape:
{
  "bloom": true|false,
  "correctness": true|false,
  "question_type": true|false,
  "appropriate": true|false,
  "reasons": {
    "bloom": "short reason",
    "correctness": "short reason",
    "question_type": "short reason",
    "appropriate": "short reason"
  }
}

Rules:
- bloom: does the question match the target Bloom level?
- correctness: is the question answerable/sound and the answer/options correct?
- question_type: does the item match the requested question type (MCQ, SHORT, etc.)?
- appropriate: is the wording clear, fair, and suitable for the topic/audience?
"""


def ensure_council_models():
    """Upsert the demo council roster into ModelConfig and return selectable verifiers."""
    configs = []
    for spec in COUNCIL_MODEL_SPECS:
        obj, _created = ModelConfig.objects.get_or_create(
            name=spec["name"],
            defaults={
                "provider": spec["provider"],
                "llm_model_id": spec["llm_model_id"],
                "temperature": 0.1,
                "max_tokens": 512,
                "is_default": False,
            },
        )
        configs.append(obj)

    # Also offer chat LLMs already in the DB (e.g. Groq Qwen) so verification
    # works in demos that don't have the Ollama council models pulled yet.
    roster_ids = {c.pk for c in configs}
    extra = (
        ModelConfig.objects.exclude(pk__in=roster_ids)
        .exclude(llm_model_id="")
        .exclude(name__icontains="mpnet")
        .exclude(name__icontains="embed")
        .filter(provider__in=["ollama", "groq", "openai", "anthropic"])
        .order_by("name")
    )
    return list(configs) + list(extra)


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\|?think\|?>[\s\S]*?<\|?/think\|?>", "", text, flags=re.IGNORECASE)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text


def parse_verify_json(raw: str) -> dict:
    data = json.loads(_strip_fences(raw))
    if not isinstance(data, dict):
        raise ValueError("Verifier response was not a JSON object")
    dims = {}
    for key in COUNCIL_DIMENSIONS:
        dims[key] = bool(data.get(key))
    dims["reasons"] = data.get("reasons") or {}
    dims["approved"] = all(dims[key] for key in COUNCIL_DIMENSIONS)
    return dims


def _question_payload(question: dict, item) -> str:
    options = question.get("options") or []
    options_block = "\n".join(f"- {opt}" for opt in options) if options else "(none)"
    return (
        f"Topic: {question.get('topic') or ''}\n"
        f"Target question type: {item.question_type}\n"
        f"Target Bloom level: {item.bloom}\n"
        f"Marks: {item.marks}\n"
        f"Question: {question.get('question_text', '')}\n"
        f"Options:\n{options_block}\n"
        f"Reference answer: {question.get('reference_answer', '')}\n"
    )


def verify_with_model(question: dict, item, model_config: ModelConfig, user=None, batch_run=None) -> dict:
    """Ask one council model to score the four dimensions."""
    try:
        response = litellm.completion(
            messages=[
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": _question_payload(question, item)},
            ],
            **get_litellm_kwargs(model_config, temperature=0.1, max_tokens=512),
        )
        usage = getattr(response, "usage", None)
        if usage and user is not None:
            record_token_usage(
                user=user,
                batch_run=batch_run,
                provider=model_config.provider,
                model_name=model_config.llm_model_id,
                request_kind="council_verify",
                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            )
        raw = response.choices[0].message.content or ""
        result = parse_verify_json(raw)
        result["model"] = model_config.name
        result["model_id"] = model_config.pk
        result["error"] = ""
        return result
    except Exception as exc:
        logger.warning("Council verify failed for %s: %s", model_config.name, exc)
        return {
            "model": model_config.name,
            "model_id": model_config.pk,
            "bloom": False,
            "correctness": False,
            "question_type": False,
            "appropriate": False,
            "approved": False,
            "reasons": {"error": str(exc)},
            "error": str(exc),
        }


def majority_threshold(n_models: int) -> int:
    """Strict majority: more than half must approve."""
    return (n_models // 2) + 1


def filter_by_council(questions: list, item, council_models, user=None, batch_run=None) -> tuple[list, list]:
    """
    Verify each question with every council model.
    Returns (approved_questions, rejected_summaries).
    Approved questions have rubrics['council'] attached.
    """
    models = list(council_models)
    if not models:
        return questions, []

    need = majority_threshold(len(models))
    kept = []
    rejected = []

    for question in questions:
        votes = [
            verify_with_model(question, item, model, user=user, batch_run=batch_run)
            for model in models
        ]
        approvals = sum(1 for vote in votes if vote.get("approved"))
        passed = approvals >= need
        council_meta = {
            "enabled": True,
            "approvals": approvals,
            "total_models": len(models),
            "threshold": need,
            "majority_passed": passed,
            "votes": votes,
        }
        rubrics = dict(question.get("rubrics") or {})
        rubrics["council"] = council_meta
        question = {**question, "rubrics": rubrics}

        if passed:
            kept.append(question)
        else:
            rejected.append(
                {
                    "question_text": (question.get("question_text") or "")[:120],
                    "approvals": approvals,
                    "total_models": len(models),
                }
            )

    return kept, rejected
