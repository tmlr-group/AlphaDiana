"""Main orchestrator for running evaluation experiments."""

from __future__ import annotations

import json
import math
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import yaml

from alphadiana.harness.registry import AgentRegistry
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.utils.rock_ports import resolve_rock_ports_from_env
from alphadiana.benchmarks.registry import BenchmarkRegistry
from alphadiana.analysis.report import ReportGenerator, RunSummary
from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.engine.config.experiment_config import REPO_ROOT
from alphadiana.engine.task_dispatcher import TaskDispatcher
from alphadiana.engine.sandbox.registry import SandboxRegistry
from alphadiana.scorer.registry import ScorerRegistry
from alphadiana.utils.math_answer import (
    extract_numeric_answer_candidate,
    parse_numeric_answer,
)
from alphadiana.utils.lifecycle_events import append_lifecycle_event

if TYPE_CHECKING:
    from alphadiana.harness.base import Agent
    from alphadiana.benchmarks.base import Benchmark
    from alphadiana.engine.config.experiment_config import ExperimentConfig
    from alphadiana.engine.sandbox.base import Sandbox
    from alphadiana.scorer.base import Scorer

logger = logging.getLogger(__name__)
OPENCLAW_CONCURRENCY_PER_SANDBOX = 1
_OPENCLAW_PROFILE_CACHE_PATH = Path(".cache/openclaw_startup_profiles.json")
_VALID_MEMORY_SCOPES = frozenset({"intra_task", "cross_sample", "cross_task"})

_SECRET_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ZEROCLAW_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b("
    + "|".join(re.escape(key) for key in _SECRET_ENV_KEYS)
    + r")=([^\s\"']+|\"[^\"]*\"|'[^']*')"
)


