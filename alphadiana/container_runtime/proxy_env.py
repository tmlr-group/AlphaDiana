"""Proxy environment helpers for Podman-managed containers."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit


_PROXY_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def podman_proxy_env(
    source: Mapping[str, str],
    *,
    host_alias: str = "host.containers.internal",
) -> dict[str, str]:
    """Return proxy env values rewritten for a Podman bridge container.

    Host shells often point proxies at 127.0.0.1. Inside a bridge-networked
    container that address means the container itself, so rewrite those proxy
    URLs to Podman's host alias.
    """

    env: dict[str, str] = {}
    for key in _PROXY_KEYS:
        raw_value = str(source.get(key, "") or "").strip()
        if not raw_value:
            continue
        if key.lower().endswith("proxy") and key.lower() != "no_proxy":
            env[key] = _rewrite_loopback_proxy(raw_value, host_alias=host_alias)
        else:
            env[key] = raw_value
    return env


def _rewrite_loopback_proxy(value: str, *, host_alias: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return value
    if parsed.hostname not in _LOOPBACK_HOSTS:
        return value
    username = parsed.username or ""
    password = parsed.password or ""
    auth = ""
    if username:
        auth = username
        if password:
            auth = f"{auth}:{password}"
        auth = f"{auth}@"
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{auth}{host_alias}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
