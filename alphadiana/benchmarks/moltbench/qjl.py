"""MoltBench QJL benchmark: CUDA optimization for QJL KV-cache quantization."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry

logger = logging.getLogger(__name__)


_DEFAULT_PROBLEM_TEMPLATE = """\
You are optimizing CUDA kernels for QJL (Quantized JL Transform), a KV-cache
compression method used in LLM inference.

## Workspace layout

```
/workspace/repo/
  qjl_kernel/
    csrc/
      qjl_score.cu        # CUDA kernel: quantized attention scoring
      qjl_quant.cu        # CUDA kernel: JL-transform quantization
      qjl_gqa_score.cu    # CUDA kernel: grouped-query attention scoring
      quantization.cu     # CUDA kernel: low-level quantization primitives
  bench_cuda.py           # Evaluation harness (DO NOT MODIFY)
  requirements.txt
```

## Goal

Reduce `runtime_ms` (median latency of quantize + attention_score) while keeping
`distortion < {distortion_threshold}`. You may modify CUDA kernel files in
`qjl_kernel/csrc/`. Do not modify `/eval/bench_cuda.py`.

## Baseline metrics

- runtime_ms: {baseline_runtime_ms}
- distortion: {baseline_distortion}

## Build & Evaluate

Use the protected evaluator:

```bash
/eval/run_eval
```

`/eval/run_eval` rebuilds the CUDA extension, runs `/eval/bench_cuda.py`, writes
structured verifier artifacts, and enforces the round limit.

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


def build_qjl_task_json(max_rounds: int = 5, prompt: str | None = None) -> dict[str, Any]:
    """Build the task.json mounted into the MoltBench container."""
    task: dict[str, Any] = {
        "benchmark_id": "qjl",
        "workdir": "/workspace/repo",
        "setup_cmd": [
            "bash",
            "-lc",
            "cd /workspace/repo/qjl_kernel && python setup.py build_ext --inplace",
        ],
        "verify_cmd": ["python3", "/eval/bench_cuda.py"],
        "agent_eval_cmd": "/eval/run_eval",
        "protected_eval_paths": ["/eval/run_eval", "/eval/bench_cuda.py"],
        "result_parser": "qjl",
        "result_json_path": "",
        "primary_edit_file": "qjl_kernel/csrc/qjl_score.cu",
        "bench_log_path": "/tmp/build.log",
        "snapshot_dir": "qjl_kernel/csrc",
        "max_rounds": max_rounds,
    }
    if prompt:
        task["prompt"] = prompt
    return task


class MoltBenchQJLBenchmark(Benchmark):
    """Benchmark for optimizing QJL CUDA kernels.

    Config keys:
        seed_config_path: path to seed_config.json (optional)
        baseline: dict with runtime_ms/distortion/correctness_pass
        baseline_json_path: path to baseline JSON, alternative to baseline
        distortion_threshold: max allowable distortion (default: 0.15)
        max_rounds: max evaluator invocations (default: 5)
        problem_template: prompt template override (optional)
    """

    name = "moltbench_qjl"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        seed_config: dict[str, Any] = {}
        seed_config_path = config.get("seed_config_path")
        if seed_config_path:
            path = Path(seed_config_path)
            if not path.exists():
                logger.warning("seed_config_path %s not found; using empty seed config", path)
            else:
                seed_config = _load_json_file(path)
                logger.info("Loaded QJL seed config from %s", path)

        baseline = config.get("baseline") or {}
        baseline_json_path = config.get("baseline_json_path")
        if not baseline and baseline_json_path:
            path = Path(baseline_json_path)
            if not path.exists():
                raise FileNotFoundError(f"baseline_json_path {path} not found")
            baseline = _load_json_file(path)
            logger.info("Loaded QJL baseline from %s", path)

        if not baseline:
            raise ValueError(
                "MoltBench QJL requires a 'baseline' dict or 'baseline_json_path' "
                "with at least runtime_ms."
            )

        distortion_threshold = float(config.get("distortion_threshold", 0.15))
        max_rounds = int(config.get("max_rounds", 5))
        template = config.get("problem_template", _DEFAULT_PROBLEM_TEMPLATE)

        optimization_hints = seed_config.get("optimization_hints", [])
        hints_text = (
            "\n".join(f"- {hint}" for hint in optimization_hints)
            if optimization_hints
            else "None provided."
        )

        problem = template.format(
            distortion_threshold=distortion_threshold,
            baseline_runtime_ms=baseline.get("runtime_ms", "unknown"),
            baseline_distortion=baseline.get("distortion", "unknown"),
            seed_config_json=json.dumps(seed_config.get("seed_config", {}), indent=2),
            optimization_hints=hints_text,
        )
        task_json = build_qjl_task_json(max_rounds=max_rounds, prompt=problem)

        return [
            BenchmarkTask(
                task_id="moltbench_qjl_optimize",
                problem=problem,
                ground_truth=baseline,
                metadata={
                    "task_json": task_json,
                    "seed_config": seed_config,
                    "max_rounds": max_rounds,
                    "distortion_threshold": distortion_threshold,
                    "result_parser": "qjl",
                },
            )
        ]

    def default_scorer(self) -> str:
        return "moltbench_qjl"


BenchmarkRegistry.register("moltbench_qjl", MoltBenchQJLBenchmark)
