"""Fresh CLI/API processes must discover the same built-in components."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entrypoint", [
    "from alphadiana.registry_loader import load_builtin_components as register",
    "from alphadiana.engine.components import register_builtin_components as register",
])
def test_fresh_registry_keeps_native_and_api_adapters(entrypoint):
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            entrypoint,
            "from alphadiana.harness.registry import AgentRegistry",
            "from alphadiana.metrics.registry import MetricRegistry",
            "register()",
            "for name in ('pi', 'deepseek_harness', 'sweagent', 'terminus2', 'terminal_bench2_pi_harbor'):",
            "    assert AgentRegistry.get(name).name == name",
            "assert MetricRegistry.get('answer_length')().name == 'answer_length'",
        ])],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
