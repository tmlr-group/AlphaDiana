#!/usr/bin/env python3
"""Generate deterministic Phase 9 SWE-bench Verified tasksets."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from alphadiana.benchmark.base import load_dataset_with_retry


DEFAULT_DATASET = "SWE-bench/SWE-bench_Verified"
DEFAULT_SPLIT = "test"
DEFAULT_SEED = "phase9-podman-swe-verified-readiness-v1"
DEFAULT_FORCE_INCLUDE = (
    "astropy__astropy-12907",
    "astropy__astropy-13033",
)
DEFAULT_TIERS = {
    "smoke": 2,
    "pilot32": 10,
    "long64": 2,
    "sample128": 2,
}


def _task_info(index: int, row: dict[str, Any]) -> dict[str, Any]:
    instance_id = str(row.get("instance_id") or "")
    return {
        "dataset_index": index,
        "instance_id": instance_id,
        "task_id": f"swe_{instance_id}",
        "repo": str(row.get("repo") or ""),
        "version": str(row.get("version") or ""),
    }


def build_tasksets(
    *,
    dataset_name: str,
    split: str,
    seed: str,
    force_include: list[str],
    tier_sizes: dict[str, int],
) -> dict[str, dict[str, Any]]:
    dataset = load_dataset_with_retry(dataset_name, None, split=split)
    rows: dict[str, tuple[int, dict[str, Any]]] = {
        str(row.get("instance_id") or index): (index, row)
        for index, row in enumerate(dataset)
    }

    available_force = [task_id for task_id in force_include if task_id in rows]
    missing_force = [task_id for task_id in force_include if task_id not in rows]
    remaining = [task_id for task_id in rows if task_id not in available_force]
    random.Random(seed).shuffle(remaining)

    output: dict[str, dict[str, Any]] = {}
    for tier, size in tier_sizes.items():
        selected = list(available_force)
        for task_id in remaining:
            if len(selected) >= size:
                break
            selected.append(task_id)
        if len(selected) != size:
            raise RuntimeError(
                f"Cannot build {tier} taskset of size {size}; only {len(selected)} tasks available"
            )
        tasks = [_task_info(*rows[task_id]) for task_id in selected]
        output[tier] = {
            "tier": tier,
            "dataset": dataset_name,
            "split": split,
            "seed": seed,
            "task_count": size,
            "force_include": list(force_include),
            "force_included": [task_id for task_id in available_force if task_id in selected],
            "missing_force_includes": list(missing_force),
            "task_ids": selected,
            "selected_task_ids": [f"swe_{task_id}" for task_id in selected],
            "tasks": tasks,
        }
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("alphadiana/context/podman-swe-verified-readiness/tasksets"),
    )
    parser.add_argument(
        "--force-include",
        action="append",
        default=list(DEFAULT_FORCE_INCLUDE),
        help="Instance id to force into every taskset when available.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = build_tasksets(
        dataset_name=args.dataset,
        split=args.split,
        seed=args.seed,
        force_include=[str(value) for value in args.force_include],
        tier_sizes=DEFAULT_TIERS,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for tier, payload in output.items():
        path = args.output_dir / f"{tier}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {path} ({payload['task_count']} tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
