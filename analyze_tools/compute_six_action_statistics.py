#!/usr/bin/env python3
"""Compute six-action, entropy/token, and tool-call statistics.

This script is a reproducible companion to ``compute_six_action_frequencies.py``.
It uses the same six-action extractor, then adds:

* action counts by success/failure outcome
* action transition counts by success/failure outcome
* entropy and token-length summaries by success/failure outcome
* tool-call type counts by success/failure outcome
* tool-call type transition counts by success/failure outcome

Rows with ``correct is None`` are kept under the ``unknown`` outcome and are not
folded into success or failure.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import compute_six_action_frequencies as six


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "analyze_tools" / "data" / "six_action_statistics"

TOOL_TYPES = (
    "bash",
    "python",
    "search",
    "web_fetch",
    "read",
    "write",
    "edit",
    "browser",
    "image",
    "tool",
    "unknown_tool",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        action="append",
        default=[],
        metavar="BENCHMARK:HARNESS:SOURCE_ID:PATH",
        help="Result-store spec. May be repeated.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def parse_spec(value: str) -> six.RunSpec:
    return six.parse_spec(value)


def path_for_report(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def load_records(spec: six.RunSpec) -> list[dict[str, Any]]:
    return six.load_task_records(spec.root / "tasks")


def task_key(rec: dict[str, Any]) -> tuple[str, Any]:
    task_id = str(rec.get("task_id") or str(rec.get("_task_file", "")).removesuffix(".json"))
    sample_index = rec.get("sample_index")
    if sample_index is None:
        sample_index = rec.get("_sample_pos", 0)
    return task_id, sample_index


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return val


def record_entropy_token_metrics(rec: dict[str, Any]) -> dict[str, float | None]:
    stats = rec.get("token_entropy_stats") if isinstance(rec.get("token_entropy_stats"), dict) else {}
    usage = rec.get("token_usage") if isinstance(rec.get("token_usage"), dict) else {}
    response = rec.get("response_json") if isinstance(rec.get("response_json"), dict) else {}
    response_usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}

    n_tokens = (
        numeric(stats.get("n_tokens"))
        or numeric(usage.get("completion_tokens"))
        or numeric(response_usage.get("completion_tokens"))
        or numeric(rec.get("n_tokens"))
    )
    return {
        "mean_entropy": numeric(stats.get("mean")),
        "max_entropy": numeric(stats.get("max")),
        "p50_entropy": numeric(stats.get("p50")),
        "p90_entropy": numeric(stats.get("p90")),
        "n_tokens": n_tokens,
        "wall_time_sec": numeric(rec.get("wall_time_sec")),
    }


def summarize_values(values: list[float]) -> dict[str, str]:
    if not values:
        return {
            "count": "0",
            "mean": "",
            "median": "",
            "std": "",
            "min": "",
            "p25": "",
            "p75": "",
            "max": "",
        }
    vals = sorted(values)
    n = len(vals)

    def q(p: float) -> float:
        if n == 1:
            return vals[0]
        pos = (n - 1) * p
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return vals[lo]
        frac = pos - lo
        return vals[lo] * (1 - frac) + vals[hi] * frac

    std = statistics.pstdev(vals) if n > 1 else 0.0
    return {
        "count": str(n),
        "mean": f"{statistics.fmean(vals):.6f}",
        "median": f"{statistics.median(vals):.6f}",
        "std": f"{std:.6f}",
        "min": f"{vals[0]:.6f}",
        "p25": f"{q(0.25):.6f}",
        "p75": f"{q(0.75):.6f}",
        "max": f"{vals[-1]:.6f}",
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def infer_tool_type_from_text(text: str, default: str = "tool") -> str:
    lower = text.lower()
    if "python" in lower or "python3" in lower:
        return "python"
    if "bash" in lower or "shell" in lower or "command" in lower or "ls " in lower or "grep" in lower:
        return "bash"
    if "search" in lower:
        return "search"
    if "web_fetch" in lower or "fetch" in lower or "url" in lower:
        return "web_fetch"
    if "read" in lower or "cat " in lower:
        return "read"
    if "write" in lower:
        return "write"
    if "edit" in lower or "patch" in lower:
        return "edit"
    if "browser" in lower:
        return "browser"
    if "image" in lower:
        return "image"
    return default


def tool_type_from_part(part: dict[str, Any]) -> str:
    tool = str(part.get("tool") or part.get("name") or part.get("type") or "tool")
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    input_obj = state.get("input") if isinstance(state.get("input"), dict) else {}
    command = str(input_obj.get("command") or "")
    if tool in {"bash", "shell"}:
        return infer_tool_type_from_text(command, default="bash")
    return infer_tool_type_from_text(f"{tool} {command}", default=tool if tool else "tool")


def opencode_tool_types(spec: six.RunSpec, task_id: str) -> list[str]:
    path = spec.root / "artifacts" / task_id / "workspace" / "opencode_output.jsonl"
    if not path.exists():
        return []
    out: list[str] = []
    try:
        with path.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "tool_use":
                    continue
                part = row.get("part") if isinstance(row.get("part"), dict) else {}
                out.append(tool_type_from_part(part))
    except OSError:
        return []
    return out


def tool_events_from_record(spec: six.RunSpec, rec: dict[str, Any]) -> list[str]:
    task_id, _sample_index = task_key(rec)
    if spec.harness == "OpenCode":
        parsed = opencode_tool_types(spec, task_id)
        if parsed:
            return parsed

    events: list[str] = []
    trajectory = rec.get("trajectory")
    if not isinstance(trajectory, list):
        return events
    for step in trajectory:
        if not isinstance(step, dict):
            continue
        stype = str(step.get("type") or "")
        role = str(step.get("role") or "")
        if stype not in {"tool_use", "tool_call", "function_call"} and role != "tool":
            continue
        if role == "tool" or stype == "tool_result":
            continue
        content = step.get("content")
        tool_calls = step.get("tool_calls") if isinstance(step.get("tool_calls"), list) else []
        if tool_calls:
            for call in tool_calls:
                if isinstance(call, dict):
                    events.append(infer_tool_type_from_text(json.dumps(call, ensure_ascii=False), default="tool"))
            continue
        if isinstance(content, list):
            events.append(infer_tool_type_from_text(json.dumps(content, ensure_ascii=False), default="tool"))
        elif isinstance(content, str):
            events.append(infer_tool_type_from_text(content, default="tool"))
        else:
            events.append("unknown_tool")
    return events


def generate_statistics(specs: list[six.RunSpec], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    action_events: list[dict[str, Any]] = []
    trajectory_metrics: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    extraction_failures: list[dict[str, Any]] = []

    for spec in specs:
        records = load_records(spec)
        source_rows.append(
            {
                "benchmark": spec.benchmark,
                "harness": spec.harness,
                "source_id": spec.source_id,
                "source_path": path_for_report(spec.root),
                "tasks_path": path_for_report(spec.root / "tasks"),
                "task_records": len(records),
            }
        )
        for rec in records:
            task_id, sample_index = task_key(rec)
            outcome = six.record_success(rec)
            base = {
                "benchmark": spec.benchmark,
                "harness": spec.harness,
                "source_id": spec.source_id,
                "task_id": task_id,
                "sample_index": sample_index,
                "outcome": outcome,
                "correct": rec.get("correct"),
                "score_status": rec.get("score_status") or "",
            }
            try:
                rows = six.event_rows_for_record(spec, rec)
            except Exception as exc:  # noqa: BLE001 - audit extraction failures.
                extraction_failures.append({**base, "failure_type": "action_extraction_error", "detail": repr(exc)})
                rows = []
            action_events.extend(rows)

            metrics = record_entropy_token_metrics(rec)
            trajectory_metrics.append(
                {
                    **base,
                    **metrics,
                    "action_event_count": len(rows),
                    "tool_call_count": 0,
                }
            )

            try:
                tool_types = tool_events_from_record(spec, rec)
            except Exception as exc:  # noqa: BLE001
                extraction_failures.append({**base, "failure_type": "tool_extraction_error", "detail": repr(exc)})
                tool_types = []
            trajectory_metrics[-1]["tool_call_count"] = len(tool_types)
            for idx, tool_type in enumerate(tool_types):
                tool_events.append({**base, "tool_event_index": idx, "tool_type": tool_type})

            if not rows:
                extraction_failures.append({**base, "failure_type": "no_action_events", "detail": "No model action spans extracted"})
            if metrics["mean_entropy"] is None:
                extraction_failures.append({**base, "failure_type": "missing_entropy", "detail": "token_entropy_stats.mean missing"})
            if metrics["n_tokens"] is None:
                extraction_failures.append({**base, "failure_type": "missing_token_length", "detail": "No n_tokens or completion token fallback"})

    write_outputs(output_dir, source_rows, trajectory_metrics, action_events, tool_events, extraction_failures)


def action_count_rows(action_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter((r["benchmark"], r["harness"], r["outcome"], r["action"]) for r in action_events)
    totals = Counter((r["benchmark"], r["harness"], r["outcome"]) for r in action_events)
    rows = []
    for (benchmark, harness, outcome, action), count in sorted(counts.items()):
        total = totals[(benchmark, harness, outcome)]
        rows.append(
            {
                "benchmark": benchmark,
                "harness": harness,
                "outcome": outcome,
                "action": action,
                "count": count,
                "event_total": total,
                "event_fraction": f"{count / total:.8f}" if total else "",
            }
        )
    return rows


def action_transition_rows(action_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, Any, str], list[dict[str, Any]]] = defaultdict(list)
    for r in action_events:
        grouped[(r["benchmark"], r["harness"], r["source_id"], r["task_id"], r["sample_index"], r["outcome"])].append(r)
    counts: Counter[tuple[str, str, str, str, str]] = Counter()
    totals: Counter[tuple[str, str, str]] = Counter()
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda x: int(x["event_index"]))
        actions = ["__START__"] + [r["action"] for r in rows] + ["__END__"]
        benchmark, harness, _source_id, _task_id, _sample_index, outcome = key
        for from_action, to_action in zip(actions, actions[1:]):
            counts[(benchmark, harness, outcome, from_action, to_action)] += 1
            totals[(benchmark, harness, outcome)] += 1
    out = []
    for (benchmark, harness, outcome, from_action, to_action), count in sorted(counts.items()):
        total = totals[(benchmark, harness, outcome)]
        out.append(
            {
                "benchmark": benchmark,
                "harness": harness,
                "outcome": outcome,
                "from_action": from_action,
                "to_action": to_action,
                "transition_count": count,
                "transition_total": total,
                "transition_fraction": f"{count / total:.8f}" if total else "",
            }
        )
    return out


def entropy_token_summary_rows(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in metrics:
        grouped[(row["benchmark"], row["harness"], row["outcome"])].append(row)
    fields = ["mean_entropy", "max_entropy", "p50_entropy", "p90_entropy", "n_tokens", "wall_time_sec", "action_event_count", "tool_call_count"]
    rows = []
    for (benchmark, harness, outcome), vals in sorted(grouped.items()):
        for field in fields:
            nums = [float(v[field]) for v in vals if v.get(field) not in (None, "")]
            summary = summarize_values(nums)
            rows.append(
                {
                    "benchmark": benchmark,
                    "harness": harness,
                    "outcome": outcome,
                    "metric": field,
                    "trajectory_records": len(vals),
                    "missing": len(vals) - int(summary["count"]),
                    **summary,
                }
            )
    return rows


def tool_count_rows(tool_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter((r["benchmark"], r["harness"], r["outcome"], r["tool_type"]) for r in tool_events)
    totals = Counter((r["benchmark"], r["harness"], r["outcome"]) for r in tool_events)
    rows = []
    for (benchmark, harness, outcome, tool_type), count in sorted(counts.items()):
        total = totals[(benchmark, harness, outcome)]
        rows.append(
            {
                "benchmark": benchmark,
                "harness": harness,
                "outcome": outcome,
                "tool_type": tool_type,
                "count": count,
                "tool_event_total": total,
                "tool_event_fraction": f"{count / total:.8f}" if total else "",
            }
        )
    return rows


def tool_transition_rows(tool_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, Any, str], list[dict[str, Any]]] = defaultdict(list)
    for r in tool_events:
        grouped[(r["benchmark"], r["harness"], r["source_id"], r["task_id"], r["sample_index"], r["outcome"])].append(r)
    counts: Counter[tuple[str, str, str, str, str]] = Counter()
    totals: Counter[tuple[str, str, str]] = Counter()
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda x: int(x["tool_event_index"]))
        tools = ["__START__"] + [r["tool_type"] for r in rows] + ["__END__"]
        benchmark, harness, _source_id, _task_id, _sample_index, outcome = key
        for from_tool, to_tool in zip(tools, tools[1:]):
            counts[(benchmark, harness, outcome, from_tool, to_tool)] += 1
            totals[(benchmark, harness, outcome)] += 1
    rows = []
    for (benchmark, harness, outcome, from_tool, to_tool), count in sorted(counts.items()):
        total = totals[(benchmark, harness, outcome)]
        rows.append(
            {
                "benchmark": benchmark,
                "harness": harness,
                "outcome": outcome,
                "from_tool_type": from_tool,
                "to_tool_type": to_tool,
                "transition_count": count,
                "transition_total": total,
                "transition_fraction": f"{count / total:.8f}" if total else "",
            }
        )
    return rows


def failure_summary_rows(metrics: list[dict[str, Any]], action_events: list[dict[str, Any]], tool_events: list[dict[str, Any]], extraction_failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failure_metrics = [m for m in metrics if m["outcome"] == "failure"]
    grouped_metrics: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for m in failure_metrics:
        grouped_metrics[(m["benchmark"], m["harness"])].append(m)
    action_counts = Counter((r["benchmark"], r["harness"], r["action"]) for r in action_events if r["outcome"] == "failure")
    tool_counts = Counter((r["benchmark"], r["harness"], r["tool_type"]) for r in tool_events if r["outcome"] == "failure")
    failure_counts = Counter((r["benchmark"], r["harness"], r["failure_type"]) for r in extraction_failures)
    rows = []
    for (benchmark, harness), vals in sorted(grouped_metrics.items()):
        top_action = ""
        top_action_count = 0
        for (b, h, action), count in action_counts.items():
            if (b, h) == (benchmark, harness) and count > top_action_count:
                top_action = action
                top_action_count = count
        top_tool = ""
        top_tool_count = 0
        for (b, h, tool), count in tool_counts.items():
            if (b, h) == (benchmark, harness) and count > top_tool_count:
                top_tool = tool
                top_tool_count = count
        failure_types = "; ".join(
            f"{ftype}={count}"
            for (b, h, ftype), count in sorted(failure_counts.items())
            if (b, h) == (benchmark, harness)
        )
        rows.append(
            {
                "benchmark": benchmark,
                "harness": harness,
                "failure_trajectories": len(vals),
                "mean_tokens": summarize_values([float(v["n_tokens"]) for v in vals if v.get("n_tokens") is not None])["mean"],
                "mean_entropy": summarize_values([float(v["mean_entropy"]) for v in vals if v.get("mean_entropy") is not None])["mean"],
                "mean_action_events": summarize_values([float(v["action_event_count"]) for v in vals])["mean"],
                "mean_tool_calls": summarize_values([float(v["tool_call_count"]) for v in vals])["mean"],
                "top_failure_action": top_action,
                "top_failure_action_count": top_action_count,
                "top_failure_tool_type": top_tool,
                "top_failure_tool_count": top_tool_count,
                "extraction_failure_types": failure_types,
            }
        )
    return rows


def write_outputs(
    output_dir: Path,
    source_rows: list[dict[str, Any]],
    trajectory_metrics: list[dict[str, Any]],
    action_events: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
    extraction_failures: list[dict[str, Any]],
) -> None:
    write_csv(output_dir / "source_manifest.csv", source_rows, ["benchmark", "harness", "source_id", "source_path", "tasks_path", "task_records"])
    write_csv(
        output_dir / "trajectory_metrics.csv",
        trajectory_metrics,
        [
            "benchmark",
            "harness",
            "source_id",
            "task_id",
            "sample_index",
            "outcome",
            "correct",
            "score_status",
            "mean_entropy",
            "max_entropy",
            "p50_entropy",
            "p90_entropy",
            "n_tokens",
            "wall_time_sec",
            "action_event_count",
            "tool_call_count",
        ],
    )
    write_csv(
        output_dir / "action_counts_by_outcome.csv",
        action_count_rows(action_events),
        ["benchmark", "harness", "outcome", "action", "count", "event_total", "event_fraction"],
    )
    write_csv(
        output_dir / "action_transitions_by_outcome.csv",
        action_transition_rows(action_events),
        ["benchmark", "harness", "outcome", "from_action", "to_action", "transition_count", "transition_total", "transition_fraction"],
    )
    write_csv(
        output_dir / "entropy_token_summary_by_outcome.csv",
        entropy_token_summary_rows(trajectory_metrics),
        ["benchmark", "harness", "outcome", "metric", "trajectory_records", "missing", "count", "mean", "median", "std", "min", "p25", "p75", "max"],
    )
    write_csv(
        output_dir / "tool_type_counts_by_outcome.csv",
        tool_count_rows(tool_events),
        ["benchmark", "harness", "outcome", "tool_type", "count", "tool_event_total", "tool_event_fraction"],
    )
    write_csv(
        output_dir / "tool_type_transitions_by_outcome.csv",
        tool_transition_rows(tool_events),
        ["benchmark", "harness", "outcome", "from_tool_type", "to_tool_type", "transition_count", "transition_total", "transition_fraction"],
    )
    write_csv(
        output_dir / "extraction_failure_log.csv",
        extraction_failures,
        ["benchmark", "harness", "source_id", "task_id", "sample_index", "outcome", "correct", "score_status", "failure_type", "detail"],
    )
    write_csv(
        output_dir / "failure_case_summary.csv",
        failure_summary_rows(trajectory_metrics, action_events, tool_events, extraction_failures),
        [
            "benchmark",
            "harness",
            "failure_trajectories",
            "mean_tokens",
            "mean_entropy",
            "mean_action_events",
            "mean_tool_calls",
            "top_failure_action",
            "top_failure_action_count",
            "top_failure_tool_type",
            "top_failure_tool_count",
            "extraction_failure_types",
        ],
    )
    write_markdown_report(output_dir, source_rows, trajectory_metrics, action_events, tool_events, extraction_failures)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open() as f:
        return list(csv.DictReader(f))


def write_markdown_report(
    output_dir: Path,
    source_rows: list[dict[str, Any]],
    trajectory_metrics: list[dict[str, Any]],
    action_events: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
    extraction_failures: list[dict[str, Any]],
) -> None:
    failure_summary = read_csv_rows(output_dir / "failure_case_summary.csv")
    action_counts = read_csv_rows(output_dir / "action_counts_by_outcome.csv")
    entropy_summary = read_csv_rows(output_dir / "entropy_token_summary_by_outcome.csv")
    tool_counts = read_csv_rows(output_dir / "tool_type_counts_by_outcome.csv")

    lines = [
        "# Six-Action Statistics Report",
        "",
        "## Scope",
        "",
        "This report computes statistics for success, failure, and unknown trajectories using the six-action space: Problem Framing, Plan Formation, Solution Execution, Tool Grounding, Result Auditing, and Answer Delivery.",
        "",
        "## Source Data Paths",
        "",
        "| Benchmark | Harness | Source ID | Source path | Task records |",
        "|---|---|---|---|---:|",
    ]
    for row in source_rows:
        lines.append(
            f"| {row['benchmark']} | {row['harness']} | `{row['source_id']}` | `{row['source_path']}` | {row['task_records']} |"
        )

    lines.extend(
        [
            "",
            "## Extraction Logic",
            "",
            "- Task records are loaded from each result store's `tasks/*.json` files.",
            "- List-valued task files are treated as multiple sample trajectories. This is required for AIME Pass@4.",
            "- Outcome is `success` when `correct is True`, `failure` when `correct is False`, and `unknown` otherwise.",
            "- Action spans are extracted with `compute_six_action_frequencies.py`: system/user/tool-result-only rows are excluded; assistant text is segmented; tool-call events are retained.",
            "- Action transition counts are computed per trajectory as `__START__ -> first_action -> ... -> last_action -> __END__`.",
            "- Entropy uses `token_entropy_stats.mean`, `max`, `p50`, and `p90` when present.",
            "- Token length uses `token_entropy_stats.n_tokens`, falling back to completion token usage when available.",
            "- Tool type extraction reads task-level tool events. For OpenCode, it also attempts `artifacts/<task_id>/workspace/opencode_output.jsonl` to recover tool names such as `bash`.",
            "- Tool type transitions are computed per trajectory as `__START__ -> first_tool_type -> ... -> last_tool_type -> __END__`.",
            "",
            "## Calculation Logic",
            "",
            "- `action_counts_by_outcome.csv`: count action event rows by benchmark, harness, outcome, and action.",
            "- `action_transitions_by_outcome.csv`: count adjacent action pairs by benchmark, harness, and outcome.",
            "- `entropy_token_summary_by_outcome.csv`: summary statistics for entropy, token length, wall time, action-event count, and tool-call count.",
            "- `tool_type_counts_by_outcome.csv`: count tool events by benchmark, harness, outcome, and inferred tool type.",
            "- `tool_type_transitions_by_outcome.csv`: count adjacent tool-type pairs by benchmark, harness, and outcome.",
            "",
            "## Failure Case Summary",
            "",
            "| Benchmark | Harness | Failure traj. | Mean tokens | Mean entropy | Mean actions | Mean tools | Top action | Top tool | Extraction failures |",
            "|---|---|---:|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for row in failure_summary:
        lines.append(
            f"| {row['benchmark']} | {row['harness']} | {row['failure_trajectories']} | "
            f"{row['mean_tokens']} | {row['mean_entropy']} | {row['mean_action_events']} | {row['mean_tool_calls']} | "
            f"{row['top_failure_action']} | {row['top_failure_tool_type']} | {row['extraction_failure_types']} |"
        )

    lines.extend(["", "## Action Count Preview", ""])
    lines.append("| Benchmark | Harness | Outcome | Action | Count |")
    lines.append("|---|---|---|---|---:|")
    for row in action_counts[:72]:
        lines.append(f"| {row['benchmark']} | {row['harness']} | {row['outcome']} | {row['action']} | {row['count']} |")

    lines.extend(["", "## Entropy and Token Preview", ""])
    lines.append("| Benchmark | Harness | Outcome | Metric | N | Missing | Mean | Median | P75 |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|")
    for row in entropy_summary:
        if row["metric"] not in {"mean_entropy", "n_tokens", "action_event_count", "tool_call_count"}:
            continue
        lines.append(
            f"| {row['benchmark']} | {row['harness']} | {row['outcome']} | {row['metric']} | "
            f"{row['count']} | {row['missing']} | {row['mean']} | {row['median']} | {row['p75']} |"
        )

    lines.extend(["", "## Tool Count Preview", ""])
    lines.append("| Benchmark | Harness | Outcome | Tool type | Count |")
    lines.append("|---|---|---|---|---:|")
    for row in tool_counts:
        lines.append(f"| {row['benchmark']} | {row['harness']} | {row['outcome']} | {row['tool_type']} | {row['count']} |")

    lines.extend(
        [
            "",
            "## Output Files",
            "",
            "- `source_manifest.csv`",
            "- `trajectory_metrics.csv`",
            "- `action_counts_by_outcome.csv`",
            "- `action_transitions_by_outcome.csv`",
            "- `entropy_token_summary_by_outcome.csv`",
            "- `tool_type_counts_by_outcome.csv`",
            "- `tool_type_transitions_by_outcome.csv`",
            "- `failure_case_summary.csv`",
            "- `extraction_failure_log.csv`",
        ]
    )
    (output_dir / "six_action_statistics_report.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    specs = [parse_spec(value) for value in args.spec]
    if not specs:
        raise SystemExit("Provide --spec entries explicitly for reproducibility.")
    generate_statistics(specs, args.output_dir)
    print(f"wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
