#!/usr/bin/env python3
"""Audit Phase 5 Podman scale-readiness pilot result artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _repo_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _safe_json_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def expected_task_count(config: dict[str, Any]) -> int:
    benchmark_config = _as_dict(_as_dict(config.get("benchmark")).get("config"))
    if benchmark_config.get("dataset_indices") is not None:
        indices = benchmark_config["dataset_indices"]
        if isinstance(indices, list):
            return len(indices)
        if isinstance(indices, str):
            try:
                parsed = json.loads(indices)
                if isinstance(parsed, list):
                    return len(parsed)
            except json.JSONDecodeError:
                return 1
        return 1
    if benchmark_config.get("dataset_index") is not None:
        return 1
    if benchmark_config.get("max_tasks") is not None:
        return int(benchmark_config["max_tasks"])
    return 0


def discover_cells(config_dir: Path) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    paths = [
        path
        for benchmark in ("aime2026", "gpqa", "hle", "imo_answerbench")
        for agent in ("openclaw", "opencode", "zeroclaw")
        for path in [config_dir / f"{benchmark}_{agent}_qwen35_27b.yaml"]
        if path.is_file()
    ]
    for path in paths:
        data = _load_yaml(path)
        agent_name = str(_as_dict(data.get("agent")).get("name") or "")
        benchmark_name = str(_as_dict(data.get("benchmark")).get("name") or "")
        benchmark_key = {
            "gpqa_diamond": "gpqa",
            "imo_answerbench": "imo",
        }.get(benchmark_name, benchmark_name)
        cells.append({
            "config_path": path,
            "config": data,
            "agent_key": agent_name,
            "benchmark_key": benchmark_key,
            "agent": agent_name,
            "benchmark": benchmark_name,
            "expected_tasks": expected_task_count(data) or 1,
        })
    return cells


def find_task_files(results_dir: Path, run_id: str) -> list[Path]:
    candidates = [
        results_dir / run_id / run_id / "tasks",
        results_dir / run_id / "tasks",
    ]
    for tasks_dir in candidates:
        if tasks_dir.exists():
            return sorted(tasks_dir.glob("*.json"))
    return []


def _read_first_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    if isinstance(data, dict):
        return data
    return None


def _artifact_path(record: dict[str, Any], results_dir: Path, run_id: str, root: Path) -> str:
    manifest = _as_dict(record.get("artifact_manifest"))
    base = results_dir / run_id / run_id / "artifacts"
    local_root = manifest.get("local_artifact_root")
    if local_root:
        return _repo_relative(base / str(local_root), root)
    files = _as_dict(manifest.get("files"))
    for value in files.values():
        if isinstance(value, str) and value:
            return _repo_relative(base / value, root)
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str) and nested:
                    return _repo_relative(base / nested, root)
    return ""


def classify_failure(record: dict[str, Any] | None, *, missing_metadata: bool = False) -> str:
    if record is None:
        return "no_task_json"
    if missing_metadata:
        return "metadata_defect"

    metadata = _as_dict(record.get("metadata"))
    error = _as_dict(record.get("error"))
    score_status = str(record.get("score_status") or "").strip()
    finish_reason = str(record.get("finish_reason") or "").strip().lower()
    failure_reason = str(metadata.get("failure_reason") or "").strip().lower()
    error_type = str(error.get("error_type") or "").strip().lower()
    error_message = str(error.get("error") or "").strip().lower()
    evidence = "\n".join([
        score_status,
        finish_reason,
        failure_reason,
        error_type,
        error_message,
        str(record.get("rationale") or ""),
        _safe_json_text(metadata),
    ]).lower()

    if (
        score_status == "agent_empty_output"
        or error_type in {"empty_response", "empty_output", "agent_empty_output"}
        or "empty_output" in evidence
        or "empty assistant" in evidence
        or "no assistant output" in evidence
        or "no assistant content" in error_message
    ):
        return "agent_empty_output"
    if (
        score_status == "provider_error"
        or error_type in {"provider_error", "badrequesterror", "apierror"}
        or "custom api error" in error_message
        or "all providers/models failed" in error_message
        or "tool choice requires" in error_message
        or "bad request" in error_message
    ):
        return "provider_error"
    if (
        "podman" in evidence
        and (
            "connection refused" in error_message
            or "localhost" in error_message
            or "container" in error_message
            or "runtime" in error_type
        )
    ):
        return "podman_runtime"
    if (
        "timeout" in finish_reason
        or "timeout" in failure_reason
        or error_type in {"timeout", "provider_timeout", "agent_timeout"}
        or "timed out" in error_message
        or "readtimeout" in error_message
    ):
        return "provider_timeout"
    if not error and score_status in {"", "valid_scored"} and not failure_reason:
        return "clean"
    if "podman" in evidence or "container" in error_type or "runtime" in error_type:
        return "podman_runtime" if "podman" in evidence else "other"
    if "verifier" in error_type or "evaluator" in evidence:
        return "benchmark_evaluator"
    if "scorer" in error_type or "scoring" in evidence:
        return "scoring_failure"
    if error or score_status not in {"", "valid_scored"}:
        return "other"
    return "clean"


def podman_related(taxonomy: str) -> str:
    if taxonomy in {"metadata_defect", "podman_runtime"}:
        return "yes"
    if taxonomy in {"provider_error", "provider_timeout", "agent_empty_output", "benchmark_evaluator", "scoring_failure"}:
        return "no"
    if taxonomy == "clean":
        return "no"
    return "unclear"


def classify_log_failure(log_path: Path) -> tuple[str, str]:
    """Best-effort root cause for configs that failed before task JSON existed."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return "no_task_json", "no_task_json"
    if not text:
        return "no_task_json", "no_task_json"
    if "podman" in text and (
        "connection refused" in text
        or "localhost:8011" in text
        or "container" in text
    ):
        return "podman_runtime", "podman_runtime"
    if (
        "datasetnotfounderror" in text
        or "gated dataset" in text
        or "failed to load" in text
    ):
        return "benchmark_evaluator", "benchmark_evaluator"
    if "timed out" in text or "timeout" in text:
        return "provider_timeout", "provider_timeout"
    return "no_task_json", "no_task_json"


