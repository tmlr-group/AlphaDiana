#!/usr/bin/env python3
"""Audit Phase 6 MMMU-Pro Podman multimodal-readiness pilot artifacts."""

from __future__ import annotations

import argparse
import base64
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import yaml


EXPECTED_AGENTS = ("openclaw", "zeroclaw", "opencode")
BENCHMARK_KEY = "mmmu_pro"
REQUIRED_TASK_COUNT = 9
MIN_THINKING_MAX_TOKENS = 8192


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _repo_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return str(path)


def _safe_json_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)


def _validate_base64_payload(payload: str) -> bool:
    candidate = payload.strip()
    if not candidate:
        return False
    try:
        base64.b64decode(candidate, validate=True)
    except Exception:
        return False
    return True


def _image_url_payload_status(value: Any) -> str:
    if not isinstance(value, str):
        return "missing"
    if value.startswith("data:image/"):
        _, _, b64 = value.partition(";base64,")
        return "present" if b64 and _validate_base64_payload(b64) else "corrupt"
    if value.startswith(("http://", "https://")):
        return "present"
    if value:
        return "present"
    return "missing"


def _image_url_status(value: Any) -> str:
    if isinstance(value, dict):
        return _image_url_payload_status(value.get("url"))
    return _image_url_payload_status(value)


def _contains_image_url(value: Any) -> tuple[str, str]:
    """Return (status, kind) for OpenAI-compatible image_url content."""
    status = "missing"
    for item in _walk_json(value):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "image_url" or "image_url" in item:
            current = _image_url_status(item.get("image_url"))
            if current == "corrupt":
                return "corrupt", "image_url"
            if current == "present":
                status = "present"
    return status, "image_url" if status == "present" else ""


def expected_task_ids(config: dict[str, Any]) -> list[str]:
    metadata = _as_dict(config.get("metadata"))
    selected = metadata.get("selected_task_ids")
    if isinstance(selected, list):
        return [str(item) for item in selected if str(item).strip()]

    benchmark_config = _as_dict(_as_dict(config.get("benchmark")).get("config"))
    dataset_indices = benchmark_config.get("dataset_indices")
    if isinstance(dataset_indices, list):
        return [f"expected_task_{idx}" for idx in range(len(dataset_indices))]
    if benchmark_config.get("dataset_index") is not None:
        return ["expected_task_0"]
    if benchmark_config.get("max_tasks") is not None:
        return [f"expected_task_{idx}" for idx in range(int(benchmark_config["max_tasks"]))]
    return []


def _cell_from_config_path(path: Path) -> tuple[str, str]:
    stem = path.stem
    if stem.endswith("_pilot"):
        stem = stem[:-len("_pilot")]
    if stem.endswith(f"_{BENCHMARK_KEY}"):
        return stem[: -len(f"_{BENCHMARK_KEY}")], BENCHMARK_KEY
    agent, benchmark = stem.rsplit("_", 1)
    return agent, benchmark


