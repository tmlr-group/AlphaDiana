from __future__ import annotations

import secrets

WEAK_OPENCLAW_GATEWAY_TOKENS = frozenset(
    {
        "",
        "OPENCLAW",
        "openclaw",
        "test",
        "token",
        "default",
        "changeme",
        "secret",
    }
)


def is_weak_openclaw_gateway_token(token: str | None) -> bool:
    return (token or "") in WEAK_OPENCLAW_GATEWAY_TOKENS


def generate_openclaw_gateway_token() -> str:
    return secrets.token_urlsafe(32)


def resolve_openclaw_gateway_token(token: str | None = None) -> str:
    if not is_weak_openclaw_gateway_token(token):
        return str(token)
    return generate_openclaw_gateway_token()