def row_from_record(
    *,
    record: dict[str, Any],
    task_json_path: Path,
    cell: dict[str, Any],
    run_id: str,
    log_path: Path,
    results_dir: Path,
    root: Path,
) -> dict[str, Any]:
    metadata = _as_dict(record.get("metadata"))
    sandbox_metadata = _as_dict(record.get("sandbox_metadata"))
    container_engine = metadata.get("container_engine")
    sandbox_container_engine = sandbox_metadata.get("container_engine")
    missing_metadata = container_engine != "podman"
    taxonomy = classify_failure(record, missing_metadata=missing_metadata)
    artifact_path = _artifact_path(record, results_dir, run_id, root)
    score_present = "score" in record
    log_exists = log_path.exists()
    artifact_exists = bool(artifact_path)
    audit_failures: list[str] = []
    if record.get("score_status") != "valid_scored":
        audit_failures.append("invalid_score_status")
    if missing_metadata:
        audit_failures.append("missing_metadata_container_engine")
    if not score_present:
        audit_failures.append("missing_score_field")
    if not log_exists:
        audit_failures.append("missing_log")
    if not artifact_exists:
        audit_failures.append("missing_artifact_path")

    error = _as_dict(record.get("error"))
    error_type = error.get("error_type") or metadata.get("failure_reason") or record.get("finish_reason") or ""
    return {
        "config": _repo_relative(cell["config_path"], root),
        "run_id": record.get("run_id") or run_id,
        "task_id": record.get("task_id") or task_json_path.stem,
        "agent": record.get("agent_name") or cell["agent"],
        "benchmark": record.get("benchmark_name") or cell["benchmark"],
        "result_status": record.get("score_status") or "",
        "score": record.get("score") if score_present else None,
        "runtime": record.get("wall_time_sec"),
        "error_type": str(error_type or "-"),
        "artifact_path": artifact_path,
        "task_json_path": _repo_relative(task_json_path, root),
        "log_path": _repo_relative(log_path, root),
        "container_engine": container_engine,
        "sandbox_container_engine": sandbox_container_engine,
        "failure_taxonomy": taxonomy,
        "podman_related": podman_related(taxonomy),
        "audit_status": "fail" if audit_failures else "pass",
        "audit_failures": audit_failures,
    }


