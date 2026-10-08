"""Pure parsing, provenance, and validation helpers for the Pi-Harbor adapter."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig


_PROVIDER_ERROR_MARKERS = (
    "model_cooldown",
    "http 429",
    "api error",
    "rate limit",
    "quota",
    "authentication",
    "unauthorized",
    "invalid api key",
    "provider error",
)


def _has_config_value(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, str):
        return True
    text = value.strip()
    if not text or text.upper() == "EMPTY":
        return False
    return re.fullmatch(
        r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)",
        text,
    ) is None


def _validate_path(
    errors: list[str],
    key: str,
    value: object,
    *,
    directory: bool,
    required: bool,
) -> None:
    if not _has_config_value(value):
        if required:
            errors.append(f"agent.config.{key} is required")
        return
    path = Path(str(value)).expanduser()
    if not path.exists():
        errors.append(f"agent.config.{key} does not exist: {path}")
    elif directory and not path.is_dir():
        errors.append(f"agent.config.{key} must be a directory: {path}")
    elif not directory and not path.is_file():
        errors.append(f"agent.config.{key} must be a file: {path}")


def validate_pi_harbor_experiment_config(config: "ExperimentConfig") -> list[str]:
    """Validate adapter inputs without creating jobs or starting Harbor."""
    errors: list[str] = []
    if config.benchmark_name != "terminal_bench2":
        errors.append(
            "agent 'terminal_bench2_pi_harbor' requires benchmark 'terminal_bench2'"
        )
    if config.scorer_name != "terminal_bench2":
        errors.append(
            "agent 'terminal_bench2_pi_harbor' requires scorer 'terminal_bench2'"
        )
    if (
        config.benchmark_config.get("harbor_task_index") is True
        and config.benchmark_config.get("categories") is not None
    ):
        errors.append(
            "benchmark.config.categories is unavailable with harbor_task_index=true"
        )

    agent_config = config.agent_config
    for key, directory in (
        ("pi_install_path", True),
        ("node_bin_path", False),
        ("models_path", False),
    ):
        _validate_path(
            errors,
            key,
            agent_config.get(key),
            directory=directory,
            required=True,
        )
    pi_install_value = agent_config.get("pi_install_path")
    if _has_config_value(pi_install_value):
        pi_install_path = Path(str(pi_install_value)).expanduser()
        pi_executable = pi_install_path / "pi"
        if pi_install_path.is_dir() and not pi_executable.is_file():
            errors.append(
                f"agent.config.pi_install_path must contain pi: {pi_executable}"
            )
    for key in ("candidate_path", "audit_path", "compose_path"):
        _validate_path(
            errors,
            key,
            agent_config.get(key),
            directory=False,
            required=False,
        )
    for key in ("pi_model", "harbor_model"):
        if not _has_config_value(agent_config.get(key)):
            errors.append(f"agent.config.{key} is required")
    missing_reward_policy = str(
        agent_config.get("missing_reward_policy", "error") or "error"
    ).strip()
    if missing_reward_policy not in {"error", "zero"}:
        errors.append("agent.config.missing_reward_policy must be 'error' or 'zero'")
    return errors


def _message_text(content: object, *, include_thinking: bool = False) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    accepted = {"text"}
    if include_thinking:
        accepted.add("thinking")
    parts = [
        str(block.get("text", block.get("thinking", "")))
        for block in content
        if isinstance(block, dict) and block.get("type") in accepted
    ]
    return "\n".join(part for part in parts if part)


def _read_pi_trace(path: Path) -> tuple[list[dict], list[dict], str]:
    trajectory: list[dict] = []
    reasoning: list[dict] = []
    final_answer = ""
    if not path.is_file():
        return trajectory, reasoning, final_answer
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message") or {}
        role = str(message.get("role") or "")
        text = _message_text(message.get("content"))
        thinking = ""
        content = message.get("content")
        if isinstance(content, list):
            thinking = "\n".join(
                str(block.get("thinking", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "thinking"
            )
        if text:
            trajectory.append({"role": role, "content": text, "type": "message"})
            if role == "assistant":
                final_answer = text
        if thinking and role == "assistant":
            reasoning.append(
                {"role": "assistant", "content": thinking, "type": "reasoning"}
            )
    return trajectory, reasoning, final_answer


def _explicit_provider_error(path: Path) -> str:
    """Return an explicit Pi provider error, ignoring ordinary tool text."""
    if not path.is_file():
        return ""

    def messages(value: object):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"errorMessage", "finalError"} and isinstance(child, str):
                    yield child
                else:
                    yield from messages(child)
        elif isinstance(value, list):
            for child in value:
                yield from messages(child)

    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for message in messages(event):
            lowered = message.lower()
            if any(marker in lowered for marker in _PROVIDER_ERROR_MARKERS):
                return message
    return ""


def _request_audit_error(
    path: Path, expected_model: str, expected_reasoning: str
) -> str:
    """Validate every captured provider request against the frozen run config."""
    if not path.is_file():
        return "request-audit.jsonl is missing"
    records = 0
    for line_number, line in enumerate(
        path.read_text(errors="replace").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return f"request audit line {line_number} is not valid JSON"
        records += 1
        model = record.get("model")
        reasoning = record.get("reasoning") or {}
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
        if model != expected_model or effort != expected_reasoning:
            return (
                f"request audit line {line_number} expected model={expected_model!r}, "
                f"reasoning={expected_reasoning!r}; got model={model!r}, reasoning={effort!r}"
            )
    if records == 0:
        return "request-audit.jsonl contains no provider requests"
    return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_entry(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "access": "read_only_post_run",
    }


def _input_identity(path: Path, *, access: str) -> dict[str, Any]:
    entry = _artifact_entry(path)
    entry["access"] = access
    return entry


def _duration_seconds(result: dict) -> float:
    try:
        start = datetime.fromisoformat(str(result["started_at"]).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(
            str(result["finished_at"]).replace("Z", "+00:00")
        )
        return max(0.0, (finish - start).total_seconds())
    except (KeyError, TypeError, ValueError):
        return 0.0


def _provider_reasoning_effort(models_path: Path, pi_model: str, reasoning: str) -> str:
    """Resolve Pi's model-level thinking map and reject explicit unsupported levels."""
    try:
        models_config = json.loads(models_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Pi models config {models_path}: {exc}") from exc
    if "/" not in pi_model:
        return reasoning
    provider_name, model_id = pi_model.split("/", 1)
    provider = (models_config.get("providers") or {}).get(provider_name)
    if not isinstance(provider, dict):
        return reasoning
    model = next(
        (
            item
            for item in (provider.get("models") or [])
            if isinstance(item, dict) and item.get("id") == model_id
        ),
        None,
    )
    if not isinstance(model, dict):
        return reasoning
    mapping = model.get("thinkingLevelMap")
    if not isinstance(mapping, dict):
        return reasoning
    if reasoning in mapping and mapping[reasoning] is None:
        raise ValueError(
            f"Pi model {pi_model!r} explicitly marks reasoning level {reasoning!r} "
            "unsupported in models.json"
        )
    mapped = mapping.get(reasoning)
    return str(mapped) if mapped is not None else reasoning
