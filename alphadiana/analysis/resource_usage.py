"""Read measured resource usage without invoking or changing agent execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from alphadiana.artifacts import Artifacts


@dataclass(frozen=True)
class MeasuredUsage:
    output_tokens: int | None
    provenance: dict


UsageExtractor = Callable[[Artifacts, dict], MeasuredUsage | None]


def _count(value):
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def openclaw_session_usage(source: Artifacts, record: dict) -> MeasuredUsage | None:
    """Use only the retained selected task session, never every runtime session."""
    files = (record.get("artifact_manifest") or {}).get("files", {})
    if not files.get("openclaw_session"):
        return None
    path = source.file(record, "openclaw_session")
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    evidence = {
        "source": "openclaw_session",
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if not rows or rows[0].get("type") != "session" or rows[0].get("version") != 3:
        return MeasuredUsage(None, dict(evidence, missing="unsupported_session_schema"))
    messages = [row for row in rows if isinstance(row.get("message"), dict)]
    user_text = []
    for row in messages:
        message = row["message"]
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            user_text.append(content)
        elif isinstance(content, list):
            user_text.extend(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    problem = str(record.get("problem") or "").strip()
    if not problem or not any(problem in text for text in user_text):
        return MeasuredUsage(None, dict(evidence, missing="task_identity_unverified"))
    assistants = [row for row in messages if row["message"].get("role") == "assistant"]
    ids = [row.get("id") for row in assistants]
    if any(not identity for identity in ids) or len(set(ids)) != len(ids):
        raise ValueError(
            f"Missing or duplicate native assistant message identity: {path}"
        )
    totals = {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": 0,
    }
    for row in assistants:
        usage = row["message"].get("usage") or {}
        counts = {key: _count(usage.get(key)) for key in totals}
        if any(value is None for value in counts.values()):
            return MeasuredUsage(
                None,
                dict(
                    evidence,
                    missing="incomplete_request_usage",
                    assistant_messages=len(assistants),
                ),
            )
        if counts["totalTokens"] != sum(
            counts[key] for key in ("input", "output", "cacheRead", "cacheWrite")
        ):
            raise ValueError(
                f"Native token totals disagree: {path}, message {row['id']}"
            )
        for key, value in counts.items():
            totals[key] += value
    if not assistants:
        return MeasuredUsage(None, dict(evidence, missing="no_assistant_messages"))
    return MeasuredUsage(
        totals["output"],
        dict(
            evidence,
            native_totals=totals,
            assistant_messages=len(assistants),
            task_identity_verified=True,
        ),
    )


def measured_usage(
    source: Artifacts,
    record: dict,
    extractors: Sequence[UsageExtractor] = (openclaw_session_usage,),
) -> MeasuredUsage:
    usage = record.get("token_usage") or {}
    output = _count(usage.get("completion_tokens", usage.get("output_tokens")))
    if output is not None:
        return MeasuredUsage(output, {"source": "persisted_token_usage"})
    for extractor in extractors:
        result = extractor(source, record)
        if result is not None:
            return result
    return MeasuredUsage(
        None, {"source": "unavailable", "missing": "no_measured_output_usage"}
    )