def discover_cells(config_dir: Path) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for path in sorted(config_dir.glob(f"*_{BENCHMARK_KEY}_pilot.yaml")):
        data = _load_yaml(path)
        agent_key, benchmark_key = _cell_from_config_path(path)
        agent_name = str(_as_dict(data.get("agent")).get("name") or agent_key)
        benchmark_name = str(_as_dict(data.get("benchmark")).get("name") or benchmark_key)
        cells.append({
            "config_path": path,
            "config": data,
            "agent_key": agent_key,
            "benchmark_key": benchmark_key,
            "agent": agent_name,
            "benchmark": benchmark_name,
            "expected_task_ids": expected_task_ids(data),
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
        data = _load_json(path)
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    if isinstance(data, dict):
        return data
    return None


def _artifact_bases(results_dir: Path, run_id: str) -> list[Path]:
    return [
        results_dir / run_id / run_id / "artifacts",
        results_dir / run_id / "artifacts",
    ]


def _iter_file_refs(value: Any) -> Iterable[str]:
    if isinstance(value, str) and value:
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_file_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_file_refs(item)


def artifact_paths(record: dict[str, Any], results_dir: Path, run_id: str) -> list[Path]:
    manifest = _as_dict(record.get("artifact_manifest"))
    paths: list[Path] = []
    for base in _artifact_bases(results_dir, run_id):
        local_root = manifest.get("local_artifact_root")
        if local_root:
            paths.append(base / str(local_root))
        files = _as_dict(manifest.get("files"))
        for rel in _iter_file_refs(files):
            paths.append(base / rel)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _artifact_path(record: dict[str, Any], results_dir: Path, run_id: str, root: Path) -> str:
    for path in artifact_paths(record, results_dir, run_id):
        if path.exists():
            return _repo_relative(path, root)
    return ""


def _iter_artifact_files(record: dict[str, Any], results_dir: Path, run_id: str) -> Iterable[Path]:
    seen: set[str] = set()
    for path in artifact_paths(record, results_dir, run_id):
        candidates: Iterable[Path]
        if path.is_dir():
            candidates = path.rglob("*")
        else:
            candidates = [path]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            yield candidate


def _read_artifact_text(path: Path, *, max_bytes: int = 5_000_000) -> str:
    try:
        if path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _artifact_file_contains_image_url(path: Path) -> tuple[str, str]:
    text = _read_artifact_text(path)
    if not text:
        return "missing", ""
    parsed = _parse_json_text(text)
    status, kind = _contains_image_url(parsed if parsed is not None else text)
    if status != "missing":
        return status, kind
    if "image_url" in text:
        return "present", "image_url_text"
    return "missing", ""


def _artifact_file_zeroclaw_vision_proxy_status(path: Path) -> tuple[str, str]:
    text = _read_artifact_text(path)
    if "vision-proxy" in text:
        if "[vision-proxy] injected" in text:
            return "present", "zeroclaw_vision_proxy_injection"
        if "[vision-proxy] shutdown:" in text and "0 injections" in text:
            return "lost", "zeroclaw_vision_proxy_no_provider_request"
    if path.name != "status.json":
        return "missing", ""
    parsed = _parse_json_text(text)
    if not isinstance(parsed, dict):
        return "missing", ""
    image_count = int(parsed.get("vision_proxy_image_count") or 0)
    request_count = int(parsed.get("vision_proxy_request_count") or 0)
    injection_count = int(parsed.get("vision_proxy_injection_count") or 0)
    if image_count <= 0:
        return "missing", ""
    if request_count > 0 and injection_count > 0:
        return "present", "zeroclaw_vision_proxy_injection"
    return "lost", "zeroclaw_vision_proxy_no_provider_request"


def _artifact_base64_status(path: Path) -> str:
    if path.suffix != ".base64":
        return "missing"
    text = _read_artifact_text(path, max_bytes=50_000_000)
    if not text:
        return "missing"
    return "present" if _validate_base64_payload(text) else "corrupt"


def _artifact_manifest_has_attachments(path: Path) -> bool:
    if path.name != "attachment_manifest.json":
        return False
    text = _read_artifact_text(path)
    parsed = _parse_json_text(text)
    attachments = _as_dict(parsed).get("attachments")
    return isinstance(attachments, list) and bool(attachments)


def _manifest_declares_attachment(record: dict[str, Any]) -> bool:
    manifest = _as_dict(record.get("artifact_manifest"))
    files = _as_dict(manifest.get("files"))
    if "attachment_manifest" in files:
        return True
    attachments = manifest.get("attachments")
    return isinstance(attachments, list) and bool(attachments)


def image_proof(
    *,
    record: dict[str, Any],
    agent: str,
    results_dir: Path,
    run_id: str,
    root: Path,
) -> dict[str, Any]:
    request_status, request_kind = _contains_image_url(record.get("request_messages"))
    if request_status == "corrupt":
        return {
            "status": "corrupt",
            "kind": "openai_image_url",
            "path": "task_json:request_messages",
        }
    if request_status == "present" and agent == "openclaw":
        return {
            "status": "present",
            "kind": "openclaw_image_url",
            "path": "task_json:request_messages",
        }

    artifact_manifest_declares_attachment = _manifest_declares_attachment(record)
    artifact_manifest_path = ""
    base64_path = ""
    task_marker_path = ""
    prompt_attachment_path = ""
    artifact_image_url_path = ""
    zeroclaw_vision_proxy_path = ""
    zeroclaw_vision_proxy_lost_path = ""
    zeroclaw_vision_proxy_kind = ""

    for path in _iter_artifact_files(record, results_dir, run_id):
        image_url_status, image_url_kind = _artifact_file_contains_image_url(path)
        if image_url_status == "corrupt":
            return {
                "status": "corrupt",
                "kind": image_url_kind or "image_url",
                "path": _repo_relative(path, root),
            }
        if image_url_status == "present" and not artifact_image_url_path:
            artifact_image_url_path = _repo_relative(path, root)

        vision_proxy_status, vision_proxy_kind = _artifact_file_zeroclaw_vision_proxy_status(path)
        if vision_proxy_status == "present" and not zeroclaw_vision_proxy_path:
            zeroclaw_vision_proxy_path = _repo_relative(path, root)
            zeroclaw_vision_proxy_kind = vision_proxy_kind
        elif vision_proxy_status == "lost" and not zeroclaw_vision_proxy_lost_path:
            zeroclaw_vision_proxy_lost_path = _repo_relative(path, root)
            zeroclaw_vision_proxy_kind = vision_proxy_kind

        b64_status = _artifact_base64_status(path)
        if b64_status == "corrupt":
            return {
                "status": "corrupt",
                "kind": "base64_image_artifact",
                "path": _repo_relative(path, root),
            }
        if b64_status == "present" and not base64_path:
            base64_path = _repo_relative(path, root)

        if _artifact_manifest_has_attachments(path) and not artifact_manifest_path:
            artifact_manifest_path = _repo_relative(path, root)

        text = _read_artifact_text(path)
        if "[IMAGE:" in text and not task_marker_path:
            task_marker_path = _repo_relative(path, root)
        if "--- Attachments ---" in text and "image" in text.lower() and not prompt_attachment_path:
            prompt_attachment_path = _repo_relative(path, root)

    if agent == "openclaw":
        if artifact_image_url_path:
            return {
                "status": "present",
                "kind": "openclaw_image_url",
                "path": artifact_image_url_path,
            }
        if request_status == "present":
            return {
                "status": "present",
                "kind": request_kind or "openai_image_url",
                "path": "task_json:request_messages",
            }
    elif agent == "opencode":
        if artifact_manifest_path:
            return {
                "status": "present",
                "kind": "opencode_attachment_manifest",
                "path": artifact_manifest_path,
            }
        if base64_path:
            return {
                "status": "present",
                "kind": "opencode_base64_artifact",
                "path": base64_path,
            }
        if prompt_attachment_path:
            return {
                "status": "present",
                "kind": "opencode_prompt_attachment_marker",
                "path": prompt_attachment_path,
            }
    elif agent == "zeroclaw":
        if artifact_image_url_path:
            return {
                "status": "present",
                "kind": "zeroclaw_provider_image_url",
                "path": artifact_image_url_path,
            }
        if zeroclaw_vision_proxy_path:
            return {
                "status": "present",
                "kind": zeroclaw_vision_proxy_kind or "zeroclaw_vision_proxy_injection",
                "path": zeroclaw_vision_proxy_path,
            }
        if zeroclaw_vision_proxy_lost_path:
            return {
                "status": "lost",
                "kind": zeroclaw_vision_proxy_kind or "zeroclaw_vision_proxy_no_provider_request",
                "path": zeroclaw_vision_proxy_lost_path,
            }
        if task_marker_path:
            return {
                "status": "lost",
                "kind": "zeroclaw_image_marker_without_provider_proof",
                "path": task_marker_path,
            }
        if artifact_manifest_path or artifact_manifest_declares_attachment:
            return {
                "status": "lost",
                "kind": "zeroclaw_attachment_manifest_without_provider_proof",
                "path": artifact_manifest_path or "task_json:artifact_manifest",
            }

    if artifact_image_url_path:
        return {
            "status": "present",
            "kind": "image_url_artifact",
            "path": artifact_image_url_path,
        }
    if request_status == "present":
        return {
            "status": "present",
            "kind": request_kind or "openai_image_url",
            "path": "task_json:request_messages",
        }
    return {"status": "missing", "kind": "", "path": ""}


def classify_failure(
    record: dict[str, Any] | None,
    *,
    missing_metadata: bool = False,
    text_only_row: bool = False,
    image_proof_status: str = "present",
    missing_artifacts: bool = False,
) -> str:
    if record is None:
        return "no_task_json"
    if missing_metadata:
        return "metadata_defect"
    if text_only_row:
        return "text_only_fallback"
    if image_proof_status == "corrupt":
        return "image_payload_corrupt"
    if image_proof_status == "lost":
        return "image_lost_before_provider_request"
    if image_proof_status != "present":
        return "image_payload_missing"
    if missing_artifacts:
        return "artifact_missing"

    metadata = _as_dict(record.get("metadata"))
    error = _as_dict(record.get("error"))
    score_status = str(record.get("score_status") or "").strip()
    finish_reason = str(record.get("finish_reason") or "").strip().lower()
    failure_reason = str(metadata.get("failure_reason") or "").strip().lower()
    error_type = str(error.get("error_type") or "").strip().lower()
    error_message = str(error.get("error") or error.get("message") or "").strip().lower()
    failure_evidence = "\n".join([
        score_status,
        finish_reason,
        failure_reason,
        error_type,
        error_message,
        _safe_json_text(error),
    ]).lower()
    evidence = "\n".join([
        score_status,
        finish_reason,
        failure_reason,
        error_type,
        error_message,
        str(record.get("rationale") or ""),
        _safe_json_text(metadata),
    ]).lower()

    if not error and score_status in {"", "valid_scored"} and not failure_reason:
        return "clean"
    if (
        "image" in failure_evidence
        or "multimodal" in failure_evidence
        or "vision" in failure_evidence
        or "content block" in failure_evidence
        or "media" in failure_evidence
    ) and (
        "reject" in failure_evidence
        or "unsupported" in failure_evidence
        or "not support" in failure_evidence
        or "does not support" in failure_evidence
        or "invalid" in failure_evidence
        or "bad request" in failure_evidence
        or "400" in failure_evidence
    ):
        return "provider_image_rejected"
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
    if "verifier" in error_type or "evaluator" in evidence:
        return "benchmark_evaluator"
    if "scorer" in error_type or "scoring" in evidence:
        return "scoring_failure"
    if error or score_status not in {"", "valid_scored"}:
        return "other"
    return "clean"


def podman_related(taxonomy: str) -> str:
    if taxonomy in {
        "metadata_defect",
        "podman_runtime",
        "artifact_missing",
        "image_lost_before_provider_request",
    }:
        return "yes"
    if taxonomy in {
        "provider_error",
        "provider_timeout",
        "provider_image_rejected",
        "agent_empty_output",
        "benchmark_evaluator",
        "scoring_failure",
        "clean",
    }:
        return "no"
    return "unclear"


def classify_log_failure(log_path: Path) -> tuple[str, str]:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return "no_task_json", "no_task_json"
    if not text:
        return "no_task_json", "no_task_json"
    if "preflight" in text and ("failed" in text or "not launched" in text):
        return "vlm_preflight_failed", "vlm_preflight_failed"
    if "image" in text and (
        "unsupported" in text
        or "does not support" in text
        or "reject" in text
        or "bad request" in text
    ):
        return "provider_image_rejected", "provider_image_rejected"
    if "podman" in text and ("connection refused" in text or "container" in text):
        return "podman_runtime", "podman_runtime"
    if "datasetnotfounderror" in text or "gated dataset" in text or "failed to load" in text:
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
    unexpected_task: bool = False,
) -> dict[str, Any]:
    metadata = _as_dict(record.get("metadata"))
    sandbox_metadata = _as_dict(record.get("sandbox_metadata"))
    task_metadata = _as_dict(record.get("task_metadata"))
    agent = str(record.get("agent_name") or cell["agent"])
    benchmark = str(record.get("benchmark_name") or cell["benchmark"])
    container_engine = metadata.get("container_engine")
    sandbox_container_engine = sandbox_metadata.get("container_engine")
    missing_metadata = container_engine != "podman"
    text_only_row = task_metadata.get("data_config") != "vision"
    artifact_path = _artifact_path(record, results_dir, run_id, root)
    proof = image_proof(
        record=record,
        agent=agent,
        results_dir=results_dir,
        run_id=run_id,
        root=root,
    )
    score_present = "score" in record
    log_exists = log_path.exists()
    artifact_exists = bool(artifact_path)
    required_record_fields = {
        "run_id": record.get("run_id"),
        "task_id": record.get("task_id"),
        "agent_name": record.get("agent_name"),
        "benchmark_name": record.get("benchmark_name"),
        "score_status": record.get("score_status"),
    }
    missing_required_fields = [
        key for key, value in required_record_fields.items()
        if value is None or str(value).strip() == ""
    ]

    taxonomy = classify_failure(
        record,
        missing_metadata=missing_metadata or bool(missing_required_fields),
        text_only_row=text_only_row,
        image_proof_status=str(proof["status"]),
        missing_artifacts=not artifact_exists or not log_exists,
    )

    audit_failures: list[str] = []
    if unexpected_task:
        audit_failures.append("unexpected_task_id")
    if missing_metadata:
        audit_failures.append("missing_metadata_container_engine")
    for field in missing_required_fields:
        audit_failures.append(f"missing_{field}")
    if not score_present:
        audit_failures.append("missing_score_field")
    if not log_exists:
        audit_failures.append("missing_log")
    if not artifact_exists:
        audit_failures.append("missing_artifact_path")
    if text_only_row:
        audit_failures.append("text_only_mmmu_pro_row")
    if proof["status"] == "missing":
        audit_failures.append("missing_image_payload_proof")
    elif proof["status"] == "corrupt":
        audit_failures.append("corrupted_multimodal_payload")
    elif proof["status"] == "lost":
        audit_failures.append("image_lost_before_provider_request")
    if taxonomy == "provider_image_rejected":
        audit_failures.append("provider_rejected_image_input")

    error = _as_dict(record.get("error"))
    error_type = error.get("error_type") or metadata.get("failure_reason") or record.get("finish_reason") or ""
    return {
        "config": _repo_relative(cell["config_path"], root),
        "run_id": record.get("run_id") or run_id,
        "task_id": record.get("task_id") or task_json_path.stem,
        "agent": agent,
        "benchmark": benchmark,
        "result_status": record.get("score_status") or "",
        "score": record.get("score") if score_present else None,
        "runtime": record.get("wall_time_sec"),
        "error_type": str(error_type or "-"),
        "artifact_path": artifact_path,
        "task_json_path": _repo_relative(task_json_path, root),
        "log_path": _repo_relative(log_path, root),
        "container_engine": container_engine,
        "sandbox_container_engine": sandbox_container_engine,
        "data_config": task_metadata.get("data_config"),
        "image_proof_status": proof["status"],
        "image_proof_kind": proof["kind"],
        "image_proof_path": proof["path"],
        "failure_taxonomy": taxonomy,
        "podman_related": podman_related(taxonomy),
        "audit_status": "fail" if audit_failures else "pass",
        "audit_failures": audit_failures,
    }


def missing_row(
    *,
    cell: dict[str, Any],
    run_id: str,
    task_id: str,
    log_path: Path,
    root: Path,
) -> dict[str, Any]:
    taxonomy, error_type = classify_log_failure(log_path)
    return {
        "config": _repo_relative(cell["config_path"], root),
        "run_id": run_id,
        "task_id": task_id,
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
        "data_config": None,
        "image_proof_status": "missing",
        "image_proof_kind": "",
        "image_proof_path": "",
        "failure_taxonomy": taxonomy,
        "podman_related": podman_related(taxonomy) if taxonomy != "no_task_json" else "unclear",
        "audit_status": "fail",
        "audit_failures": ["missing_task_json"],
    }


def _load_preflight_status(path: Path | None, *, root: Path) -> dict[str, Any]:
    if path is None:
        return {
            "ok": False,
            "status": "missing",
            "failure_taxonomy": "vlm_preflight_failed",
            "path": "",
            "error": "No preflight status file was supplied.",
        }
    if not path.exists():
        return {
            "ok": False,
            "status": "missing",
            "failure_taxonomy": "vlm_preflight_failed",
            "path": _repo_relative(path, root),
            "error": "Preflight status file is missing.",
        }
    try:
        data = _load_json(path)
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "ok": False,
            "status": "invalid",
            "failure_taxonomy": "vlm_preflight_failed",
            "path": _repo_relative(path, root),
            "error": str(exc),
        }
    status = _as_dict(data)
    status.setdefault("failure_taxonomy", "" if status.get("ok") else "vlm_preflight_failed")
    status.setdefault("status", "pass" if status.get("ok") else "fail")
    status["path"] = _repo_relative(path, root)
    return status


