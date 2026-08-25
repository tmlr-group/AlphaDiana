"""Import built-in components so their registry decorators execute."""

from __future__ import annotations


def load_builtin_benchmarks() -> None:
    import alphadiana.benchmarks.aime.benchmark  # noqa: F401
    import alphadiana.benchmarks.custom.benchmark  # noqa: F401
    import alphadiana.benchmarks.gpqa.benchmark  # noqa: F401
    import alphadiana.benchmarks.hle.benchmark  # noqa: F401
    import alphadiana.benchmarks.imo.benchmark  # noqa: F401
    import alphadiana.benchmarks.mmmu_pro.benchmark  # noqa: F401
    import alphadiana.benchmarks.swe_bench.benchmark  # noqa: F401
    import alphadiana.benchmarks.terminal_bench2.benchmark  # noqa: F401


def load_builtin_agents() -> None:
    import alphadiana.benchmarks.swe_bench.harness  # noqa: F401
    import alphadiana.benchmarks.terminal_bench2.harness.docker  # noqa: F401
    import alphadiana.benchmarks.terminal_bench2.harness.openclaw  # noqa: F401
    import alphadiana.benchmarks.terminal_bench2.harness.opencode  # noqa: F401
    import alphadiana.benchmarks.terminal_bench2.harness.pi_harbor  # noqa: F401
    import alphadiana.benchmarks.terminal_bench2.harness.zeroclaw  # noqa: F401
    import alphadiana.harness.direct_llm  # noqa: F401
    import alphadiana.harness.openclaw.agent  # noqa: F401
    import alphadiana.harness.opencode.agent  # noqa: F401
    import alphadiana.harness.zeroclaw.agent  # noqa: F401


def load_builtin_agent_validators() -> None:
    """Load only adapters that expose config hooks without optional runtimes."""
    import alphadiana.benchmarks.terminal_bench2.harness.pi_harbor  # noqa: F401


def load_builtin_sandboxes() -> None:
    import alphadiana.engine.sandbox.local  # noqa: F401
    import alphadiana.engine.sandbox.podman  # noqa: F401
    import alphadiana.engine.sandbox.rock  # noqa: F401
    import alphadiana.engine.sandbox.swebench_container  # noqa: F401


def load_builtin_scorers() -> None:
    import alphadiana.benchmarks.imo.verify  # noqa: F401
    import alphadiana.benchmarks.swe_bench.scorer  # noqa: F401
    import alphadiana.benchmarks.terminal_bench2.scorer  # noqa: F401
    import alphadiana.scorer.exact_match  # noqa: F401
    import alphadiana.scorer.llm_judge  # noqa: F401
    import alphadiana.scorer.math_verify_scorer  # noqa: F401
    import alphadiana.scorer.numeric  # noqa: F401


def load_builtin_metrics() -> None:
    import alphadiana.metrics.answer_length  # noqa: F401


def load_builtin_components() -> None:
    """Load every built-in registry family used by ``Runner``."""
    load_builtin_benchmarks()
    load_builtin_agents()
    load_builtin_sandboxes()
    load_builtin_scorers()
    load_builtin_metrics()
