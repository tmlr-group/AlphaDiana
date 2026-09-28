"""Built-in registrations shared by the CLI Runner and public composition API.

Custom components register with the existing registries without editing this list.
"""

from importlib import import_module

_BUILTINS = (
    "alphadiana.benchmarks.aime.benchmark",
    "alphadiana.benchmarks.custom.benchmark",
    "alphadiana.benchmarks.swe_bench.benchmark",
    "alphadiana.benchmarks.gpqa.benchmark",
    "alphadiana.benchmarks.hle.benchmark",
    "alphadiana.benchmarks.imo.benchmark",
    "alphadiana.benchmarks.mmmu_pro.benchmark",
    "alphadiana.benchmarks.terminal_bench2.benchmark",
    "alphadiana.harness.direct_llm",
    "alphadiana.harness.openclaw.agent",
    "alphadiana.harness.pi.agent",
    "alphadiana.harness.deepseek_harness.agent",
    "alphadiana.harness.opencode.agent",
    "alphadiana.benchmarks.swe_bench.harness",
    "alphadiana.benchmarks.terminal_bench2.harness.docker",
    "alphadiana.benchmarks.terminal_bench2.harness.openclaw",
    "alphadiana.benchmarks.terminal_bench2.harness.opencode",
    "alphadiana.benchmarks.terminal_bench2.harness.zeroclaw",
    "alphadiana.harness.zeroclaw.agent",
    "alphadiana.engine.sandbox.local",
    "alphadiana.engine.sandbox.podman",
    "alphadiana.engine.sandbox.rock",
    "alphadiana.engine.sandbox.swebench_container",
    "alphadiana.scorer.exact_match",
    "alphadiana.benchmarks.imo.verify",
    "alphadiana.scorer.llm_judge",
    "alphadiana.scorer.math_verify_scorer",
    "alphadiana.scorer.numeric",
    "alphadiana.benchmarks.swe_bench.scorer",
    "alphadiana.benchmarks.terminal_bench2.scorer",
)


def register_builtin_components() -> None:
    """Import built-ins once (Python caches imports) before registry resolution."""
    for module in _BUILTINS:
        import_module(module)
