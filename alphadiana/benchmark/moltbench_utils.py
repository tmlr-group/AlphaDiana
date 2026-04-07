"""MoltBench utilities: task loading, prompt construction, artifact parsing."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task loading from HuggingFace KernelBench
# ---------------------------------------------------------------------------

def load_kernelbench_problems(
    levels: list[int],
    problems: str | None = None,
    max_tasks: int | None = None,
) -> list[dict]:
    """Load KernelBench problems from HuggingFace.

    Returns a list of dicts with keys:
      level, problem_id, name, source_code
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise RuntimeError(
            "The 'datasets' library is required for MoltBench. "
            "Install with: pip install datasets"
        )

    problem_set = _parse_problem_range(problems)
    all_problems: list[dict] = []

    for level in levels:
        try:
            ds = load_dataset(
                "ScalingIntelligence/KernelBench",
                split=f"level_{level}",
            )
        except Exception:
            # Fallback: try as config name
            try:
                ds = load_dataset(
                    "ScalingIntelligence/KernelBench",
                    f"level_{level}",
                    split="train",
                )
            except Exception as exc:
                logger.warning("Failed to load KernelBench level %d: %s", level, exc)
                continue

        for item in ds:
            pid = item.get("problem_id", item.get("id", 0))
            if problem_set and pid not in problem_set:
                continue
            all_problems.append({
                "level": level,
                "problem_id": pid,
                "name": item.get("name", f"L{level}_P{pid}"),
                "source_code": item.get("code", item.get("source_code", "")),
            })
            if max_tasks and len(all_problems) >= max_tasks:
                return all_problems

    return all_problems


def _parse_problem_range(spec: str | None) -> set[int] | None:
    """Parse problem range spec like '1-20' or '1,3,5' or None (all)."""
    if not spec:
        return None
    result: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            result.update(range(int(start), int(end) + 1))
        else:
            result.add(int(part))
    return result


# ---------------------------------------------------------------------------
# Super() bug fix
# ---------------------------------------------------------------------------

def fix_super_bug(source_code: str) -> str:
    """Fix the known bridge.py bug where super(Model, self) is not renamed.

    When bridge.py renames class Model to class ModelNew, it doesn't
    update super(Model, self).__init__() calls. This fixes them.
    """
    return re.sub(
        r"super\(Model\s*,\s*self\)",
        "super()",
        source_code,
    )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_task_prompt(
    level: int,
    problem_id: int,
    name: str,
    source_code: str,
    max_rounds: int,
    backend: str = "triton",
) -> str:
    """Build the agent prompt for a KernelBench optimization task.

    Aligned with original AutoKernel behavior: the agent is told to read
    kernelbench/program_kb.md for full instructions, then given the specific
    problem to optimize.  No workspace instruction files (CLAUDE.md, AGENTS.md)
    are injected — all agents receive identical information via this user message.
    See docs/moltbench_agent_alignment.md for rationale.
    """
    return f"""Read kernelbench/program_kb.md for full optimization instructions.

Then optimize KernelBench problem Level {level}, Problem {problem_id}: {name}.
The problem is already set up — kernel.py and workspace/kb_active/ are ready.
You have {max_rounds} optimization rounds. Backend: {backend}.
"""


def build_task_json(
    level: int,
    problem_id: int,
    max_rounds: int = 10,
    backend: str = "triton",
    prompt: str | None = None,
) -> dict:
    """Build the task.json that gets mounted into the container."""
    task = {
        "benchmark_id": "autokernel",
        "level": level,
        "problem_id": problem_id,
        "max_rounds": max_rounds,
        "backend": backend,
        "workdir": "/workspace/autokernel",
        "setup_cmd": [
            "python3",
            "kernelbench/bridge.py",
            "setup",
            "--level",
            str(level),
            "--problem",
            str(problem_id),
            "--backend",
            backend,
            "--source",
            "hf",
        ],
        "verify_cmd": ["python3", "kernelbench/bench_kb.py"],
        "result_parser": "bench_kb",
        "primary_edit_file": "kernel.py",
        "bench_log_path": "run.log",
        "snapshot_dir": "workspace",
    }
    if prompt:
        task["prompt"] = prompt
    return task


# ---------------------------------------------------------------------------
# Artifact parsing
# ---------------------------------------------------------------------------

def parse_results_json(artifacts_dir: str | Path) -> dict:
    """Parse results.json from artifacts directory."""
    results_path = Path(artifacts_dir) / "results.json"
    if results_path.exists():
        try:
            return json.loads(results_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse results.json: %s", exc)
    return {"correctness": "MISSING", "speedup": 0.0}


def parse_bench_log(log_text: str) -> dict:
    """Parse greppable lines from bench_kb.py output."""
    results: dict[str, Any] = {}
    for line in log_text.splitlines():
        line = line.strip()
        if line.startswith("correctness:"):
            results["correctness"] = line.split(":", 1)[1].strip()
        elif line.startswith("speedup:"):
            val = line.split(":", 1)[1].strip().rstrip("x")
            try:
                results["speedup"] = float(val)
            except ValueError:
                pass
        elif line.startswith("kernel_time_ms:"):
            try:
                results["kernel_time_ms"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("reference_time_ms:"):
            try:
                ref_time = float(line.split(":", 1)[1].strip())
                results["reference_time_ms"] = ref_time
                results["pytorch_time_ms"] = ref_time
            except ValueError:
                pass
        elif line.startswith("pytorch_time_ms:"):
            try:
                ref_time = float(line.split(":", 1)[1].strip())
                results["reference_time_ms"] = ref_time
                results["pytorch_time_ms"] = ref_time
            except ValueError:
                pass
    return results


def parse_trajectory(artifacts_dir: str | Path) -> list[dict]:
    """Parse agent trajectory from artifacts directory."""
    artifacts = Path(artifacts_dir)
    trajectory: list[dict] = []

    # OpenClaw JSONL trajectory
    traj_path = artifacts / "trajectory.jsonl"
    if traj_path.exists():
        try:
            for line in traj_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    trajectory.append(json.loads(line))
        except Exception as exc:
            logger.warning("Failed to parse trajectory.jsonl: %s", exc)

    return trajectory


def compute_fast_p_flags(correctness: str, speedup: float, thresholds: list[float]) -> dict:
    """Compute per-problem fast_p binary flags for given thresholds."""
    is_correct = correctness.upper() == "PASS"
    flags = {}
    for t in thresholds:
        key = f"fast_p_{str(t).replace('.', '_')}"
        flags[key] = 1.0 if (is_correct and speedup >= t) else 0.0
    return flags
