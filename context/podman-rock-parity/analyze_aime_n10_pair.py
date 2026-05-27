#!/usr/bin/env python3
"""Compare an AIME N=10 OpenClaw ROCK/Podman pair.

The script reads AlphaDiana run directories, not raw logs. It expects task JSON
files under ``tasks/`` and artifact files under ``artifacts/``.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


REQUIRED_ALIASES = (
    "openclaw_session",
    "session_trace",
    "openclaw_output",
    "response_stream",
    "workspace_listing",
    "workspace_state",
    "selected_response",
    "response_json",
    "request_messages",
    "sandbox_metadata",
    "normalized_trace",
    "system_prompt",
)


def _load_task_rows(run_dir: Path, tasks: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task_id in tasks:
        path = run_dir / "tasks" / f"{task_id}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            rows.extend(r for r in data if isinstance(r, dict))
        elif isinstance(data, dict):
            rows.append(data)
    return rows


def _artifact_path(run_dir: Path, row: dict[str, Any], key: str) -> Path | None:
    files = ((row.get("artifact_manifest") or {}).get("files") or {})
    rel = files.get(key)
    if not isinstance(rel, str) or not rel:
        return None
    return run_dir / "artifacts" / rel


def _load_session_text(run_dir: Path, row: dict[str, Any]) -> str:
    path = _artifact_path(run_dir, row, "openclaw_session")
    if path and path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def _session_tool_events(session_text: str) -> tuple[list[str], int]:
    calls: list[str] = []
    results = 0
    for line in session_text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") if isinstance(record, dict) else None
        role = str(message.get("role", "")) if isinstance(message, dict) else ""
        if role.replace("_", "").lower() in {"tool", "toolresult"}:
            results += 1
            continue
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type", ""))
            normalized = kind.replace("_", "").lower()
            if normalized == "toolcall":
                calls.append(str(item.get("name") or item.get("tool") or "unknown"))
            elif normalized == "toolresult":
                results += 1
    return calls, results


def _trajectory_tool_events(row: dict[str, Any]) -> tuple[list[str], int]:
    calls: list[str] = []
    results = 0
    for step in row.get("trajectory") or []:
        if not isinstance(step, dict):
            continue
        if step.get("type") == "tool_use":
            for call in step.get("tool_calls") or []:
                if isinstance(call, dict):
                    calls.append(str(call.get("tool") or call.get("name") or "unknown"))
        elif step.get("type") == "tool_result":
            results += len(step.get("tool_results") or []) or 1
    return calls, results


def _tool_events(run_dir: Path, row: dict[str, Any]) -> tuple[list[str], int, str]:
    session_text = _load_session_text(run_dir, row)
    calls, results = _session_tool_events(session_text)
    if calls or results:
        return calls, results, "session"
    calls, results = _trajectory_tool_events(row)
    return calls, results, "trajectory"


def _artifact_format(row: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    manifest = row.get("artifact_manifest") or {}
    files = manifest.get("files") or {}
    present_aliases = {k for k in REQUIRED_ALIASES if isinstance(files.get(k), str) and files.get(k)}
    existing_aliases = set()
    for key in present_aliases:
        path = _artifact_path(run_dir, row, key)
        if path and path.exists():
            existing_aliases.add(key)
    return {
        "manifest_keys": sorted(files.keys()),
        "present_required_aliases": sorted(present_aliases),
        "existing_required_aliases": sorted(existing_aliases),
        "missing_required_aliases": sorted(set(REQUIRED_ALIASES) - present_aliases),
        "missing_required_files": sorted(present_aliases - existing_aliases),
        "workspace_file_count": len(files.get("workspace_files") or {}),
    }


def _summarize(run_dir: Path, tasks: list[str]) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    rows = _load_task_rows(run_dir, tasks)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sample_rows: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        task_id = str(row.get("task_id", ""))
        if task_id not in tasks:
            continue
        sample_index = int(row.get("sample_index", 0) or 0)
        key = (task_id, sample_index)
        previous = sample_rows.get(key)
        if previous is None or bool(row.get("correct")) or not bool(previous.get("correct")):
            sample_rows[key] = row
    for (task_id, _sample_index), row in sample_rows.items():
        by_task[task_id].append(row)

    tool_counts: list[int] = []
    tool_result_counts: list[int] = []
    tool_names: Counter[str] = Counter()
    extraction_sources: Counter[str] = Counter()
    sample_details: list[dict[str, Any]] = []
    format_details: list[dict[str, Any]] = []
    correct_samples = 0
    scored_samples = 0

    for task_id in tasks:
        for row in sorted(by_task.get(task_id, []), key=lambda r: int(r.get("sample_index", 0) or 0)):
            calls, result_count, source = _tool_events(run_dir, row)
            tool_counts.append(len(calls))
            tool_result_counts.append(result_count)
            tool_names.update(calls)
            extraction_sources[source] += 1
            if row.get("correct"):
                correct_samples += 1
            if row.get("score_status") in {"ok", "scored", "valid_scored"}:
                scored_samples += 1
            fmt = _artifact_format(row, run_dir)
            format_details.append(fmt)
            sample_details.append({
                "task_id": task_id,
                "sample_index": int(row.get("sample_index", 0) or 0),
                "score_status": row.get("score_status"),
                "correct": bool(row.get("correct")),
                "predicted": row.get("predicted"),
                "tool_call_count": len(calls),
                "tool_result_count": result_count,
                "tool_names": calls,
                "tool_source": source,
                "missing_required_aliases": fmt["missing_required_aliases"],
                "missing_required_files": fmt["missing_required_files"],
                "workspace_file_count": fmt["workspace_file_count"],
            })

    pass_by_task = {
        task_id: any(bool(row.get("correct")) for row in by_task.get(task_id, []))
        for task_id in tasks
    }
    n_tasks = len(tasks)
    expected_samples = n_tasks * 4
    all_alias_keys = [set(d["manifest_keys"]) for d in format_details]
    common_alias_keys = sorted(set.intersection(*all_alias_keys)) if all_alias_keys else []
    union_alias_keys = sorted(set.union(*all_alias_keys)) if all_alias_keys else []

    return {
        "run_id": run_dir.name,
        "run_dir": _run_dir_label(run_dir),
        "tasks_requested": tasks,
        "tasks_with_rows": sorted(by_task),
        "samples_seen": len(sample_rows),
        "samples_expected": expected_samples,
        "scored_samples": scored_samples,
        "correct_samples": correct_samples,
        "pass_at_4": sum(pass_by_task.values()) / n_tasks if n_tasks else 0.0,
        "avg_at_4": correct_samples / expected_samples if expected_samples else 0.0,
        "task_pass": pass_by_task,
        "tool_call_counts": tool_counts,
        "tool_result_counts": tool_result_counts,
        "tool_count_summary": _numeric_summary(tool_counts),
        "tool_result_summary": _numeric_summary(tool_result_counts),
        "tool_names": dict(sorted(tool_names.items())),
        "tool_name_set": sorted(tool_names),
        "tool_extraction_sources": dict(extraction_sources),
        "artifact_common_alias_keys": common_alias_keys,
        "artifact_union_alias_keys": union_alias_keys,
        "sample_details": sample_details,
    }


def _run_dir_label(run_dir: Path) -> str:
    try:
        return str(run_dir.relative_to(Path.cwd()))
    except ValueError:
        return run_dir.name


def _numeric_summary(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "min": None, "median": None, "mean": None, "max": None, "total": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": median(values),
        "mean": mean(values),
        "max": max(values),
        "total": sum(values),
    }


def _mcnemar_exact(a_pass: dict[str, bool], b_pass: dict[str, bool]) -> dict[str, Any]:
    b_only = 0
    a_only = 0
    for task in sorted(set(a_pass) | set(b_pass)):
        a = bool(a_pass.get(task))
        b = bool(b_pass.get(task))
        if a and not b:
            a_only += 1
        elif b and not a:
            b_only += 1
    n = a_only + b_only
    if n == 0:
        p = 1.0
    else:
        k = min(a_only, b_only)
        cdf = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
        p = min(1.0, 2 * cdf)
    return {"a_only": a_only, "b_only": b_only, "discordant": n, "two_sided_p": p}


def _write_markdown(path: Path, result: dict[str, Any]) -> None:
    rock = result["rock"]
    podman = result["podman"]
    cmp = result["comparison"]
    lines = [
        "# AIME N=10 ROCK vs Podman Pair Analysis",
        "",
        f"- ROCK run: `{rock['run_id']}`",
        f"- Podman run: `{podman['run_id']}`",
        f"- Tasks: `{', '.join(result['tasks'])}`",
        "",
        "## Summary",
        "",
        "| Metric | ROCK | Podman | Delta / ratio |",
        "|---|---:|---:|---:|",
        f"| samples seen | {rock['samples_seen']} | {podman['samples_seen']} | {podman['samples_seen'] - rock['samples_seen']:+d} |",
        f"| scored samples | {rock['scored_samples']} | {podman['scored_samples']} | {podman['scored_samples'] - rock['scored_samples']:+d} |",
        f"| correct samples | {rock['correct_samples']} | {podman['correct_samples']} | {podman['correct_samples'] - rock['correct_samples']:+d} |",
        f"| pass@4 | {rock['pass_at_4']:.4f} | {podman['pass_at_4']:.4f} | {cmp['pass_at_4_delta']:+.4f} |",
        f"| avg@4 | {rock['avg_at_4']:.4f} | {podman['avg_at_4']:.4f} | {cmp['avg_at_4_delta']:+.4f} |",
        f"| total tool calls | {rock['tool_count_summary']['total']} | {podman['tool_count_summary']['total']} | {cmp['tool_call_total_ratio']:.4f} |",
        f"| mean tool calls/sample | {rock['tool_count_summary']['mean']:.4f} | {podman['tool_count_summary']['mean']:.4f} | {cmp['tool_call_mean_ratio']:.4f} |",
        f"| median tool calls/sample | {rock['tool_count_summary']['median']} | {podman['tool_count_summary']['median']} | |",
        f"| tool-name Jaccard | | | {cmp['tool_name_jaccard']:.4f} |",
        f"| artifact alias Jaccard | | | {cmp['artifact_alias_jaccard']:.4f} |",
        "",
        "## Pass@4 Pairing",
        "",
        f"- McNemar discordant tasks: `{cmp['mcnemar']['discordant']}`",
        f"- ROCK-only pass tasks: `{cmp['mcnemar']['a_only']}`",
        f"- Podman-only pass tasks: `{cmp['mcnemar']['b_only']}`",
        f"- Exact two-sided p-value: `{cmp['mcnemar']['two_sided_p']:.4f}`",
        "",
        "## Tool Names",
        "",
        f"- ROCK: `{', '.join(rock['tool_name_set']) or '(none)'}`",
        f"- Podman: `{', '.join(podman['tool_name_set']) or '(none)'}`",
        f"- Intersection: `{', '.join(cmp['tool_name_intersection']) or '(none)'}`",
        "",
        "## Artifact Aliases",
        "",
        f"- ROCK common aliases: `{', '.join(rock['artifact_common_alias_keys'])}`",
        f"- Podman common aliases: `{', '.join(podman['artifact_common_alias_keys'])}`",
        f"- Missing from Podman common set vs ROCK: `{', '.join(cmp['aliases_missing_in_podman']) or '(none)'}`",
        f"- Extra in Podman common set vs ROCK: `{', '.join(cmp['aliases_extra_in_podman']) or '(none)'}`",
        "",
        "## Per-Task Pass",
        "",
        "| Task | ROCK pass@4 | Podman pass@4 |",
        "|---|---:|---:|",
    ]
    for task in result["tasks"]:
        lines.append(f"| `{task}` | {str(rock['task_pass'].get(task)).lower()} | {str(podman['task_pass'].get(task)).lower()} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rock-run", required=True, type=Path)
    parser.add_argument("--podman-run", required=True, type=Path)
    parser.add_argument("--out-json", required=True, type=Path)
    parser.add_argument("--out-md", required=True, type=Path)
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=[f"aime_{i}" for i in range(1, 11)],
    )
    args = parser.parse_args()

    rock = _summarize(args.rock_run, args.tasks)
    podman = _summarize(args.podman_run, args.tasks)
    rock_tools = set(rock["tool_name_set"])
    podman_tools = set(podman["tool_name_set"])
    rock_aliases = set(rock["artifact_common_alias_keys"])
    podman_aliases = set(podman["artifact_common_alias_keys"])
    comparison = {
        "pass_at_4_delta": podman["pass_at_4"] - rock["pass_at_4"],
        "avg_at_4_delta": podman["avg_at_4"] - rock["avg_at_4"],
        "tool_call_total_ratio": (
            podman["tool_count_summary"]["total"] / rock["tool_count_summary"]["total"]
            if rock["tool_count_summary"]["total"] else None
        ),
        "tool_call_mean_ratio": (
            podman["tool_count_summary"]["mean"] / rock["tool_count_summary"]["mean"]
            if rock["tool_count_summary"]["mean"] else None
        ),
        "tool_name_intersection": sorted(rock_tools & podman_tools),
        "tool_name_jaccard": _jaccard(rock_tools, podman_tools),
        "artifact_alias_jaccard": _jaccard(rock_aliases, podman_aliases),
        "aliases_missing_in_podman": sorted(rock_aliases - podman_aliases),
        "aliases_extra_in_podman": sorted(podman_aliases - rock_aliases),
        "mcnemar": _mcnemar_exact(rock["task_pass"], podman["task_pass"]),
    }
    result = {
        "tasks": args.tasks,
        "rock": rock,
        "podman": podman,
        "comparison": comparison,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    _write_markdown(args.out_md, result)
    print(args.out_json)
    print(args.out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
