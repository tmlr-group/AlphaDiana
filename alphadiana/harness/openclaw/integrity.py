"""Reject incomplete or tainted OpenClaw responses before benchmark scoring."""

from __future__ import annotations
from typing import TYPE_CHECKING
from alphadiana.engine.sandbox.base import response_evidence_text

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig


def _normalize_openclaw_request_text(value: object) -> str:
    return response_evidence_text(value).replace("\r\n", "\n").strip()


def _openclaw_request_content(response: object) -> str:
    request_messages = getattr(response, "request_messages", None)
    if not isinstance(request_messages, list):
        return ""
    for message in request_messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "") or "").lower() != "user":
            continue
        return _normalize_openclaw_request_text(message.get("content", ""))
    return ""


def _slice_openclaw_trajectory_to_request(
    trajectory: list[dict],
    expected_user_content: str,
) -> list[dict]:
    expected = _normalize_openclaw_request_text(expected_user_content)
    if not expected or not isinstance(trajectory, list):
        return trajectory

    start: int | None = None
    for index, entry in enumerate(trajectory):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("role", "") or "").lower() != "user":
            continue
        candidate = _normalize_openclaw_request_text(entry.get("content", ""))
        if candidate == expected:
            start = index
            break
    if start is None:
        return trajectory

    end = len(trajectory)
    for index in range(start + 1, len(trajectory)):
        entry = trajectory[index]
        if not isinstance(entry, dict):
            continue
        if str(entry.get("role", "") or "").lower() != "user":
            continue
        candidate = _normalize_openclaw_request_text(entry.get("content", ""))
        if candidate != expected:
            end = index
            break
    return trajectory[start:end]


class OpenClawResponseRejected(RuntimeError):
    """Raised when a completed OpenClaw response fails harness integrity checks."""

    def __init__(
        self,
        reason: str,
        response: object,
        *,
        error_type: str = "openclaw_response_rejected",
    ) -> None:
        super().__init__(f"OpenClaw response rejected by integrity guard: {reason}")
        self.reason = reason
        self.error_type = error_type
        self.partial_response = response
        self.response_body = {"guard_reason": reason}


def _iter_openclaw_taint_text(response: object):
    """Yield response text that can prove the assistant saw the heartbeat file.

    OpenClaw workspaces normally contain a ``HEARTBEAT.md`` artifact. Artifact
    manifests and tool/workspace listings are not taint evidence by themselves;
    only assistant-visible/output text or prompt text with the old heartbeat
    probe should trip the guard.
    """
    for attr in ("raw_output", "response_json"):
        value = getattr(response, attr, None)
        if value:
            yield response_evidence_text(value)

    expected_user_content = _openclaw_request_content(response)
    for attr in ("trajectory", "reasoning_trajectory", "request_messages"):
        value = getattr(response, attr, None)
        if not value:
            continue
        if attr == "trajectory" and expected_user_content and isinstance(value, list):
            value = _slice_openclaw_trajectory_to_request(value, expected_user_content)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    role = str(item.get("role", "") or "").lower()
                    if role in {"assistant", "user", "system"}:
                        yield response_evidence_text(item.get("content", ""))
                else:
                    yield response_evidence_text(item)
        else:
            yield response_evidence_text(value)


def integrity_guard_reason(
    config: "ExperimentConfig", response: object
) -> tuple[str, str]:
    """Return a rejection reason/error_type for tainted OpenClaw responses."""
    if config.agent_name != "openclaw":
        return "", ""

    metadata = getattr(response, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    timeout_scored_zero = metadata.get("openclaw_timeout_scored_zero") is True
    if metadata.get("session_tainted") is True:
        return "session_tainted", "openclaw_session_tainted"
    if not timeout_scored_zero and metadata.get("received_done") is False:
        return "stream_incomplete", "incomplete_stream"
    if (
        not timeout_scored_zero
        and str(getattr(response, "finish_reason", "") or "").strip() == "incomplete"
    ):
        return "finish_reason_incomplete", "incomplete_stream"

    for text in _iter_openclaw_taint_text(response):
        if "Read HEARTBEAT.md" in text or "HEARTBEAT_OK" in text:
            return "heartbeat_trace", "openclaw_heartbeat_taint"
    return "", ""
