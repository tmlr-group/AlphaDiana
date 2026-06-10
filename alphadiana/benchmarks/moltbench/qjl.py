"""MoltBench QJL CUDA optimization task pending Level 3 GPU revalidation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.moltbench.qjl_contract import (
    QJL_COMMIT,
    QJL_DISTORTION_THRESHOLD,
    QJL_EDITABLE_GLOB,
    QJL_REPOSITORY,
    QJL_RETENTION_THRESHOLD,
)
from alphadiana.benchmarks.registry import BenchmarkRegistry

logger = logging.getLogger(__name__)


_DEFAULT_PROBLEM_TEMPLATE = """\
You are optimizing CUDA kernels for QJL (Quantized JL Transform), a KV-cache
compression method used in LLM inference.

## Goal

Reduce official `runtime_ms` while preserving numerical correctness. The
official correctness gate requires hidden-input distortion below
`{distortion_threshold}`.

You may edit only:

```text
qjl_kernel/csrc/*.cu
```

All verifier code, benchmark inputs, score files, and `/eval/run_eval` are
protected. Changes outside the editable path are rejected by official
evaluation.

## Episode

- `stage_01_bootstrap`: inspect the pinned QJL seed and establish a strategy.
- `stage_02_improvement`: make candidate edits and request feedback.
- Each `/eval/run_eval` call is one `EvalAttempt`.
- Maximum EvalAttempts: {max_attempts}.

`/eval/run_eval` sends only the editable CUDA sources to a verifier outside
the agent container. It returns limited structured feedback. The final score
comes from a separate `official_eval` after the agent exits, using a clean
workspace and hidden inputs.

## Baseline

- runtime_ms: {baseline_runtime_ms}
- distortion: {baseline_distortion}
- gain formula: baseline_runtime_ms / candidate_runtime_ms

## Seed configuration

{seed_config_json}

## Optimization hints

{optimization_hints}
"""


def _load_json_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def build_qjl_task_json(
    *,
    max_attempts: int = 5,
    attempt_timeout_sec: int = 1200,
    total_timeout_sec: int = 3600,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Build the public task contract mounted into the agent container."""
    task: dict[str, Any] = {
        "task_id": "moltbench_qjl_optimize",
        "family": "cuda_kernel_optimization",
        "task_name": "QJL KV-cache CUDA optimization",
        "benchmark_id": "qjl",
        "moltbench_task_level": "optimization_only",
        "workdir": "/workspace/repo",
        "seed_project": {
            "source": QJL_REPOSITORY,
            "commit_or_version": QJL_COMMIT,
            "license": "Apache-2.0",
            "baseline_command": "official_eval --candidate seed",
            "environment_lockfile": "moltbench/tasks/qjl/environment.lock.json",
        },
        "stages": [
            {"stage_id": "stage_01_bootstrap", "type": "bootstrap"},
            {"stage_id": "stage_02_improvement", "type": "improvement"},
        ],
        "workspace": {
            "editable_paths": [QJL_EDITABLE_GLOB],
            "protected_paths": [
                "/eval/run_eval",
                "/verifier",
                "/hidden_tests",
                "/scoring",
                "/official_eval",
            ],
        },
        "editable_paths": [QJL_EDITABLE_GLOB],
        "protected_eval_paths": [
            "/eval/run_eval",
            "/verifier",
            "/hidden_tests",
            "/official_eval",
        ],
        "setup_cmd": [
            "bash",
            "-lc",
            "test -d /workspace/repo/qjl_kernel/csrc",
        ],
        "agent_eval_cmd": "/eval/run_eval",
        "official_eval_external": True,
        "external_verifier": "qjl",
        "primary_edit_file": "qjl_kernel/csrc/qjl_score_kernel.cu",
        "snapshot_dir": "qjl_kernel/csrc",
        "budget": {
            "max_stages": 2,
            "max_attempts_per_stage": max_attempts,
            "attempt_timeout_sec": attempt_timeout_sec,
            "total_timeout_sec": total_timeout_sec,
            "max_tool_calls": 200,
            "max_model_calls": 100,
            "max_tokens": 65536,
            "max_cost_usd": None,
        },
        "max_attempts": max_attempts,
        "max_rounds": 100,
        "artifacts": {
            "required_outputs": [
                "manifest.json",
                "score.json",
                "patch.diff",
                "changed_files.json",
                "audit_report.md",
            ],
            "required_logs": [
                "attempts.jsonl",
                "events.jsonl",
                "verifier_results.jsonl",
                "tool_calls.jsonl",
                "model_calls.jsonl",
                "file_diffs.jsonl",
            ],
            "required_snapshots": [
                "initial_workspace_snapshot",
                "final_workspace_snapshot",
                "best_workspace_snapshot",
            ],
        },
        "contamination": {
            "training_allowed": False,
            "hidden_data_exposed": False,
            "retrieval_allowed": True,
            "network_allowed": True,
            "network_policy": (
                "agent network is deployment-controlled; official verifier uses no "
                "network and feedback seeds are disjoint from official seeds"
            ),
        },
        "hardware": {
            "validated_gpu_arches": ["sm_80"],
            "validated_gpu": "NVIDIA A800 80GB",
            "torch_cuda_arch_list": ["8.0"],
            "cuda": "12.1",
        },
    }
    if prompt:
        task["prompt"] = prompt
    return task


