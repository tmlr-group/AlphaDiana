#!/usr/bin/env python3
"""Summarize AlphaDiana Podman replacement runs.

This is intentionally context-local: it reads result directories and produces
review evidence for runtime parity checks without changing the harness.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


VALID_SCORED_STATUSES = {"ok", "scored", "valid_scored"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _repo_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


def _resolve_run_dir(path: Path) -> Path:
    if (path / "tasks").is_dir():
        return path
    nested = path / path.name
    if (nested / "tasks").is_dir():
        return nested
    return path


def _load_task_records(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _load_rows(run_dir: Path) -> list[dict[str, Any]]:
    tasks_dir = run_dir / "tasks"
    rows: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob("*.json")):
        rows.extend(_load_task_records(path))
    return rows


def _iter_file_refs(value: Any) -> Iterable[str]:
    if isinstance(value, str) and value:
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_file_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_file_refs(item)


def _has_relative_file_ref(value: Any) -> bool:
    return any(not Path(ref).is_absolute() for ref in _iter_file_refs(value))


def _artifact_bases(run_dir: Path, row: dict[str, Any]) -> list[Path]:
    bases = [run_dir / "artifacts"]
    manifest = _as_dict(row.get("artifact_manifest"))
    local_root = manifest.get("local_artifact_root")
    if isinstance(local_root, str) and local_root:
        bases.append(run_dir / "artifacts" / local_root)
    return bases


def _artifact_path(run_dir: Path, row: dict[str, Any], alias: str) -> Path | None:
    files = _as_dict(_as_dict(row.get("artifact_manifest")).get("files"))
    ref = files.get(alias)
    if not ref:
        return None
    for rel in _iter_file_refs(ref):
        if Path(rel).is_absolute():
            continue
        for base in _artifact_bases(run_dir, row):
            candidate = base / rel
            try:
                exists = candidate.exists()
            except OSError:
                exists = False
            if exists:
                return candidate
    return None


def _read_artifact_text(run_dir: Path, row: dict[str, Any], *aliases: str) -> str:
    for alias in aliases:
        path = _artifact_path(run_dir, row, alias)
        if path and path.is_file():
            return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def _tool_name(value: Any) -> str:
    if isinstance(value, dict):
        function = value.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        return str(value.get("tool") or value.get("name") or value.get("id") or "unknown")
    return "unknown"


def _session_tool_events(text: str) -> tuple[list[str], int]:
    calls: list[str] = []
    results = 0
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        messages = []
        if isinstance(record, dict):
            if isinstance(record.get("message"), dict):
                messages.append(record["message"])
            if isinstance(record.get("messages"), list):
                messages.extend(item for item in record["messages"] if isinstance(item, dict))
            if record.get("role") or record.get("tool_calls"):
                messages.append(record)
        for message in messages:
            role = str(message.get("role") or "").replace("_", "").lower()
            if role in {"tool", "toolresult"}:
                results += 1
            for call in _as_list(message.get("tool_calls")):
                calls.append(_tool_name(call))
            content = message.get("content")
            for item in _as_list(content):
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("type") or "").replace("_", "").lower()
                if kind in {"toolcall", "tooluse"}:
                    calls.append(_tool_name(item))
                elif kind == "toolresult":
                    results += 1
    return calls, results


def _trajectory_tool_events(items: list[Any]) -> tuple[list[str], int]:
    calls: list[str] = []
    results = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").replace("_", "").lower()
        role = str(item.get("role") or "").replace("_", "").lower()
        if kind in {"toolresult"} or role in {"tool", "toolresult"}:
            results += max(1, len(_as_list(item.get("tool_results"))))
        if kind in {"tooluse", "toolcall"}:
            direct_name = item.get("tool") or item.get("name")
            if direct_name:
                calls.append(str(direct_name))
        for call in _as_list(item.get("tool_calls")):
            calls.append(_tool_name(call))
        for call in _as_list(item.get("actions")):
            if isinstance(call, dict) and (call.get("tool") or call.get("name")):
                calls.append(_tool_name(call))
        content = item.get("content")
        for part in _as_list(content):
            if not isinstance(part, dict):
                continue
            part_kind = str(part.get("type") or "").replace("_", "").lower()
            if part_kind in {"toolcall", "tooluse"}:
                calls.append(_tool_name(part))
            elif part_kind == "toolresult":
                results += 1
    return calls, results


def _tool_events(run_dir: Path, row: dict[str, Any]) -> tuple[list[str], int, str]:
    session_text = _read_artifact_text(
        run_dir,
        row,
        "openclaw_session",
        "session_trace",
        "normalized_trace",
    )
    calls, results = _session_tool_events(session_text)
    if calls or results:
        return calls, results, "artifact_session"
    calls, results = _trajectory_tool_events(_as_list(row.get("trajectory")))
    if calls or results:
        return calls, results, "trajectory"
    calls, results = _trajectory_tool_events(_as_list(row.get("reasoning_trajectory")))
    if calls or results:
        return calls, results, "reasoning_trajectory"
    return [], 0, "none"


def _manifest_files(row: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(row.get("artifact_manifest")).get("files"))


def _existing_aliases(run_dir: Path, row: dict[str, Any]) -> set[str]:
    existing: set[str] = set()
    for alias in _manifest_files(row):
        if _artifact_path(run_dir, row, alias):
            existing.add(alias)
    return existing


def _missing_file_ref_aliases(run_dir: Path, row: dict[str, Any]) -> set[str]:
    missing: set[str] = set()
    for alias, value in _manifest_files(row).items():
        if _has_relative_file_ref(value) and not _artifact_path(run_dir, row, alias):
            missing.add(alias)
    return missing


def _error_taxonomy(row: dict[str, Any]) -> str:
    status = str(row.get("score_status") or "")
    error = _as_dict(row.get("error"))
    metadata = _as_dict(row.get("metadata"))
    error_type = str(error.get("error_type") or "").lower()
    error_message = str(error.get("error") or "").lower()
    finish_reason = str(row.get("finish_reason") or "").lower()
    failure_reason = str(metadata.get("failure_reason") or "").lower()
    evidence = "\n".join([
        status,
        error_type,
        error_message,
        finish_reason,
        failure_reason,
    ]).lower()
    if (
        finish_reason in {"length", "max_tokens"}
        or failure_reason == "token_budget_exhausted"
        or "token_budget" in evidence
    ):
        return "token_budget"
    if status in VALID_SCORED_STATUSES and not error:
        return "clean"
    if "timeout" in evidence or "timed out" in evidence:
        return "timeout"
    if "provider" in evidence or "bad request" in evidence or "context" in evidence:
        return "provider"
    if "empty" in evidence or "no assistant" in evidence:
        return "agent_empty_output"
    if "podman" in evidence or "container" in evidence or "runtime" in evidence:
        return "runtime"
    if "scor" in evidence or "verifier" in evidence:
        return "scoring"
    return status or error_type or "unknown"


def _numeric_summary(values: list[float]) -> dict[str, Any]:
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


def summarize_run(path: Path, *, required_aliases: set[str]) -> dict[str, Any]:
    run_dir = _resolve_run_dir(path)
    rows = _load_rows(run_dir)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row.get("task_id") or "")].append(row)

    agent_names = Counter(str(row.get("agent_name") or row.get("agent") or "") for row in rows)
    benchmark_names = Counter(str(row.get("benchmark_name") or "") for row in rows)
    statuses = Counter(str(row.get("score_status") or "") for row in rows)
    error_taxonomy = Counter(_error_taxonomy(row) for row in rows)
    tool_names: Counter[str] = Counter()
    tool_sources: Counter[str] = Counter()
    tool_call_counts: list[float] = []
    tool_result_counts: list[float] = []
    alias_sets: list[set[str]] = []
    existing_alias_sets: list[set[str]] = []
    missing_required_rows = 0
    missing_file_ref_rows = 0
    sample_details: list[dict[str, Any]] = []

    scored = 0
    correct = 0
    score_sum = 0.0
    for row in rows:
        calls, results, source = _tool_events(run_dir, row)
        tool_call_counts.append(float(len(calls)))
        tool_result_counts.append(float(results))
        tool_names.update(calls)
        tool_sources[source] += 1

        aliases = set(_manifest_files(row))
        existing = _existing_aliases(run_dir, row)
        alias_sets.append(aliases)
        existing_alias_sets.append(existing)
        missing_required = sorted(required_aliases - aliases)
        missing_files = sorted(_missing_file_ref_aliases(run_dir, row))
        if missing_required:
            missing_required_rows += 1
        if missing_files:
            missing_file_ref_rows += 1
        if row.get("score_status") in VALID_SCORED_STATUSES:
            scored += 1
        if bool(row.get("correct")):
            correct += 1
        if isinstance(row.get("score"), (int, float)):
            score_sum += float(row["score"])
        sample_details.append({
            "task_id": row.get("task_id"),
            "sample_index": row.get("sample_index"),
            "score_status": row.get("score_status"),
            "score": row.get("score"),
            "correct": row.get("correct"),
            "finish_reason": row.get("finish_reason"),
            "error_taxonomy": _error_taxonomy(row),
            "tool_call_count": len(calls),
            "tool_result_count": results,
            "tool_names": calls,
            "tool_source": source,
            "artifact_alias_count": len(aliases),
            "missing_required_aliases": missing_required,
            "missing_file_refs": missing_files[:20],
        })

    task_pass = {
        task_id: any(bool(row.get("correct")) for row in task_rows)
        for task_id, task_rows in by_task.items()
    }
    common_aliases = sorted(set.intersection(*alias_sets)) if alias_sets else []
    union_aliases = sorted(set.union(*alias_sets)) if alias_sets else []
    common_existing_aliases = sorted(set.intersection(*existing_alias_sets)) if existing_alias_sets else []

    return {
        "run_id": run_dir.name,
        "run_dir": _repo_label(run_dir),
        "agent_names": dict(agent_names),
        "benchmark_names": dict(benchmark_names),
        "tasks": len(by_task),
        "task_ids": sorted(by_task),
        "samples": len(rows),
        "valid_scored_samples": scored,
        "correct_samples": correct,
        "score_sum": score_sum,
        "sample_accuracy": correct / len(rows) if rows else None,
        "score_rate": score_sum / len(rows) if rows else None,
        "pass_at_observed": sum(task_pass.values()) / len(task_pass) if task_pass else None,
        "status_counts": dict(statuses),
        "error_taxonomy": dict(error_taxonomy),
        "tool_call_summary": _numeric_summary(tool_call_counts),
        "tool_result_summary": _numeric_summary(tool_result_counts),
        "tool_names": dict(sorted(tool_names.items())),
        "tool_name_set": sorted(tool_names),
        "tool_sources": dict(tool_sources),
        "artifact_common_aliases": common_aliases,
        "artifact_union_aliases": union_aliases,
        "artifact_common_existing_aliases": common_existing_aliases,
        "required_aliases": sorted(required_aliases),
        "rows_missing_required_aliases": missing_required_rows,
        "rows_missing_file_refs": missing_file_ref_rows,
        "sample_details": sample_details,
    }


def _fmt_float(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def compare_runs(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(summaries) < 2:
        return []
    baseline = summaries[0]
    rows = []
    base_tools = set(baseline["tool_name_set"])
    base_tool_total = baseline["tool_call_summary"]["total"] or 0
    for current in summaries[1:]:
        current_tools = set(current["tool_name_set"])
        current_tool_total = current["tool_call_summary"]["total"] or 0
        ratio = None
        if base_tool_total:
            ratio = current_tool_total / base_tool_total
        rows.append({
            "baseline": baseline["run_id"],
            "candidate": current["run_id"],
            "pass_at_observed_delta": (
                None
                if baseline["pass_at_observed"] is None or current["pass_at_observed"] is None
                else current["pass_at_observed"] - baseline["pass_at_observed"]
            ),
            "score_rate_delta": (
                None
                if baseline["score_rate"] is None or current["score_rate"] is None
                else current["score_rate"] - baseline["score_rate"]
            ),
            "tool_call_total_ratio": ratio,
            "tool_name_overlap": sorted(base_tools & current_tools),
            "tool_names_only_baseline": sorted(base_tools - current_tools),
            "tool_names_only_candidate": sorted(current_tools - base_tools),
            "artifact_aliases_only_baseline": sorted(
                set(baseline["artifact_common_aliases"]) - set(current["artifact_common_aliases"])
            ),
            "artifact_aliases_only_candidate": sorted(
                set(current["artifact_common_aliases"]) - set(baseline["artifact_common_aliases"])
            ),
        })
    return rows


def render_markdown(title: str, summaries: list[dict[str, Any]], comparisons: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    lines.extend([
        "| Run | Agent | Benchmark | Tasks | Samples | Valid | Score rate | Pass@observed | Tool calls | Tool mean | Tool names | Artifact common aliases | Format gaps |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ])
    for summary in summaries:
        agent = ", ".join(k for k in summary["agent_names"] if k) or "-"
        benchmark = ", ".join(k for k in summary["benchmark_names"] if k) or "-"
        tool_names = ", ".join(summary["tool_name_set"][:12]) or "-"
        aliases = ", ".join(summary["artifact_common_aliases"][:16]) or "-"
        gaps = []
        if summary["rows_missing_required_aliases"]:
            gaps.append(f"{summary['rows_missing_required_aliases']} missing required")
        if summary["rows_missing_file_refs"]:
            gaps.append(f"{summary['rows_missing_file_refs']} missing file refs")
        lines.append(
            "| "
            + " | ".join([
                summary["run_id"],
                agent,
                benchmark,
                str(summary["tasks"]),
                str(summary["samples"]),
                str(summary["valid_scored_samples"]),
                _fmt_float(summary["score_rate"]),
                _fmt_float(summary["pass_at_observed"]),
                str(int(summary["tool_call_summary"]["total"])),
                _fmt_float(summary["tool_call_summary"]["mean"]),
                tool_names,
                aliases,
                "; ".join(gaps) or "none",
            ])
            + " |"
        )

    if comparisons:
        lines.extend(["", "## Pairwise Against First Run", ""])
        lines.extend([
            "| Candidate | Pass delta | Score-rate delta | Tool-call ratio | Tool overlap | Tool only baseline | Tool only candidate | Artifact common alias deltas |",
            "| --- | ---: | ---: | ---: | --- | --- | --- | --- |",
        ])
        for cmp in comparisons:
            alias_delta = []
            if cmp["artifact_aliases_only_baseline"]:
                alias_delta.append("baseline only: " + ", ".join(cmp["artifact_aliases_only_baseline"][:12]))
            if cmp["artifact_aliases_only_candidate"]:
                alias_delta.append("candidate only: " + ", ".join(cmp["artifact_aliases_only_candidate"][:12]))
            lines.append(
                "| "
                + " | ".join([
                    cmp["candidate"],
                    _fmt_float(cmp["pass_at_observed_delta"]),
                    _fmt_float(cmp["score_rate_delta"]),
                    _fmt_float(cmp["tool_call_total_ratio"]),
                    ", ".join(cmp["tool_name_overlap"][:12]) or "-",
                    ", ".join(cmp["tool_names_only_baseline"][:12]) or "-",
                    ", ".join(cmp["tool_names_only_candidate"][:12]) or "-",
                    "; ".join(alias_delta) or "none",
                ])
                + " |"
            )

    lines.extend(["", "## Status Counts", ""])
    for summary in summaries:
        lines.append(f"- `{summary['run_id']}`: statuses={summary['status_counts']}; errors={summary['error_taxonomy']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path, help="Result run directories.")
    parser.add_argument("--title", default="Podman Replacement Parity Summary")
    parser.add_argument("--required-alias", action="append", default=[])
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    args = parser.parse_args()

    required_aliases = {str(alias) for alias in args.required_alias if str(alias).strip()}
    summaries = [summarize_run(path, required_aliases=required_aliases) for path in args.runs]
    comparisons = compare_runs(summaries)
    payload = {
        "title": args.title,
        "runs": summaries,
        "comparisons": comparisons,
    }
    md = render_markdown(args.title, summaries, comparisons)

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(md, encoding="utf-8")
    if not args.out_json and not args.out_md:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