def _preflight_gate_failures(preflight: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    remote = _as_dict(preflight.get("remote_image_url"))
    data_url = _as_dict(preflight.get("data_url"))
    if preflight.get("ok") is not True:
        failures.append("preflight_status_not_ok")
    if remote.get("ok") is not True:
        failures.append("remote_image_url_probe_failed")
    if data_url.get("ok") is not True:
        failures.append("data_url_probe_failed")
    if preflight.get("container_engine") != "podman":
        failures.append("preflight_not_podman_runtime")
    if preflight.get("podman_runtime_ok") is not True:
        failures.append("preflight_podman_runtime_not_proven")
    if preflight.get("thinking_mode") is not True:
        failures.append("thinking_mode_not_enabled")
    try:
        max_tokens = int(preflight.get("max_tokens") or 0)
    except (TypeError, ValueError):
        max_tokens = 0
    if max_tokens < MIN_THINKING_MAX_TOKENS:
        failures.append("preflight_max_tokens_too_small")
    return failures


def audit(
    *,
    run_prefix: str,
    config_dir: Path,
    results_dir: Path,
    logs_dir: Path,
    root: Path,
    preflight_status_file: Path | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    cells = discover_cells(config_dir)
    for cell in cells:
        run_id = f"{run_prefix}_{cell['agent_key']}_{cell['benchmark_key']}"
        log_path = logs_dir / f"{run_id}.log"
        task_files = find_task_files(results_dir, run_id)
        task_files_by_id = {task_file.stem: task_file for task_file in task_files}
        expected_ids = list(cell["expected_task_ids"])
        if expected_ids:
            for task_id in expected_ids:
                task_file = task_files_by_id.pop(task_id, None)
                if task_file is None:
                    rows.append(
                        missing_row(
                            cell=cell,
                            run_id=run_id,
                            task_id=task_id,
                            log_path=log_path,
                            root=root,
                        )
                    )
                    continue
                record = _read_first_record(task_file)
                if record is None:
                    rows.append(
                        missing_row(
                            cell=cell,
                            run_id=run_id,
                            task_id=task_id,
                            log_path=log_path,
                            root=root,
                        )
                    )
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
            for task_file in task_files_by_id.values():
                record = _read_first_record(task_file)
                if record is None:
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
                        unexpected_task=True,
                    )
                )
        else:
            for task_file in task_files:
                record = _read_first_record(task_file)
                if record is None:
                    rows.append(
                        missing_row(
                            cell=cell,
                            run_id=run_id,
                            task_id=task_file.stem,
                            log_path=log_path,
                            root=root,
                        )
                    )
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

    taxonomy_summary = Counter(row["failure_taxonomy"] for row in rows)
    preflight = _load_preflight_status(preflight_status_file, root=root)
    preflight_failures = _preflight_gate_failures(preflight)
    preflight_ok = not preflight_failures
    if preflight_failures:
        taxonomy_summary["vlm_preflight_failed"] += 1
    expected_tasks = sum(len(cell["expected_task_ids"]) for cell in cells)
    matrix_agents = sorted(cell["agent_key"] for cell in cells)
    per_cell_task_count_ok = all(len(cell["expected_task_ids"]) == 3 for cell in cells)
    matrix_complete = (
        sorted(EXPECTED_AGENTS) == matrix_agents
        and expected_tasks == REQUIRED_TASK_COUNT
        and per_cell_task_count_ok
    )
    audit_failures = [row for row in rows if row["audit_status"] != "pass"]
    task_count_ok = len(rows) == REQUIRED_TASK_COUNT
    return {
        "run_prefix": run_prefix,
        "expected_cells": len(cells),
        "expected_agents": list(EXPECTED_AGENTS),
        "expected_tasks": expected_tasks,
        "required_tasks": REQUIRED_TASK_COUNT,
        "matrix_complete": matrix_complete,
        "per_cell_task_count_ok": per_cell_task_count_ok,
        "preflight": preflight,
        "preflight_failures": preflight_failures,
        "rows": rows,
        "taxonomy_summary": dict(sorted(taxonomy_summary.items())),
        "audit_passed": (
            not audit_failures
            and preflight_ok
            and matrix_complete
            and task_count_ok
            and len(rows) == expected_tasks
        ),
        "audit_failure_count": (
            len(audit_failures)
            + len(preflight_failures)
            + (0 if matrix_complete else 1)
            + (0 if task_count_ok else 1)
        ),
    }


