#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


AGENTS = ("directllm", "openclaw", "opencode", "zeroclaw")
EXPECTED_TASKS = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", default="v01")
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"ERROR: {message}")


def verify_direct(root: Path, run_id: str) -> None:
    run_dir = root / "sweagent_results" / run_id
    require(run_dir.is_dir(), f"missing run directory: {run_dir}")
    preds_path = run_dir / "preds.json"
    require(preds_path.is_file(), f"missing predictions file: {preds_path}")
    payload = json.loads(preds_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        instance_ids = list(payload)
    elif isinstance(payload, list):
        instance_ids = [
            str(item.get("instance_id", ""))
            for item in payload
            if isinstance(item, dict) and item.get("instance_id")
        ]
    else:
        raise SystemExit(f"ERROR: unsupported preds.json shape: {type(payload).__name__}")
    require(len(instance_ids) == EXPECTED_TASKS, f"expected 50 predictions, found {len(instance_ids)}")
    require(len(set(instance_ids)) == EXPECTED_TASKS, "prediction instance IDs are not unique")
    print(f"OK: {run_id}: 50 unique official SWE-agent predictions")


def verify_native(root: Path, run_id: str) -> None:
    result_root = root / "results"
    run_dir = result_root / run_id
    manifest_path = run_dir / "run_manifest.json"
    aggregate_path = result_root / f"{run_id}.jsonl"
    tasks_dir = run_dir / "tasks"

    require(manifest_path.is_file(), f"missing run manifest: {manifest_path}")
    require(aggregate_path.is_file(), f"missing aggregate JSONL: {aggregate_path}")
    require(tasks_dir.is_dir(), f"missing task directory: {tasks_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("expected_task_count") == EXPECTED_TASKS, "manifest expected_task_count is not 50")
    require(manifest.get("num_samples") == 1, "manifest num_samples is not 1")

    task_files = sorted(tasks_dir.glob("*.json"))
    require(len(task_files) == EXPECTED_TASKS, f"expected 50 task files, found {len(task_files)}")

    task_ids: list[str] = []
    status_counts: dict[str, int] = {}
    record_count = 0
    for task_file in task_files:
        payload = json.loads(task_file.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else [payload]
        require(len(records) == 1, f"{task_file.name} has {len(records)} samples, expected 1")
        record = records[0]
        require(isinstance(record, dict), f"{task_file.name} does not contain an object")
        task_id = str(record.get("task_id", ""))
        require(bool(task_id), f"{task_file.name} has no task_id")
        task_ids.append(task_id)
        status = str(record.get("score_status", "<missing>"))
        status_counts[status] = status_counts.get(status, 0) + 1
        record_count += 1

    require(len(set(task_ids)) == EXPECTED_TASKS, "task IDs are not unique")
    aggregate_records = [
        json.loads(line)
        for line in aggregate_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    require(len(aggregate_records) == EXPECTED_TASKS, f"aggregate JSONL has {len(aggregate_records)} records")

    print(f"OK: {run_id}: {record_count} unique tasks, one sample each")
    print("score_status:", json.dumps(status_counts, sort_keys=True))
    if set(status_counts) != {"valid_scored"}:
        raise SystemExit("ERROR: run is structurally complete but contains non-valid_scored task records")


def main() -> None:
    args = parse_args()
    require(args.version.startswith("v") and args.version[1:].isdigit(), "--version must look like vNN")
    run_id = f"full_swe_bench_verified_mini_{args.agent}_gemma4_31b_{args.version}"
    if args.agent == "directllm":
        verify_direct(args.root.resolve(), run_id)
    else:
        verify_native(args.root.resolve(), run_id)


if __name__ == "__main__":
    main()
