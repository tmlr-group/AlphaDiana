"""Main orchestrator for running evaluation experiments."""

from __future__ import annotations

import json
import math
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from alphadiana.agent.registry import AgentRegistry
from alphadiana.utils.rock_ports import resolve_rock_ports_from_env
from alphadiana.benchmark.registry import BenchmarkRegistry
from alphadiana.results.report import ReportGenerator, RunSummary
from alphadiana.results.result_store import ResultStore
from alphadiana.runner.task_dispatcher import TaskDispatcher
from alphadiana.sandbox.registry import SandboxRegistry
from alphadiana.scorer.registry import ScorerRegistry

if TYPE_CHECKING:
    from alphadiana.agent.base import Agent
    from alphadiana.benchmark.base import Benchmark
    from alphadiana.config.experiment_config import ExperimentConfig
    from alphadiana.sandbox.base import Sandbox
    from alphadiana.scorer.base import Scorer

logger = logging.getLogger(__name__)
OPENCLAW_CONCURRENCY_PER_SANDBOX = 1
_OPENCLAW_PROFILE_CACHE_PATH = Path(".cache/openclaw_startup_profiles.json")


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


def _build_error_info(exc: Exception) -> dict:
    """Build a serializable error dict from an exception."""
    error_type = getattr(exc, "error_type", type(exc).__name__)
    return {
        "error": str(exc),
        "error_type": error_type,
        "request_payload": getattr(exc, "request_payload", None),
        "response_body": getattr(exc, "response_body", None),
    }


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

    def setup(self) -> None:
        """Resolve and instantiate all components from their registries."""
        # Import all benchmark/agent/sandbox/scorer modules to trigger registration.
        import alphadiana.benchmark.aime  # noqa: F401
        import alphadiana.benchmark.custom  # noqa: F401

        # Import agent modules to trigger registration.
        import alphadiana.agent.openclaw  # noqa: F401
        import alphadiana.agent.direct_llm  # noqa: F401

        # Import sandbox modules to trigger registration.
        import alphadiana.sandbox.local  # noqa: F401
        import alphadiana.sandbox.rock  # noqa: F401

        # Import scorer modules to trigger registration.
        import alphadiana.scorer.exact_match  # noqa: F401
        import alphadiana.scorer.numeric  # noqa: F401
        import alphadiana.scorer.llm_judge  # noqa: F401
        import alphadiana.scorer.math_verify_scorer  # noqa: F401

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
                completed_samples = self.result_store.completed_sample_ids()
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
                completed = self.result_store.completed_task_ids()
                if completed:
                    before = len(work_items)
                    work_items = [(t, si) for t, si in work_items if t.task_id not in completed]
                    logger.info(
                        "Checkpoint: skipping %d completed tasks, %d remaining",
                        before - len(work_items),
                        len(work_items),
                    )

        logger.info("Total work items: %d (tasks=%d, num_samples=%d)", len(work_items), len(tasks), num_samples)

        # Initialize plain-text dashboard.
        dashboard = None
        try:
            from alphadiana.runner.dashboard import PlainTextDashboard
            from pathlib import Path
            status_dir = Path(self.config.output_dir) / self.config.run_id / "status"
            status_dir.mkdir(parents=True, exist_ok=True)
            dashboard = PlainTextDashboard(
                status_dir / "dashboard.txt", [t for t, _ in work_items],
            )
        except Exception:
            logger.debug("Dashboard initialization skipped", exc_info=True)

        # Optional multi-sandbox predeploy for OpenClaw auto-deploy mode.
        # This is the CLI equivalent of the dashboard deploy-and-run flow:
        # create N sandboxes up front, start one gateway per sandbox, and let
        # OpenClaw round-robin across the resulting gateway_pool.
        predeployed_sessions = []
        predeployed_session_by_sandbox_id: dict[str, object] = {}
        predeployed_session_reset_lock = threading.Lock()
        reset_predeployed_between_tasks = bool(
            self.config.agent_config.get("reset_predeployed_between_tasks", True)
        )
        if (
            self.sandbox is None
            and self.config.agent_name == "openclaw"
            and self.config.agent_config.get("rock_agent_config_path")
            and self.config.agent_config.get("openclaw_config_path")
        ):
            explicit_num = int(self.config.agent_config.get("num_sandboxes", 0) or 0)
            auto_num = (
                math.ceil(self.config.max_concurrent / OPENCLAW_CONCURRENCY_PER_SANDBOX)
                if self.config.max_concurrent > 1 else 1
            )
            desired_num = max(1, explicit_num or auto_num)
            if desired_num > 1:
                try:
                    import alphadiana.sandbox.rock  # noqa: F401 — trigger registration
                    from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager
                    from alphadiana.sandbox.registry import SandboxRegistry

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
                        "startup_timeout": int(self.config.agent_config.get("rock_startup_timeout", 300)),
                        "auto_clear_seconds": int(self.config.agent_config.get("rock_auto_clear_seconds", 7200)),
                        "start_retries": int(self.config.agent_config.get("rock_start_retries", 3)),
                        "reset_between_tasks": False,
                        "proxy_timeout": int(self.config.agent_config.get("proxy_timeout", 1800)),
                        "network_mode": self.config.agent_config.get("network_mode", None),
                    }
                    cache_key = _build_openclaw_profile_cache_key(
                        self.config,
                        str(auto_sandbox_config["admin_base_url"]),
                    )
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

                    def _deploy_one_gateway(sb_idx: int):
                        max_attempts = 3
                        last_error = None
                        for attempt in range(1, max_attempts + 1):
                            session = None
                            try:
                                logger.info(
                                    "Predeploy sandbox %d/%d attempt %d/%d",
                                    sb_idx + 1, desired_num, attempt, max_attempts,
                                )
                                sandbox_backend = rock_cls()
                                sandbox_config = dict(auto_sandbox_config)
                                if share_startup_profile and preferred_profile is not None:
                                    sandbox_config["memory"] = preferred_profile[0]
                                    sandbox_config["cpus"] = preferred_profile[1]
                                    logger.info(
                                        "Predeploy sandbox %d/%d reusing startup profile memory=%s cpus=%s",
                                        sb_idx + 1,
                                        desired_num,
                                        preferred_profile[0],
                                        preferred_profile[1],
                                    )
                                sandbox_backend.setup(sandbox_config)
                                session = sandbox_backend.create_session()
                                runtime_manager = OpenClawRuntimeManager(self.config.agent_config)
                                info = runtime_manager.ensure_ready(session)
                                md = session.metadata() if hasattr(session, "metadata") else {}
                                profile_memory = str(md.get("memory", sandbox_config["memory"]))
                                profile_cpus = float(md.get("cpus", sandbox_config["cpus"]))
                                return session, info, (profile_memory, profile_cpus)
                            except Exception as exc:
                                last_error = exc
                                logger.warning(
                                    "Predeploy sandbox %d/%d attempt %d/%d failed: %s",
                                    sb_idx + 1, desired_num, attempt, max_attempts, exc,
                                )
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

                    logger.info(
                        "Predeploying %d OpenClaw sandboxes for CLI concurrency "
                        "(max_concurrent=%d, target=%d tasks/sandbox)",
                        desired_num,
                        self.config.max_concurrent,
                        OPENCLAW_CONCURRENCY_PER_SANDBOX,
                    )
                    deployment_results = []
                    stagger_sec = float(self.config.agent_config.get("predeploy_stagger_seconds", 2.0) or 0.0)
                    for i in range(desired_num):
                        try:
                            deployed = _deploy_one_gateway(i)
                            deployment_results.append(deployed)
                            preferred_profile = deployed[2]
                        except Exception as exc:
                            if not deployment_results:
                                raise
                            logger.warning(
                                "Predeploy stopped early at sandbox %d/%d: %s. "
                                "Continuing with %d predeployed sandbox(es).",
                                i + 1,
                                desired_num,
                                exc,
                                len(deployment_results),
                            )
                            break
                        if stagger_sec > 0 and i + 1 < desired_num:
                            time.sleep(stagger_sec)

                    predeployed_sessions = [session for session, _, _ in deployment_results]
                    gateway_pool = [info["api_base"] for _, info, _ in deployment_results]
                    predeployed_session_by_sandbox_id = {
                        str(getattr(session, "sandbox_id", "")): session
                        for session in predeployed_sessions
                        if str(getattr(session, "sandbox_id", ""))
                    }
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
                    self.config.agent_config["api_base"] = gateway_pool[0]
                    self.config.agent_config["sandbox_id"] = predeployed_sessions[0].sandbox_id
                    self.config.agent_config["rock_sandbox_url"] = (
                        predeployed_sessions[0].metadata().get("proxy_base_url", "")
                    )
                    if preferred_profile is not None:
                        _save_cached_openclaw_profile(
                            cache_key,
                            preferred_profile[0],
                            preferred_profile[1],
                        )
                        logger.info(
                            "Persisted OpenClaw startup profile memory=%s cpus=%s",
                            preferred_profile[0],
                            preferred_profile[1],
                        )
                    self.agent.setup(self.config.agent_config)
                    logger.info(
                        "OpenClaw gateway_pool ready with %d sandboxes: %s",
                        len(gateway_pool),
                        gateway_pool,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to predeploy %d OpenClaw sandboxes: %s. "
                        "Falling back to single-sandbox auto-deploy.",
                        desired_num,
                        exc,
                    )
                    for session in predeployed_sessions:
                        try:
                            session.close()
                        except Exception:
                            pass
                    predeployed_sessions = []

        # Auto-create a ROCK sandbox when:
        #   - agent is "openclaw" with auto-deploy config
        #     (rock_agent_config_path + openclaw_config_path in agent config)
        #   - no sandbox_name was explicitly configured (sandbox: null)
        # Note: this creates a single sandbox for auto-deploy.
        # For concurrent execution, openclaw handles isolation internally via
        # gateway_pool (multi-sandbox).  Do NOT use SandboxPool with openclaw.
        _auto_sandbox = None
        if (
            self.sandbox is None
            and not predeployed_sessions
            and self.config.agent_name == "openclaw"
            and self.config.agent_config.get("rock_agent_config_path")
            and self.config.agent_config.get("openclaw_config_path")
        ):
            try:
                import alphadiana.sandbox.rock  # noqa: F401 — trigger registration
                from alphadiana.sandbox.registry import SandboxRegistry
                rock_cls = SandboxRegistry.get("rock")
                _auto_sandbox = rock_cls()
                # Build sandbox config from agent config, with openclaw-friendly defaults.
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
                    "startup_timeout": int(self.config.agent_config.get("rock_startup_timeout", 300)),
                    "auto_clear_seconds": int(self.config.agent_config.get("rock_auto_clear_seconds", 7200)),
                    "start_retries": int(self.config.agent_config.get("rock_start_retries", 3)),
                    # Do NOT reset workspace between tasks: the OpenClaw gateway process
                    # keeps running in the container and owns the workspace lifecycle.
                    "reset_between_tasks": False,
                    "proxy_timeout": int(self.config.agent_config.get("proxy_timeout", 1800)),
                    "network_mode": self.config.agent_config.get("network_mode", None),
                }
                _auto_sandbox.setup(auto_sandbox_config)
                logger.info(
                    "Auto-created ROCK sandbox for openclaw concurrent isolation "
                    "(max_concurrent=%d, memory=%s, cpus=%s)",
                    self.config.max_concurrent,
                    auto_sandbox_config["memory"],
                    auto_sandbox_config["cpus"],
                )
                # Treat _auto_sandbox as the sandbox for pool creation below.
                self.sandbox = _auto_sandbox
            except Exception as exc:
                logger.warning(
                    "Failed to auto-create ROCK sandbox for openclaw isolation: %s. "
                    "Falling back to shared gateway (may cause workspace contention at max_concurrent>1).",
                    exc,
                )
                _auto_sandbox = None

        # Set up sandbox pool for concurrent execution.
        # Skip pool for openclaw: it handles concurrency internally via
        # gateway_pool (multi-sandbox).  A SandboxPool would create N sessions
        # inside a single container, causing workspace contention.
        pool = None
        if (
            self.sandbox is not None
            and self.config.max_concurrent > 1
            and self.config.agent_name != "openclaw"
        ):
            from alphadiana.sandbox.pool import SandboxPool
            pool_size = self.config.max_concurrent
            logger.info("Creating SandboxPool with %d sessions", pool_size)
            pool = SandboxPool(self.sandbox, pool_size)

        # For sequential mode, create a single shared session to reuse across
        # all tasks instead of creating (and tearing down) one per task.
        shared_session = None
        if self.sandbox is not None and pool is None:
            logger.info("Creating shared sandbox session for sequential execution")
            shared_session = self.sandbox.create_session()

        def _reset_predeployed_session(sandbox_id: str, task_id: str) -> None:
            if not reset_predeployed_between_tasks or not sandbox_id:
                return
            session = predeployed_session_by_sandbox_id.get(sandbox_id)
            if session is None:
                return
            with predeployed_session_reset_lock:
                try:
                    reset = getattr(session, "reset", None)
                    if callable(reset):
                        reset()
                except Exception as exc:
                    logger.warning(
                        "Predeployed session reset failed for task %s sandbox_id=%s: %s",
                        task_id,
                        sandbox_id,
                        exc,
                    )

        # Create the solve function that wraps agent + sandbox + scorer.
        def solve_fn(work_item):
            nonlocal shared_session
            task, sample_index = work_item
            # Acquire sandbox session: from pool (concurrent) or shared (sequential).
            sandbox_session = None
            used_pool = False
            if pool is not None:
                sandbox_session = pool.acquire()
                used_pool = True
            elif shared_session is not None:
                sandbox_session = shared_session
            response_sandbox_id = ""
            start = time.monotonic()
            response = None
            try:
                # Run the agent.
                response = self.agent.solve(task, sandbox_session)
                # Propagate sandbox metadata if not already set.
                if sandbox_session is not None and not response.sandbox_metadata:
                    response.sandbox_metadata = sandbox_session.metadata()
                if sandbox_session is not None and not response.sandbox_id:
                    response.sandbox_id = response.sandbox_metadata.get("sandbox_id", "")
                response_sandbox_id = str(response.sandbox_id or "")
                if self.sandbox is not None:
                    response.metadata.setdefault("sandbox_backend", self.sandbox.name)
                # Score the result.
                score = self.scorer.score(task, response)
                # Store the result.
                self.result_store.append(task, response, score, sample_index=sample_index)
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
                error_response = response
                if error_response is None:
                    from alphadiana.agent.base import AgentResponse
                    error_response = AgentResponse(
                        answer=None,
                        wall_time_sec=time.monotonic() - start,
                    )
                # Collect sandbox metadata and artifacts on failure.
                if sandbox_session is not None:
                    if not error_response.sandbox_metadata:
                        error_response.sandbox_metadata = sandbox_session.metadata()
                    if not error_response.sandbox_id:
                        error_response.sandbox_id = error_response.sandbox_metadata.get("sandbox_id", "")
                    response_sandbox_id = str(error_response.sandbox_id or "")
                    runtime_manager = getattr(self.agent, "_runtime_manager", None)
                    if runtime_manager is not None and getattr(runtime_manager, "is_configured", False):
                        try:
                            artifact_data = runtime_manager.collect_artifacts(sandbox_session)
                            error_response.artifact_manifest = artifact_data["artifact_manifest"]
                            error_response.gateway_log_excerpt = artifact_data["gateway_log_excerpt"]
                            error_response.workspace_snapshot_paths = artifact_data["workspace_snapshot_paths"]
                            error_response.workspace_file_contents = artifact_data["workspace_file_contents"]
                            error_response.sandbox_metadata = artifact_data["sandbox_metadata"]
                        except Exception as artifact_exc:
                            logger.warning("Artifact collection failed for task %s: %s", task.task_id, artifact_exc)
                    if not error_response.gateway_url and hasattr(sandbox_session, "proxy_v1_base"):
                        error_response.gateway_url = f"{sandbox_session.proxy_v1_base()}/chat/completions"
                if self.sandbox is not None:
                    error_response.metadata.setdefault("sandbox_backend", self.sandbox.name)
                if not response_sandbox_id:
                    response_sandbox_id = str(getattr(error_response, "sandbox_id", "") or "")
                retry_responses = getattr(exc, "retry_responses", None)
                if retry_responses and "retry_responses" not in error_response.metadata:
                    error_response.metadata["retry_responses"] = retry_responses
                self.result_store.append_error(
                    task,
                    error=_build_error_info(exc),
                    response=error_response,
                    sample_index=sample_index,
                )
                raise
            finally:
                if predeployed_session_by_sandbox_id and response_sandbox_id:
                    _reset_predeployed_session(response_sandbox_id, task.task_id)
                if sandbox_session is not None:
                    try:
                        if used_pool:
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
            max_concurrent=self.config.max_concurrent,
            cancel_event=self.cancel_event,
            task_retries=getattr(self.config, "task_retries", 0),
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
            for session in predeployed_sessions:
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
