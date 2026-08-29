"""Provider-aware model adapter for the legacy MM-Bench evaluator."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, Optional, Sequence, Tuple

try:
    import openai
except ImportError:  # Keep routing helpers testable in minimal environments.
    openai = None


logger = logging.getLogger(__name__)
completion_tokens = prompt_tokens = 0

MODEL_ALIASES = {
    "gpt5.6sol": "gpt-5.6-sol",
    "gpt-5.6": "gpt-5.6-sol",
    "deepseekv4pro": "deepseek-v4-pro",
}
REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}


def normalize_model_name(model: str) -> str:
    value = (model or "").strip()
    return MODEL_ALIASES.get(value.lower(), value)


def _is_deepseek(model: str, base_url: str = "") -> bool:
    model_id = normalize_model_name(model).lower().rsplit("/", 1)[-1]
    if model_id.startswith(("gpt-", "o1", "o3", "o4")):
        return False
    return model_id.startswith("deepseek-") or "deepseek" in (base_url or "").lower()


def _is_reasoning_model(model: str) -> bool:
    model_id = normalize_model_name(model).lower().rsplit("/", 1)[-1]
    return model_id.startswith(("gpt-5", "o1", "o3", "o4", "deepseek-v4")) or (
        model_id == "deepseek-reasoner"
    )


def resolve_provider_config(
    model: str,
    base_url: Optional[str] = None,
    key: Optional[str] = None,
) -> Tuple[str, str, str, str]:
    """Return normalized model, provider, endpoint and provider-bound key."""
    normalized = normalize_model_name(model or "gpt-5.6-sol")
    deepseek = _is_deepseek(normalized, base_url or "")
    provider = "deepseek" if deepseek else "openai"
    if deepseek:
        selected_base = base_url
        if not selected_base or selected_base.rstrip("/") == "https://api.openai.com/v1":
            selected_base = os.getenv(
                "DEEPSEEK_BASE_URL",
                os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
            )
        selected_key = key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("MMAGENT_API_KEY")
    else:
        selected_base = base_url
        if not selected_base or "api.deepseek.com" in selected_base.lower():
            selected_base = os.getenv(
                "OPENAI_BASE_URL",
                os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
            )
        selected_key = key or os.getenv("OPENAI_API_KEY") or os.getenv("MMAGENT_API_KEY")
    if not selected_key:
        raise ValueError(
            f"No API key configured for {provider}; set the provider environment variable"
        )
    return normalized, provider, selected_base.rstrip("/"), selected_key


def build_completion_params(
    messages: Sequence[Dict[str, str]],
    model: str,
    *,
    temperature: Optional[float] = 0.7,
    max_tokens: int = 1000,
    count: int = 1,
    stop: Optional[Sequence[str] | str] = None,
    top_p: Optional[float] = 1.0,
    reasoning_effort: Optional[str] = None,
) -> Dict[str, Any]:
    """Build Chat Completions parameters without incompatible sampling fields."""
    model = normalize_model_name(model)
    reasoning = _is_reasoning_model(model)
    deepseek = _is_deepseek(model)
    effort = (reasoning_effort or os.getenv("MMAGENT_REASONING_EFFORT", "high")).lower()
    if effort not in REASONING_EFFORTS:
        raise ValueError("Invalid reasoning effort")

    params: Dict[str, Any] = {"messages": list(messages), "model": model}
    if count > 1:
        params["n"] = count
    if reasoning:
        if deepseek:
            params["max_tokens"] = max_tokens
            if effort in {"medium", "xhigh"}:
                effort = "high"
            if effort != "none":
                params["reasoning_effort"] = effort
            params["extra_body"] = {
                "thinking": {"type": "disabled" if effort == "none" else "enabled"}
            }
        else:
            params["max_completion_tokens"] = max_tokens
            # GPT-5.6 accepts an explicit `none`; do not silently use a
            # provider default when the caller requested reasoning off.
            params["reasoning_effort"] = effort
    else:
        params["max_tokens"] = max_tokens
        if temperature is not None:
            params["temperature"] = temperature
        if top_p is not None:
            params["top_p"] = top_p
        if stop is not None:
            params["stop"] = stop
    return params


def completions_with_backoff(
    client: Any,
    messages: Sequence[Dict[str, str]],
    model: str,
    temperature: Optional[float],
    max_tokens: int,
    cnt: int,
    stop: Optional[Sequence[str] | str],
    top_p: Optional[float],
    reasoning_effort: Optional[str] = None,
):
    params = build_completion_params(
        messages,
        model,
        temperature=temperature,
        max_tokens=max_tokens,
        count=cnt,
        stop=stop,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
    )
    for attempt in range(5):
        try:
            return client.chat.completions.create(**params)
        except Exception as exc:
            retryable = openai is not None and isinstance(exc, openai.OpenAIError)
            if not retryable or attempt == 4:
                raise
            # Do not echo provider exceptions here: they may contain headers or
            # request data. The exception type is sufficient for diagnostics.
            logger.warning(
                "Evaluation request failed (%s); retrying attempt %d/5",
                type(exc).__name__,
                attempt + 2,
            )
            time.sleep(min(2**attempt, 8))
    raise RuntimeError("unreachable")


def gpt(
    prompt: str,
    base_url: Optional[str] = None,
    key: Optional[str] = None,
    model: str = "gpt-5.6-sol",
    temperature: Optional[float] = 0.7,
    max_tokens: int = 2000,
    n: int = 1,
    stop: Optional[Sequence[str] | str] = None,
    top_p: Optional[float] = 0.9,
    reasoning_effort: Optional[str] = None,
):
    if openai is None:
        raise RuntimeError("openai package is required for live evaluation calls")
    model, _provider, base_url, key = resolve_provider_config(model, base_url, key)
    messages = [{"role": "user", "content": prompt}]
    client = openai.OpenAI(api_key=key, base_url=base_url)
    return generate(
        client,
        messages,
        model,
        temperature,
        max_tokens,
        n,
        stop,
        top_p,
        reasoning_effort,
    )


def generate(
    client: Any,
    messages: Sequence[Dict[str, str]],
    model: str,
    temperature: Optional[float] = 0.7,
    max_tokens: int = 1000,
    n: int = 1,
    stop: Optional[Sequence[str] | str] = None,
    top_p: Optional[float] = 1.0,
    reasoning_effort: Optional[str] = None,
):
    global completion_tokens, prompt_tokens
    outputs = []
    remaining = max(1, int(n))
    reasoning = _is_reasoning_model(model)
    while remaining > 0:
        # Some reasoning endpoints do not support batched `n`; one completion
        # per request is the interoperable path.
        cnt = 1 if reasoning else min(remaining, 20)
        remaining -= cnt
        response = completions_with_backoff(
            client,
            messages,
            model,
            temperature,
            max_tokens,
            cnt,
            stop,
            top_p,
            reasoning_effort,
        )
        for choice in getattr(response, "choices", []):
            content = getattr(getattr(choice, "message", None), "content", None)
            if not isinstance(content, str):
                logger.warning("Skipping an evaluation response without text content")
                continue
            match = re.search(r"end\s+of\s+answer\.", content, re.IGNORECASE)
            if match:
                content = content[: match.start()]
            questions = list(re.finditer(r"Question:", content))
            if len(questions) >= 2:
                content = content[: questions[1].start()]
            outputs.append(content)

        usage = getattr(response, "usage", None)
        completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
    return outputs


def gpt_usage(backend: str = "gpt-5.6-sol"):
    """Return token counts; legacy price estimates are intentionally omitted."""
    return {
        "backend": normalize_model_name(backend),
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "cost": None,
        "cost_note": "Pricing is not hard-coded; use the provider's current billing data.",
    }