def missing_row(
    *,
    cell: dict[str, Any],
    run_id: str,
    log_path: Path,
    root: Path,
    index: int,
) -> dict[str, Any]:
    taxonomy, error_type = classify_log_failure(log_path)
    return {
        "config": _repo_relative(cell["config_path"], root),
        "run_id": run_id,
        "task_id": f"missing_task_{index}",
        "agent": cell["agent"],
        "benchmark": cell["benchmark"],
        "result_status": "no_task_json",
        "score": None,
        "runtime": None,
        "error_type": error_type,
        "artifact_path": "",
        "task_json_path": "",
        "log_path": _repo_relative(log_path, root),
        "container_engine": None,
        "sandbox_container_engine": None,
        "failure_taxonomy": taxonomy,
        "podman_related": podman_related(taxonomy) if taxonomy != "no_task_json" else "unclear",
        "audit_status": "fail",
        "audit_failures": ["missing_task_json"],
    }


def audit(
    *,
    run_prefix: str,
    config_dir: Path,
    results_dir: Path,
    logs_dir: Path,
    root: Path,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for cell in discover_cells(config_dir):
        run_id = f"{run_prefix}_{cell['agent_key']}_{cell['benchmark_key']}"
        log_path = logs_dir / f"{run_id}.log"
        task_files = find_task_files(results_dir, run_id)
        for task_file in task_files:
            record = _read_first_record(task_file)
            if record is None:
                rows.append(missing_row(cell=cell, run_id=run_id, log_path=log_path, root=root, index=len(rows)))
                continue
            rows.append(
                row_from_record(
                    record=record,
                    task_json_path=task_file,
                    cell=cell,
                    run_id=run_id,
                    log_path=log_path,
                    results_dir=results_dir,
                    root=root,
                )
            )
        missing_count = max(int(cell["expected_tasks"]) - len(task_files), 0)
        for idx in range(missing_count):
            rows.append(missing_row(cell=cell, run_id=run_id, log_path=log_path, root=root, index=idx))

    taxonomy_summary = Counter(row["failure_taxonomy"] for row in rows)
    audit_failures = [row for row in rows if row["audit_status"] != "pass"]
    return {
        "run_prefix": run_prefix,
        "expected_cells": len(discover_cells(config_dir)),
        "expected_tasks": sum(int(cell["expected_tasks"]) for cell in discover_cells(config_dir)),
        "rows": rows,
        "taxonomy_summary": dict(sorted(taxonomy_summary.items())),
        "audit_passed": not audit_failures,
        "audit_failure_count": len(audit_failures),
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Podman Scale Readiness Audit: `{result['run_prefix']}`",
        "",
        f"Audit passed: `{str(result['audit_passed']).lower()}`",
        f"Expected cells: `{result['expected_cells']}`",
        f"Expected tasks: `{result['expected_tasks']}`",
        f"Audit failures: `{result['audit_failure_count']}`",
        "",
        "## Rows",
        "",
        "| Config | Task ID | Agent | Benchmark | Status | Score | Runtime | Error Type | Taxonomy | Podman Related | Audit | Artifact | Log |",
        "|---|---|---|---|---|---:|---:|---|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        score = "-" if row["score"] is None else row["score"]
        runtime = "-" if row["runtime"] is None else row["runtime"]
        lines.append(
            "| {config} | {task_id} | {agent} | {benchmark} | {status} | {score} | {runtime} | {error_type} | {taxonomy} | {podman_related} | {audit} | {artifact} | {log} |".format(
                config=row["config"],
                task_id=row["task_id"],
                agent=row["agent"],
                benchmark=row["benchmark"],
                status=row["result_status"],
                score=score,
                runtime=runtime,
                error_type=row["error_type"],
                taxonomy=row["failure_taxonomy"],
                podman_related=row["podman_related"],
                audit=row["audit_status"],
                artifact=row["artifact_path"] or "-",
                log=row["log_path"] or "-",
            )
        )

    lines.extend(["", "## Failure Taxonomy", ""])
    lines.append("| Category | Rows |")
    lines.append("|---|---:|")
    for category, count in result["taxonomy_summary"].items():
        lines.append(f"| `{category}` | {count} |")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--logs-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path.cwd()
    result = audit(
        run_prefix=args.run_prefix,
        config_dir=args.config_dir,
        results_dir=args.results_dir,
        logs_dir=args.logs_dir,
        root=root,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"audit-{args.run_prefix}.json"
    md_path = args.output_dir / f"audit-{args.run_prefix}.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0 if result["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