def render_markdown(result: dict[str, Any]) -> str:
    preflight = _as_dict(result.get("preflight"))
    lines = [
        f"# Podman MMMU-Pro Multimodal Readiness Audit: `{result['run_prefix']}`",
        "",
        f"Audit passed: `{str(result['audit_passed']).lower()}`",
        f"Expected cells: `{result['expected_cells']}`",
        f"Expected tasks: `{result['expected_tasks']}`",
        f"Required pilot tasks: `{result.get('required_tasks', REQUIRED_TASK_COUNT)}`",
        f"Audit failures: `{result['audit_failure_count']}`",
        f"VLM preflight passed: `{str(not result.get('preflight_failures')).lower()}`",
        "",
        "## Rows",
        "",
        "| Config | Task ID | Agent | Benchmark | Data Config | Status | Score | Error Type | Image Proof | Taxonomy | Audit | Artifact | Log |",
        "|---|---|---|---|---|---|---:|---|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        score = "-" if row["score"] is None else row["score"]
        proof = row["image_proof_status"]
        if row["image_proof_kind"]:
            proof = f"{proof}:{row['image_proof_kind']}"
        lines.append(
            "| {config} | {task_id} | {agent} | {benchmark} | {data_config} | {status} | {score} | {error_type} | {proof} | {taxonomy} | {audit} | {artifact} | {log} |".format(
                config=row["config"],
                task_id=row["task_id"],
                agent=row["agent"],
                benchmark=row["benchmark"],
                data_config=row["data_config"] or "-",
                status=row["result_status"],
                score=score,
                error_type=row["error_type"],
                proof=proof,
                taxonomy=row["failure_taxonomy"],
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

    lines.extend(["", "## VLM Preflight", ""])
    lines.append(f"- Status: `{'pass' if not result.get('preflight_failures') else 'fail'}`")
    if preflight.get("model"):
        lines.append(f"- Model: `{preflight['model']}`")
    if preflight.get("base_url"):
        lines.append(f"- Base URL: `{preflight['base_url']}`")
    if preflight.get("api_base_host"):
        lines.append(f"- API base host: `{preflight['api_base_host']}`")
    if preflight.get("container_engine"):
        lines.append(f"- Container engine: `{preflight['container_engine']}`")
    if preflight.get("network_mode"):
        lines.append(f"- Network mode: `{preflight['network_mode']}`")
    if "thinking_mode" in preflight:
        lines.append(f"- Thinking mode: `{str(bool(preflight.get('thinking_mode'))).lower()}`")
    if preflight.get("max_tokens"):
        lines.append(f"- Max tokens: `{preflight['max_tokens']}`")
    remote = _as_dict(preflight.get("remote_image_url"))
    data_url = _as_dict(preflight.get("data_url"))
    if remote:
        lines.append(f"- Remote image URL probe: `{'pass' if remote.get('ok') else 'fail'}`")
    if data_url:
        lines.append(f"- Data URL probe: `{'pass' if data_url.get('ok') else 'fail'}`")
    preflight_failures = result.get("preflight_failures") or []
    if preflight_failures:
        lines.append(f"- Preflight gate failures: `{', '.join(preflight_failures)}`")
    if preflight.get("error_type"):
        lines.append(f"- Error type: `{preflight['error_type']}`")
    if preflight.get("error"):
        lines.append(f"- Error: `{str(preflight['error'])[:240]}`")
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
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0 if result["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
