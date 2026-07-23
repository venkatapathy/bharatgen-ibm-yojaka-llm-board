"""Council-of-models verification for generated questions.

Each selected council model scores four dimensions:
  bloom, correctness, question_type, appropriate

A model *approves* only when all four dimensions pass.
A question is *kept* when enough successful council votes approve
(at least half of models that cast a real vote).

Infrastructure / JSON failures abstain (excluded from the denominator).
If every model abstains, keep the question with a degraded council flag.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import time

from apps.core.llm import completion_with_retry, get_litellm_kwargs
from apps.core.models import ModelConfig
from apps.core.provisioning import record_token_usage

logger = logging.getLogger(__name__)

COUNCIL_DIMENSIONS = ("bloom", "correctness", "question_type", "appropriate")

# Prefer smaller / stabler tags first to reduce Ollama GPU thrash.
_MODEL_SIZE_HINT = (
    "1.5b",
    "2b",
    "3.8b",
    "3b",
    "4b",
    "7b",
    "8b",
    "e4b",
)

COUNCIL_MODEL_SPECS = [
    {"name": "Gemma-E4B", "llm_model_id": "ollama/gemma4:e4b", "provider": "ollama"},
    {"name": "Qwen3.5-4B", "llm_model_id": "ollama/qwen3.5:4b", "provider": "ollama"},
    {"name": "Phi4-mini-3.8B", "llm_model_id": "ollama/phi4-mini:3.8b", "provider": "ollama"},
    {"name": "DeepSeek-R1-1.5B", "llm_model_id": "ollama/deepseek-r1:1.5b", "provider": "ollama"},
    {"name": "DeepSeek-R1-7B", "llm_model_id": "ollama/deepseek-r1:7b", "provider": "ollama"},
    {"name": "Granite4.1-8B", "llm_model_id": "ollama/granite4.1:8b", "provider": "ollama"},
]

VERIFY_SYSTEM = """You verify one exam question. Reply with ONLY this JSON object:
{"bloom":true,"correctness":true,"question_type":true,"appropriate":true,"reasons":{"bloom":"ok","correctness":"ok","question_type":"ok","appropriate":"ok"}}

