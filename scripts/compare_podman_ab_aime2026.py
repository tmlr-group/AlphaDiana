#!/usr/bin/env python3
"""Compare AIME 2026 pass@4 / avg@4 across the podman-ab-aime2026 runs.

Reads result JSONL files and reports pass@4, avg@4, accuracy, and blank/error
rate per run, plus an alignment delta between the OpenClaw harness and the
direct-LLM baseline.

avg@4 follows the runner convention: correct samples / (n_tasks * num_samples),
with samples that never completed (blank/error) counted as 0.

Usage:
  python scripts/compare_podman_ab_aime2026.py [results_dir]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

NUM_SAMPLES = 4
RUNS = {
    "OpenClaw (Podman 031bfc chatmax)": "ab_aime2026_openclaw_podman_chatmax_full_20260524",
    "OpenClaw (Podman a17a diagnostic)": "ab_aime2026_openclaw_podman_reasoning_guardstrip_full_20260523",
    "OpenClaw (Podman timeout-only)": "ab_aime2026_openclaw_podman_qwen35_27b",
    "OpenClaw (old sandbox/ROCK)": "ab_aime2026_openclaw_docker_qwen35_27b",
    "DirectLLM baseline": "ab_aime2026_directllm_qwen35_27b",
}


def load_rows(jsonl: Path) -> list[dict]:
    rows = []
    with jsonl.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def summarize(rows: list[dict]) -> dict:
    # Collapse retry-duplicate rows: key = (task_id, sample_index).
    # A sample is "correct" if any row for it is correct; "scored" if any row
    # has a real score; otherwise it is a blank/error sample (counts as 0).
    samples: dict[tuple, dict] = defaultdict(
        lambda: {"correct": False, "scored": False}
    )
    for r in rows:
        key = (str(r.get("task_id")), r.get("sample_index"))
        s = samples[key]
        if r.get("correct"):
            s["correct"] = True
        if r.get("score_status") in ("ok", "scored", "valid_scored"):
            s["scored"] = True

    tasks: dict[str, list] = defaultdict(list)
    for (task_id, _si), s in samples.items():
        tasks[task_id].append(s)

    n_tasks = len(tasks)
    denom = n_tasks * NUM_SAMPLES
    correct_samples = sum(1 for s in samples.values() if s["correct"])
    scored_samples = sum(1 for s in samples.values() if s["scored"])
    blank_samples = len(samples) - scored_samples
    passk = sum(1 for t, ss in tasks.items() if any(s["correct"] for s in ss))

    return {
        "n_tasks": n_tasks,
        "samples_seen": len(samples),
        "scored": scored_samples,
        "blank_or_error": blank_samples,
        "correct": correct_samples,
        "pass_at_4": passk / n_tasks if n_tasks else 0.0,
        "avg_at_4": correct_samples / denom if denom else 0.0,
        "accuracy_scored": correct_samples / scored_samples if scored_samples else 0.0,
        "task_correct": {t: any(s["correct"] for s in ss) for t, ss in tasks.items()},
    }


def main() -> int:
    results_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results")
    summ: dict[str, dict] = {}
    for label, run_id in RUNS.items():
        jsonl = results_dir / f"{run_id}.jsonl"
        if not jsonl.exists():
            print(f"  [skip] {label}: no {jsonl.name}")
            if label == "OpenClaw (old sandbox/ROCK)":
                print("         old sandbox artifact unavailable in this checkout")
            continue
        summ[label] = summarize(load_rows(jsonl))

    print("\n=== AIME 2026 pass@4 / avg@4 — Qwen3.5-27B ===\n")
    cols = list(summ)
    w = max((len(c) for c in cols), default=10) + 2
    hdr = f"{'metric':<20}" + "".join(f"{c:>{w}}" for c in cols)
    print(hdr)
    print("-" * len(hdr))
    for label, key, fmt in [
        ("tasks", "n_tasks", "{:d}"),
        ("samples scored", "scored", "{:d}"),
        ("blank/error", "blank_or_error", "{:d}"),
        ("pass@4", "pass_at_4", "{:.4f}"),
        ("avg@4", "avg_at_4", "{:.4f}"),
        ("accuracy (scored)", "accuracy_scored", "{:.4f}"),
    ]:
        row = f"{label:<20}"
        for c in cols:
            row += f"{fmt.format(summ[c][key]):>{w}}"
        print(row)

    base = "DirectLLM baseline"
    for candidate in (
        "OpenClaw (Podman 031bfc chatmax)",
        "OpenClaw (Podman a17a diagnostic)",
        "OpenClaw (Podman timeout-only)",
    ):
        if candidate in summ:
            harn = candidate
            break
    else:
        harn = ""
    if base in summ and harn in summ:
        b, h = summ[base], summ[harn]
        print(f"\n--- alignment: {harn} vs {base} ---")
        print(f"  pass@4 delta : {h['pass_at_4'] - b['pass_at_4']:+.4f}")
        print(f"  avg@4 delta  : {h['avg_at_4'] - b['avg_at_4']:+.4f}")
        tasks = sorted(set(b["task_correct"]) | set(h["task_correct"]),
                       key=lambda t: int(t.split("_")[-1]) if t.split("_")[-1].isdigit() else 0)
        disagree = [t for t in tasks
                    if b["task_correct"].get(t) != h["task_correct"].get(t)]
        print(f"  per-problem pass@4 agreement: {len(tasks) - len(disagree)}/{len(tasks)}")
        for t in disagree:
            print(f"    {t}: harness={h['task_correct'].get(t)}  base={b['task_correct'].get(t)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
