"""Small OpenAI-compatible client used by the research CLI.

The original implementation mixed model routing, accounting and error
handling in one method. This module keeps the public LLM API compatible
while making provider-specific reasoning parameters explicit and safe.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    import openai
except ImportError:  # keep configuration/profile helpers importable in minimal envs
    openai = None
try:
    from dotenv import load_dotenv
except ImportError:  # optional for callers that already export env variables
    def load_dotenv():
        return False

load_dotenv()
logger = logging.getLogger(__name__)

MODEL_ALIASES = {
    "deepseekv4pro": "deepseek-v4-pro",
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek pro": "deepseek-v4-pro",
    "gpt5.6sol": "gpt-5.6-sol",
    "gpt-5.6-sol": "gpt-5.6-sol",
    "gpt-5.6": "gpt-5.6-sol",
}


@dataclass(frozen=True)
class ModelProfile:
    model: str
    base_url: str
    provider: str
    reasoning: bool
    reasoning_effort: Optional[str]


class LLMGenerationError(RuntimeError):
    """Raised when an API request fails without leaking credentials."""


def normalize_model_name(model_name: str) -> str:
    raw = (model_name or "").strip()
    return MODEL_ALIASES.get(raw.lower(), raw)


def _is_deepseek(model: str, base_url: str = "") -> bool:
    model_id = normalize_model_name(model).lower().rsplit("/", 1)[-1]
    if model_id.startswith(("gpt-", "o1", "o3", "o4")):
        return False
    return model_id.startswith("deepseek-") or "deepseek" in (base_url or "").lower()


def _is_reasoning_model(model: str) -> bool:
    model_id = normalize_model_name(model).lower().rsplit("/", 1)[-1]
    return (
        model_id.startswith("gpt-5")
        or model_id.startswith("o1")
        or model_id.startswith("o3")
        or model_id.startswith("o4")
        or model_id.startswith("deepseek-v4")
        or model_id == "deepseek-reasoner"
    )


def resolve_model_profile(
    model_name: str,
    api_base: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> ModelProfile:
    model = normalize_model_name(
        model_name or os.getenv("MMAGENT_MODEL_NAME", "gpt-5.6-sol")
    )
    configured_base = api_base or os.getenv("MMAGENT_BASE_URL")
    if configured_base and model.lower().startswith("deepseek-") and "api.openai.com" in configured_base.lower():
        raise ValueError("DeepSeek models cannot use the official OpenAI endpoint")
    if configured_base and model.lower().startswith(("gpt-", "o1", "o3", "o4")) and "api.deepseek.com" in configured_base.lower():
        raise ValueError("OpenAI models cannot use the official DeepSeek endpoint")
    if _is_deepseek(model, configured_base or ""):
        base_url = configured_base or os.getenv(
            "DEEPSEEK_API_BASE",
            os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        provider = "deepseek"
    else:
        base_url = configured_base or os.getenv(
            "OPENAI_BASE_URL",
            os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
        )
        provider = "openai"

    effort = reasoning_effort or os.getenv("MMAGENT_REASONING_EFFORT")
    if effort:
        effort = effort.lower()
        if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError(
                "reasoning_effort must be one of none, low, medium, high, xhigh, max"
            )
    if provider == "deepseek" and effort in {"medium", "xhigh", "max"}:
        # DeepSeek's OpenAI-compatible endpoint exposes low/high/max-style
        # reasoning controls; map unsupported OpenAI granularity explicitly.
        effort = "high" if effort != "max" else "max"
    return ModelProfile(
        model=model,
        base_url=base_url,
        provider=provider,
        reasoning=_is_reasoning_model(model),
        reasoning_effort=effort,
    )


def _usage_dict(response: Any) -> Dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0}

    def read(name: str) -> int:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(name, 0)
        return int(value or 0)

    return {
        "completion_tokens": read("completion_tokens"),
        "prompt_tokens": read("prompt_tokens"),
        "total_tokens": read("total_tokens"),
    }


class LLM:
    def __init__(
        self,
        model_name: str,
        key: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        user_id: Optional[str] = None,
        api_base: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.user_id = user_id
        self.profile = resolve_model_profile(model_name, api_base, reasoning_effort)
        provider_key = (
            os.getenv("DEEPSEEK_API_KEY")
            if self.profile.provider == "deepseek"
            else os.getenv("OPENAI_API_KEY")
        )
        self.api_key = key or provider_key or os.getenv("MMAGENT_API_KEY")
        if not self.api_key:
            raise ValueError(
                "API key not found; pass --key or set the provider key "
                "(OPENAI_API_KEY/DEEPSEEK_API_KEY) or MMAGENT_API_KEY"
            )
        self.model_name = self.profile.model
        self.api_base = self.profile.base_url
        self.reasoning_effort = self.profile.reasoning_effort
        self.usages: list[Dict[str, int]] = []
        if openai is None:
            raise RuntimeError(
                "openai package is required for LLM calls; install it from requirements.txt"
            )
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base)

    def reset(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model_name: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ):
        previous_provider = self.profile.provider
        if api_key:
            self.api_key = api_key
        if model_name or api_base or reasoning_effort:
            # When the model changes providers, do not carry the previous
            # provider's default endpoint into the new profile.
            profile_base = api_base
            if profile_base is None and not model_name:
                profile_base = self.api_base
            self.profile = resolve_model_profile(
                model_name or self.model_name,
                profile_base,
                reasoning_effort or self.reasoning_effort,
            )
            self.model_name = self.profile.model
            self.api_base = self.profile.base_url
            self.reasoning_effort = self.profile.reasoning_effort
        if not api_key:
            provider_key = (
                os.getenv("DEEPSEEK_API_KEY")
                if self.profile.provider == "deepseek"
                else os.getenv("OPENAI_API_KEY")
            )
            configured_key = provider_key or os.getenv("MMAGENT_API_KEY")
            if configured_key:
                self.api_key = configured_key
            elif self.profile.provider != previous_provider:
                raise ValueError(
                    f"No API key configured for provider {self.profile.provider}"
                )
        if openai is None:
            raise RuntimeError(
                "openai package is required for LLM calls; install it from requirements.txt"
            )
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base)

    def _request_params(
        self,
        prompt: str,
        system: str,
        *,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if max_tokens is not None:
            if self.profile.provider == "openai" and self.profile.reasoning:
                params["max_completion_tokens"] = max_tokens
            else:
                params["max_tokens"] = max_tokens
        if response_format is not None:
            params["response_format"] = response_format
        if self.profile.reasoning:
            if self.reasoning_effort is not None and not (
                self.profile.provider == "deepseek"
                and self.reasoning_effort == "none"
            ):
                params["reasoning_effort"] = self.reasoning_effort
            if self.profile.provider == "deepseek":
                thinking = (
                    "disabled"
                    if self.reasoning_effort == "none"
                    else "enabled"
                )
                params["extra_body"] = {"thinking": {"type": thinking}}
        else:
            params.update(
                {
                    "temperature": float(
                        os.getenv("MMAGENT_TEMPERATURE", "0.7")
                    ),
                    "top_p": 1.0,
                }
            )
        return params

    def generate(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        usage: bool = True,
        **kwargs: Any,
    ) -> str:
        params = self._request_params(
            prompt,
            system,
            max_tokens=kwargs.get("max_tokens"),
            response_format=kwargs.get("response_format"),
        )
        try:
            response = self.client.chat.completions.create(**params)
            if not getattr(response, "choices", None):
                raise LLMGenerationError("provider returned no choices")
            message = response.choices[0].message
            answer = getattr(message, "content", None) or ""
            if usage:
                self.usages.append(_usage_dict(response))
            self.logger.info(
                "[LLM] model=%s user=%s usage=%s",
                self.model_name,
                self.user_id or "anonymous",
                self.usages[-1] if usage else {},
            )
            return answer
        except Exception as exc:
            self.logger.error(
                "LLM generation failed for model=%s: %s",
                self.model_name,
                str(exc).replace(self.api_key, "[REDACTED]")[:300],
            )
            raise LLMGenerationError(
                f"LLM request failed for model {self.model_name}: "
                f"{str(exc).replace(self.api_key, '[REDACTED]')[:300]}"
            ) from exc

    def get_total_usage(self) -> Dict[str, int]:
        total = {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }
        for item in self.usages:
            for key in total:
                total[key] += int(item.get(key, 0))
        return total

    def clear_usage(self):
        self.usages.clear()
