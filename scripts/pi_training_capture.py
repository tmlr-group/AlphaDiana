#!/usr/bin/env python3
"""Collect a native-token Pi SFT/GRPO batch from a public task JSON file.

The script intentionally has no scorer or optimizer.  A controller such as
AlphaHebe supplies the sampling plan and can replace the zero reward callback
with its evaluator after inspecting the durable native capture.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    # Keep the checked-out delivery command runnable before an editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.config.experiment_config import ExperimentConfig
from alphadiana.harness.pi.agent import PiAgent
from alphadiana.harness.pi.training import PiTrainingCollector, SamplingPlan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", required=True, help="Pi YAML config; agent.config is reused"
    )
    parser.add_argument(
        "--tasks",
        required=True,
        help="JSON list of public {task_id, problem} objects (no gold fields are required)",
    )
    parser.add_argument(
        "--plan",
        required=True,
        help="JSON SamplingPlan object including model/policy identities",
    )
    parser.add_argument("--output", help="Write the batch JSON to this file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = ExperimentConfig.from_yaml(args.config)
    plan_value = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan = SamplingPlan.from_dict(plan_value)
    raw_tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    if not isinstance(raw_tasks, list):
        raise ValueError("tasks JSON must be a list")
    tasks = [
        BenchmarkTask(
            task_id=str(row["task_id"]),
            problem=str(row["problem"]),
            ground_truth=None,
        )
        for row in raw_tasks
    ]
    collector = PiTrainingCollector(PiAgent)
    batch = collector.collect(
        tasks,
        plan,
        config.agent_config,
        # Scoring belongs to the controller; this keeps the capture command
        # usable for both private-evaluator and later reward pipelines.
        score=lambda _task, _response: 0.0,
    )
    text = json.dumps(batch.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
