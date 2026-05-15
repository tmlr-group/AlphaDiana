#!/usr/bin/env python3
"""Audit Phase 7 TerminalBench2 Podman task-container readiness artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


REQUIRED_TAXONOMY = [
    "podman_runtime",
    "image_pull",
    "task_setup",
    "agent_timeout",
    "agent_empty_output",
    "verifier_failure",
    "no_task_json",
    "metadata_defect",
    "other",
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


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


def _cell_from_config(path: Path) -> dict[str, Any]:
    data = _load_yaml(path)
    metadata = _as_dict(data.get("metadata"))
    run_suffix = str(metadata.get("run_suffix") or path.stem.removesuffix("_pilot"))
    benchmark_config = _as_dict(_as_dict(data.get("benchmark")).get("config"))
    task_names = metadata.get("selected_task_names")
    if not isinstance(task_names, list):
        task_names = benchmark_config.get("task_ids") if isinstance(benchmark_config.get("task_ids"), list) else []
    selected_task_ids = metadata.get("selected_task_ids")
    if not isinstance(selected_task_ids, list):
        selected_task_ids = [f"tb2_{str(value).removeprefix('tb2_')}" for value in task_names]
    return {
        "config_path": path,
        "config": data,
        "run_suffix": run_suffix,
        "agent": str(_as_dict(data.get("agent")).get("name") or ""),
        "benchmark": str(_as_dict(data.get("benchmark")).get("name") or ""),
        "selected_task_ids": [str(value) for value in selected_task_ids],
    }


def discover_cells(config_dir: Path) -> list[dict[str, Any]]:
    return [_cell_from_config(path) for path in sorted(config_dir.glob("*_pilot.yaml"))]


def find_task_files(results_dir: Path, run_id: str) -> dict[str, Path]:
    candidates = [
        results_dir / run_id / run_id / "tasks",
        results_dir / run_id / "tasks",
    ]
    for tasks_dir in candidates:
        if tasks_dir.exists():
            return {path.stem: path for path in sorted(tasks_dir.glob("*.json"))}
    return {}


def _artifact_path(record: dict[str, Any], results_dir: Path, run_id: str, root: Path) -> tuple[str, bool]:
    manifest = _as_dict(record.get("artifact_manifest"))
    artifacts_root = results_dir / run_id / run_id / "artifacts"
    local_root = manifest.get("local_artifact_root")
    if local_root:
        path = artifacts_root / str(local_root)
        return _repo_relative(path, root), path.exists()
    files = _as_dict(manifest.get("files"))
    for value in files.values():
        if isinstance(value, str) and value:
            path = artifacts_root / value
            return _repo_relative(path, root), path.exists()
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str) and nested:
                    path = artifacts_root / nested
                    return _repo_relative(path, root), path.exists()
    return "", False


def _evidence_text(record: dict[str, Any] | None, log_text: str = "") -> str:
    if record is None:
        return log_text.lower()
    metadata = _as_dict(record.get("metadata"))
    error = _as_dict(record.get("error"))
    parts = [
        str(record.get("score_status") or ""),
        str(record.get("finish_reason") or ""),
        str(record.get("rationale") or ""),
        str(error.get("error_type") or ""),
        str(error.get("error") or ""),
        _safe_json_text(metadata),
        log_text,
    ]
    return "\n".join(parts).lower()


def classify_failure(record: dict[str, Any] | None, *, missing_metadata: bool = False, log_text: str = "") -> str:
    if record is None:
        evidence = _evidence_text(None, log_text)
        if "pull access denied" in evidence or "manifest unknown" in evidence or "error pulling image" in evidence:
            return "image_pull"
        if "no such image" in evidence or "image not known" in evidence or "image not found" in evidence:
            return "image_pull"
        if "task.toml" in evidence or "instruction.md" in evidence or "terminal_bench2_dir" in evidence:
            return "task_setup"
        if "podman" in evidence or "container" in evidence or "runtime" in evidence:
            return "podman_runtime"
        if "timeout" in evidence or "timed out" in evidence:
            return "agent_timeout"
        return "no_task_json"

    if missing_metadata:
        return "metadata_defect"

    metadata = _as_dict(record.get("metadata"))
    error = _as_dict(record.get("error"))
    score_status = str(record.get("score_status") or "").strip().lower()
    finish_reason = str(record.get("finish_reason") or "").strip().lower()
    error_type = str(error.get("error_type") or "").strip().lower()
    verifier_status = str(metadata.get("verifier_status") or "").strip().lower()
    reward_observed = bool(metadata.get("verifier_reward_observed"))
    if "verifier_status" in metadata and verifier_status == "skipped_duplicate":
        verifier_problem = not reward_observed
    elif "verifier_status" in metadata:
        verifier_problem = verifier_status not in {"", "ok"}
    else:
        verifier_problem = False
    evidence = _evidence_text(record, log_text)

    if "pull access denied" in evidence or "manifest unknown" in evidence or "error pulling image" in evidence:
        return "image_pull"
    if "no such image" in evidence or "image not known" in evidence or "image not found" in evidence:
        return "image_pull"
    if "task.toml" in evidence or "instruction.md" in evidence or "missing tests" in evidence:
        return "task_setup"
    if (
        "timeout" in finish_reason
        or "timeout" in error_type
        or "timeout" in score_status
        or "timed out" in evidence
        or "exit_code=124" in evidence
    ):
        return "agent_timeout"
    if (
        score_status == "agent_empty_output"
        or error_type in {"empty_response", "empty_output", "agent_empty_output"}
        or "empty assistant" in evidence
        or "no assistant output" in evidence
        or "no assistant content" in evidence
    ):
        return "agent_empty_output"
    if (
        score_status in {"verifier_error", "scoring_error"}
        or "verifier" in error_type
        or "reward" in error_type
        or verifier_problem
    ):
        return "verifier_failure"
    if not error and score_status in {"", "valid_scored"}:
        return "clean"
    if (
        "podman" in evidence
        and (
            "connection refused" in evidence
            or "container" in error_type
            or "runtime" in error_type
            or "podmanerror" in evidence
        )
    ):
        return "podman_runtime"
    if error or score_status not in {"", "valid_scored"}:
        return "other"
    return "clean"


def _read_log_tail(log_path: Path) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-12000:]


def row_from_record(
    *,
    record: dict[str, Any],
    task_json_path: Path,
    expected_task_id: str,
    cell: dict[str, Any],
    run_id: str,
    log_path: Path,
    results_dir: Path,
    root: Path,
) -> dict[str, Any]:
    metadata = _as_dict(record.get("metadata"))
    container_engine = metadata.get("container_engine")
    missing_metadata = container_engine != "podman"
    log_exists = log_path.exists()
    artifact_path, artifact_exists = _artifact_path(record, results_dir, run_id, root)
    taxonomy = classify_failure(record, missing_metadata=missing_metadata, log_text=_read_log_tail(log_path))
    score_present = "score" in record
    audit_failures: list[str] = []

    if missing_metadata:
        audit_failures.append("missing_metadata_container_engine")
    if not score_present:
        audit_failures.append("missing_score_field")
    if not (record.get("agent_name") or cell["agent"]):
        audit_failures.append("missing_agent")
    if not (record.get("benchmark_name") or cell["benchmark"]):
        audit_failures.append("missing_benchmark")
    if not (record.get("run_id") or run_id):
        audit_failures.append("missing_run_id")
    if not (record.get("task_id") or expected_task_id):
        audit_failures.append("missing_task_id")
    if not log_exists:
        audit_failures.append("missing_log")
    if not artifact_path or not artifact_exists:
        audit_failures.append("missing_artifact_path")

    return {
        "config": _repo_relative(cell["config_path"], root),
        "run_id": record.get("run_id") or run_id,
        "task_id": record.get("task_id") or expected_task_id,
        "agent": record.get("agent_name") or cell["agent"],
        "benchmark": record.get("benchmark_name") or cell["benchmark"],
        "result_status": record.get("score_status") or record.get("status") or "",
        "score": record.get("score") if score_present else None,
        "reward": record.get("reward", metadata.get("reward")),
        "verifier_status": metadata.get("verifier_status", ""),
        "runtime": record.get("wall_time_sec"),
        "artifact_path": artifact_path,
        "task_json_path": _repo_relative(task_json_path, root),
        "log_path": _repo_relative(log_path, root),
        "container_engine": container_engine,
        "failure_taxonomy": taxonomy,
        "audit_status": "fail" if audit_failures else "pass",
        "audit_failures": audit_failures,
    }


def missing_row(
    *,
    expected_task_id: str,
    cell: dict[str, Any],
    run_id: str,
    log_path: Path,
    root: Path,
) -> dict[str, Any]:
    taxonomy = classify_failure(None, log_text=_read_log_tail(log_path))
    return {
        "config": _repo_relative(cell["config_path"], root),
        "run_id": run_id,
        "task_id": expected_task_id,
        "agent": cell["agent"],
        "benchmark": cell["benchmark"],
        "result_status": "no_task_json",
        "score": None,
        "reward": None,
        "verifier_status": "",
        "runtime": None,
        "artifact_path": "",
        "task_json_path": "",
        "log_path": _repo_relative(log_path, root),
        "container_engine": None,
        "failure_taxonomy": taxonomy,
        "audit_status": "fail",
        "audit_failures": ["missing_task_json"],
    }


def load_preflight(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"ok": False, "status": "missing", "failures": ["missing_preflight_status"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"ok": False, "status": "missing", "failures": ["missing_preflight_status"]}
    if isinstance(data, dict):
        data.setdefault("status", "present")
        return data
    return {"ok": False, "status": "invalid", "failures": ["invalid_preflight_status"]}


def audit(
    *,
    run_prefix: str,
    config_dir: Path,
    results_dir: Path,
    logs_dir: Path,
    root: Path,
    preflight_status_file: Path | None,
) -> dict[str, Any]:
    preflight = load_preflight(preflight_status_file)
    rows: list[dict[str, Any]] = []
    cells = discover_cells(config_dir)

    for cell in cells:
        run_id = f"{run_prefix}_{cell['run_suffix']}"
        log_path = logs_dir / f"{run_id}.log"
        task_files = find_task_files(results_dir, run_id)
        for expected_task_id in cell["selected_task_ids"]:
            task_file = task_files.get(expected_task_id)
            if not task_file:
                rows.append(
                    missing_row(
                        expected_task_id=expected_task_id,
                        cell=cell,
                        run_id=run_id,
                        log_path=log_path,
                        root=root,
                    )
                )
                continue
            record = _read_first_record(task_file)
            if record is None:
                rows.append(
                    missing_row(
                        expected_task_id=expected_task_id,
                        cell=cell,
                        run_id=run_id,
                        log_path=log_path,
                        root=root,
                    )
                )
                continue
            rows.append(
                row_from_record(
                    record=record,
                    task_json_path=task_file,
                    expected_task_id=expected_task_id,
                    cell=cell,
                    run_id=run_id,
                    log_path=log_path,
                    results_dir=results_dir,
                    root=root,
                )
            )

    taxonomy_summary = Counter(row["failure_taxonomy"] for row in rows)
    audit_failures = [row for row in rows if row["audit_status"] != "pass"]
    preflight_ok = preflight.get("ok") is True
    preflight_failures = [] if preflight_ok else ["preflight_failed"]
    return {
        "run_prefix": run_prefix,
        "preflight": {
            "ok": preflight_ok,
            "status": preflight.get("status", "present"),
            "failures": preflight.get("failures", []),
            "failure_taxonomy": preflight.get("failure_taxonomy", ""),
            "selected_task_ids": preflight.get("selected_task_ids", []),
            "runtime_image_present": preflight.get("runtime_image_present"),
            "task_images_missing_locally": preflight.get("task_images_missing_locally", []),
        },
        "expected_cells": len(cells),
        "expected_tasks": sum(len(cell["selected_task_ids"]) for cell in cells),
        "required_taxonomy": REQUIRED_TAXONOMY,
        "rows": rows,
        "taxonomy_summary": dict(sorted(taxonomy_summary.items())),
        "audit_passed": not audit_failures and preflight_ok,
        "audit_failure_count": len(audit_failures) + len(preflight_failures),
        "audit_failures": preflight_failures,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Podman TerminalBench2 Readiness Audit: `{result['run_prefix']}`",
        "",
        f"Audit passed: `{str(result['audit_passed']).lower()}`",
        f"Preflight passed: `{str(result['preflight']['ok']).lower()}`",
        f"Expected cells: `{result['expected_cells']}`",
        f"Expected tasks: `{result['expected_tasks']}`",
        f"Audit failures: `{result['audit_failure_count']}`",
        "",
        "## Rows",
        "",
        "| Config | Task ID | Agent | Benchmark | Status | Score | Reward | Runtime | Verifier | Taxonomy | Audit | Artifact | Log |",
        "|---|---|---|---|---|---:|---|---:|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        score = "-" if row["score"] is None else row["score"]
        reward = "-" if row["reward"] is None else row["reward"]
        runtime = "-" if row["runtime"] is None else row["runtime"]
        verifier = row["verifier_status"] or "-"
        lines.append(
            "| {config} | {task_id} | {agent} | {benchmark} | {status} | {score} | {reward} | {runtime} | {verifier} | {taxonomy} | {audit} | {artifact} | {log} |".format(
                config=row["config"],
                task_id=row["task_id"],
                agent=row["agent"],
                benchmark=row["benchmark"],
                status=row["result_status"],
                score=score,
                reward=reward,
                runtime=runtime,
                verifier=verifier,
                taxonomy=row["failure_taxonomy"],
                audit=row["audit_status"],
                artifact=row["artifact_path"] or "-",
                log=row["log_path"] or "-",
            )
        )

    lines.extend(["", "## Failure Taxonomy", ""])
    lines.append("| Category | Rows | Required |")
    lines.append("|---|---:|---|")
    summary = result["taxonomy_summary"]
    for category in sorted(set(result["required_taxonomy"]) | set(summary)):
        lines.append(f"| `{category}` | {summary.get(category, 0)} | {'yes' if category in result['required_taxonomy'] else 'no'} |")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--logs-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--preflight-status-file", type=Path)
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
        preflight_status_file=args.preflight_status_file,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"audit-{args.run_prefix}.json"
    md_path = args.output_dir / f"audit-{args.run_prefix}.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0 if result["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
