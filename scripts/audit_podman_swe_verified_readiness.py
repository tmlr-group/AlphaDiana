#!/usr/bin/env python3
"""Audit Phase 9 SWE-bench Verified Podman readiness artifacts."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from alphadiana.utils.lifecycle_events import LIFECYCLE_STAGES


REQUIRED_TAXONOMY = [
    "clean",
    "podman_socket",
    "docker_api_version",
    "podman_short_name_image",
    "image_pull_or_proxy",
    "hf_dataset_access",
    "swebench_env_build",
    "swebench_instance_build",
    "agent_runtime",
    "agent_empty_output",
    "provider_failure",
    "provider_empty_response",
    "reasoning_only_length",
    "external_provider_saturated",
    "scorer_failure",
    "no_task_json",
    "metadata_missing",
    "artifact_missing",
    "timeout",
    "other",
]

INFRASTRUCTURE_GATING_CATEGORIES = {
    "podman_socket",
    "docker_api_version",
    "podman_short_name_image",
    "image_pull_or_proxy",
    "hf_dataset_access",
    "swebench_env_build",
    "swebench_instance_build",
    "agent_runtime",
    "agent_empty_output",
    "provider_failure",
    "provider_empty_response",
    "external_provider_saturated",
    "scorer_failure",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
        data = _load_json(path)
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    if isinstance(data, dict):
        return data
    return None


def _taskset_path(config: dict[str, Any], root: Path) -> Path:
    metadata = _as_dict(config.get("metadata"))
    benchmark_config = _as_dict(_as_dict(config.get("benchmark")).get("config"))
    raw = str(metadata.get("taskset_path") or benchmark_config.get("taskset_path") or "").strip()
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _task_ids_from_taskset(path: Path) -> list[str]:
    data = _load_json(path)
    if not isinstance(data, dict):
        return []
    selected = data.get("selected_task_ids")
    if isinstance(selected, list) and selected:
        return [str(value) for value in selected]
    raw_ids = data.get("task_ids")
    if isinstance(raw_ids, list):
        return [f"swe_{str(value).removeprefix('swe_')}" for value in raw_ids]
    tasks = data.get("tasks")
    if isinstance(tasks, list):
        out = []
        for item in tasks:
            if isinstance(item, dict):
                raw = item.get("task_id") or item.get("instance_id")
            else:
                raw = item
            if raw:
                out.append(f"swe_{str(raw).removeprefix('swe_')}")
        return out
    return []


def _cell_from_config(path: Path, root: Path) -> dict[str, Any]:
    config = _load_yaml(path)
    metadata = _as_dict(config.get("metadata"))
    tier = str(metadata.get("tier") or path.stem.rsplit("_", 1)[-1]).strip()
    run_suffix = str(metadata.get("run_suffix") or path.stem).strip()
    taskset = _taskset_path(config, root)
    return {
        "config_path": path,
        "config": config,
        "tier": tier,
        "run_suffix": run_suffix,
        "agent": str(_as_dict(config.get("agent")).get("name") or ""),
        "taskset_path": taskset,
        "selected_task_ids": _task_ids_from_taskset(taskset) if taskset.exists() else [],
    }


def discover_cells(config_dir: Path, root: Path, tiers: set[str] | None = None) -> list[dict[str, Any]]:
    cells = [_cell_from_config(path, root) for path in sorted(config_dir.glob("*.yaml"))]
    if tiers:
        cells = [cell for cell in cells if cell["tier"] in tiers]
    return cells


def find_task_files(results_dir: Path, run_id: str) -> dict[str, Path]:
    candidates = [
        results_dir / run_id / run_id / "tasks",
        results_dir / run_id / "tasks",
    ]
    for tasks_dir in candidates:
        if tasks_dir.exists():
            return {path.stem: path for path in sorted(tasks_dir.glob("*.json"))}
    return {}


def find_lifecycle_file(results_dir: Path, run_id: str, task_id: str) -> Path | None:
    candidates = [
        results_dir / run_id / run_id / "lifecycle" / f"{task_id}.jsonl",
        results_dir / run_id / "lifecycle" / f"{task_id}.jsonl",
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def _read_lifecycle_events(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _infer_lifecycle_flags_from_log(log_text: str) -> dict[str, bool]:
    text = log_text or ""
    flags = {stage: False for stage in LIFECYCLE_STAGES}
    if "response headers received" in text or "streaming response established" in text:
        flags["provider_connected"] = True
    completed_match = re.search(
        r"completed stream: output_chars=(\d+) reasoning_chars=(\d+)",
        text,
    )
    if completed_match:
        output_chars = int(completed_match.group(1))
        reasoning_chars = int(completed_match.group(2))
        if output_chars > 0:
            flags["content_seen"] = True
            flags["first_token_seen"] = True
        if reasoning_chars > 0:
            flags["reasoning_seen"] = True
            flags["first_token_seen"] = True
    if "logprob" in text.lower() and "count=" in text.lower():
        flags["logprobs_seen"] = True
    if '"usage"' in text or "completion_tokens" in text:
        flags["usage_seen"] = True
    return flags


def _lifecycle_summary(
    events: list[dict[str, Any]],
    *,
    log_text: str,
) -> dict[str, Any]:
    event_stages_all = [str(event.get("stage") or "") for event in events if event.get("stage")]
    event_stages = list(dict.fromkeys(event_stages_all))
    flags = {stage: False for stage in LIFECYCLE_STAGES}
    for stage in event_stages:
        if stage in flags:
            flags[stage] = True
    inferred = _infer_lifecycle_flags_from_log(log_text)
    inferred_stages: list[str] = []
    for stage, seen in inferred.items():
        if seen and not flags.get(stage):
            flags[stage] = True
            inferred_stages.append(stage)
    flags["audit_seen"] = True
    return {
        "event_count": len(events),
        "stages": event_stages,
        "inferred_stages": inferred_stages,
        "last_stage": event_stages_all[-1] if event_stages_all else "",
        "stage_flags": flags,
    }


def _count_artifact_files(path: Path) -> int:
    if path.is_file():
        return 1
    if not path.is_dir():
        return 0
    return sum(1 for child in path.rglob("*") if child.is_file())


def _manifest_files(record: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(record.get("artifact_manifest")).get("files"))


def _artifact_root(record: dict[str, Any], results_dir: Path, run_id: str) -> Path | None:
    manifest = _as_dict(record.get("artifact_manifest"))
    local_root = str(manifest.get("local_artifact_root") or "").strip()
    if not local_root:
        return None
    return results_dir / run_id / run_id / "artifacts" / local_root


def _manifest_file_path(record: dict[str, Any], results_dir: Path, run_id: str, key: str) -> Path | None:
    value = _manifest_files(record).get(key)
    if not isinstance(value, str) or not value:
        return None
    return results_dir / run_id / run_id / "artifacts" / value


def _workspace_artifact_path(
    record: dict[str, Any],
    results_dir: Path,
    run_id: str,
    suffix: str,
) -> Path | None:
    workspace_files = _as_dict(_manifest_files(record).get("workspace_files"))
    for local_rel in workspace_files.values():
        if isinstance(local_rel, str) and local_rel.endswith(suffix):
            return results_dir / run_id / run_id / "artifacts" / local_rel
    return None


def _podman_metadata(record: dict[str, Any]) -> bool:
    metadata = _as_dict(record.get("metadata"))
    sandbox_metadata = _as_dict(record.get("sandbox_metadata"))
    score_metadata = _as_dict(record.get("score_metadata"))
    swe_eval = _as_dict(metadata.get("swe_bench_eval"))
    values = [
        metadata.get("container_engine"),
        sandbox_metadata.get("container_engine"),
        score_metadata.get("container_engine"),
        swe_eval.get("container_engine"),
    ]
    return any(str(value).strip().lower() == "podman" for value in values)


def _assistant_evidence_present(record: dict[str, Any]) -> bool:
    if str(record.get("predicted") or "").strip():
        return True
    if str(record.get("raw_output") or "").strip():
        return True
    if record.get("reasoning_trajectory"):
        return True
    response_json = _as_dict(record.get("response_json"))
    if str(response_json.get("output_text") or "").strip():
        return True
    for step in record.get("trajectory") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("role") or "").lower() != "assistant":
            continue
        if str(step.get("content") or "").strip():
            return True
        if str(step.get("thinking") or "").strip():
            return True
    return False


def _observed_abnormal_trajectory(record: dict[str, Any]) -> bool:
    metadata = _as_dict(record.get("metadata"))
    if str(metadata.get("trajectory_status") or "").strip().lower() != "abnormal":
        return False
    return bool(
        metadata.get("failure_reason")
        or metadata.get("trajectory_failure_reason")
        or metadata.get("zeroclaw_empty_assistant_output")
        or metadata.get("opencode_empty_assistant_output")
    )


def _evidence_text(record: dict[str, Any] | None, log_text: str = "") -> str:
    if record is None:
        return log_text.lower()
    metadata = _as_dict(record.get("metadata"))
    sandbox_metadata = _as_dict(record.get("sandbox_metadata"))
    error = _as_dict(record.get("error"))
    score_metadata = _as_dict(record.get("score_metadata"))
    parts = [
        str(record.get("score_status") or ""),
        str(record.get("finish_reason") or ""),
        str(record.get("rationale") or ""),
        str(error.get("error_type") or ""),
        str(error.get("error") or ""),
        str(record.get("raw_output") or ""),
        _safe_json_text(metadata),
        _safe_json_text(sandbox_metadata),
        _safe_json_text(score_metadata),
        log_text,
    ]
    return "\n".join(parts).lower()


def classify_failure(record: dict[str, Any] | None, *, log_text: str = "", missing_metadata: bool = False) -> str:
    if record is None:
        evidence = _evidence_text(None, log_text)
        if "running:" in evidence and "waiting:" in evidence and "gpu kv cache usage" in evidence:
            return "external_provider_saturated"
        if "provider_empty_response" in evidence or "empty sse body" in evidence:
            return "provider_empty_response"
        if "api version" in evidence or "client version" in evidence:
            return "docker_api_version"
        if "buildimageerror" in evidence or "environment image" in evidence:
            if "instance image" in evidence or "build_instance" in evidence or "sweb.eval" in evidence:
                return "swebench_instance_build"
            return "swebench_env_build"
        if (
            "short-name" in evidence
            or "unqualified" in evidence
            or re.search(r"(^|[\s:])sweb\.env", evidence)
        ):
            return "podman_short_name_image"
        if "podman" in evidence and ("socket" in evidence or "connection refused" in evidence):
            return "podman_socket"
        if "huggingface" in evidence or "hf_endpoint" in evidence or "dataset" in evidence:
            return "hf_dataset_access"
        if "timeout" in evidence or "timed out" in evidence:
            return "timeout"
        return "no_task_json"

    if missing_metadata:
        return "metadata_missing"

    metadata = _as_dict(record.get("metadata"))
    score_status = str(record.get("score_status") or "").strip().lower()
    finish_reason = str(record.get("finish_reason") or "").strip().lower()
    error = _as_dict(record.get("error"))
    error_type = str(error.get("error_type") or "").strip().lower()
    evidence = _evidence_text(
        record,
        log_text if error or score_status != "valid_scored" else "",
    )

    if (
        metadata.get("provider_empty_response") is True
        or "provider_empty_response" in evidence
        or "empty_sse_body" in evidence
        or "empty sse body" in evidence
        or int(metadata.get("provider_proxy_empty_content_reasoning_and_tool_response_count") or 0) > 0
        or int(metadata.get("provider_proxy_empty_content_and_reasoning_response_count") or 0) > 0
    ):
        return "provider_empty_response"
    if ("length" in finish_reason or "max_tokens" in finish_reason) and record.get("reasoning_trajectory"):
        return "reasoning_only_length"
    if (
        score_status == "valid_scored"
        and not error
        and _assistant_evidence_present(record)
        and not _observed_abnormal_trajectory(record)
    ):
        return "clean"
    if "api version" in evidence or "client version" in evidence:
        return "docker_api_version"
    if "buildimageerror" in evidence:
        if "instance image" in evidence or "build_instance" in evidence or "sweb.eval" in evidence:
            return "swebench_instance_build"
        return "swebench_env_build"
    if (
        "short-name" in evidence
        or "unqualified" in evidence
        or re.search(r"(^|[\s:])sweb\.env", evidence)
    ):
        return "podman_short_name_image"
    if "contextoverflow" in evidence or "vllmvalidationerror" in evidence:
        return "provider_failure"
    if "running:" in evidence and "waiting:" in evidence and "gpu kv cache usage" in evidence:
        return "external_provider_saturated"
    if "provider" in error_type or "provider" in evidence or "api error" in evidence:
        return "provider_failure"
    if "podman" in evidence and ("socket" in evidence or "connection refused" in evidence):
        return "podman_socket"
    if "pull access denied" in evidence or "manifest unknown" in evidence or "proxyconnect" in evidence:
        return "image_pull_or_proxy"
    if "report" in evidence and "swe-bench evaluation did not produce" in evidence:
        return "scorer_failure"
    if "timeout" in finish_reason or "timeout" in error_type or "timed out" in evidence:
        return "timeout"
    if not _assistant_evidence_present(record):
        return "agent_empty_output"
    if "runtime" in error_type or "agent" in error_type or score_status == "runtime_error":
        return "agent_runtime"
    if score_status == "valid_scored":
        return "clean"
    if error:
        return "other"
    return "clean"


def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def audit(
    *,
    run_prefix: str,
    config_dir: Path,
    results_dir: Path,
    logs_dir: Path,
    root: Path,
    output_dir: Path,
    preflight_status_file: Path | None,
    tiers: set[str] | None = None,
) -> dict[str, Any]:
    cells = discover_cells(config_dir, root, tiers)
    rows: list[dict[str, Any]] = []
    taxonomy = Counter()
    audit_failures: list[str] = []

    preflight: dict[str, Any] = {}
    if preflight_status_file and preflight_status_file.exists():
        loaded = _load_json(preflight_status_file)
        preflight = loaded if isinstance(loaded, dict) else {}
    elif preflight_status_file:
        preflight = {"ok": False, "failures": ["missing_preflight_status"]}

    for cell in cells:
        run_id = f"{run_prefix}_{cell['run_suffix']}"
        log_path = logs_dir / f"{run_id}.log"
        log_exists = log_path.exists() and log_path.is_file()
        log_text = _read_text(log_path)
        task_files = find_task_files(results_dir, run_id)
        for task_id in cell["selected_task_ids"]:
            task_path = task_files.get(task_id)
            record = _read_first_record(task_path) if task_path else None
            lifecycle_path = find_lifecycle_file(results_dir, run_id, task_id)
            lifecycle_events = _read_lifecycle_events(lifecycle_path)
            lifecycle = _lifecycle_summary(lifecycle_events, log_text=log_text)
            failures: list[str] = []
            result_status = "loaded" if record else "no_task_json"
            if not task_path or record is None:
                failures.append("no_task_json")
            if not log_exists:
                failures.append("missing_raw_log")

            artifact_path = ""
            artifact_exists = False
            artifact_file_count = 0
            sandbox_metadata_path = ""
            sandbox_metadata_exists = False
            swe_report_exists = False
            swe_run_log_exists = False
            swe_test_output_exists = False
            podman_metadata = False
            assistant_evidence = False
            observed_abnormal = False
            missing_metadata = False
            log_failure_category = ""
            if record is not None:
                podman_metadata = _podman_metadata(record)
                missing_metadata = not podman_metadata
                if not podman_metadata:
                    failures.append("missing_podman_metadata")
                root_path = _artifact_root(record, results_dir, run_id)
                if root_path is not None:
                    artifact_path = _repo_relative(root_path, root)
                    artifact_file_count = _count_artifact_files(root_path)
                    artifact_exists = root_path.exists() and artifact_file_count > 0
                if not artifact_exists:
                    failures.append("missing_artifact")
                sandbox_path = _manifest_file_path(record, results_dir, run_id, "sandbox_metadata")
                if sandbox_path is not None:
                    sandbox_metadata_path = _repo_relative(sandbox_path, root)
                    sandbox_metadata_exists = sandbox_path.exists() and sandbox_path.is_file()
                if not sandbox_metadata_exists:
                    failures.append("missing_sandbox_metadata_artifact")
                swe_report = _workspace_artifact_path(record, results_dir, run_id, "/report.json")
                swe_run_log = _workspace_artifact_path(record, results_dir, run_id, "/run_instance.log")
                swe_test_output = _workspace_artifact_path(record, results_dir, run_id, "/test_output.txt")
                swe_report_exists = bool(swe_report and swe_report.exists())
                swe_run_log_exists = bool(swe_run_log and swe_run_log.exists())
                swe_test_output_exists = bool(swe_test_output and swe_test_output.exists())
                assistant_evidence = _assistant_evidence_present(record)
                observed_abnormal = _observed_abnormal_trajectory(record)
                failure_category = classify_failure(
                    record,
                    log_text=log_text,
                    missing_metadata=missing_metadata,
                )
                if (
                    failure_category == "agent_empty_output"
                    and str(record.get("score_status") or "").lower() == "valid_scored"
                    and not observed_abnormal
                ):
                    failures.append("agent_empty_output_masked_as_valid_scored")
                if failure_category == "provider_empty_response":
                    failures.append("provider_empty_response_observed")
                if failure_category == "podman_short_name_image":
                    failures.append("unqualified_podman_short_name_image")
                if failure_category in INFRASTRUCTURE_GATING_CATEGORIES:
                    failures.append(f"infrastructure_failure:{failure_category}")
            else:
                failure_category = "no_task_json"
                log_failure_category = classify_failure(None, log_text=log_text)

            if task_path and record is not None:
                lifecycle["stage_flags"]["task_json_written"] = True
            if log_exists:
                lifecycle["stage_flags"].setdefault("raw_log_seen", True)
            taxonomy[failure_category] += 1
            row = {
                "tier": cell["tier"],
                "agent": cell["agent"],
                "run_id": run_id,
                "config": _repo_relative(cell["config_path"], root),
                "taskset": _repo_relative(cell["taskset_path"], root),
                "expected_task_id": task_id,
                "task_id": task_id,
                "result_status": result_status,
                "task_json_path": _repo_relative(task_path, root) if task_path else "",
                "task_json_exists": bool(task_path and task_path.exists()),
                "raw_log_path": _repo_relative(log_path, root),
                "raw_log_exists": log_exists,
                "lifecycle_path": _repo_relative(lifecycle_path, root) if lifecycle_path else "",
                "lifecycle_exists": bool(lifecycle_path and lifecycle_path.exists()),
                "lifecycle_event_count": lifecycle["event_count"],
                "lifecycle_stages": lifecycle["stages"],
                "lifecycle_inferred_stages": lifecycle["inferred_stages"],
                "last_lifecycle_stage": lifecycle["last_stage"],
                "lifecycle_stage_flags": lifecycle["stage_flags"],
                "score_status": str(record.get("score_status") or "") if record else "",
                "score": record.get("score") if record else None,
                "correct": record.get("correct") if record else None,
                "resolved": (
                    _as_dict(record.get("score_metadata")).get(
                        "resolved",
                        _as_dict(_as_dict(record.get("metadata")).get("swe_bench_eval")).get("resolved"),
                    )
                    if record
                    else None
                ),
                "podman_metadata": podman_metadata,
                "artifact_path": artifact_path,
                "artifact_exists": artifact_exists,
                "artifact_file_count": artifact_file_count,
                "sandbox_metadata_path": sandbox_metadata_path,
                "sandbox_metadata_exists": sandbox_metadata_exists,
                "swe_report_exists": swe_report_exists,
                "swe_run_log_exists": swe_run_log_exists,
                "swe_test_output_exists": swe_test_output_exists,
                "assistant_evidence_present": assistant_evidence,
                "observed_abnormal_trajectory": observed_abnormal,
                "failure_category": failure_category,
                "log_failure_category": log_failure_category,
                "gating_failure": bool(failures),
                "audit_status": "fail" if failures else "pass",
                "audit_failures": failures,
            }
            rows.append(row)
            for failure in failures:
                audit_failures.append(f"{run_id}:{task_id}:{failure}")

    if preflight and not preflight.get("ok", False):
        audit_failures.insert(0, "preflight_failed")

    result = {
        "audit_passed": not audit_failures,
        "run_prefix": run_prefix,
        "tiers": sorted(tiers) if tiers else sorted({cell["tier"] for cell in cells}),
        "expected_cells": len(cells),
        "expected_tasks": sum(len(cell["selected_task_ids"]) for cell in cells),
        "observed_rows": len(rows),
        "audit_failure_count": len(audit_failures),
        "audit_failures": audit_failures,
        "taxonomy_summary": dict(sorted(taxonomy.items())),
        "required_taxonomy": REQUIRED_TAXONOMY,
        "preflight": {
            "ok": preflight.get("ok"),
            "failures": preflight.get("failures", []),
        } if preflight else {},
        "rows": rows,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    tiers_tag = "-".join(result["tiers"]) if result["tiers"] else "all"
    json_path = output_dir / f"audit-{run_prefix}-{tiers_tag}.json"
    md_path = output_dir / f"audit-{run_prefix}-{tiers_tag}.md"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    return result


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# SWE-bench Verified Podman Readiness Audit: {result['run_prefix']}",
        "",
        f"- audit_passed: {result['audit_passed']}",
        f"- tiers: {', '.join(result['tiers'])}",
        f"- expected_cells: {result['expected_cells']}",
        f"- expected_tasks: {result['expected_tasks']}",
        f"- audit_failure_count: {result['audit_failure_count']}",
        "",
        "## Taxonomy",
        "",
    ]
    for key, count in result["taxonomy_summary"].items():
        lines.append(f"- {key}: {count}")
    lines.extend([
        "",
        "## Rows",
        "",
        "| tier | agent | expected_task_id | status | category | last_stage | gating | failures |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for row in result["rows"]:
        failures = ", ".join(row["audit_failures"]) if row["audit_failures"] else "-"
        lines.append(
            f"| {row['tier']} | {row['agent']} | {row['expected_task_id']} | "
            f"{row['audit_status']} | {row['failure_category']} | "
            f"{row['last_lifecycle_stage'] or '-'} | "
            f"{row['gating_failure']} | {failures} |"
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--logs-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--preflight-status-file", type=Path)
    parser.add_argument(
        "--tiers",
        default="",
        help="Comma-separated tiers to audit. Default audits every config in the directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tiers = {part.strip() for part in args.tiers.split(",") if part.strip()} or None
    result = audit(
        run_prefix=args.run_prefix,
        config_dir=args.config_dir,
        results_dir=args.results_dir,
        logs_dir=args.logs_dir,
        root=args.root,
        output_dir=args.output_dir,
        preflight_status_file=args.preflight_status_file,
        tiers=tiers,
    )
    print(json.dumps({
        "audit_passed": result["audit_passed"],
        "audit_failure_count": result["audit_failure_count"],
        "expected_tasks": result["expected_tasks"],
    }, sort_keys=True))
    return 0 if result["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
