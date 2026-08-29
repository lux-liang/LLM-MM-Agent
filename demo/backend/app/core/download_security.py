"""Validation for server-side downloads of sandbox-generated assets."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit


_TRUSTED_ASSET_SUFFIXES = (
    "e2b.dev",
    "e2b.app",
    "amazonaws.com",
)
_TRUSTED_ASSET_HOSTS = {"storage.googleapis.com"}


def safe_asset_download_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("asset URL must be https and contain no embedded credentials")
    configured = {
        item.strip().lower().rstrip(".")
        for item in os.getenv("MMAGENT_ALLOWED_ASSET_HOSTS", "").split(",")
        if item.strip()
    }
    trusted = host in _TRUSTED_ASSET_HOSTS or any(
        host == suffix or host.endswith("." + suffix)
        for suffix in _TRUSTED_ASSET_SUFFIXES
    )
    if not (trusted or host in configured):
        raise ValueError("asset URL host is not allowlisted")
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(
                host, parsed.port or 443, type=socket.SOCK_STREAM
            )
        }
    except OSError as exc:
        raise ValueError("asset URL host could not be resolved") from exc
    if not addresses:
        raise ValueError("asset URL host has no address")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("asset URL resolves to a private or reserved address")
    return url


def asset_url_for_log(url: str) -> str:
    """Drop signed query/fragment values before logging."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:300]
