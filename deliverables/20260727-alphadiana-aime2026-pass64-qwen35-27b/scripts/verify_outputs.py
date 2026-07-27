#!/usr/bin/env python3
"""Verify a finished AIME 2026 pass@64 run: 30 tasks x 64 samples, plus pass@64.

Checks, per agent run:
  - run_manifest.json exists with expected_task_count=30 and num_samples=64
  - results/<run_id>/tasks/ holds 30 unique task files, 64 samples each,
    with sample_index 0..63 present exactly once
  - the aggregate <run_id>.jsonl holds 30*64 records
  - score_status histogram is reported; non-valid_scored records fail the check
  - sampling diversity: if every task's 64 raw outputs are identical, the server
    almost certainly ignored temperature - fail loudly instead of reporting a
    degenerate pass@64
Reports pass@64 (task passes if any sample is correct) on the valid records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

AGENTS = ("openclaw", "opencode", "zeroclaw")
EXPECTED_TASKS = 30
EXPECTED_SAMPLES = 64


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--root", type=Path, required=True, help="AlphaDiana checkout root")
    parser.add_argument("--version", default="v01")
    parser.add_argument(
        "--expected-samples",
        type=int,
        default=EXPECTED_SAMPLES,
        help="Override for smoke runs (e.g. 2)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Verify the smoke_ run instead: 1 task x 2 samples",
    )
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"ERROR: {message}")


def main() -> None:
    args = parse_args()
    require(
        args.version.startswith("v") and args.version[1:].isdigit(),
        "--version must look like vNN",
    )
    if args.smoke:
        expected_tasks = 1
        expected_samples = 2 if args.expected_samples == EXPECTED_SAMPLES else args.expected_samples
        run_id = f"smoke_aime2026_pass64_{args.agent}_qwen35_27b_{args.version}"
    else:
        expected_tasks = EXPECTED_TASKS
        expected_samples = args.expected_samples
        run_id = f"full_aime2026_pass64_{args.agent}_qwen35_27b_{args.version}"
    root = args.root.resolve()

    result_root = root / "results"
    run_dir = result_root / run_id
    manifest_path = run_dir / "run_manifest.json"
    aggregate_path = result_root / f"{run_id}.jsonl"
    tasks_dir = run_dir / "tasks"

    require(manifest_path.is_file(), f"missing run manifest: {manifest_path}")
    require(aggregate_path.is_file(), f"missing aggregate JSONL: {aggregate_path}")
    require(tasks_dir.is_dir(), f"missing task directory: {tasks_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(
        manifest.get("expected_task_count") == expected_tasks,
        f"manifest expected_task_count is {manifest.get('expected_task_count')}, not {expected_tasks}",
    )
    require(
        manifest.get("num_samples") == expected_samples,
        f"manifest num_samples is {manifest.get('num_samples')}, not {expected_samples}",
    )

    task_files = sorted(tasks_dir.glob("*.json"))
    require(
        len(task_files) == expected_tasks,
        f"expected {expected_tasks} task files, found {len(task_files)}",
    )

    task_ids: list[str] = []
    status_counts: dict[str, int] = {}
    tasks_passed = 0
    diverse_tasks = 0
    for task_file in task_files:
        payload = json.loads(task_file.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else [payload]
        require(
            len(records) == expected_samples,
            f"{task_file.name} has {len(records)} samples, expected {expected_samples}",
        )
        sample_indices = sorted(
            int(r.get("sample_index", -1)) for r in records if isinstance(r, dict)
        )
        require(
            sample_indices == list(range(expected_samples)),
            f"{task_file.name} sample_index set is not 0..{expected_samples - 1}",
        )
        task_id = str(records[0].get("task_id", ""))
        require(bool(task_id), f"{task_file.name} has no task_id")
        task_ids.append(task_id)

        output_hashes = set()
        any_correct = False
        for record in records:
            status = str(record.get("score_status", "<missing>"))
            status_counts[status] = status_counts.get(status, 0) + 1
            if record.get("correct") is True:
                any_correct = True
            raw = record.get("raw_output")
            if isinstance(raw, str) and raw:
                output_hashes.add(hashlib.sha1(raw.encode("utf-8")).hexdigest())
        if any_correct:
            tasks_passed += 1
        if len(output_hashes) > 1:
            diverse_tasks += 1

    require(len(set(task_ids)) == expected_tasks, "task IDs are not unique")

    aggregate_count = sum(
        1 for line in aggregate_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    require(
        aggregate_count == expected_tasks * expected_samples,
        f"aggregate JSONL has {aggregate_count} records, expected {expected_tasks * expected_samples}",
    )

    print(f"OK: {run_id}: {expected_tasks} tasks x {expected_samples} samples")
    print("score_status:", json.dumps(status_counts, sort_keys=True))
    print(f"pass@{expected_samples}: {tasks_passed}/{expected_tasks} = {tasks_passed / expected_tasks:.4f}")

    if expected_samples > 1 and diverse_tasks == 0:
        raise SystemExit(
            "ERROR: every task's samples are byte-identical - the provider almost "
            "certainly ignored temperature=0.6; pass@64 would be meaningless."
        )
    if expected_samples > 1:
        print(f"sampling diversity: {diverse_tasks}/{expected_tasks} tasks have >1 distinct output")

    if set(status_counts) != {"valid_scored"}:
        raise SystemExit(
            "ERROR: run is structurally complete but contains non-valid_scored records"
        )


if __name__ == "__main__":
    main()
