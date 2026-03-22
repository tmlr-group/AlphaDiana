"""Main orchestrator for running evaluation experiments."""

from __future__ import annotations

import math
import logging
import threading
import time
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

        # Optional multi-sandbox predeploy for OpenClaw dynamic bootstrap.
        # This is the CLI equivalent of the dashboard deploy-and-run flow:
        # create N sandboxes up front, start one gateway per sandbox, and let
        # OpenClaw round-robin across the resulting gateway_pool.
        predeployed_sessions = []
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
                    rock_cls = SandboxRegistry.get("rock")

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
                                sandbox_backend.setup(auto_sandbox_config)
                                session = sandbox_backend.create_session()
                                runtime_manager = OpenClawRuntimeManager(self.config.agent_config)
                                info = runtime_manager.ensure_ready(session)
                                return session, info
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
                    deployment_results = [
                        _deploy_one_gateway(i)
                        for i in range(desired_num)
                    ]

                    predeployed_sessions = [session for session, _ in deployment_results]
                    gateway_pool = [info["api_base"] for _, info in deployment_results]
                    self.config.agent_config["gateway_pool"] = gateway_pool
                    self.config.agent_config["api_base"] = gateway_pool[0]
                    self.config.agent_config["sandbox_id"] = predeployed_sessions[0].sandbox_id
                    self.config.agent_config["rock_sandbox_url"] = (
                        predeployed_sessions[0].metadata().get("proxy_base_url", "")
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
                        "Falling back to single-sandbox dynamic bootstrap.",
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
        #   - agent is "openclaw" with dynamic bootstrap config
        #     (rock_agent_config_path + openclaw_config_path in agent config)
        #   - no sandbox_name was explicitly configured (sandbox: null)
        # Note: this creates a single sandbox for dynamic bootstrap.
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