class MoltBenchQJLBenchmark(Benchmark):
    """Load the QJL optimization-only episode."""

    name = "moltbench_qjl"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        seed_config: dict[str, Any] = {}
        seed_config_path = config.get("seed_config_path")
        if seed_config_path:
            path = Path(seed_config_path)
            if not path.exists():
                raise FileNotFoundError(f"seed_config_path {path} not found")
            seed_config = _load_json_file(path)

        baseline = dict(config.get("baseline") or {})
        baseline_json_path = config.get("baseline_json_path")
        if not baseline and baseline_json_path:
            baseline = _load_json_file(baseline_json_path)
        if not baseline or float(baseline.get("runtime_ms", 0.0)) <= 0:
            raise ValueError(
                "MoltBench QJL requires a reproducible baseline with runtime_ms > 0."
            )

        distortion_threshold = float(
            config.get("distortion_threshold", QJL_DISTORTION_THRESHOLD)
        )
        if distortion_threshold != QJL_DISTORTION_THRESHOLD:
            raise ValueError(
                "QJL distortion_threshold must match the locked task contract: "
                f"{QJL_DISTORTION_THRESHOLD}"
            )
        baseline_distortion = float(baseline.get("distortion", float("inf")))
        if baseline_distortion >= distortion_threshold:
            raise ValueError(
                "MoltBench QJL baseline must pass the locked distortion threshold."
            )
        baseline_threshold = baseline.get("distortion_threshold")
        if (
            baseline_threshold is not None
            and float(baseline_threshold) != distortion_threshold
        ):
            raise ValueError(
                "MoltBench QJL baseline distortion_threshold does not match "
                "the locked task contract."
            )
        max_attempts = int(config.get("max_attempts", 5))
        attempt_timeout_sec = int(config.get("attempt_timeout_sec", 1200))
        total_timeout_sec = int(config.get("total_timeout_sec", 3600))
        retention_threshold = float(
            config.get("retention_threshold", QJL_RETENTION_THRESHOLD)
        )
        if max_attempts <= 0:
            raise ValueError("MoltBench QJL max_attempts must be positive.")
        if attempt_timeout_sec <= 0:
            raise ValueError("MoltBench QJL attempt_timeout_sec must be positive.")
        if total_timeout_sec < attempt_timeout_sec:
            raise ValueError(
                "MoltBench QJL total_timeout_sec must be at least "
                "attempt_timeout_sec."
            )
        if not 0 < retention_threshold <= 1:
            raise ValueError(
                "MoltBench QJL retention_threshold must be in the interval (0, 1]."
            )
        template = config.get("problem_template", _DEFAULT_PROBLEM_TEMPLATE)

        optimization_hints = seed_config.get("optimization_hints", [])
        hints_text = (
            "\n".join(f"- {hint}" for hint in optimization_hints)
            if optimization_hints
            else "None provided."
        )
        problem = template.format(
            distortion_threshold=distortion_threshold,
            max_attempts=max_attempts,
            baseline_runtime_ms=baseline["runtime_ms"],
            baseline_distortion=baseline.get("distortion", "unknown"),
            seed_config_json=json.dumps(seed_config.get("seed_config", {}), indent=2),
            optimization_hints=hints_text,
        )
        task_json = build_qjl_task_json(
            max_attempts=max_attempts,
            attempt_timeout_sec=attempt_timeout_sec,
            total_timeout_sec=total_timeout_sec,
            prompt=problem,
        )
        task_json["seed_project"]["baseline_score"] = baseline
        task_json["verifier"] = {
            "run_eval_command": "/eval/run_eval",
            "official_eval_command": "host:official_eval",
            "hidden_files": ["/verifier/bench_cuda.py", "/hidden_tests/qjl_inputs"],
            "timeout_sec": attempt_timeout_sec,
            "hidden_seeds_sha256": "host-owned",
        }
        task_json["scoring"] = {
            "correctness_metric": f"distortion < {distortion_threshold}",
            "gain_metric": "baseline_runtime_ms / candidate_runtime_ms",
            "stability_metric": (
                "final_valid_gain / episode_best_valid_gain "
                f">= {retention_threshold}"
            ),
            "memory_metric": "N/A",
            "drift_metric": "N/A",
            "cost_metric": (
                "attempts, verifier calls, wall time, GPU time, model/tool calls, "
                "tokens, and cost when available"
            ),
        }

        metadata = {
            "task_json": task_json,
            "seed_config": seed_config,
            "max_attempts": max_attempts,
            "distortion_threshold": distortion_threshold,
            "retention_threshold": retention_threshold,
            "hidden_seeds": "generated by the host per episode",
            "external_verifier": "qjl",
        }
        return [
            BenchmarkTask(
                task_id="moltbench_qjl_optimize",
                problem=problem,
                ground_truth=baseline,
                metadata=metadata,
            )
        ]

    def default_scorer(self) -> str:
        return "moltbench_qjl"


BenchmarkRegistry.register("moltbench_qjl", MoltBenchQJLBenchmark)
