#!/usr/bin/env python3
"""Phase 9 runtime monitor for the GPQA-Diamond + Qwen3.5-27B vLLM run.

Invoked by Codex via `codex:rescue`. Polls results/{run_id}/tasks/ every
poll-interval seconds and emits `ALERT:` lines on stdout when:

  (1) task timeout — any per-task JSON shows wall_time_sec > task_timeout
  (2) run silence — no new task JSON for > silence_timeout seconds
  (3) consecutive failures — K tasks in a row with non-null 'error'

Usage:
    python scripts/phase9_monitor.py \
        --run-dir results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs \
        --task-timeout 1800 \
        --silence-timeout 2700 \
        --max-consecutive-failures 5 \
        --poll-interval 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _load_task_records(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return raw if isinstance(raw, list) else [raw]


def monitor_once(
    run_dir: Path,
    state: dict,
    *,
    task_timeout: int,
    silence_timeout: int,
    max_failures: int,
    now: float | None = None,
) -> tuple[dict, list[tuple[str, Any]]]:
    """One polling pass. Returns (new_state, list_of_alerts).

    Pure function — does NOT sleep or print. Printing/looping happens in monitor().
    Alert tuples:
        ("consecutive_failures", int)
        ("silence", float seconds)
        ("task_timeout", task_id, wall_time)
    """
    now = now if now is not None else time.time()
    seen: set[str] = set(state.get("seen_tasks") or set())
    last_result_time: float = state.get("last_result_time") or now
    consecutive_failures: int = int(state.get("consecutive_failures") or 0)

    alerts: list[tuple[str, Any]] = []
    tasks_dir = run_dir / "tasks"
    if tasks_dir.exists():
        for tf in sorted(tasks_dir.glob("*.json")):
            if tf.stem in seen:
                continue
            records = _load_task_records(tf)
            if not records:
                continue
            seen.add(tf.stem)
            last_result_time = now
            for rec in records:
                wall = rec.get("wall_time_sec")
                if isinstance(wall, (int, float)) and wall > task_timeout:
                    alerts.append(("task_timeout", tf.stem, float(wall)))
                if rec.get("error"):
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

    if consecutive_failures >= max_failures:
        alerts.append(("consecutive_failures", consecutive_failures))
    if seen and (now - last_result_time) > silence_timeout:
        alerts.append(("silence", now - last_result_time))

    return (
        {
            "seen_tasks": seen,
            "last_result_time": last_result_time,
            "consecutive_failures": consecutive_failures,
        },
        alerts,
    )


def _format_alert(alert: tuple[str, Any]) -> str:
    kind = alert[0]
    if kind == "consecutive_failures":
        return f"ALERT: {alert[1]} consecutive task failures. Investigate."
    if kind == "silence":
        return f"ALERT: No new results in {alert[1]:.0f}s. Run may be stalled."
    if kind == "task_timeout":
        return f"ALERT: task {alert[1]} exceeded timeout (wall_time={alert[2]:.0f}s)."
    return f"ALERT: {alert}"


def monitor(
    run_dir: Path,
    *,
    task_timeout: int,
    silence_timeout: int,
    max_failures: int,
    poll_interval: int,
    max_iterations: int | None = None,
) -> int:
    """Blocking polling loop. Returns exit code (0 = ok, 1 = terminated)."""
    state: dict = {"seen_tasks": set(), "last_result_time": time.time(), "consecutive_failures": 0}
    iteration = 0
    while True:
        state, alerts = monitor_once(
            run_dir, state,
            task_timeout=task_timeout,
            silence_timeout=silence_timeout,
            max_failures=max_failures,
        )
        for a in alerts:
            print(_format_alert(a), flush=True)
            if a[0] == "consecutive_failures":
                return 1
        iteration += 1
        if max_iterations is not None and iteration >= max_iterations:
            return 0
        if poll_interval > 0:
            time.sleep(poll_interval)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True,
                   help="results/{run_id} directory to monitor")
    p.add_argument("--task-timeout", type=int, default=1800,
                   help="per-task wall-clock timeout in seconds (default 1800)")
    p.add_argument("--silence-timeout", type=int, default=2700,
                   help="run-silence alert threshold in seconds (default 2700 = 1.5x task timeout)")
    p.add_argument("--max-consecutive-failures", type=int, default=5,
                   help="exit after this many consecutive failing tasks (default 5)")
    p.add_argument("--poll-interval", type=int, default=30,
                   help="seconds between polls (default 30; 0 = single pass)")
    p.add_argument("--max-iterations", type=int, default=None,
                   help="stop after N iterations (for tests; default run forever)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return monitor(
        args.run_dir,
        task_timeout=args.task_timeout,
        silence_timeout=args.silence_timeout,
        max_failures=args.max_consecutive_failures,
        poll_interval=args.poll_interval,
        max_iterations=args.max_iterations,
    )


if __name__ == "__main__":
    sys.exit(main())