Approve (true) unless clearly wrong. Use JSON booleans only. No markdown."""


def _ollama_base_url() -> str:
    return (os.environ.get("OLLAMA_BASE_URL") or "http://10.129.6.47:11435").rstrip("/")


def _ollama_model_tag(model_config: ModelConfig) -> str:
    """Strip litellm 'ollama/' prefix → raw Ollama tag (e.g. phi4-mini:3.8b)."""
    tag = (model_config.llm_model_id or "").strip()
    if tag.startswith("ollama/"):
        tag = tag[len("ollama/") :]
    return tag


def load_ollama_model(tag: str) -> bool:
    """
    Explicitly load a model into GPU before inference (PhD guidance).
    Uses os.system so the demo backend drives load as a hard barrier before verify.
    """
    if not tag:
        return False
    base = _ollama_base_url()
    logger.info("Council GPU load: %s", tag)
    # os.system + inline Python: reliable inside Docker (no curl required).
    script = (
        "import json,urllib.request;"
        f"req=urllib.request.Request({base!r}+'/api/generate',data=json.dumps("
        f'{{"model":{tag!r},"prompt":"ping","stream":False,"keep_alive":"10m",'
        f'"think":False,"options":{{"num_predict":1}}}}'
        ").encode(),headers={'Content-Type':'application/json'},method='POST');"
        "urllib.request.urlopen(req,timeout=180).read()"
    )
    rc = os.system(f"python3 -c {shlex.quote(script)} >/dev/null 2>&1")
    ok = rc == 0
    if not ok:
        logger.warning("Council GPU load failed for %s (exit=%s)", tag, rc)
    time.sleep(2.0)
    return ok


def unload_ollama_model(tag: str) -> bool:
    """
    Explicitly free GPU by unloading the model after inference (PhD guidance).
    keep_alive=0 → Ollama unloads (done_reason=unload). Then optional `ollama stop`.
    """
    if not tag:
        return False
    base = _ollama_base_url()
    logger.info("Council GPU unload: %s", tag)
    script = (
        "import json,urllib.request;"
        f"req=urllib.request.Request({base!r}+'/api/generate',data=json.dumps("
        f'{{"model":{tag!r},"keep_alive":0}}'
        ").encode(),headers={'Content-Type':'application/json'},method='POST');"
        "urllib.request.urlopen(req,timeout=60).read()"
    )
    rc = os.system(f"python3 -c {shlex.quote(script)} >/dev/null 2>&1")

    host = base.replace("http://", "").replace("https://", "")
    os.system(
        f"OLLAMA_HOST={shlex.quote(host)} ollama stop {shlex.quote(tag)} >/dev/null 2>&1"
    )

    # Allow CUDA allocator to reclaim before the next model loads.
    time.sleep(3.0)
    ok = rc == 0
    if not ok:
        logger.warning("Council GPU unload may have failed for %s (exit=%s)", tag, rc)
    return ok


def ensure_council_models():
    """Upsert the fixed council roster (available in Admin to opt-in)."""
    configs = []
    for spec in COUNCIL_MODEL_SPECS:
        obj, _created = ModelConfig.objects.get_or_create(
            name=spec["name"],
            defaults={
                "provider": spec["provider"],
                "llm_model_id": spec["llm_model_id"],
                "temperature": 0.1,
                "max_tokens": 256,
                "is_default": False,
                "is_council_member": False,
            },
        )
        updates = []
        if obj.max_tokens > 256:
            obj.max_tokens = 256
            updates.append("max_tokens")
        if obj.temperature > 0.2:
            obj.temperature = 0.1
            updates.append("temperature")
        if updates:
            obj.save(update_fields=updates)
        configs.append(obj)
    return configs


def get_active_council_models():
    """
    Models admins marked as council members.
    Seeds roster rows if missing, but only returns is_council_member=True.
    """
    ensure_council_models()
    return list(
        ModelConfig.objects.filter(is_council_member=True).order_by("name")
    )


def _strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")


def _strip_fences(raw: str) -> str:
    text = _strip_control_chars(raw).strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\|?think\|?>[\s\S]*?<\|?/think\|?>", "", text, flags=re.IGNORECASE)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "pass", "approved", "ok"}
    return False


def parse_verify_json(raw: str) -> dict:
    text = _strip_fences(raw)
    candidates = [
        text,
        re.sub(r"\bTrue\b", "true", re.sub(r"\bFalse\b", "false", text)),
    ]
    data = None
    last_exc = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_exc = exc
    if data is None:
        raise last_exc or ValueError("Could not parse verifier JSON")
    if not isinstance(data, dict):
        raise ValueError("Verifier response was not a JSON object")
    dims = {key: _coerce_bool(data.get(key)) for key in COUNCIL_DIMENSIONS}
    dims["reasons"] = data.get("reasons") or {}
    dims["approved"] = all(dims[key] for key in COUNCIL_DIMENSIONS)
    return dims


def _question_payload(question: dict, item) -> str:
    options = question.get("options") or []
    options_block = "\n".join(f"- {opt}" for opt in options) if options else "(none)"
    return (
        f"Type={item.question_type} Bloom={item.bloom} Marks={item.marks}\n"
        f"Q: {(question.get('question_text') or '')[:600]}\n"
        f"Options:\n{options_block}\n"
        f"Answer: {(question.get('reference_answer') or '')[:200]}\n"
    )


def _size_rank(model_config: ModelConfig) -> int:
    tag = (model_config.llm_model_id or "").lower()
    for idx, hint in enumerate(_MODEL_SIZE_HINT):
        if hint in tag:
            return idx
    return len(_MODEL_SIZE_HINT)


def order_council_models(models):
    """Smaller models first to reduce Ollama swap failures."""
    return sorted(list(models), key=_size_rank)


def majority_threshold(n_models: int) -> int:
    """At least half of voting models must approve (tie = pass when even)."""
    if n_models <= 0:
        return 1
    return max(1, (n_models + 1) // 2)


def verify_with_model(question: dict, item, model_config: ModelConfig, user=None, batch_run=None) -> dict:
    """Ask one council model to score the four dimensions.

    For Ollama: explicitly load model → verify → unload (free GPU) so the next
    council model gets a clean VRAM slot (PhD guidance: load/free/load).
    """
    is_ollama = (model_config.provider or "").lower() == "ollama"
    tag = _ollama_model_tag(model_config) if is_ollama else ""
    try:
        if is_ollama and tag:
            load_ollama_model(tag)

        response = completion_with_retry(
            messages=[
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": _question_payload(question, item)},
            ],
            max_retries=4,
            base_wait=4.0,
            **get_litellm_kwargs(
                model_config,
                temperature=0.0,
                max_tokens=220,
                force_json=True,
            ),
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
                charge_credits=False,
            )
        raw = response.choices[0].message.content or ""
        if not raw.strip():
            msg = response.choices[0].message
            raw = getattr(msg, "thinking", None) or getattr(msg, "reasoning_content", None) or ""
        if not str(raw).strip():
            raise ValueError("Empty verifier response")
        result = parse_verify_json(str(raw))
        result["model"] = model_config.name
        result["model_id"] = model_config.pk
        result["error"] = ""
        result["abstain"] = False
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
            "abstain": True,
            "reasons": {"error": str(exc)[:240]},
            "error": str(exc)[:240],
        }
    finally:
        if is_ollama and tag:
            try:
                unload_ollama_model(tag)
            except Exception as unload_exc:
                logger.warning("Council unload failed for %s: %s", tag, unload_exc)


def filter_by_council(questions: list, item, council_models, user=None, batch_run=None) -> tuple[list, list]:
    """
    Verify each question with every council model.
    Returns (approved_questions, rejected_summaries).
    """
    models = order_council_models(council_models)
    if not models:
        return questions, []

    kept = []
    rejected = []

    for question in questions:
        votes = []
        for i, model in enumerate(models):
            # Explicit load/unload inside verify_with_model already frees GPU;
            # short pause only before switching to the next model tag.
            if i:
                time.sleep(1.0)
            votes.append(
                verify_with_model(question, item, model, user=user, batch_run=batch_run)
            )

        counted = [vote for vote in votes if not vote.get("abstain")]
        approvals = sum(1 for vote in counted if vote.get("approved"))
        if counted:
            need = majority_threshold(len(counted))
            passed = approvals >= need
            degraded = False
        else:
            need = 0
            passed = True
            degraded = True

        council_meta = {
            "enabled": True,
            "approvals": approvals,
            "total_models": len(models),
            "voted_models": len(counted),
            "abstentions": len(votes) - len(counted),
            "threshold": need,
            "majority_passed": passed,
            "degraded": degraded,
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
                    "voted_models": len(counted),
                    "threshold": need,
                }
            )

    return kept, rejected


def attach_forced_council_meta(questions: list, reason: str) -> list:
    """Keep questions with an explicit forced-keep council marker."""
    out = []
    for question in questions:
        rubrics = dict(question.get("rubrics") or {})
        existing = dict(rubrics.get("council") or {})
        existing.update(
            {
                "enabled": True,
                "majority_passed": False,
                "forced_keep": True,
                "degraded": True,
                "reason": reason,
            }
        )
        rubrics["council"] = existing
        out.append({**question, "rubrics": rubrics})
    return out
