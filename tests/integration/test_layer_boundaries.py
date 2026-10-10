"""Dataset/scoring discovery must be independent of solver implementations."""
import ast
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("discover", ["load_builtin_benchmarks", "load_builtin_scorers"])
def test_dataset_and_scorer_discovery_does_not_import_agents(discover):
    code = f"""
import sys
from alphadiana.engine.components import {discover}
{discover}()
assert not any(name.startswith((
    'alphadiana.harness.swe_bench', 'alphadiana.harness.terminal_bench2',
    'alphadiana.harness.openclaw.agent', 'alphadiana.harness.opencode.agent',
    'alphadiana.harness.zeroclaw.agent', 'harbor', 'sweagent',
)) for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_benchmark_and_harness_imports_cross_only_the_task_contract():
    root = Path(__file__).parents[2] / "alphadiana"
    for owner, forbidden in (("benchmarks", "alphadiana.harness"), ("harness", "alphadiana.benchmarks")):
        for path in (root / owner).rglob("*.py"):
            if "skills" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([node.module or ""] if isinstance(node, ast.ImportFrom) else
                         [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                for name in names:
                    if name == "alphadiana.benchmarks.base":
                        continue
                    assert not name.startswith(forbidden), (path, name)


def test_agent_discovery_does_not_load_concrete_datasets():
    code = """
import sys
from alphadiana.engine.components import load_builtin_agents
load_builtin_agents()
assert not any(name.startswith('alphadiana.benchmarks.') and name != 'alphadiana.benchmarks.base'
               for name in sys.modules)
assert not any(name in sys.modules for name in ('harbor', 'sweagent', 'swebench'))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
