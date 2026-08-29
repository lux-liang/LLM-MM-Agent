"""Shared outbound LLM URL and credential-boundary validation."""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Optional
from urllib.parse import urlsplit


_KNOWN_LLM_HOSTS = {
    "api.openai.com",
    "api.deepseek.com",
    "api.anthropic.com",
    "openrouter.ai",
    "open.bigmodel.cn",
    "api.siliconflow.cn",
}

_MODEL_ALIASES = {
    "gpt5.6sol": "gpt-5.6-sol",
    "gpt-5.6": "gpt-5.6-sol",
    "deepseekv4pro": "deepseek-v4-pro",
}


def normalize_llm_model(model_name: Optional[str]) -> str:
    value = (model_name or "").strip()
    return _MODEL_ALIASES.get(value.lower(), value)


def safe_llm_base_url(base_url: Optional[str], model_name: Optional[str]) -> str:
    """Reject arbitrary/private targets before any server-side model call."""
    model_name = normalize_llm_model(model_name)
    if not base_url:
        base_url = (
            "https://api.deepseek.com"
            if model_name.lower().startswith("deepseek")
            else "https://api.openai.com/v1"
        )
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "baseUrl must be an https URL without credentials, query, or fragment"
        )

    configured = {
        item.strip().lower().rstrip(".")
        for item in os.getenv("MMAGENT_ALLOWED_LLM_HOSTS", "").split(",")
        if item.strip()
    }
    local_allowed = os.getenv("ALLOW_LOCAL_LLM_URLS", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    local = host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")
    if not (host in _KNOWN_LLM_HOSTS or host in configured or (local_allowed and local)):
        raise ValueError(
            "baseUrl host is not allowlisted; set MMAGENT_ALLOWED_LLM_HOSTS "
            "for a trusted gateway"
        )

    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(
                host, parsed.port or 443, type=socket.SOCK_STREAM
            )
        }
    except OSError as exc:
        raise ValueError("baseUrl host could not be resolved") from exc
    if not addresses:
        raise ValueError("baseUrl host has no address")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global and not (local_allowed and local):
            raise ValueError("baseUrl resolves to a non-global address")
    return base_url.rstrip("/")


def server_key_allowed_for_target(
    base_url: str,
    model_name: Optional[str],
    configured_server_base: Optional[str] = None,
) -> bool:
    """Only send server-managed secrets to their intended provider endpoint."""
    normalized = base_url.rstrip("/").lower()
    if configured_server_base and normalized == configured_server_base.rstrip("/").lower():
        return True
    host = (urlsplit(base_url).hostname or "").lower().rstrip(".")
    model = normalize_llm_model(model_name).lower().rsplit("/", 1)[-1]
    if model.startswith("deepseek"):
        return host == "api.deepseek.com"
    if model.startswith("gpt-") or model.startswith(("o1", "o3", "o4")):
        return host == "api.openai.com"
    return False
