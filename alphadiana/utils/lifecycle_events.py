"""Small JSONL lifecycle event helper for long-running task rows."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LIFECYCLE_STAGES = (
    "selected",
    "launched",
    "sandbox_started",
    "provider_connected",
    "first_token_seen",
    "reasoning_seen",
    "content_seen",
    "logprobs_seen",
    "usage_seen",
    "agent_done",
    "scorer_started",
    "task_json_written",
    "audit_seen",
)

_REDACTED = "<redacted>"
_SECRET_ENV_ASSIGNMENT_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)="
    r"([^\s\"']+|\"[^\"]*\"|'[^']*')"
)
_AUTH_HEADER_RE = re.compile(
    r"\b(Authorization\s*:\s*(?:Bearer\s+)?)([^\s,;]+)",
    re.IGNORECASE,
)


def _redact_text(value: str) -> str:
    value = _SECRET_ENV_ASSIGNMENT_RE.sub(r"\1=" + _REDACTED, value)
    return _AUTH_HEADER_RE.sub(r"\1" + _REDACTED, value)


def _redact_value(value: Any, *, key: object | None = None) -> Any:
    normalized_key = str(key or "").lower().replace("-", "_")
    if any(part in normalized_key for part in ("api_key", "token", "secret", "password", "authorization")):
        return _REDACTED
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {item_key: _redact_value(item_value, key=item_key) for item_key, item_value in value.items()}
    return value


def append_lifecycle_event(
    path: str | Path | None,
    *,
    run_id: str,
    task_id: str,
    sample_index: int,
    stage: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a best-effort lifecycle event without affecting task execution."""
    if not path:
        return
    try:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "task_id": task_id,
            "sample_index": sample_index,
            "stage": stage,
            "metadata": _redact_value(metadata or {}),
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    except Exception:
        return
