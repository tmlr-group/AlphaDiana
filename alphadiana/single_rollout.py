"""Run exactly one AlphaDiana task for an external controller.

This is a thin adapter around :class:`alphadiana.engine.runner.Runner`.
The caller supplies a normal AlphaDiana YAML config that loads one task (or
selects one with ``--task-id``). Every call executes the task in a fresh run
directory and emits a compact JSON result for controllers such as AlphaHebe.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from alphadiana.analysis.io.status import VALID_SCORE_STATUS, infer_score_status
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.config.experiment_config import (
    ExperimentConfig,
    deep_merge,
    parse_override,
)
from alphadiana.engine.config.validator import ConfigValidator
from alphadiana.engine.runner import Runner


@dataclass
class _SingleTaskBenchmark:
    """Small runner-facing view that exposes only one already-loaded task."""

    task: BenchmarkTask

    def load_tasks(self, _config: dict[str, Any]) -> list[BenchmarkTask]:
        return [self.task]


def _select_task(tasks: list[BenchmarkTask], task_id: str | None) -> BenchmarkTask:
    if task_id:
        matches = [task for task in tasks if task.task_id == task_id]
        if not matches:
            available = ", ".join(task.task_id for task in tasks[:20]) or "<none>"
            raise ValueError(
                f"task_id {task_id!r} was not found; available tasks: {available}"
            )
        return matches[0]
    if len(tasks) != 1:
        raise ValueError(
            "single_rollout requires a config that loads exactly one task, "
            "or an explicit --task-id"
        )
    return tasks[0]


def _record_payload(
    config: ExperimentConfig,
    runner: Runner,
    task_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    run_dir = Path(config.output_dir) / config.run_id
    score_status = infer_score_status(record)
    scored = score_status == VALID_SCORE_STATUS
    error = record.get("error") or {}
    error_type = str(
        error.get("error_type", "") if isinstance(error, dict) else ""
    ).lower()
    failure_categories = {
        "provider_error": "serving_failure",
        "verifier_error": "evaluation_failure",
        "scorer_error": "evaluation_failure",
        "no_answer": "candidate_failure",
        "agent_empty_output": "candidate_failure",
        "agent_error": "execution_failure",
    }
    if score_status == "runtime_error":
        # A sanitized native_harness_execution failure can be a model request
        # or a Pi/tool failure; do not mislabel it as infrastructure.
        failure_categories["runtime_error"] = (
            "infrastructure_failure"
            if error_type in {"native_harness_container", "native_harness_timeout"}
            else "execution_failure"
        )
    return {
        "status": "complete" if scored else score_status,
        "score_status": score_status,
        "outcome_category": (
            "candidate_score"
            if scored
            else failure_categories.get(score_status, "unscored_failure")
        ),
        "run_id": config.run_id,
        "task_id": task_id,
        "agent": {
            "name": config.agent_name,
            "version": config.agent_version,
        },
        "benchmark": config.benchmark_name,
        "scorer": config.scorer_name,
        "predicted": record.get("predicted"),
        "correct": record.get("correct") if scored else None,
        "score": record.get("score") if scored else None,
        "rationale": record.get("rationale", ""),
        "cost": {
            "wall_seconds": record.get("wall_time_sec"),
            "tokens": record.get("token_usage"),
        },
        "evidence": {
            "result_jsonl": str(Path(runner.result_store.path).resolve()),
            "task_record": str((run_dir / "tasks" / f"{task_id}.json").resolve()),
            "run_manifest": str(runner.result_store.manifest_path.resolve()),
            "artifact_manifest": record.get("artifact_manifest", {}),
            "trajectory_available": bool(record.get("trajectory")),
        },
    }


def run_single(
    config_yaml: str,
    *,
    task_id: str | None = None,
    overrides: Sequence[str] = (),
    imports: Sequence[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run one task in a fresh run directory and return the compact result."""
    # Plugin modules register benchmarks, scorers, or harnesses before lookup.
    for module in imports:
        importlib.import_module(module)

    merged_overrides: dict[str, Any] = {}
    for override in overrides:
        merged_overrides = deep_merge(merged_overrides, parse_override(override))

    config = ExperimentConfig.from_yaml(config_yaml, overrides=merged_overrides or None)
    if config.num_samples != 1:
        raise ValueError("single_rollout requires num_samples == 1")
    # Never resume: a reused run directory would return an earlier record
    # without executing the task again.
    config.run_id = (run_id or f"{config.run_id}-{uuid.uuid4().hex[:12]}").replace("/", "_")
    config.redo_all = True
    output_dir = Path(config.output_dir)
    if (output_dir / f"{config.run_id}.jsonl").exists() or (output_dir / config.run_id).exists():
        raise ValueError(f"run_id {config.run_id!r} already has results in {output_dir}")

    errors = ConfigValidator().validate(config)
    if errors:
        raise ValueError("Config validation failed: " + "; ".join(errors))

    runner = Runner(config)
    try:
        runner.setup()
        assert runner.benchmark is not None
        tasks = runner.benchmark.load_tasks(config.benchmark_config)
        selected = _select_task(tasks, task_id)
        runner.benchmark = _SingleTaskBenchmark(selected)
        runner.run()

        records = [
            record
            for record in runner.result_store.load()
            if record.get("task_id") == selected.task_id
        ]
        if not records:
            raise RuntimeError(
                f"Runner completed without a record for {selected.task_id!r}"
            )
        return _record_payload(config, runner, selected.task_id, records[-1])
    finally:
        runner.teardown()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one AlphaDiana task.")
    parser.add_argument("config_yaml", help="AlphaDiana experiment YAML config")
    parser.add_argument(
        "--task-id", help="Task ID when the config loads multiple tasks"
    )
    parser.add_argument(
        "--override",
        "-o",
        action="append",
        default=[],
        help="Config override such as agent.config.temperature=0.2",
    )
    parser.add_argument(
        "--import",
        dest="imports",
        action="append",
        default=[],
        metavar="MODULE",
        help="Plugin module to import before setup; may be repeated",
    )
    parser.add_argument(
        "--run-id", help="Fresh run ID; defaults to the config run_id plus a random suffix"
    )
    parser.add_argument("--output", help="Also write the JSON result to this file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = run_single(
            args.config_yaml,
            task_id=args.task_id,
            overrides=args.override,
            imports=args.imports,
            run_id=args.run_id,
        )
        code = 0
    except Exception as exc:
        payload = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        code = 1
    text = json.dumps(payload, ensure_ascii=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