def _redact_secret_assignments(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    return _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", text)


def _sanitize_history_entries(entries: object) -> list[dict]:
    sanitized_entries: list[dict] = []
    if not isinstance(entries, list):
        return sanitized_entries
    for entry in entries:
        if not isinstance(entry, dict):
            sanitized_entries.append(entry)
            continue
        sanitized = dict(entry)
        for key in ("command", "stdout", "stderr"):
            if isinstance(sanitized.get(key), str):
                sanitized[key] = _redact_secret_assignments(sanitized[key])
        sanitized_entries.append(sanitized)
    return sanitized_entries


def _sanitize_sandbox_metadata(metadata: dict | None, *, keep_command_history: bool) -> dict:
    """Redact secrets from sandbox metadata and optionally preserve command history."""
    if not isinstance(metadata, dict):
        return {}
    sanitized = dict(metadata)
    if keep_command_history:
        if "command_history" in sanitized:
            sanitized["command_history"] = _sanitize_history_entries(sanitized.get("command_history"))
    else:
        sanitized.pop("command_history", None)
    if "artifact_collection_history" in sanitized:
        sanitized["artifact_collection_history"] = _sanitize_history_entries(
            sanitized.get("artifact_collection_history")
        )
    if not sanitized.get("artifact_collection_history"):
        sanitized.pop("artifact_collection_history", None)
    return sanitized


def _sanitize_success_sandbox_metadata(metadata: dict | None) -> dict:
    """Strip noisy debug-only fields from normal successful task results."""
    return _sanitize_sandbox_metadata(metadata, keep_command_history=False)


def _agent_error_provenance_metadata(agent: object) -> dict:
    provider = getattr(agent, "error_provenance_metadata", None)
    if not callable(provider):
        return {}
    try:
        metadata = provider()
    except Exception:
        logger.debug("Agent error provenance provider failed", exc_info=True)
        return {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def _normalize_numeric_response_answer(config: "ExperimentConfig", response: object) -> None:
    """Normalize stored predictions only for the numeric scorer path."""
    if getattr(config, "scorer_name", "") != "numeric":
        return
    answer = getattr(response, "answer", None)
    if not isinstance(answer, str) or not answer.strip():
        return
    normalized = extract_numeric_answer_candidate(answer)
    if not normalized or normalized == answer:
        return
    if parse_numeric_answer(normalized) is None:
        return
    metadata = getattr(response, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.setdefault("numeric_answer_original", answer)
    metadata["numeric_answer_normalized"] = True
    metadata["numeric_answer_normalizer"] = "extract_numeric_answer_candidate"
    response.metadata = metadata
    response.answer = normalized


def _apply_error_provenance_metadata(response: object, metadata: dict) -> None:
    if not metadata:
        return
    response_metadata = dict(getattr(response, "metadata", None) or {})
    for key, value in metadata.items():
        if value not in (None, ""):
            response_metadata.setdefault(key, value)
    response.metadata = response_metadata


def _merge_artifact_manifests(existing: dict | None, incoming: dict | None) -> dict:
    """Merge artifact manifests without dropping previously preserved file aliases."""
    merged = dict(existing or {})
    for key, value in dict(incoming or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = [*merged[key], *value]
        else:
            merged[key] = value
    return merged


def _coerce_config_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _has_openclaw_direct_gateway(config: "ExperimentConfig") -> bool:
    """Return whether OpenClaw should use an already-running gateway."""
    if config.agent_name != "openclaw":
        return False
    api_base = str(config.agent_config.get("api_base", "") or "").strip()
    gateway_pool = config.agent_config.get("gateway_pool", []) or []
    return bool(api_base or gateway_pool)


def _is_gateway_autodeploy_agent(config: "ExperimentConfig") -> bool:
    if config.agent_name == "openclaw":
        if str(config.agent_config.get("runtime_backend", "") or "").strip().lower() == "podman":
            return False
        return bool(
            not _has_openclaw_direct_gateway(config)
            and
            config.agent_config.get("rock_agent_config_path")
            and config.agent_config.get("openclaw_config_path")
        )
    return False


def _stabilize_openclaw_gateway_token(config: "ExperimentConfig") -> None:
    """Resolve one strong auto-deploy token shared by every runtime instance."""
    if not _is_gateway_autodeploy_agent(config):
        return
    from alphadiana.utils.openclaw_security import (
        is_weak_openclaw_gateway_token,
        resolve_openclaw_gateway_token,
    )

    token = str(config.agent_config.get("gateway_token", "") or "")
    if is_weak_openclaw_gateway_token(token):
        config.agent_config["gateway_token"] = resolve_openclaw_gateway_token(token)


def _should_predeploy_openclaw(config: "ExperimentConfig") -> bool:
    """Use the fresh gateway pool only when state does not span work items."""
    return _is_gateway_autodeploy_agent(config) and _configured_memory_scope(config) not in {
        "cross_sample",
        "cross_task",
    }


def _needs_auto_rock_sandbox(config: "ExperimentConfig") -> bool:
    if _is_gateway_autodeploy_agent(config):
        return True
    if config.agent_name == "zeroclaw":
        if str(config.agent_config.get("runtime_backend", "") or "").strip().lower() == "podman":
            return False
        return bool(config.agent_config.get("rock_image"))
    return False


def _make_gateway_runtime_manager(config: "ExperimentConfig"):
    if config.agent_name == "openclaw":
        from alphadiana.harness.openclaw.runtime import OpenClawRuntimeManager

        return OpenClawRuntimeManager(config.agent_config)
    if config.agent_name == "zeroclaw":
        if str(config.agent_config.get("runtime_backend", "") or "").strip().lower() == "podman":
            from alphadiana.harness.zeroclaw.runtime import ZeroClawPodmanRuntimeManager

            return ZeroClawPodmanRuntimeManager(config.agent_config)
        from alphadiana.harness.zeroclaw.runtime import ZeroClawRuntimeManager

        return ZeroClawRuntimeManager(config.agent_config)
    raise RuntimeError(f"Unsupported gateway auto-deploy agent: {config.agent_name}")


def _build_openclaw_profile_cache_key(config: "ExperimentConfig", admin_base_url: str) -> str:
    dataset = str(config.benchmark_config.get("dataset", ""))
    split = str(config.benchmark_config.get("split", ""))
    image = str(config.agent_config.get("rock_image", ""))
    model_name = str(config.agent_config.get("OPENAI_MODEL_NAME", config.agent_config.get("model", "")))
    return "|".join([
        config.agent_name,
        config.benchmark_name,
        image,
        model_name,
        dataset,
        split,
        admin_base_url,
    ])


def _load_cached_openclaw_profile(cache_key: str) -> tuple[str, float] | None:
    try:
        if not _OPENCLAW_PROFILE_CACHE_PATH.exists():
            return None
        payload = json.loads(_OPENCLAW_PROFILE_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        item = payload.get(cache_key)
        if not isinstance(item, dict):
            return None
        memory = str(item.get("memory", "")).strip()
        cpus = float(item.get("cpus", 0))
        if not memory or cpus <= 0:
            return None
        return memory, cpus
    except Exception:
        logger.debug("Failed to load OpenClaw startup profile cache", exc_info=True)
        return None


def _should_load_cached_openclaw_profile(config: "ExperimentConfig", requested_cpus: float) -> bool:
    """Return whether a persisted OpenClaw startup profile may override config."""
    if requested_cpus <= 0:
        return False
    return bool(config.agent_config.get("use_cached_predeploy_startup_profile", True))


def _save_cached_openclaw_profile(cache_key: str, memory: str, cpus: float) -> None:
    try:
        _OPENCLAW_PROFILE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, dict[str, object]] = {}
        if _OPENCLAW_PROFILE_CACHE_PATH.exists():
            existing = json.loads(_OPENCLAW_PROFILE_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload.update(existing)
        payload[cache_key] = {
            "memory": str(memory),
            "cpus": float(cpus),
            "updated_at": int(time.time()),
        }
        _OPENCLAW_PROFILE_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("Failed to save OpenClaw startup profile cache", exc_info=True)


def _is_sandbox_disconnect(exc: Exception) -> bool:
    """Return True if *exc* looks like a sandbox connection failure."""
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    try:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return True
    except Exception:
        pass
    msg = str(exc).lower()
    return "connection" in msg and ("refused" in msg or "reset" in msg or "timeout" in msg)


def _payload_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _normalize_openclaw_request_text(value: object) -> str:
    return _payload_text(value).replace("\r\n", "\n").strip()


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
    trajectory: list[dict], expected_user_content: str,
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


def _predeployed_session_failure_reason(exc: Exception, response: object | None = None) -> str:
    """Return a quarantine reason when a predeployed gateway session is no longer usable."""
    error_type = str(getattr(exc, "error_type", "") or "").lower()
    if error_type == "control_plane_unavailable":
        return "control_plane_unavailable"

    chunks: list[str] = [
        str(exc),
        error_type,
        _payload_text(getattr(exc, "response_body", None)),
        _payload_text(getattr(exc, "retry_responses", None)),
    ]
    if response is not None:
        chunks.extend([
            _payload_text(getattr(response, "metadata", None)),
            _payload_text(getattr(response, "response_json", None)),
            _payload_text(getattr(response, "sandbox_metadata", None)),
            str(getattr(response, "gateway_url", "") or ""),
            str(getattr(response, "gateway_log_excerpt", "") or ""),
        ])
    text = "\n".join(chunk for chunk in chunks if chunk).lower()
    if "not started" in text:
        return "sandbox_not_started"
    if "not alive" in text:
        return "sandbox_not_alive"
    if "upstream server is not reachable" in text:
        return "sandbox_upstream_unreachable"
    if "run in session failed" in text and "service unavailable" in text:
        return "sandbox_upstream_unreachable"
    if "connection refused" in text or "[errno 111]" in text:
        return "gateway_connection_refused"
    if "failed to connect" in text or "connecterror" in text:
        return "gateway_connect_failed"
    if "connection reset by peer" in text or "[errno 104]" in text:
        return "gateway_connection_reset"
    if "peer closed connection" in text or "incomplete chunked read" in text:
        return "gateway_stream_interrupted"
    return ""


def _is_recoverable_task_failure(exc: Exception) -> bool:
    """Return True when a failed task should be retried on fresh infrastructure."""
    return bool(getattr(exc, "retryable_task_failure", False))


def _configured_memory_scope(config: "ExperimentConfig") -> str:
    """Return and validate the optional harness-native memory scope.

    An empty value preserves legacy behavior. Explicit scopes make the
    persistence contract auditable and prevent a config from claiming a scope
    whose ``persistent_memory`` setting implements different semantics.
    """
    agent_config = getattr(config, "agent_config", {}) or {}
    raw_scope = str(agent_config.get("memory_scope", "") or "").strip().lower()
    if not raw_scope:
        return ""
    if raw_scope not in _VALID_MEMORY_SCOPES:
        valid = ", ".join(sorted(_VALID_MEMORY_SCOPES))
        raise ValueError(f"agent.config.memory_scope must be one of: {valid}")

    persistent = bool(agent_config.get("persistent_memory", False))
    expected_persistent = raw_scope != "intra_task"
    if persistent != expected_persistent:
        raise ValueError(
            f"memory_scope={raw_scope!r} requires "
            f"persistent_memory={str(expected_persistent).lower()}"
        )
    return raw_scope


class _MemoryScopeLifecycle:
    """Track task-major work items and identify cross-sample reset points."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self._task_id = ""

    def before_work_item(self, task_id: str, sample_index: int) -> bool:
        del sample_index  # Ordering is task-major; task identity defines the boundary.
        if self.scope != "cross_sample":
            return False
        task_id = str(task_id)
        should_reset = bool(self._task_id and task_id != self._task_id)
        self._task_id = task_id
        return should_reset


def _validate_stateful_checkpoint_resume(
    scope: str,
    work_items: list[tuple["BenchmarkTask", int]],
    completed_samples: set[tuple[str, int]],
) -> None:
    """Reject checkpoint resumes that cannot reconstruct native memory state."""
    if scope not in {"cross_sample", "cross_task"} or not completed_samples:
        return

    expected_samples = {(task.task_id, sample_index) for task, sample_index in work_items}
    completed_expected = expected_samples & completed_samples
    if not completed_expected or completed_expected == expected_samples:
        return

    if scope == "cross_task":
        raise ValueError(
            "Cannot resume a partial cross_task memory run because prior native "
            "memory cannot be reconstructed; use a new run_id or --redo-all."
        )

    completed_by_task: dict[str, set[int]] = {}
    expected_by_task: dict[str, set[int]] = {}
    for task_id, sample_index in expected_samples:
        expected_by_task.setdefault(task_id, set()).add(sample_index)
    for task_id, sample_index in completed_expected:
        completed_by_task.setdefault(task_id, set()).add(sample_index)
    partially_completed_tasks = sorted(
        task_id
        for task_id, completed in completed_by_task.items()
        if completed != expected_by_task[task_id]
    )
    if partially_completed_tasks:
        raise ValueError(
            "Cannot resume partially sampled task(s) in a cross_sample memory run "
            f"({', '.join(partially_completed_tasks)}); use a new run_id or --redo-all."
        )


def _effective_dispatch_concurrency(config: "ExperimentConfig") -> int:
    """Return the safe dispatcher concurrency for a configured run."""
    requested = max(1, int(getattr(config, "max_concurrent", 1) or 1))
    memory_scope = _configured_memory_scope(config)
    if memory_scope in {"cross_sample", "cross_task"} and requested > 1:
        logger.warning(
            "memory_scope=%s requires deterministic state inheritance; lowering "
            "effective max_concurrent from %d to 1.",
            memory_scope,
            requested,
        )
        return 1
    if getattr(config, "sandbox_name", "") == "decodingtrust" and requested > 1:
        logger.warning(
            "DecodingTrust sandbox uses process-wide DTAP state and is not safe "
            "for in-process task concurrency; lowering effective max_concurrent "
            "from %d to 1.",
            requested,
        )
        return 1
    return requested


def _is_decodingtrust_process_sharded_run(config: "ExperimentConfig") -> bool:
    strategy = str(getattr(config, "parallel_strategy", "") or "").strip().lower()
    shards = int(getattr(config, "process_shards", 1) or 1)
    return (
        getattr(config, "benchmark_name", "") == "decodingtrust"
        and strategy == "process_shards"
        and shards > 1
    )


def _chunk_round_robin(items: list[BenchmarkTask], shard_count: int) -> list[list[BenchmarkTask]]:
    shards: list[list[BenchmarkTask]] = [[] for _ in range(shard_count)]
    for index, item in enumerate(items):
        shards[index % shard_count].append(item)
    return shards


def _safe_shard_env_fragment(value: object, *, max_len: int = 40) -> str:
    fragment = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("_.-")
    return (fragment or "run")[:max_len]


def _experiment_config_to_yaml_data(config: "ExperimentConfig") -> dict:
    """Serialize the supported ExperimentConfig surface back to YAML data."""
    data: dict[str, object] = {
        "run_id": config.run_id,
        "agent": {
            "name": config.agent_name,
            "version": config.agent_version,
            "config": dict(config.agent_config or {}),
        },
        "benchmark": {
            "name": config.benchmark_name,
            "config": dict(config.benchmark_config or {}),
        },
        "scorer": {
            "name": config.scorer_name,
            "config": dict(config.scorer_config or {}),
        },
        "max_concurrent": int(getattr(config, "max_concurrent", 1) or 1),
        "num_samples": int(getattr(config, "num_samples", 1) or 1),
        "task_retries": int(getattr(config, "task_retries", 0) or 0),
        "task_retry_on_recoverable_only": bool(
            getattr(config, "task_retry_on_recoverable_only", False)
        ),
        "output_dir": str(config.output_dir),
        "redo_all": bool(getattr(config, "redo_all", False)),
        "sandbox_retries": int(getattr(config, "sandbox_retries", 1) or 1),
        "strict_report": bool(getattr(config, "strict_report", False)),
        "strict_isolation": bool(getattr(config, "strict_isolation", False)),
        "metadata": dict(getattr(config, "metadata", {}) or {}),
    }
    if config.sandbox_name:
        data["sandbox"] = {
            "name": config.sandbox_name,
            "config": dict(config.sandbox_config or {}),
        }
    return data


class _OpenClawResponseRejected(RuntimeError):
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
            yield _payload_text(value)

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
                        yield _payload_text(item.get("content", ""))
                else:
                    yield _payload_text(item)
        else:
            yield _payload_text(value)


def _openclaw_integrity_guard_reason(config: "ExperimentConfig", response: object) -> tuple[str, str]:
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


def _build_error_info(exc: Exception) -> dict:
    """Build a serializable error dict from an exception."""
    error_type = getattr(exc, "error_type", type(exc).__name__)
    return {
        "error": str(exc),
        "error_type": error_type,
        "request_payload": getattr(exc, "request_payload", None),
        "response_body": getattr(exc, "response_body", None),
    }


def _sandbox_config_provenance_metadata(config: "ExperimentConfig") -> dict:
    """Return runtime metadata available before a sandbox session exists."""
    sandbox_config = dict(getattr(config, "sandbox_config", None) or {})
    metadata: dict[str, object] = {}
    sandbox_backend = getattr(config, "sandbox_name", "") or ""
    if sandbox_backend:
        metadata["sandbox_backend"] = sandbox_backend
    container_engine = str(sandbox_config.get("container_engine", "") or "").strip()
    if container_engine:
        metadata["container_engine"] = container_engine
    docker_api_version = str(sandbox_config.get("docker_api_version", "") or "").strip()
    podman_socket = str(sandbox_config.get("podman_socket", "") or "").strip()
    if container_engine.lower() == "podman":
        try:
            from alphadiana.engine.container_runtime.podman_socket import (
                podman_socket_env,
                resolve_podman_docker_api_version,
            )

            docker_api_version = resolve_podman_docker_api_version(docker_api_version)
            podman_socket = podman_socket_env(podman_socket or None)["DOCKER_HOST"]
        except Exception:
            logger.debug("Failed to resolve Podman provenance metadata", exc_info=True)
    if docker_api_version:
        metadata["docker_api_version"] = docker_api_version
    if podman_socket:
        metadata["podman_socket"] = podman_socket
    network_mode = str(sandbox_config.get("network_mode", "") or "").strip()
    if network_mode:
        metadata["network_mode"] = network_mode
    return metadata


def _bind_runtime_task(task: BenchmarkTask, sample_index: int) -> BenchmarkTask:
    """Return a per-execution task copy with runtime metadata attached."""
    metadata = dict(task.metadata)
    metadata["sample_index"] = sample_index
    metadata["execution_id"] = uuid4().hex
    return replace(task, metadata=metadata)


def _strip_lifecycle_metadata(task: BenchmarkTask) -> BenchmarkTask:
    """Return a task copy without runner-only lifecycle metadata."""
    metadata = {
        key: value
        for key, value in dict(task.metadata or {}).items()
        if not str(key).startswith("_lifecycle_")
    }
    return replace(task, metadata=metadata)


class Runner:
    """Top-level orchestrator that loads config, initializes components,
    runs the evaluation loop, and writes results."""

    def __init__(self, config: "ExperimentConfig", cancel_event: "threading.Event | None" = None) -> None:
        self.config = config
        self.cancel_event = cancel_event
        self.benchmark: Benchmark | None = None
        self.agent: Agent | None = None
        self.sandbox: Sandbox | None = None
        self.scorer: Scorer | None = None
        self.result_store: ResultStore | None = None
        self.report_generator: ReportGenerator | None = None

    def _reset_cross_sample_state(self, shared_session):
        """Recreate harness-native state before the first sample of a new task."""
        if self.agent is not None:
            self.agent.teardown()
        agent_cls = AgentRegistry.get(self.config.agent_name)
        self.agent = agent_cls()
        self.agent.version = self.config.agent_version
        self.agent.setup(self.config.agent_config)

        if shared_session is None:
            return None
        shared_session.close()
        return self.sandbox.create_session() if self.sandbox is not None else None

    def setup(self) -> None:
        """Resolve and instantiate all components from their registries."""
        _configured_memory_scope(self.config)
        _stabilize_openclaw_gateway_token(self.config)
        # Import all benchmark/agent/sandbox/scorer modules to trigger registration.
        import alphadiana.benchmarks.aime.benchmark  # noqa: F401
        import alphadiana.benchmarks.custom.benchmark  # noqa: F401
        import alphadiana.benchmarks.swe_bench.benchmark  # noqa: F401
        import alphadiana.benchmarks.gpqa.benchmark  # noqa: F401
        import alphadiana.benchmarks.hle.benchmark  # noqa: F401
        import alphadiana.benchmarks.imo.benchmark  # noqa: F401
        import alphadiana.benchmarks.mmmu_pro.benchmark  # noqa: F401
        import alphadiana.benchmarks.swebench_pro.benchmark  # noqa: F401
        import alphadiana.benchmarks.terminal_bench2.benchmark  # noqa: F401
        import alphadiana.benchmarks.decodingtrust.benchmark  # noqa: F401

        # Import agent modules to trigger registration.
        import alphadiana.harness.direct_llm  # noqa: F401
        import alphadiana.harness.openclaw.agent  # noqa: F401
        import alphadiana.harness.opencode.agent  # noqa: F401
        import alphadiana.benchmarks.swe_bench.harness  # noqa: F401
        import alphadiana.benchmarks.terminal_bench2.harness.docker  # noqa: F401
        import alphadiana.benchmarks.terminal_bench2.harness.openclaw  # noqa: F401
        import alphadiana.benchmarks.terminal_bench2.harness.opencode  # noqa: F401
        import alphadiana.benchmarks.terminal_bench2.harness.zeroclaw  # noqa: F401
        import alphadiana.harness.zeroclaw.agent  # noqa: F401

        # Import sandbox modules to trigger registration.
        import alphadiana.engine.sandbox.local  # noqa: F401
        import alphadiana.engine.sandbox.podman  # noqa: F401
        import alphadiana.engine.sandbox.rock  # noqa: F401
        import alphadiana.engine.sandbox.swebench_container  # noqa: F401
        import alphadiana.engine.sandbox.decodingtrust  # noqa: F401

        # Import scorer modules to trigger registration.
        import alphadiana.scorer.exact_match  # noqa: F401
        import alphadiana.benchmarks.imo.verify  # noqa: F401
        import alphadiana.scorer.llm_judge  # noqa: F401
        import alphadiana.scorer.math_verify_scorer  # noqa: F401
        import alphadiana.scorer.numeric  # noqa: F401
        import alphadiana.benchmarks.swe_bench.scorer  # noqa: F401
        import alphadiana.benchmarks.swebench_pro.scorer  # noqa: F401
        import alphadiana.benchmarks.terminal_bench2.scorer  # noqa: F401
        import alphadiana.scorer.decodingtrust  # noqa: F401

        try:
            # Resolve and instantiate benchmark.
            benchmark_cls = BenchmarkRegistry.get(self.config.benchmark_name)
            self.benchmark = benchmark_cls()

            # Resolve and instantiate agent.
            agent_cls = AgentRegistry.get(self.config.agent_name)
            self.agent = agent_cls()
            self.agent.version = self.config.agent_version
            self.agent.setup(self.config.agent_config)

            # Resolve and instantiate sandbox (if configured).
            if self.config.sandbox_name:
                sandbox_cls = SandboxRegistry.get(self.config.sandbox_name)
                self.sandbox = sandbox_cls()
                self.sandbox.setup(self.config.sandbox_config)

            # Resolve and instantiate scorer.
            scorer_cls = ScorerRegistry.get(self.config.scorer_name)
            self.scorer = scorer_cls()
            self.scorer.setup(self.config.scorer_config)
        except Exception:
            self.teardown()
            raise

        # Initialize result store and report generator.
        self.result_store = ResultStore(
            output_dir=self.config.output_dir,
            run_id=self.config.run_id,
            run_metadata={
                "run_id": self.config.run_id,
                "agent_name": self.config.agent_name,
                "agent_version": self.config.agent_version,
                "benchmark_name": self.config.benchmark_name,
                "scorer_name": self.config.scorer_name,
                "num_samples": getattr(self.config, "num_samples", 1),
            },
        )
        self.report_generator = ReportGenerator()

        # Setup logging with run_id context.
        logging.basicConfig(
            level=logging.INFO,
            format=f"%(asctime)s [%(levelname)s] [{self.config.run_id}] %(name)s: %(message)s",
        )
        logger.info("Setup complete for run %s", self.config.run_id)

    def _run_decodingtrust_process_shards(self, tasks: list[BenchmarkTask]) -> RunSummary:
        """Run DecodingTrust through isolated serial child processes."""
        assert self.result_store is not None
        assert self.report_generator is not None

        shard_count = max(1, int(getattr(self.config, "process_shards", 1) or 1))
        task_shards = [shard for shard in _chunk_round_robin(tasks, shard_count) if shard]

        run_root = self.result_store.output_dir / self.config.run_id
        shard_root = run_root / "shards"
        shard_config_dir = shard_root / "configs"
        shard_log_dir = shard_root / "logs"
        shard_config_dir.mkdir(parents=True, exist_ok=True)
        shard_log_dir.mkdir(parents=True, exist_ok=True)

        base_port_start = int(
            (getattr(self.config, "metadata", {}) or {}).get("dt_shard_port_start", 8000)
        )
        port_stride = int(
            (getattr(self.config, "metadata", {}) or {}).get("dt_shard_port_stride", 3000)
        )
        shard_run_ids: list[str] = []
        processes: list[tuple[int, str, subprocess.Popen, object]] = []

        for shard_index, shard_tasks in enumerate(task_shards):
            shard_run_id = f"{self.config.run_id}__shard_{shard_index}"
            shard_run_ids.append(shard_run_id)

            shard_data = _experiment_config_to_yaml_data(self.config)
            shard_data["run_id"] = shard_run_id
            shard_data["max_concurrent"] = 1
            shard_data["parallel_strategy"] = ""
            shard_data["process_shards"] = 1
            shard_metadata = dict(shard_data.get("metadata", {}) or {})
            shard_metadata.update(
                {
                    "parent_run_id": self.config.run_id,
                    "shard_index": shard_index,
                    "process_shards": len(task_shards),
                }
            )
            shard_data["metadata"] = shard_metadata

            benchmark = dict(shard_data.get("benchmark", {}) or {})
            benchmark_config = dict(benchmark.get("config", {}) or {})
            benchmark_config["task_ids"] = [task.task_id for task in shard_tasks]
            benchmark_config.pop("limit", None)
            benchmark["config"] = benchmark_config
            shard_data["benchmark"] = benchmark

            shard_config_path = shard_config_dir / f"{shard_run_id}.yaml"
            shard_config_path.write_text(
                yaml.safe_dump(shard_data, sort_keys=False, allow_unicode=False),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["DT_POOL_PREFIX"] = f"ad_{_safe_shard_env_fragment(self.config.run_id)}_s{shard_index}"
            port_start = base_port_start + shard_index * port_stride
            env["DT_PORT_RANGE_START"] = str(port_start)
            env["DT_PORT_RANGE_END"] = str(port_start + port_stride - 1)

            log_handle = (shard_log_dir / f"{shard_run_id}.log").open("w", encoding="utf-8")
            cmd = [sys.executable, "-m", "alphadiana.cli", "run", str(shard_config_path)]
            logger.info(
                "Starting DecodingTrust shard %d/%d with %d tasks: %s",
                shard_index + 1,
                len(task_shards),
                len(shard_tasks),
                " ".join(cmd),
            )
            proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            processes.append((shard_index, shard_run_id, proc, log_handle))

        failures: list[str] = []
        try:
            for shard_index, shard_run_id, proc, log_handle in processes:
                code = proc.wait()
                log_handle.close()
                if code != 0:
                    failures.append(
                        f"shard {shard_index} ({shard_run_id}) exited with code {code}; "
                        f"log={shard_log_dir / f'{shard_run_id}.log'}"
                    )
        finally:
            for _, _, proc, log_handle in processes:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    log_handle.close()
                except Exception:
                    pass

        if failures:
            raise RuntimeError("DecodingTrust process shard run failed: " + "; ".join(failures))

        self.result_store.merge_result_stores(
            target_run_id=self.config.run_id,
            shard_run_ids=shard_run_ids,
            expected_tasks=tasks,
            num_samples=int(getattr(self.config, "num_samples", 1) or 1),
            config_metadata={
                "benchmark_config": self.config.benchmark_config,
                "metadata": self.config.metadata,
                "parallel_strategy": "process_shards",
                "process_shards": len(task_shards),
            },
        )
        return self.report_generator.generate(self.result_store, self.config)

    def run(self) -> RunSummary:
        """Execute the full evaluation loop and return a summary report."""
        assert self.benchmark is not None, "Call setup() before run()"
        assert self.agent is not None, "Call setup() before run()"
        assert self.scorer is not None, "Call setup() before run()"
        assert self.result_store is not None, "Call setup() before run()"
        assert self.report_generator is not None, "Call setup() before run()"

        # Load tasks from benchmark.
        tasks = self.benchmark.load_tasks(self.config.benchmark_config)
        logger.info("Loaded %d tasks from benchmark '%s'", len(tasks), self.config.benchmark_name)

        if _is_decodingtrust_process_sharded_run(self.config):
            return self._run_decodingtrust_process_shards(tasks)

        num_samples = getattr(self.config, "num_samples", 1)
        memory_scope = _configured_memory_scope(self.config)
        memory_lifecycle = _MemoryScopeLifecycle(memory_scope)
        self.result_store.save_manifest({
            "run_id": self.config.run_id,
            "benchmark_name": self.config.benchmark_name,
            "agent_name": self.config.agent_name,
            "agent_version": self.config.agent_version,
            "scorer_name": self.config.scorer_name,
            "num_samples": num_samples,
            "memory_scope": memory_scope,
            "expected_task_count": len(tasks),
            "expected_sample_count": len(tasks) * num_samples,
            "expected_task_ids": [task.task_id for task in tasks],
            "task_metadata_by_id": {
                task.task_id: task.metadata
                for task in tasks
            },
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config_metadata": {
                "benchmark_config": self.config.benchmark_config,
                "metadata": self.config.metadata,
            },
        })

        # Expand tasks into (task, sample_index) work items.
        work_items = [
            (task, si)
            for task in tasks
            for si in range(num_samples)
        ]

        # Checkpoint: skip already-completed samples unless redo_all is set.
        if not getattr(self.config, "redo_all", False):
            # Detect scorer mismatch: warn if existing records used a different scorer.
            existing_records = self.result_store.load()
            if existing_records:
                prev_scorers = {r.get("scorer_name") for r in existing_records if r.get("scorer_name")}
                if prev_scorers and self.config.scorer_name not in prev_scorers:
                    logger.warning(
                        "Scorer mismatch: existing results used %s but current config uses '%s'. "
                        "Set redo_all=true to re-evaluate all tasks with the new scorer.",
                        prev_scorers, self.config.scorer_name,
                    )

            if num_samples > 1:
                completed_samples = self.result_store.completed_sample_ids(
                    scorer_name=self.config.scorer_name,
                )
                _validate_stateful_checkpoint_resume(
                    memory_scope,
                    work_items,
                    completed_samples,
                )
                if completed_samples:
                    before = len(work_items)
                    work_items = [
                        (t, si) for t, si in work_items
                        if (t.task_id, si) not in completed_samples
                    ]
                    logger.info(
                        "Checkpoint: skipping %d completed samples, %d remaining",
                        before - len(work_items),
                        len(work_items),
                    )
            else:
                completed = self.result_store.completed_task_ids(
                    scorer_name=self.config.scorer_name,
                )
                _validate_stateful_checkpoint_resume(
                    memory_scope,
                    work_items,
                    {(task_id, 0) for task_id in completed},
                )
                if completed:
                    before = len(work_items)
                    work_items = [(t, si) for t, si in work_items if t.task_id not in completed]
                    logger.info(
                        "Checkpoint: skipping %d completed tasks, %d remaining",
                        before - len(work_items),
                        len(work_items),
                    )

        logger.info("Total work items: %d (tasks=%d, num_samples=%d)", len(work_items), len(tasks), num_samples)

        def _lifecycle_path_for(task_id: str, sample_index: int) -> Path:
            suffix = f"{task_id}.jsonl" if sample_index <= 0 else f"{task_id}.sample_{sample_index}.jsonl"
            return self.result_store.output_dir / self.config.run_id / "lifecycle" / suffix

        def _record_lifecycle(
            task: BenchmarkTask,
            sample_index: int,
            stage: str,
            metadata: dict | None = None,
        ) -> None:
            append_lifecycle_event(
                _lifecycle_path_for(task.task_id, sample_index),
                run_id=self.config.run_id,
                task_id=task.task_id,
                sample_index=sample_index,
                stage=stage,
                metadata=metadata or {},
            )

        for selected_task, selected_sample_index in work_items:
            _record_lifecycle(
                selected_task,
                selected_sample_index,
                "selected",
                {
                    "agent": self.config.agent_name,
                    "benchmark": self.config.benchmark_name,
                    "scorer": self.config.scorer_name,
                    "max_concurrent": self.config.max_concurrent,
                },
            )

        # Initialize plain-text dashboard.
        dashboard = None
        try:
            from alphadiana.engine.dashboard import PlainTextDashboard
            status_dir = self.result_store.output_dir / self.config.run_id / "status"
            status_dir.mkdir(parents=True, exist_ok=True)
            dashboard = PlainTextDashboard(
                status_dir / "dashboard.txt", [t for t, _ in work_items],
            )
        except Exception:
            logger.debug("Dashboard initialization skipped", exc_info=True)

        gateway_agent_label = self.config.agent_name

        # Optional multi-sandbox predeploy for gateway auto-deploy mode.
        # This is the CLI equivalent of the dashboard deploy-and-run flow:
        # create N sandboxes up front, start one gateway/bridge per sandbox, and let
        # the agent round-robin across the resulting gateway_pool.
        predeployed_sessions = []
        predeployed_session_by_sandbox_id: dict[str, object] = {}
        predeployed_logprob_proxies: dict[str, object] = {}
        predeployed_gateway_api_base_by_sandbox_id: dict[str, str] = {}
        predeployed_in_use_session_ids: set[str] = set()
        predeployed_session_reset_lock = threading.Lock()
        predeployed_session_pool_lock = threading.Lock()
        predeployed_replacement_state_lock = threading.Lock()
        predeployed_replenishment_threads: list[threading.Thread] = []
        predeployed_replacements_in_progress = 0
        predeployed_shutdown_event = threading.Event()
        predeployed_gateway_deployer = None
        predeployed_replacement_count = 0
        predeployed_work_start_lock = threading.Lock()
        predeployed_started_work_items = 0
        predeploy_partial = False
        desired_num = 1
        predeployed_active_target = 1
        predeployed_replenish_concurrency = 1
        predeployed_replacement_slots = threading.BoundedSemaphore(1)
        reuse_predeployed_sandboxes = _coerce_config_bool(
            self.config.agent_config.get("reuse_predeployed_sandboxes"),
            default=False,
        )
        standby_sandboxes = max(
            0,
            int(self.config.agent_config.get("standby_sandboxes", 0) or 0),
        )
        reset_predeployed_between_tasks = _coerce_config_bool(
            self.config.agent_config.get("reset_predeployed_between_tasks"),
            default=True,
        )
        predeployed_lease_probe_enabled = _coerce_config_bool(
            self.config.agent_config.get("predeployed_lease_probe"),
            default=True,
        )
        predeployed_lease_probe_timeout = float(
            self.config.agent_config.get("predeployed_lease_probe_timeout", 2.0) or 0.0
        )
        fresh_predeployed_mode = False
        if (
            self.sandbox is None
            and _should_predeploy_openclaw(self.config)
        ):
            explicit_num = int(self.config.agent_config.get("num_sandboxes", 0) or 0)
            auto_num = (
                math.ceil(self.config.max_concurrent / OPENCLAW_CONCURRENCY_PER_SANDBOX)
                if self.config.max_concurrent > 1 else 1
            )
            predeployed_active_target = max(1, explicit_num or auto_num)
            fresh_predeployed_mode = not reuse_predeployed_sandboxes
            desired_num = predeployed_active_target
            if fresh_predeployed_mode:
                desired_num += standby_sandboxes
            explicit_replenish_concurrency = self.config.agent_config.get(
                "predeploy_replenish_concurrency"
            )
            if explicit_replenish_concurrency is None:
                default_replenish_concurrency = min(
                    predeployed_active_target,
                    standby_sandboxes or predeployed_active_target,
                )
                predeployed_replenish_concurrency = max(1, default_replenish_concurrency)
            else:
                predeployed_replenish_concurrency = max(
                    1,
                    int(explicit_replenish_concurrency or 1),
                )
            predeployed_replacement_slots = threading.BoundedSemaphore(
                predeployed_replenish_concurrency
            )
            if desired_num > 1 or fresh_predeployed_mode:
                deployment_results = []
                try:
                    import alphadiana.engine.sandbox.rock  # noqa: F401 — trigger registration
                    from alphadiana.engine.sandbox.registry import SandboxRegistry

                    auto_sandbox_config = {
                        "admin_base_url": self.config.agent_config.get(
                            "admin_base_url",
                            self.config.agent_config.get("rock_admin_url", ""),
                        ) or resolve_rock_ports_from_env().base_url,
                        "proxy_base_url": self.config.agent_config.get(
                            "proxy_base_url",
                            self.config.agent_config.get("rock_proxy_url", ""),
                        ) or resolve_rock_ports_from_env().proxy_api_url,
                        "image": self.config.agent_config.get("rock_image", "python:3.11"),
                        "memory": self.config.agent_config.get("rock_memory", "2g"),
                        "cpus": float(self.config.agent_config.get("rock_cpus", 0.5)),
                        "limit_cpus": (
                            float(
                                self.config.agent_config.get(
                                    "rock_limit_cpus",
                                    self.config.agent_config.get("limit_cpus"),
                                )
                            )
                            if self.config.agent_config.get(
                                "rock_limit_cpus",
                                self.config.agent_config.get("limit_cpus"),
                            ) is not None
                            else None
                        ),
                        "startup_timeout": int(self.config.agent_config.get("rock_startup_timeout", 300)),
                        "auto_clear_seconds": int(self.config.agent_config.get("rock_auto_clear_seconds", 7200)),
                        "start_retries": int(self.config.agent_config.get("rock_start_retries", 3)),
                        "reset_between_tasks": False,
                        "proxy_timeout": int(self.config.agent_config.get("proxy_timeout", 1800)),
                        "network_mode": self.config.agent_config.get("network_mode", None),
                        "use_kata_runtime": bool(
                            self.config.agent_config.get(
                                "rock_use_kata_runtime",
                                self.config.agent_config.get("use_kata_runtime", False),
                            )
                        ),
                    }
                    cache_key = _build_openclaw_profile_cache_key(
                        self.config,
                        str(auto_sandbox_config["admin_base_url"]),
                    )
                    cached_profile = None
                    if _should_load_cached_openclaw_profile(
                        self.config,
                        float(auto_sandbox_config["cpus"]),
                    ):
                        cached_profile = _load_cached_openclaw_profile(cache_key)
                    if cached_profile is not None:
                        auto_sandbox_config["memory"] = cached_profile[0]
                        auto_sandbox_config["cpus"] = cached_profile[1]
                        logger.info(
                            "Loaded persisted OpenClaw startup profile memory=%s cpus=%s",
                            cached_profile[0],
                            cached_profile[1],
                        )
                    rock_cls = SandboxRegistry.get("rock")
                    share_startup_profile = bool(
                        self.config.agent_config.get("share_predeploy_startup_profile", True)
                    )
                    preferred_profile: tuple[str, float] | None = None

                    def _deploy_one_gateway(sb_idx: int, *, label: str = "Predeploy"):
                        max_attempts = 3
                        last_error = None
                        for attempt in range(1, max_attempts + 1):
                            session = None
                            logprob_proxy = None
                            try:
                                logger.info(
                                    "%s sandbox %d attempt %d/%d",
                                    label, sb_idx + 1, attempt, max_attempts,
                                )
                                sandbox_backend = rock_cls()
                                sandbox_config = dict(auto_sandbox_config)
                                if share_startup_profile and preferred_profile is not None:
                                    sandbox_config["memory"] = preferred_profile[0]
                                    sandbox_config["cpus"] = preferred_profile[1]
                                    logger.info(
                                        "%s sandbox %d reusing startup profile memory=%s cpus=%s",
                                        label,
                                        sb_idx + 1,
                                        preferred_profile[0],
                                        preferred_profile[1],
                                    )
                                sandbox_backend.setup(sandbox_config)
                                session = sandbox_backend.create_session()
                                runtime_config = self.config.agent_config
                                start_logprob_proxy = getattr(
                                    self.agent,
                                    "start_logprob_proxy_for_gateway",
                                    None,
                                )
                                if callable(start_logprob_proxy):
                                    started_proxy = start_logprob_proxy()
                                    if started_proxy is not None:
                                        logprob_proxy, proxy_api_base, proxy_api_key = started_proxy
                                        runtime_config = dict(self.config.agent_config)
                                        runtime_config.update({
                                            "OPENAI_BASE_URL": proxy_api_base,
                                            "openai_base_url": proxy_api_base,
                                            "OPENAI_API_KEY": proxy_api_key,
                                            "openai_api_key": proxy_api_key,
                                        })
                                runtime_manager = _make_gateway_runtime_manager(
                                    replace(self.config, agent_config=runtime_config)
                                )
                                info = runtime_manager.ensure_ready(session)
                                md = session.metadata() if hasattr(session, "metadata") else {}
                                profile_memory = str(md.get("memory", sandbox_config["memory"]))
                                profile_cpus = float(md.get("cpus", sandbox_config["cpus"]))
                                return session, info, (profile_memory, profile_cpus), logprob_proxy
                            except Exception as exc:
                                last_error = exc
                                logger.warning(
                                    "%s sandbox %d attempt %d/%d failed: %s",
                                    label, sb_idx + 1, attempt, max_attempts, exc,
                                )
                                if logprob_proxy is not None:
                                    try:
                                        logprob_proxy.stop()
                                    except Exception:
                                        pass
                                if session is not None:
                                    try:
                                        session.close()
                                    except Exception:
                                        pass
                                if attempt == max_attempts:
                                    break
                                time.sleep(5)
                        assert last_error is not None
                        raise last_error

                    predeployed_gateway_deployer = _deploy_one_gateway

                    logger.info(
                        "Predeploying %d %s sandboxes for CLI concurrency "
                        "(max_concurrent=%d, target=%d tasks/sandbox, reuse=%s, "
                        "standby=%d, replenish_concurrency=%d)",
                        desired_num,
                        gateway_agent_label,
                        self.config.max_concurrent,
                        OPENCLAW_CONCURRENCY_PER_SANDBOX,
                        reuse_predeployed_sandboxes,
                        standby_sandboxes if fresh_predeployed_mode else 0,
                        predeployed_replenish_concurrency if fresh_predeployed_mode else 1,
                    )
                    stagger_sec = float(self.config.agent_config.get("predeploy_stagger_seconds", 2.0) or 0.0)
                    for i in range(desired_num):
                        try:
                            deployed = _deploy_one_gateway(i)
                            deployment_results.append(deployed)
                            preferred_profile = deployed[2]
                        except Exception as exc:
                            if not deployment_results:
                                raise
                            if self.config.strict_isolation:
                                raise RuntimeError(
                                    "strict_isolation=true: predeploy stopped early after "
                                    f"{len(deployment_results)}/{desired_num} sandbox(es): {exc}"
                                ) from exc
                            logger.warning(
                                "Predeploy stopped early at sandbox %d/%d: %s. "
                                "Continuing with %d predeployed sandbox(es).",
                                i + 1,
                                desired_num,
                                exc,
                                len(deployment_results),
                            )
                            predeploy_partial = True
                            break
                        if stagger_sec > 0 and i + 1 < desired_num:
                            time.sleep(stagger_sec)

                    predeployed_sessions = [session for session, _, _, _ in deployment_results]
                    gateway_pool = [info["api_base"] for _, info, _, _ in deployment_results]
                    predeployed_session_by_sandbox_id = {
                        str(getattr(session, "sandbox_id", "")): session
                        for session in predeployed_sessions
                        if str(getattr(session, "sandbox_id", ""))
                    }
                    predeployed_gateway_api_base_by_sandbox_id = {
                        str(getattr(session, "sandbox_id", "")): str(info["api_base"])
                        for session, info, _, _ in deployment_results
                        if str(getattr(session, "sandbox_id", ""))
                    }
                    predeployed_logprob_proxies = {
                        str(getattr(session, "sandbox_id", "")): proxy
                        for session, _, _, proxy in deployment_results
                        if str(getattr(session, "sandbox_id", "")) and proxy is not None
                    }
                    if fresh_predeployed_mode:
                        effective_capacity = max(
                            1,
                            min(predeployed_active_target, len(gateway_pool)),
                        )
                    else:
                        effective_capacity = max(
                            1,
                            len(gateway_pool) * OPENCLAW_CONCURRENCY_PER_SANDBOX,
                        )
                    if self.config.max_concurrent > effective_capacity:
                        logger.warning(
                            "Lowering max_concurrent from %d to %d due to available sandbox capacity.",
                            self.config.max_concurrent,
                            effective_capacity,
                        )
                        self.config.max_concurrent = effective_capacity
                    self.config.agent_config["gateway_pool"] = gateway_pool
                    if self.config.agent_name == "openclaw":
                        self.config.agent_config["api_base"] = gateway_pool[0]
                    else:
                        self.config.agent_config["gateway_api_base"] = gateway_pool[0]
                    self.config.agent_config["sandbox_id"] = predeployed_sessions[0].sandbox_id
                    self.config.agent_config["rock_sandbox_url"] = (
                        predeployed_sessions[0].metadata().get("proxy_base_url", "")
                    )
                    if predeployed_gateway_api_base_by_sandbox_id:
                        self.config.agent_config["_predeployed_gateway_api_base_by_sandbox_id"] = (
                            predeployed_gateway_api_base_by_sandbox_id
                        )
                    if predeployed_logprob_proxies:
                        self.config.agent_config["_predeployed_logprob_proxies"] = (
                            predeployed_logprob_proxies
                        )
                    if preferred_profile is not None:
                        _save_cached_openclaw_profile(
                            cache_key,
                            preferred_profile[0],
                            preferred_profile[1],
                        )
                        logger.info(
                            "Persisted %s startup profile memory=%s cpus=%s",
                            gateway_agent_label,
                            preferred_profile[0],
                            preferred_profile[1],
                        )
                    self.agent.setup(self.config.agent_config)
                    logger.info(
                        "%s gateway_pool ready with %d sandboxes: %s",
                        gateway_agent_label,
                        len(gateway_pool),
                        gateway_pool,
                    )
                except Exception as exc:
                    for session, _, _, proxy in deployment_results:
                        if proxy is not None:
                            try:
                                proxy.stop()
                            except Exception:
                                pass
                        try:
                            session.close()
                        except Exception:
                            pass
                    if self.config.strict_isolation:
                        raise RuntimeError(
                            "strict_isolation=true: failed to predeploy "
                            f"{desired_num} {gateway_agent_label} sandbox(es): {exc}"
                        ) from exc
                    logger.warning(
                        "Failed to predeploy %d %s sandboxes: %s. "
                        "Falling back to single-sandbox auto-deploy.",
                        desired_num,
                        gateway_agent_label,
                        exc,
                    )
                    for session in predeployed_sessions:
                        try:
                            session.close()
                        except Exception:
                            pass
                    predeployed_sessions = []

        # Auto-create a ROCK sandbox when the agent requires one implicitly and
        # no sandbox_name was explicitly configured (sandbox: null).
        _auto_sandbox = None
        if (
            self.sandbox is None
            and not predeployed_sessions
            and _needs_auto_rock_sandbox(self.config)
        ):
            try:
                import alphadiana.engine.sandbox.rock  # noqa: F401 — trigger registration
                from alphadiana.engine.sandbox.registry import SandboxRegistry
                rock_cls = SandboxRegistry.get("rock")
                _auto_sandbox = rock_cls()
                # Build sandbox config from agent config, with gateway-friendly defaults.
                auto_sandbox_config = {
                    "admin_base_url": self.config.agent_config.get(
                        "admin_base_url",
                        self.config.agent_config.get("rock_admin_url", ""),
                    ) or resolve_rock_ports_from_env().base_url,
                    "proxy_base_url": self.config.agent_config.get(
                        "proxy_base_url",
                        self.config.agent_config.get("rock_proxy_url", ""),
                    ) or resolve_rock_ports_from_env().proxy_api_url,
                    "image": self.config.agent_config.get("rock_image", "python:3.11"),
                    # Lower resource profile to support multiple parallel sandboxes.
                    "memory": self.config.agent_config.get("rock_memory", "2g"),
                    "cpus": float(self.config.agent_config.get("rock_cpus", 0.5)),
                    "limit_cpus": (
                        float(
                            self.config.agent_config.get(
                                "rock_limit_cpus",
                                self.config.agent_config.get("limit_cpus"),
                            )
                        )
                        if self.config.agent_config.get(
                            "rock_limit_cpus",
                            self.config.agent_config.get("limit_cpus"),
                        ) is not None
                        else None
                    ),
                    "startup_timeout": int(self.config.agent_config.get("rock_startup_timeout", 300)),
                    "auto_clear_seconds": int(self.config.agent_config.get("rock_auto_clear_seconds", 7200)),
                    "start_retries": int(self.config.agent_config.get("rock_start_retries", 3)),
                    # Do NOT reset workspace between tasks: the in-sandbox gateway/bridge
                    # keeps running in the container and owns the workspace lifecycle.
                    "reset_between_tasks": False,
                    "proxy_timeout": int(self.config.agent_config.get("proxy_timeout", 1800)),
                    "network_mode": self.config.agent_config.get("network_mode", None),
                    "use_kata_runtime": bool(
                        self.config.agent_config.get(
                            "rock_use_kata_runtime",
                            self.config.agent_config.get("use_kata_runtime", False),
                        )
                    ),
                }
                _auto_sandbox.setup(auto_sandbox_config)
                logger.info(
                    "Auto-created ROCK sandbox for %s concurrent isolation "
                    "(max_concurrent=%d, memory=%s, cpus=%s)",
                    gateway_agent_label,
                    self.config.max_concurrent,
                    auto_sandbox_config["memory"],
                    auto_sandbox_config["cpus"],
                )
                # Treat _auto_sandbox as the sandbox for pool creation below.
                self.sandbox = _auto_sandbox
            except Exception as exc:
                if self.config.agent_name == "zeroclaw":
                    if self.config.strict_isolation:
                        raise RuntimeError(
                            "strict_isolation=true: failed to auto-create ROCK sandbox; "
                            "refusing shared-gateway fallback"
                        ) from exc
                    raise RuntimeError(
                        "ZeroClaw requires a ROCK sandbox; auto-create failed and "
                        "host/shared fallback is disabled."
                    ) from exc
                if self.config.strict_isolation:
                    raise RuntimeError(
                        "strict_isolation=true: failed to auto-create ROCK sandbox; "
                        "refusing shared-gateway fallback"
                    ) from exc
                logger.warning(
                    "Failed to auto-create ROCK sandbox for %s isolation: %s. "
                    "Falling back to shared gateway (may cause workspace contention at max_concurrent>1).",
                    gateway_agent_label,
                    exc,
                )
                _auto_sandbox = None

        isolation_mode = "shared_gateway"
        if predeployed_sessions:
            if fresh_predeployed_mode:
                isolation_mode = (
                    "partial_fresh_predeployed_pool"
                    if predeploy_partial
                    else "fresh_predeployed_pool"
                )
            else:
                isolation_mode = "partial_predeploy" if predeploy_partial else "predeployed_pool"
        elif _auto_sandbox is not None:
            isolation_mode = "auto_single_sandbox"
        elif self.sandbox is not None:
            isolation_mode = "explicit_sandbox"
        self.result_store._run_metadata["strict_isolation"] = self.config.strict_isolation
        self.result_store._run_metadata["isolation_mode"] = isolation_mode
        if predeployed_sessions:
            self.result_store._run_metadata["reuse_predeployed_sandboxes"] = reuse_predeployed_sandboxes
            self.result_store._run_metadata["standby_sandboxes"] = (
                standby_sandboxes if fresh_predeployed_mode else 0
            )
            self.result_store._run_metadata["predeploy_replenish_concurrency"] = (
                predeployed_replenish_concurrency if fresh_predeployed_mode else 1
            )
        manifest = self.result_store.load_manifest()
        if manifest:
            manifest["strict_isolation"] = self.config.strict_isolation
            manifest["isolation_mode"] = isolation_mode
            if predeployed_sessions:
                manifest["reuse_predeployed_sandboxes"] = reuse_predeployed_sandboxes
                manifest["standby_sandboxes"] = (
                    standby_sandboxes if fresh_predeployed_mode else 0
                )
                manifest["predeploy_replenish_concurrency"] = (
                    predeployed_replenish_concurrency if fresh_predeployed_mode else 1
                )
            self.result_store.save_manifest(manifest)

        # Set up sandbox pool for concurrent execution.
        # Skip pool for openclaw: it handles concurrency internally via
        # gateway_pool (multi-sandbox).  A SandboxPool would create N sessions
        # inside a single container, causing workspace contention.
        pool = None
        sandbox_supports_pooling = True
        if self.sandbox is not None:
            supports_pooling = getattr(self.sandbox, "supports_pooling", None)
            if callable(supports_pooling):
                sandbox_supports_pooling = bool(supports_pooling())

        if (
            self.sandbox is not None
            and self.config.max_concurrent > 1
            and sandbox_supports_pooling
            and self.config.agent_name != "openclaw"
        ):
            from alphadiana.engine.sandbox.pool import SandboxPool
            pool_size = self.config.max_concurrent
            logger.info("Creating SandboxPool with %d sessions", pool_size)
            pool = SandboxPool(self.sandbox, pool_size)

        # For sequential mode, create a single shared session to reuse across
        # all tasks instead of creating (and tearing down) one per task.
        #
        # OpenClaw is excluded here: benchmark fairness requires a fresh
        # sandbox/runtime per task so stale gateway state cannot leak across
        # tasks inside a reused session.
        shared_session = None
        sandbox_supports_shared_session = True
        if self.sandbox is not None:
            supports_shared_session = getattr(self.sandbox, "supports_shared_session", None)
            if callable(supports_shared_session):
                sandbox_supports_shared_session = bool(supports_shared_session())
        _persistent_memory = bool(self.config.agent_config.get("persistent_memory", False))
        if (
            self.sandbox is not None
            and pool is None
            and sandbox_supports_shared_session
            and (self.config.agent_name != "openclaw" or _persistent_memory)
        ):
            logger.info("Creating shared sandbox session for sequential execution")
            shared_session = self.sandbox.create_session()
        predeployed_session_queue = None
        predeployed_live_session_ids: set[str] = set()
        if predeployed_sessions:
            predeployed_session_queue = queue.Queue()
            for session in predeployed_sessions:
                predeployed_session_queue.put(session)
                sandbox_id = str(getattr(session, "sandbox_id", "") or "")
                if sandbox_id:
                    predeployed_live_session_ids.add(sandbox_id)

        def _live_predeployed_count() -> int:
            with predeployed_session_pool_lock:
                return len(predeployed_live_session_ids)

        def _fresh_predeployed_pool_snapshot() -> dict[str, int]:
            with predeployed_work_start_lock:
                remaining_not_started = max(
                    0,
                    len(work_items) - predeployed_started_work_items,
                )
            with predeployed_session_pool_lock:
                live_count = len(predeployed_live_session_ids)
                in_use_count = len(predeployed_in_use_session_ids)
                ready_count = max(0, live_count - in_use_count)
            with predeployed_replacement_state_lock:
                in_progress = predeployed_replacements_in_progress
            target_live = min(
                desired_num,
                remaining_not_started + in_use_count,
            )
            deficit = max(0, target_live - live_count - in_progress)
            return {
                "remaining_not_started": remaining_not_started,
                "live": live_count,
                "in_use": in_use_count,
                "ready": ready_count,
                "in_progress": in_progress,
                "target_live": target_live,
                "deficit": deficit,
            }

        def _drop_predeployed_agent_maps(sandbox_id: str, proxy: object | None) -> None:
            agent_gateway_map = getattr(self.agent, "_predeployed_gateway_api_base_by_sandbox_id", None)
            if isinstance(agent_gateway_map, dict):
                agent_gateway_map.pop(sandbox_id, None)
            agent_proxy_map = getattr(self.agent, "_predeployed_logprob_proxies", None)
            if isinstance(agent_proxy_map, dict):
                agent_proxy_map.pop(sandbox_id, None)
            agent_proxies = getattr(self.agent, "_logprob_proxies", None)
            if proxy is not None and isinstance(agent_proxies, list):
                try:
                    agent_proxies.remove(proxy)
                except ValueError:
                    pass

        def _drop_predeployed_gateway_pool_entry(api_base: str) -> None:
            api_base = str(api_base or "").strip()
            if not api_base:
                return
            gateway_pool_config = self.config.agent_config.get("gateway_pool")
            if isinstance(gateway_pool_config, list):
                gateway_pool_config[:] = [
                    item for item in gateway_pool_config if str(item).strip() != api_base
                ]
            agent_gateway_pool = getattr(self.agent, "_gateway_pool", None)
            if agent_gateway_pool is not None:
                try:
                    remaining = [
                        item for item in list(agent_gateway_pool)
                        if str(item).strip() != api_base
                    ]
                    agent_gateway_pool.clear()
                    agent_gateway_pool.extend(remaining)
                except Exception:
                    logger.debug("Failed to drop stale predeployed gateway pool entry", exc_info=True)

        def _unregister_predeployed_session(session: object, sandbox_id: str) -> tuple[str, object | None]:
            resolved_sandbox_id = str(sandbox_id or getattr(session, "sandbox_id", "") or "")
            proxy = None
            api_base = ""
            with predeployed_session_pool_lock:
                if resolved_sandbox_id:
                    predeployed_live_session_ids.discard(resolved_sandbox_id)
                    predeployed_in_use_session_ids.discard(resolved_sandbox_id)
                    predeployed_session_by_sandbox_id.pop(resolved_sandbox_id, None)
                    api_base = predeployed_gateway_api_base_by_sandbox_id.pop(
                        resolved_sandbox_id,
                        "",
                    )
                    proxy = predeployed_logprob_proxies.pop(resolved_sandbox_id, None)
                    _drop_predeployed_agent_maps(resolved_sandbox_id, proxy)
                    gateway_map = self.config.agent_config.get(
                        "_predeployed_gateway_api_base_by_sandbox_id"
                    )
                    if isinstance(gateway_map, dict):
                        gateway_map.pop(resolved_sandbox_id, None)
                    proxy_map = self.config.agent_config.get("_predeployed_logprob_proxies")
                    if isinstance(proxy_map, dict):
                        proxy_map.pop(resolved_sandbox_id, None)
                try:
                    predeployed_sessions.remove(session)
                except ValueError:
                    pass
            _drop_predeployed_gateway_pool_entry(api_base)
            return resolved_sandbox_id, proxy

        def _register_predeployed_replacement(
            session: object,
            info: dict,
            proxy: object | None,
            reason: str,
            task_id: str,
        ) -> bool:
            if predeployed_session_queue is None:
                return False
            sandbox_id = str(getattr(session, "sandbox_id", "") or info.get("sandbox_id", "") or "")
            api_base = str(info.get("api_base", "") or "")
            if predeployed_shutdown_event.is_set() or not sandbox_id or not api_base:
                logger.warning(
                    "Replacement predeployed sandbox for task %s is no longer needed "
                    "or missing sandbox_id/api_base; closing it",
                    task_id,
                )
                try:
                    stop_proxy = getattr(proxy, "stop", None)
                    if callable(stop_proxy):
                        stop_proxy()
                except Exception:
                    logger.debug("Failed to stop incomplete replacement logprob proxy", exc_info=True)
                try:
                    close = getattr(session, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    logger.debug("Failed to close incomplete replacement session", exc_info=True)
                return False

            with predeployed_session_pool_lock:
                predeployed_sessions.append(session)
                predeployed_session_by_sandbox_id[sandbox_id] = session
                predeployed_gateway_api_base_by_sandbox_id[sandbox_id] = api_base
                predeployed_live_session_ids.add(sandbox_id)
                gateway_pool_config = self.config.agent_config.setdefault("gateway_pool", [])
                if isinstance(gateway_pool_config, list) and api_base not in gateway_pool_config:
                    gateway_pool_config.append(api_base)
                gateway_map = self.config.agent_config.setdefault(
                    "_predeployed_gateway_api_base_by_sandbox_id",
                    {},
                )
                if isinstance(gateway_map, dict):
                    gateway_map[sandbox_id] = api_base
                if proxy is not None:
                    predeployed_logprob_proxies[sandbox_id] = proxy
                    proxy_map = self.config.agent_config.setdefault("_predeployed_logprob_proxies", {})
                    if isinstance(proxy_map, dict):
                        proxy_map[sandbox_id] = proxy
                agent_gateway_map = getattr(self.agent, "_predeployed_gateway_api_base_by_sandbox_id", None)
                if isinstance(agent_gateway_map, dict):
                    agent_gateway_map[sandbox_id] = api_base
                agent_gateway_pool = getattr(self.agent, "_gateway_pool", None)
                if agent_gateway_pool is not None and api_base not in list(agent_gateway_pool):
                    try:
                        agent_gateway_pool.append(api_base)
                    except Exception:
                        logger.debug("Failed to append replacement gateway to agent pool", exc_info=True)
                if proxy is not None:
                    agent_proxy_map = getattr(self.agent, "_predeployed_logprob_proxies", None)
                    if isinstance(agent_proxy_map, dict):
                        agent_proxy_map[sandbox_id] = proxy
                    agent_proxies = getattr(self.agent, "_logprob_proxies", None)
                    if isinstance(agent_proxies, list) and proxy not in agent_proxies:
                        agent_proxies.append(proxy)
                remaining = len(predeployed_live_session_ids)
            predeployed_session_queue.put(session)
            logger.info(
                "Replacement predeployed sandbox ready for task %s sandbox_id=%s reason=%s live=%d",
                task_id,
                sandbox_id,
                reason,
                remaining,
            )
            return True

        def _predeployed_replacement_in_progress_count() -> int:
            with predeployed_replacement_state_lock:
                return predeployed_replacements_in_progress

        def _create_predeployed_replacement(
            reason: str,
            task_id: str,
            *,
            force: bool = False,
            require_fresh_need: bool = True,
        ) -> bool:
            nonlocal predeployed_replacement_count, predeployed_replacements_in_progress, preferred_profile
            if predeployed_gateway_deployer is None or predeployed_shutdown_event.is_set():
                return False
            if not predeployed_replacement_slots.acquire(blocking=False):
                logger.info(
                    "Skipping replacement predeploy for task %s reason=%s: "
                    "replenish concurrency limit reached (%d)",
                    task_id,
                    reason,
                    predeployed_replenish_concurrency,
                )
                return False
            with predeployed_replacement_state_lock:
                predeployed_replacements_in_progress += 1
            try:
                if predeployed_shutdown_event.is_set():
                    return False
                if (
                    fresh_predeployed_mode
                    and require_fresh_need
                    and not _fresh_predeployed_needs_replenishment()
                ):
                    return True
                if not fresh_predeployed_mode and not force and _live_predeployed_count() > 0:
                    return True
                with predeployed_replacement_state_lock:
                    predeployed_replacement_count += 1
                    replacement_idx = desired_num + predeployed_replacement_count - 1
                try:
                    deployed = predeployed_gateway_deployer(
                        replacement_idx,
                        label="Replacement predeploy",
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to create replacement predeployed sandbox for task %s reason=%s: %s",
                        task_id,
                        reason,
                        exc,
                    )
                    return False
                session, info, profile, proxy = deployed
                preferred_profile = profile
                return _register_predeployed_replacement(session, info, proxy, reason, task_id)
            finally:
                with predeployed_replacement_state_lock:
                    predeployed_replacements_in_progress = max(
                        0,
                        predeployed_replacements_in_progress - 1,
                    )
                try:
                    predeployed_replacement_slots.release()
                except ValueError:
                    logger.debug("Replacement predeploy slot release overflow", exc_info=True)

        def _start_predeployed_replacement(
            reason: str,
            task_id: str,
            *,
            force: bool = True,
            require_fresh_need: bool = True,
        ) -> bool:
            if predeployed_gateway_deployer is None or predeployed_shutdown_event.is_set():
                return False

            def _target() -> None:
                _create_predeployed_replacement(
                    reason,
                    task_id,
                    force=force,
                    require_fresh_need=require_fresh_need,
                )

            thread = threading.Thread(
                target=_target,
                name=f"predeploy-replenish-{task_id}",
                daemon=True,
            )
            predeployed_replenishment_threads.append(thread)
            thread.start()
            return True

        def _start_fresh_predeployed_replenishment(reason: str, task_id: str) -> int:
            if (
                not fresh_predeployed_mode
                or predeployed_gateway_deployer is None
                or predeployed_shutdown_event.is_set()
            ):
                return 0
            snapshot = _fresh_predeployed_pool_snapshot()
            in_progress = snapshot["in_progress"]
            available_slots = max(0, predeployed_replenish_concurrency - in_progress)
            to_start = min(snapshot["deficit"], available_slots)
            if to_start <= 0:
                return 0
            logger.info(
                "Starting %d OpenClaw predeploy replenishment(s) for task %s "
                "reason=%s live=%d ready=%d in_use=%d in_progress=%d "
                "target=%d deficit=%d replenish_concurrency=%d",
                to_start,
                task_id,
                reason,
                snapshot["live"],
                snapshot["ready"],
                snapshot["in_use"],
                in_progress,
                snapshot["target_live"],
                snapshot["deficit"],
                predeployed_replenish_concurrency,
            )
            started = 0
            for _ in range(to_start):
                if _start_predeployed_replacement(reason, task_id, force=True):
                    started += 1
            return started

        def _probe_predeployed_session_before_lease(session: object, task_id: str) -> str:
            if (
                not predeployed_lease_probe_enabled
                or predeployed_lease_probe_timeout <= 0
            ):
                return ""
            has_published_probe = callable(getattr(session, "published_base", None)) or callable(
                getattr(session, "published_port", None)
            )
            if not has_published_probe:
                return ""
            sandbox_id = str(getattr(session, "sandbox_id", "") or "")
            if not sandbox_id:
                return ""
            with predeployed_session_pool_lock:
                api_base = str(predeployed_gateway_api_base_by_sandbox_id.get(sandbox_id, "") or "")
            if not api_base:
                return "gateway_missing_api_base"
            probe_url = f"{api_base.rstrip('/')}/models"
            try:
                response = httpx.get(
                    probe_url,
                    timeout=predeployed_lease_probe_timeout,
                    trust_env=False,
                )
                # OpenClaw's prebuilt gateway can return 404 for /v1/models; any
                # response below 500 still proves the host-published gateway is reachable.
                if response.status_code >= 500:
                    return f"gateway_probe_http_{response.status_code}"
                return ""
            except Exception as exc:
                reason = _predeployed_session_failure_reason(exc)
                if reason:
                    return reason
                return "gateway_probe_failed"

        def _discard_unhealthy_predeployed_session(
            session: object,
            reason: str,
            task_id: str,
        ) -> None:
            sandbox_id = str(getattr(session, "sandbox_id", "") or "")
            resolved_sandbox_id, proxy = _unregister_predeployed_session(session, sandbox_id)
            logger.warning(
                "Discarding unhealthy predeployed sandbox before task %s sandbox_id=%s reason=%s",
                task_id,
                resolved_sandbox_id or "<unknown>",
                reason,
            )
            if proxy is not None:
                try:
                    proxy.stop()
                except Exception:
                    logger.debug(
                        "Failed to stop logprob proxy for unhealthy sandbox_id=%s",
                        resolved_sandbox_id,
                        exc_info=True,
                    )
            try:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            except Exception as exc:
                logger.warning(
                    "Failed to close unhealthy predeployed sandbox_id=%s: %s",
                    resolved_sandbox_id or "<unknown>",
                    exc,
                )
            if (
                fresh_predeployed_mode
                and not predeployed_shutdown_event.is_set()
                and not (self.cancel_event is not None and self.cancel_event.is_set())
            ):
                _start_fresh_predeployed_replenishment(reason, task_id)

        def _acquire_predeployed_session(task_id: str):
            if predeployed_session_queue is None:
                raise RuntimeError("predeployed session queue is not configured")
            while True:
                try:
                    session = predeployed_session_queue.get(timeout=1.0)
                    lease_probe_reason = _probe_predeployed_session_before_lease(
                        session,
                        task_id,
                    )
                    if lease_probe_reason:
                        _discard_unhealthy_predeployed_session(
                            session,
                            lease_probe_reason,
                            task_id,
                        )
                        continue
                    if fresh_predeployed_mode:
                        sandbox_id = str(getattr(session, "sandbox_id", "") or "")
                        if sandbox_id:
                            with predeployed_session_pool_lock:
                                predeployed_in_use_session_ids.add(sandbox_id)
                    return session
                except queue.Empty:
                    if fresh_predeployed_mode:
                        _start_fresh_predeployed_replenishment(
                            "predeployed_queue_wait",
                            task_id,
                        )
                    if fresh_predeployed_mode and _predeployed_replacement_in_progress_count() > 0:
                        continue
                    if _live_predeployed_count() == 0:
                        if _create_predeployed_replacement(
                            "predeployed_pool_depleted",
                            task_id,
                            force=True,
                            require_fresh_need=False,
                        ):
                            continue
                        raise RuntimeError(
                            "No live predeployed sandbox sessions remain; "
                            "restart the run to create a fresh gateway pool"
                        )
                    if self.cancel_event is not None and self.cancel_event.is_set():
                        raise RuntimeError(f"Cancelled while waiting for predeployed sandbox for task {task_id}")

        def _fresh_predeployed_needs_replenishment(
            *,
            replacement_in_progress_offset: int = 0,
        ) -> bool:
            snapshot = _fresh_predeployed_pool_snapshot()
            adjusted_deficit = snapshot["target_live"] - snapshot["live"] - max(
                0,
                snapshot["in_progress"] - replacement_in_progress_offset,
            )
            return adjusted_deficit > 0

        def _quarantine_predeployed_session(session: object, sandbox_id: str, reason: str, task_id: str) -> None:
            resolved_sandbox_id, proxy = _unregister_predeployed_session(session, sandbox_id)
            remaining = _live_predeployed_count()
            logger.warning(
                "Quarantining predeployed sandbox for task %s sandbox_id=%s reason=%s remaining=%d",
                task_id,
                resolved_sandbox_id or "<unknown>",
                reason,
                remaining,
            )
            if proxy is not None:
                try:
                    proxy.stop()
                except Exception:
                    logger.debug(
                        "Failed to stop logprob proxy for quarantined sandbox_id=%s",
                        resolved_sandbox_id,
                        exc_info=True,
                    )
            try:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            except Exception as exc:
                logger.warning(
                    "Failed to close quarantined predeployed sandbox_id=%s: %s",
                    resolved_sandbox_id or "<unknown>",
                    exc,
                )
            if not (self.cancel_event is not None and self.cancel_event.is_set()):
                if fresh_predeployed_mode and _fresh_predeployed_needs_replenishment():
                    _start_fresh_predeployed_replenishment(reason, task_id)
                elif not fresh_predeployed_mode:
                    _create_predeployed_replacement(reason, task_id, force=True)

        def _retire_fresh_predeployed_session(
            session: object,
            sandbox_id: str,
            task_id: str,
        ) -> None:
            resolved_sandbox_id, proxy = _unregister_predeployed_session(session, sandbox_id)
            replenish = _fresh_predeployed_needs_replenishment()
            logger.info(
                "Closing fresh predeployed sandbox for completed task %s sandbox_id=%s replenish=%s",
                task_id,
                resolved_sandbox_id or "<unknown>",
                replenish,
            )
            if proxy is not None:
                try:
                    proxy.stop()
                except Exception:
                    logger.debug(
                        "Failed to stop logprob proxy for retired sandbox_id=%s",
                        resolved_sandbox_id,
                        exc_info=True,
                    )
            try:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            except Exception as exc:
                logger.warning(
                    "Failed to close fresh predeployed sandbox_id=%s: %s",
                    resolved_sandbox_id or "<unknown>",
                    exc,
                )
            if (
                replenish
                and not predeployed_shutdown_event.is_set()
                and not (self.cancel_event is not None and self.cancel_event.is_set())
            ):
                _start_fresh_predeployed_replenishment(
                    "fresh_predeployed_task_complete",
                    task_id,
                )

        def _reset_predeployed_session(sandbox_id: str, task_id: str) -> bool:
            if not reset_predeployed_between_tasks or not sandbox_id:
                return True
            session = predeployed_session_by_sandbox_id.get(sandbox_id)
            if session is None:
                return True
            with predeployed_session_reset_lock:
                try:
                    reset = getattr(session, "reset", None)
                    if callable(reset):
                        reset()
                    return True
                except Exception as exc:
                    logger.warning(
                        "Predeployed session reset failed for task %s sandbox_id=%s: %s",
                        task_id,
                        sandbox_id,
                        exc,
                    )
                    return False

        # Create the solve function that wraps agent + sandbox + scorer.
        def solve_fn(work_item):
            nonlocal shared_session, predeployed_started_work_items
            task, sample_index = work_item
            if memory_lifecycle.before_work_item(task.task_id, sample_index):
                logger.info(
                    "Resetting harness-native memory at cross-sample task boundary: %s",
                    task.task_id,
                )
                shared_session = self._reset_cross_sample_state(shared_session)
            runtime_task = _bind_runtime_task(task, sample_index)
            if memory_scope:
                runtime_task.metadata["memory_scope"] = memory_scope
            runtime_task.metadata["_lifecycle_path"] = str(
                _lifecycle_path_for(task.task_id, sample_index)
            )
            runtime_task.metadata["_lifecycle_run_id"] = self.config.run_id
            if fresh_predeployed_mode and predeployed_session_queue is not None:
                with predeployed_work_start_lock:
                    predeployed_started_work_items += 1
            _record_lifecycle(
                task,
                sample_index,
                "launched",
                {
                    "agent": self.config.agent_name,
                    "execution_id": runtime_task.metadata.get("execution_id", ""),
                },
            )
            # Acquire sandbox session: from pool (concurrent) or shared (sequential).
            sandbox_session = None
            used_pool = False
            used_predeployed_pool = False
            predeployed_quarantine_reason = ""
            pooled_session_replacement_reason = ""
            response_sandbox_id = ""
            start = time.monotonic()
            response = None
            try:
                if pool is not None:
                    sandbox_session = pool.acquire()
                    used_pool = True
                elif predeployed_session_queue is not None:
                    sandbox_session = _acquire_predeployed_session(task.task_id)
                    used_predeployed_pool = True
                elif shared_session is not None:
                    sandbox_session = shared_session
                elif self.sandbox is not None:
                    if self.config.sandbox_name in {"swebench_container", "decodingtrust"}:
                        sandbox_session = self.sandbox.create_session(task=runtime_task)
                    else:
                        sandbox_session = self.sandbox.create_session()
            except Exception as exc:
                logger.error("Task %s failed before agent start: %s", task.task_id, exc)
                from alphadiana.harness.base import AgentResponse

                sandbox_provenance = _sandbox_config_provenance_metadata(self.config)
                exception_sandbox_metadata = getattr(exc, "sandbox_metadata", None)
                if isinstance(exception_sandbox_metadata, dict):
                    sandbox_provenance.update(exception_sandbox_metadata)
                error_response = AgentResponse(
                    answer=None,
                    wall_time_sec=time.monotonic() - start,
                    metadata={
                        "sample_index": sample_index,
                        "execution_id": runtime_task.metadata.get("execution_id", ""),
                        "sandbox_create_failed": True,
                        "failure_stage": "sandbox_create",
                    },
                    sandbox_metadata=sandbox_provenance,
                )
                error_response.metadata.update({
                    key: value
                    for key, value in sandbox_provenance.items()
                    if key in {"container_engine", "sandbox_backend", "docker_api_version", "podman_socket"}
                    and value not in (None, "")
                })
                if self.sandbox is not None:
                    sandbox_backend_name = getattr(self.sandbox, "name", type(self.sandbox).__name__)
                    error_response.metadata.setdefault("sandbox_backend", sandbox_backend_name)
                _apply_error_provenance_metadata(
                    error_response,
                    _agent_error_provenance_metadata(self.agent),
                )
                self.result_store.append_error(
                    _strip_lifecycle_metadata(runtime_task),
                    error=_build_error_info(exc),
                    response=error_response,
                    sample_index=sample_index,
                )
                _record_lifecycle(
                    task,
                    sample_index,
                    "task_json_written",
                    {
                        "task_json_path": str(
                            self.result_store.output_dir
                            / self.config.run_id
                            / "tasks"
                            / f"{task.task_id}.json"
                        ),
                        "error_type": getattr(exc, "error_type", type(exc).__name__),
                        "failure_stage": "sandbox_create",
                    },
                )
                raise
            if sandbox_session is not None:
                try:
                    lifecycle_sandbox_metadata = _sanitize_success_sandbox_metadata(
                        sandbox_session.metadata()
                    )
                except Exception:
                    lifecycle_sandbox_metadata = {}
                _record_lifecycle(
                    task,
                    sample_index,
                    "sandbox_started",
                    {
                        "sandbox_id": str(getattr(sandbox_session, "sandbox_id", "") or ""),
                        "container_engine": lifecycle_sandbox_metadata.get("container_engine", ""),
                        "sandbox_backend": getattr(self.sandbox, "name", type(self.sandbox).__name__)
                        if self.sandbox is not None
                        else "",
                    },
                )
            try:
                # Run the agent.
                response = self.agent.solve(runtime_task, sandbox_session)
                _record_lifecycle(
                    task,
                    sample_index,
                    "agent_done",
                    {
                        "finish_reason": getattr(response, "finish_reason", ""),
                        "answer_present": bool(getattr(response, "answer", None)),
                        "raw_output_chars": len(str(getattr(response, "raw_output", "") or "")),
                    },
                )
                response.metadata.setdefault("sample_index", sample_index)
                response.metadata.setdefault(
                    "execution_id",
                    runtime_task.metadata.get("execution_id", ""),
                )
                # Propagate sandbox metadata if not already set.
                if sandbox_session is not None and not response.sandbox_metadata:
                    response.sandbox_metadata = _sanitize_success_sandbox_metadata(
                        sandbox_session.metadata()
                    )
                elif response.sandbox_metadata:
                    response.sandbox_metadata = _sanitize_success_sandbox_metadata(
                        response.sandbox_metadata
                    )
                if sandbox_session is not None and not response.sandbox_id:
                    response.sandbox_id = response.sandbox_metadata.get("sandbox_id", "")
                response_sandbox_id = str(response.sandbox_id or "")
                if self.sandbox is not None:
                    sandbox_backend_name = getattr(self.sandbox, "name", type(self.sandbox).__name__)
                    response.metadata.setdefault("sandbox_backend", sandbox_backend_name)
                guard_reason, guard_error_type = _openclaw_integrity_guard_reason(
                    self.config,
                    response,
                )
                if guard_reason:
                    response.metadata["openclaw_integrity_guard"] = True
                    response.metadata["openclaw_integrity_guard_reason"] = guard_reason
                    raise _OpenClawResponseRejected(
                        guard_reason,
                        response,
                        error_type=guard_error_type,
                    )
                _normalize_numeric_response_answer(self.config, response)
                # Score the result.
                _record_lifecycle(
                    task,
                    sample_index,
                    "scorer_started",
                    {"scorer": self.config.scorer_name},
                )
                persisted_task = _strip_lifecycle_metadata(runtime_task)
                score = self.scorer.score(persisted_task, response)
                # Store the result.
                self.result_store.append(persisted_task, response, score, sample_index=sample_index)
                _record_lifecycle(
                    task,
                    sample_index,
                    "task_json_written",
                    {
                        "task_json_path": str(
                            self.result_store.output_dir
                            / self.config.run_id
                            / "tasks"
                            / f"{task.task_id}.json"
                        ),
                    },
                )
                # Log predicted vs ground_truth comparison.
                sample_tag = f" [sample {sample_index}]" if num_samples > 1 else ""
                logger.info(
                    "Task %s%s done: predicted=%r vs ground_truth=%r correct=%s",
                    task.task_id,
                    sample_tag,
                    response.answer,
                    task.ground_truth,
                    score.correct,
                )
                # Update dashboard.
                if dashboard is not None:
                    try:
                        dashboard.update(task.task_id, score.correct)
                    except Exception:
                        pass
                return {
                    "task_id": task.task_id,
                    "correct": score.correct,
                    "score": score.score,
                }
            except Exception as exc:
                logger.error("Task %s failed: %s", task.task_id, exc)
                # Build a partial response for error recording.
                error_response = getattr(exc, "partial_response", None) or response
                if error_response is None:
                    from alphadiana.harness.base import AgentResponse
                    error_response = AgentResponse(
                        answer=None,
                        wall_time_sec=time.monotonic() - start,
                    )
                # Collect sandbox metadata and artifacts on failure.
                if sandbox_session is not None:
                    if not error_response.sandbox_metadata:
                        error_response.sandbox_metadata = _sanitize_sandbox_metadata(
                            sandbox_session.metadata(),
                            keep_command_history=True,
                        )
                    elif error_response.sandbox_metadata:
                        error_response.sandbox_metadata = _sanitize_sandbox_metadata(
                            error_response.sandbox_metadata,
                            keep_command_history=True,
                        )
                    if not error_response.sandbox_id:
                        error_response.sandbox_id = error_response.sandbox_metadata.get("sandbox_id", "")
                    response_sandbox_id = str(error_response.sandbox_id or "")
                    runtime_manager = getattr(self.agent, "_runtime_manager", None)
                    if runtime_manager is not None and getattr(runtime_manager, "is_configured", False):
                        try:
                            artifact_data = runtime_manager.collect_artifacts(sandbox_session)
                            existing_manifest = dict(error_response.artifact_manifest or {})
                            existing_files = dict(error_response.workspace_file_contents or {})
                            error_response.artifact_manifest = _merge_artifact_manifests(
                                existing_manifest,
                                artifact_data.get("artifact_manifest", {}),
                            )
                            if not error_response.gateway_log_excerpt:
                                error_response.gateway_log_excerpt = artifact_data.get("gateway_log_excerpt", "")
                            existing_paths = list(error_response.workspace_snapshot_paths or [])
                            new_paths = list(artifact_data.get("workspace_snapshot_paths", []) or [])
                            error_response.workspace_snapshot_paths = existing_paths + [
                                path for path in new_paths if path not in existing_paths
                            ]
                            existing_files.update(artifact_data.get("workspace_file_contents", {}) or {})
                            error_response.workspace_file_contents = existing_files
                            sandbox_metadata = dict(error_response.sandbox_metadata or {})
                            sandbox_metadata.update(artifact_data.get("sandbox_metadata", {}) or {})
                            error_response.sandbox_metadata = _sanitize_sandbox_metadata(
                                sandbox_metadata,
                                keep_command_history=True,
                            )
                        except Exception as artifact_exc:
                            logger.warning("Artifact collection failed for task %s: %s", task.task_id, artifact_exc)
                    if not error_response.gateway_url and hasattr(sandbox_session, "proxy_v1_base"):
                        error_response.gateway_url = f"{sandbox_session.proxy_v1_base()}/chat/completions"
                    elif not error_response.gateway_url and hasattr(sandbox_session, "gateway_api_base"):
                        error_response.gateway_url = f"{sandbox_session.gateway_api_base()}/chat/completions"
                if self.sandbox is not None:
                    sandbox_backend_name = getattr(self.sandbox, "name", type(self.sandbox).__name__)
                    error_response.metadata.setdefault("sandbox_backend", sandbox_backend_name)
                _apply_error_provenance_metadata(
                    error_response,
                    _agent_error_provenance_metadata(self.agent),
                )
                error_response.metadata.setdefault("sample_index", sample_index)
                error_response.metadata.setdefault(
                    "execution_id",
                    runtime_task.metadata.get("execution_id", ""),
                )
                if not response_sandbox_id:
                    response_sandbox_id = str(getattr(error_response, "sandbox_id", "") or "")
                retry_responses = getattr(exc, "retry_responses", None)
                if retry_responses and "retry_responses" not in error_response.metadata:
                    error_response.metadata["retry_responses"] = retry_responses
                if used_predeployed_pool:
                    predeployed_quarantine_reason = _predeployed_session_failure_reason(
                        exc,
                        error_response,
                    )
                    if predeployed_quarantine_reason:
                        error_response.metadata["predeployed_session_quarantined"] = True
                        error_response.metadata["predeployed_session_quarantine_reason"] = (
                            predeployed_quarantine_reason
                        )
                if used_pool:
                    pooled_session_replacement_reason = _predeployed_session_failure_reason(
                        exc,
                        error_response,
                    )
                    if pooled_session_replacement_reason:
                        error_response.metadata["pooled_session_replaced"] = True
                        error_response.metadata["pooled_session_replacement_reason"] = (
                            pooled_session_replacement_reason
                        )
                task_retry_reason = predeployed_quarantine_reason or pooled_session_replacement_reason
                try:
                    setattr(exc, "retryable_task_failure", bool(task_retry_reason))
                    if task_retry_reason:
                        setattr(exc, "task_retry_reason", task_retry_reason)
                except Exception:
                    pass
                self.result_store.append_error(
                    _strip_lifecycle_metadata(runtime_task),
                    error=_build_error_info(exc),
                    response=error_response,
                    sample_index=sample_index,
                )
                _record_lifecycle(
                    task,
                    sample_index,
                    "task_json_written",
                    {
                        "task_json_path": str(
                            self.result_store.output_dir
                            / self.config.run_id
                            / "tasks"
                            / f"{task.task_id}.json"
                        ),
                        "error_type": getattr(exc, "error_type", type(exc).__name__),
                    },
                )
                raise
            finally:
                final_sandbox_id = response_sandbox_id or str(
                    getattr(sandbox_session, "sandbox_id", "") or ""
                )
                if (
                    not predeployed_quarantine_reason
                    and not fresh_predeployed_mode
                    and predeployed_session_by_sandbox_id
                    and final_sandbox_id
                ):
                    if not _reset_predeployed_session(final_sandbox_id, task.task_id):
                        predeployed_quarantine_reason = "predeployed_reset_failed"
                if sandbox_session is not None:
                    try:
                        if used_predeployed_pool and predeployed_quarantine_reason:
                            _quarantine_predeployed_session(
                                sandbox_session,
                                final_sandbox_id,
                                predeployed_quarantine_reason,
                                task.task_id,
                            )
                        elif used_predeployed_pool and fresh_predeployed_mode:
                            _retire_fresh_predeployed_session(
                                sandbox_session,
                                final_sandbox_id,
                                task.task_id,
                            )
                        elif used_predeployed_pool:
                            predeployed_session_queue.put(sandbox_session)
                        elif used_pool:
                            if pooled_session_replacement_reason:
                                pool.discard_and_replace(
                                    sandbox_session,
                                    reason=pooled_session_replacement_reason,
                                )
                            else:
                                pool.release(sandbox_session)
                        elif shared_session is not None:
                            # Shared session: reset for next task, don't close.
                            try:
                                sandbox_session.reset()
                            except Exception as reset_exc:
                                logger.warning(
                                    "shared_session reset failed for task %s: %s, recreating session",
                                    task.task_id, reset_exc,
                                )
                                try:
                                    sandbox_session.close()
                                except Exception:
                                    pass
                                try:
                                    shared_session = self.sandbox.create_session()
                                except Exception as create_exc:
                                    logger.error("Failed to recreate shared_session: %s", create_exc)
                                    shared_session = None
                        else:
                            sandbox_session.close()
                    except Exception as cleanup_exc:
                        logger.warning("Cleanup failed for task %s: %s", task.task_id, cleanup_exc)

        # Dispatch work items (task, sample_index) tuples.
        effective_max_concurrent = _effective_dispatch_concurrency(self.config)
        dispatcher = TaskDispatcher(
            max_concurrent=effective_max_concurrent,
            cancel_event=self.cancel_event,
            task_retries=getattr(self.config, "task_retries", 0),
            retry_if=(
                _is_recoverable_task_failure
                if getattr(self.config, "task_retry_on_recoverable_only", False)
                else None
            ),
        )
        try:
            outcomes = dispatcher.dispatch(work_items, solve_fn)
        finally:
            # Teardown pool / shared session even if dispatch raises.
            if pool is not None:
                try:
                    pool.teardown()
                except Exception as exc:
                    logger.warning("Pool teardown error: %s", exc)
            if shared_session is not None:
                try:
                    shared_session.close()
                except Exception as exc:
                    logger.warning("Shared session close error: %s", exc)
            # Teardown auto-created ROCK sandbox (if any) and clear the reference
            # so Runner.teardown() does not double-teardown.
            if _auto_sandbox is not None:
                try:
                    _auto_sandbox.teardown()
                except Exception as exc:
                    logger.warning("Auto-sandbox teardown error: %s", exc)
                if self.sandbox is _auto_sandbox:
                    self.sandbox = None
            predeployed_shutdown_event.set()
            for thread in list(predeployed_replenishment_threads):
                thread.join(timeout=0.1)
            for session in list(predeployed_sessions):
                try:
                    session.close()
                except Exception as exc:
                    logger.warning("Predeployed sandbox close error: %s", exc)

        succeeded = sum(1 for o in outcomes if o["success"])
        failed = sum(1 for o in outcomes if not o["success"])
        logger.info("Dispatch complete: %d succeeded, %d failed", succeeded, failed)

        # Generate summary report.
        summary = self.report_generator.generate(self.result_store, self.config)
        logger.info(
            "Run %s complete: accuracy=%.4f, mean_score=%.4f, pass@%d=%.4f, avg@%d=%.4f",
            self.config.run_id,
            summary.accuracy,
            summary.mean_score,
            summary.num_samples,
            summary.pass_at_k,
            summary.num_samples,
            summary.avg_at_k,
        )
        return summary

    def teardown(self) -> None:
        """Cleanup agent and sandbox resources."""
        if self.agent is not None:
            try:
                self.agent.teardown()
            except Exception as exc:
                logger.warning("Agent teardown error: %s", exc)
        if self.sandbox is not None:
            try:
                self.sandbox.teardown()
            except Exception as exc:
                logger.warning("Sandbox teardown error: %s", exc)
        logger.info("Teardown complete for run %s", self.config.run_id)
