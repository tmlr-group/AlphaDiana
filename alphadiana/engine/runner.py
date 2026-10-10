"""Main orchestrator for running evaluation experiments."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from alphadiana.analysis.export import ReportGenerator, RunSummary
from alphadiana.scorer.answers import (
    extract_numeric_answer_candidate,
    parse_numeric_answer,
)
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.config.experiment_config import (
    coerce_config_bool as _coerce_config_bool,
)
from alphadiana.engine.sandbox.base import session_failure_reason
from alphadiana.engine.sandbox.rock_ports import resolve_rock_ports_from_env
from alphadiana.engine.task_dispatcher import TaskDispatcher
from alphadiana.engine.observations import measure_observations
from alphadiana.harness.openclaw.gateway_pool import (
    OpenClawGatewayPool,
    is_openclaw_autodeploy,
    stabilize_gateway_token,
)
from alphadiana.harness.openclaw.integrity import (
    OpenClawResponseRejected,
    integrity_guard_reason,
)
from alphadiana.engine.components import (
    AgentRegistry,
    BenchmarkRegistry,
    SandboxRegistry,
    ScorerRegistry,
)
from alphadiana.results.lifecycle import append_lifecycle_event
from alphadiana.results.store import ResultStore

if TYPE_CHECKING:
    from alphadiana.benchmarks.base import Benchmark
    from alphadiana.engine.config.experiment_config import ExperimentConfig
    from alphadiana.engine.sandbox.base import Sandbox
    from alphadiana.harness.base import Agent
    from alphadiana.engine.observations import EvaluationMetric
    from alphadiana.scorer.base import Scorer

logger = logging.getLogger(__name__)
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


def _needs_auto_rock_sandbox(config: "ExperimentConfig") -> bool:
    if is_openclaw_autodeploy(config):
        return True
    if config.agent_name == "zeroclaw":
        if str(config.agent_config.get("runtime_backend", "") or "").strip().lower() == "podman":
            return False
        return bool(config.agent_config.get("rock_image"))
    return False


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

    persistent = _coerce_config_bool(
        agent_config.get("persistent_memory"), default=False
    )
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

    def needs_reset(self, task_id: str) -> bool:
        """Return whether ``task_id`` crosses a committed task boundary."""
        if self.scope != "cross_sample":
            return False
        task_id = str(task_id)
        return bool(self._task_id and task_id != self._task_id)

    def commit(self, task_id: str) -> None:
        """Record a boundary only after any required reset has succeeded."""
        if self.scope == "cross_sample":
            self._task_id = str(task_id)


def _validate_stateful_run_policy(config: "ExperimentConfig") -> str:
    """Validate failure semantics for runs whose native state spans work items."""
    scope = _configured_memory_scope(config)
    if scope in {"cross_sample", "cross_task"}:
        retries = int(getattr(config, "task_retries", 0) or 0)
        if retries:
            raise ValueError(
                f"memory_scope={scope!r} requires task_retries=0 because native "
                "memory writes cannot be rolled back safely"
            )
    return scope


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
    return requested


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
        self.metrics: list[EvaluationMetric] = []
        self.result_store: ResultStore | None = None
        self.report_generator: ReportGenerator | None = None

    def _reset_cross_sample_state(self, shared_session):
        """Recreate harness-native state before the first sample of a new task."""
        old_agent = self.agent
        agent_cls = AgentRegistry.get(self.config.agent_name)
        new_agent = agent_cls()
        new_agent.version = self.config.agent_version
        new_session = None
        try:
            new_agent.setup(self.config.agent_config)
            if shared_session is not None and self.sandbox is not None:
                # Create the replacement before closing the current session so a
                # transient startup failure leaves the committed state usable.
                new_session = self.sandbox.create_session()
                shared_session.close()
            if old_agent is not None:
                old_agent.teardown()
        except Exception:
            try:
                new_agent.teardown()
            except Exception:
                logger.debug("Replacement agent teardown failed", exc_info=True)
            if new_session is not None:
                try:
                    new_session.close()
                except Exception:
                    logger.debug("Replacement session teardown failed", exc_info=True)
            raise
        self.agent = new_agent
        return new_session if shared_session is not None else None

    def setup(self) -> None:
        """Resolve and instantiate all components from their registries."""
        _validate_stateful_run_policy(self.config)
        stabilize_gateway_token(self.config)
        from alphadiana.engine.components import register_builtin_components

        register_builtin_components()

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

            from alphadiana.engine.components import MetricRegistry

            self.metrics = []
            for metric_spec in self.config.metrics:
                metric_cls = MetricRegistry.get(str(metric_spec["name"]))
                metric = metric_cls()
                metric.setup(dict(metric_spec.get("config", {}) or {}))
                self.metrics.append(metric)
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
                "metric_names": [metric.name for metric in self.metrics],
            },
        )
        self.report_generator = ReportGenerator()

        # Setup logging with run_id context.
        logging.basicConfig(
            level=logging.INFO,
            format=f"%(asctime)s [%(levelname)s] [{self.config.run_id}] %(name)s: %(message)s",
        )
        logger.info("Setup complete for run %s", self.config.run_id)

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

        num_samples = getattr(self.config, "num_samples", 1)
        memory_scope = _configured_memory_scope(self.config)
        effective_max_concurrent = _effective_dispatch_concurrency(self.config)
        memory_lifecycle = _MemoryScopeLifecycle(memory_scope)
        self.result_store.save_manifest({
            "run_id": self.config.run_id,
            "benchmark_name": self.config.benchmark_name,
            "agent_name": self.config.agent_name,
            "agent_version": self.config.agent_version,
            "scorer_name": self.config.scorer_name,
            "num_samples": num_samples,
            "memory_scope": memory_scope,
            "samples_independent": memory_scope not in {"cross_sample", "cross_task"},
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
                "metrics": self.config.metrics,
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
                    "max_concurrent": effective_max_concurrent,
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
        gateway_pool = OpenClawGatewayPool(
            self.config, self.agent, self.cancel_event, len(work_items),
        )
        effective_max_concurrent = gateway_pool.prepare(
            sandbox_configured=self.sandbox is not None,
            effective_max_concurrent=effective_max_concurrent,
            memory_scope=memory_scope,
        )

        # Auto-create a ROCK sandbox when the agent requires one implicitly and
        # no sandbox_name was explicitly configured (sandbox: null).
        _auto_sandbox = None
        if (
            self.sandbox is None
            and not gateway_pool.active
            and _needs_auto_rock_sandbox(self.config)
        ):
            try:
                import alphadiana.engine.sandbox.rock  # noqa: F401 — trigger registration
                from alphadiana.engine.components import SandboxRegistry
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
                    effective_max_concurrent,
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

        declared_isolation_mode = str(
            getattr(self.agent, "runtime_isolation_mode", "") or ""
        ).strip()
        isolation_mode = declared_isolation_mode or "shared_gateway"
        if not declared_isolation_mode and gateway_pool.active:
            isolation_mode = gateway_pool.isolation_mode
        elif _auto_sandbox is not None:
            isolation_mode = "auto_single_sandbox"
        elif self.sandbox is not None:
            isolation_mode = "explicit_sandbox"
        self.result_store._run_metadata["strict_isolation"] = self.config.strict_isolation
        self.result_store._run_metadata["isolation_mode"] = isolation_mode
        if gateway_pool.active:
            self.result_store._run_metadata.update(gateway_pool.metadata)
        manifest = self.result_store.load_manifest()
        if manifest:
            manifest["strict_isolation"] = self.config.strict_isolation
            manifest["isolation_mode"] = isolation_mode
            if gateway_pool.active:
                manifest.update(gateway_pool.metadata)
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
            and effective_max_concurrent > 1
            and sandbox_supports_pooling
            and self.config.agent_name != "openclaw"
        ):
            from alphadiana.engine.sandbox.pool import SandboxPool
            pool_size = effective_max_concurrent
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
        gateway_pool.activate()

        # Create the solve function that wraps agent + sandbox + scorer.
        def solve_fn(work_item):
            nonlocal shared_session
            task, sample_index = work_item
            if memory_lifecycle.needs_reset(task.task_id):
                logger.info(
                    "Resetting harness-native memory at cross-sample task boundary: %s",
                    task.task_id,
                )
                shared_session = self._reset_cross_sample_state(shared_session)
            memory_lifecycle.commit(task.task_id)
            runtime_task = _bind_runtime_task(task, sample_index)
            if memory_scope:
                runtime_task.metadata["memory_scope"] = memory_scope
            runtime_task.metadata["_lifecycle_path"] = str(
                _lifecycle_path_for(task.task_id, sample_index)
            )
            runtime_task.metadata["_lifecycle_run_id"] = self.config.run_id
            gateway_pool.task_started()
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
                elif gateway_pool.enabled:
                    sandbox_session = gateway_pool.acquire(task.task_id)
                    used_predeployed_pool = True
                elif shared_session is not None:
                    sandbox_session = shared_session
                elif self.sandbox is not None:
                    if self.config.sandbox_name == "swebench_container":
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
                guard_reason, guard_error_type = integrity_guard_reason(
                    self.config,
                    response,
                )
                if guard_reason:
                    response.metadata["openclaw_integrity_guard"] = True
                    response.metadata["openclaw_integrity_guard_reason"] = guard_reason
                    raise OpenClawResponseRejected(
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
                # Metrics are read-only observations computed after the primary
                # scorer. A broken optional metric is recorded but cannot turn
                # a valid benchmark result into a different score.
                measure_observations(
                    self.metrics,
                    persisted_task,
                    response,
                    score,
                )
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
                    predeployed_quarantine_reason = session_failure_reason(
                        exc,
                        error_response,
                    )
                    if predeployed_quarantine_reason:
                        error_response.metadata["predeployed_session_quarantined"] = True
                        error_response.metadata["predeployed_session_quarantine_reason"] = (
                            predeployed_quarantine_reason
                        )
                if used_pool:
                    pooled_session_replacement_reason = session_failure_reason(
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
                if not predeployed_quarantine_reason:
                    if not gateway_pool.reset(final_sandbox_id, task.task_id):
                        predeployed_quarantine_reason = "predeployed_reset_failed"
                if sandbox_session is not None:
                    try:
                        if used_predeployed_pool:
                            gateway_pool.release(
                                sandbox_session, final_sandbox_id, task.task_id,
                                quarantine_reason=predeployed_quarantine_reason,
                            )
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
        dispatcher = TaskDispatcher(
            max_concurrent=effective_max_concurrent,
            cancel_event=self.cancel_event,
            task_retries=getattr(self.config, "task_retries", 0),
            retry_if=(
                _is_recoverable_task_failure
                if getattr(self.config, "task_retry_on_recoverable_only", False)
                else None
            ),
            stop_on_error=memory_scope in {"cross_sample", "cross_task"},
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
            gateway_pool.close()

        succeeded = sum(1 for o in outcomes if o["success"])
        failed = sum(1 for o in outcomes if not o["success"])
        logger.info("Dispatch complete: %d succeeded, %d failed", succeeded, failed)

        # Generate summary report.
        summary = self.report_generator.generate(self.result_store, self.config)
        pass_label = "pass" if summary.samples_independent else "sequential-any"
        avg_label = "avg" if summary.samples_independent else "sequential-mean"
        logger.info(
            "Run %s complete: accuracy=%.4f, mean_score=%.4f, %s@%d=%.4f, %s@%d=%.4f",
            self.config.run_id,
            summary.accuracy,
            summary.mean_score,
            pass_label,
            summary.num_samples,
            summary.pass_at_k,
            avg_label,
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
