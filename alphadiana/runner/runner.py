"""Main orchestrator for running evaluation experiments."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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


class Runner:
    """Top-level orchestrator that loads config, initializes components,
    runs the evaluation loop, and writes results."""

    def __init__(self, config: "ExperimentConfig") -> None:
        self.config = config
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

        # Create the solve function that wraps agent + sandbox + scorer.
        def solve_fn(task):
            # Create sandbox session if configured.
            sandbox_session = None
            if self.sandbox is not None:
                sandbox_session = self.sandbox.create_session()
            try:
                # Run the agent.
                response = self.agent.solve(task, sandbox_session)
                # Score the result.
                score = self.scorer.score(task, response)
                # Store the result.
                self.result_store.append(task, response, score)
                return {
                    "task_id": task.task_id,
                    "correct": score.correct,
                    "score": score.score,
                }
            finally:
                if sandbox_session is not None:
                    sandbox_session.close()

        # Dispatch tasks.
        dispatcher = TaskDispatcher(max_concurrent=self.config.max_concurrent)
        outcomes = dispatcher.dispatch(tasks, solve_fn)

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
