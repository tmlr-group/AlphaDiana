"""Main orchestrator for running evaluation experiments."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

import httpx

from alphadiana.agent.registry import AgentRegistry
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
        import alphadiana.benchmark.math_bench  # noqa: F401
        import alphadiana.benchmark.hle  # noqa: F401
        import alphadiana.benchmark.frontier_math  # noqa: F401
        import alphadiana.benchmark.terminal_bench  # noqa: F401
        import alphadiana.benchmark.osworld  # noqa: F401

        # Import agent modules to trigger registration.
        import alphadiana.agent.openclaw  # noqa: F401
        import alphadiana.agent.direct_llm  # noqa: F401

        # Import sandbox modules to trigger registration.
        import alphadiana.sandbox.local  # noqa: F401
        import alphadiana.sandbox.rock  # noqa: F401
        import alphadiana.sandbox.boxlite  # noqa: F401

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

        # Set up sandbox pool for concurrent execution.
        pool = None
        if self.sandbox is not None and self.config.max_concurrent > 1:
            from alphadiana.sandbox.pool import SandboxPool
            pool_size = self.config.max_concurrent
            logger.info("Creating SandboxPool with %d sessions", pool_size)
            pool = SandboxPool(self.sandbox, pool_size)

        # Create the solve function that wraps agent + sandbox + scorer.
        def solve_fn(work_item):
            task, sample_index = work_item
            # Acquire sandbox session: from pool (concurrent) or create fresh (sequential).
            sandbox_session = None
            used_pool = False
            if pool is not None:
                sandbox_session = pool.acquire()
                used_pool = True
            elif self.sandbox is not None:
                sandbox_session = self.sandbox.create_session()
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
                        else:
                            sandbox_session.close()
                    except Exception as cleanup_exc:
                        logger.warning("Cleanup failed for task %s: %s", task.task_id, cleanup_exc)

        # Dispatch work items (task, sample_index) tuples.
        dispatcher = TaskDispatcher(
            max_concurrent=self.config.max_concurrent,
            cancel_event=self.cancel_event,
        )
        outcomes = dispatcher.dispatch(work_items, solve_fn)

        # Teardown pool if used.
        if pool is not None:
            pool.teardown()

        succeeded = sum(1 for o in outcomes if o["success"])
        failed = sum(1 for o in outcomes if not o["success"])
        logger.info("Dispatch complete: %d succeeded, %d failed", succeeded, failed)

        # Generate summary report.
        summary = self.report_generator.generate(self.result_store, self.config)
        logger.info(
            "Run %s complete: accuracy=%.4f, mean_score=%.4f",
            self.config.run_id,
            summary.accuracy,
            summary.mean_score,
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
