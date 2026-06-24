"""Append-only JSONL storage for evaluation results."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alphadiana.harness.base import AgentResponse
from alphadiana.harness.proxies.preservation import add_artifact_file_refs
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.analysis.io.logprob_artifacts import INT16_PROB_SCALE, raw_record_to_int16_record
from alphadiana.analysis.io.normalized_trace import (
    TRACE_ARTIFACT_NAME,
    build_normalized_trace,
    normalize_persisted_trajectory,
    normalize_reasoning_trajectory,
)
from alphadiana.scorer.base import ScoreResult
from alphadiana.analysis.io.status import (
    infer_score_status,
    is_valid_completed_record,
    normalize_legacy_timeout_zero_record,
)

logger = logging.getLogger(__name__)

_REDACTED = "<redacted>"
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "bearer_token",
    "authorization",
    "password",
    "secret",
)
_SECRET_ENV_ASSIGNMENT_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)="
    r"([^\s\"']+|\"[^\"]*\"|'[^']*')"
)
_AUTH_HEADER_RE = re.compile(
    r"\b(Authorization\s*:\s*(?:Bearer\s+)?)([^\s,;]+)",
    re.IGNORECASE,
)


def _looks_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _redact_text(value: str) -> str:
    value = _SECRET_ENV_ASSIGNMENT_RE.sub(r"\1=" + _REDACTED, value)
    return _AUTH_HEADER_RE.sub(r"\1" + _REDACTED, value)


def _redact_for_persistence(value: Any, *, key: object | None = None) -> Any:
    """Return a copy safe for persisted result records and artifacts."""
    if _looks_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_for_persistence(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_for_persistence(item) for item in value]
    if isinstance(value, dict):
        return {
            item_key: _redact_for_persistence(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    return value


class ResultStore:
    """Append-only JSONL result storage.

    Each line in the file is a JSON object representing the outcome of
    evaluating one benchmark task.
    """

    def __init__(
        self,
        output_dir: str,
        run_id: str,
        *,
        run_metadata: dict | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.run_id = run_id
        self.path = str(self.output_dir / f"{run_id}.jsonl")
        self.manifest_path = self.output_dir / run_id / "run_manifest.json"
        self.artifacts_dir = self.output_dir / run_id / "artifacts"
        self._dirs_created = False
        self._write_lock = threading.Lock()
        # Per-key locks for artifact writes to avoid concurrent overwrites.
        self._artifact_locks: dict[str, threading.Lock] = {}
        self._artifact_locks_guard = threading.Lock()
        self._task_json_locks: dict[str, threading.Lock] = {}
        self._task_json_locks_guard = threading.Lock()
        # Run-level metadata embedded into every record.
        self._run_metadata: dict = run_metadata or {}

    def _ensure_dirs(self) -> None:
        """Lazily create output directories on first write."""
        if not self._dirs_created:
            os.makedirs(self.output_dir, exist_ok=True)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            self._dirs_created = True

    def _get_artifact_lock(self, key: str) -> threading.Lock:
        """Return a per-key lock for artifact writes."""
        with self._artifact_locks_guard:
            if key not in self._artifact_locks:
                self._artifact_locks[key] = threading.Lock()
            return self._artifact_locks[key]

    def _get_task_json_lock(self, task_id: str) -> threading.Lock:
        """Return a per-task lock for read-modify-write task JSON updates."""
        with self._task_json_locks_guard:
            if task_id not in self._task_json_locks:
                self._task_json_locks[task_id] = threading.Lock()
            return self._task_json_locks[task_id]

    def append(
        self,
        task: BenchmarkTask,
        response: AgentResponse,
        score: ScoreResult,
        *,
        sample_index: int = 0,
    ) -> None:
        """Append a single result record to the JSONL file."""
        self._ensure_dirs()
        artifact_key = f"{task.task_id}:{sample_index}"
        normalized_trajectory = normalize_persisted_trajectory(response.trajectory)
        normalized_reasoning_trajectory = normalize_reasoning_trajectory(
            response.reasoning_trajectory,
            trajectory=response.trajectory,
        )
        with self._get_artifact_lock(artifact_key):
            response_metadata = dict(response.metadata or {})
            logprob_sidecars = self.write_logprob_sidecars(
                task.task_id,
                response_metadata,
                sample_index=sample_index,
            )
            self._add_missing_artifact_file_refs(
                response,
                logprob_sidecars["artifact_file_refs"],
            )
            artifact_manifest = self._persist_artifacts(
                task,
                response,
                sample_index=sample_index,
                normalized_trajectory=normalized_trajectory,
                normalized_reasoning_trajectory=normalized_reasoning_trajectory,
            )
            record = {
                "task_id": task.task_id,
                "sample_index": sample_index,
                **self._run_metadata,
                "problem": task.problem,
                "ground_truth": task.ground_truth,
                "task_metadata": task.metadata,
                "predicted": response.answer,
                "correct": score.correct,
                "score": score.score,
                "rationale": score.rationale,
                "score_metadata": score.metadata,
                "trajectory": normalized_trajectory,
                "reasoning_trajectory": normalized_reasoning_trajectory,
                "raw_output": response.raw_output,
                "request_messages": response.request_messages,
                "response_json": response.response_json,
                "token_usage": response.token_usage,
                "token_entropy_stats": getattr(response, "token_entropy_stats", {}),
                "logprobs_path": logprob_sidecars["logprobs_path"],
                "logprobs_int16_path": logprob_sidecars["logprobs_int16_path"],
                "top_logprobs": logprob_sidecars["top_logprobs"],
                "int16_probability_scale": logprob_sidecars["int16_probability_scale"],
                "wall_time_sec": response.wall_time_sec,
                "sandbox_id": response.sandbox_id,
                "gateway_url": response.gateway_url,
                "artifact_manifest": artifact_manifest,
                "gateway_log_excerpt": response.gateway_log_excerpt,
                "workspace_snapshot_paths": response.workspace_snapshot_paths,
                "sandbox_metadata": response.sandbox_metadata,
                "system_prompt": response.system_prompt,
                "finish_reason": getattr(response, "finish_reason", ""),
                "metadata": response_metadata,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            record = _redact_for_persistence(record)
            record["score_status"] = infer_score_status(record)
            with self._write_lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            self._save_per_task_json(task.task_id, record)

    def append_error(
        self,
        task: BenchmarkTask,
        *,
        error: dict,
        response: AgentResponse | None = None,
        sample_index: int = 0,
    ) -> None:
        """Append a failed task attempt with preserved runtime artifacts."""
        self._ensure_dirs()
        response = response or AgentResponse(answer=None)
        artifact_key = f"{task.task_id}:{sample_index}"
        normalized_trajectory = normalize_persisted_trajectory(response.trajectory)
        normalized_reasoning_trajectory = normalize_reasoning_trajectory(
            response.reasoning_trajectory,
            trajectory=response.trajectory,
        )
        with self._get_artifact_lock(artifact_key):
            response_metadata = dict(response.metadata or {})
            logprob_sidecars = self.write_logprob_sidecars(
                task.task_id,
                response_metadata,
                sample_index=sample_index,
            )
            self._add_missing_artifact_file_refs(
                response,
                logprob_sidecars["artifact_file_refs"],
            )
            artifact_manifest = self._persist_artifacts(
                task,
                response,
                sample_index=sample_index,
                normalized_trajectory=normalized_trajectory,
                normalized_reasoning_trajectory=normalized_reasoning_trajectory,
            )
            record = {
                "task_id": task.task_id,
                "sample_index": sample_index,
                **self._run_metadata,
                "problem": task.problem,
                "ground_truth": task.ground_truth,
                "task_metadata": task.metadata,
                "predicted": response.answer,
                "correct": None,
                "score": None,
                "rationale": error.get("error", ""),
                "score_metadata": {},
                "trajectory": normalized_trajectory,
                "reasoning_trajectory": normalized_reasoning_trajectory,
                "raw_output": response.raw_output,
                "request_messages": response.request_messages,
                "response_json": response.response_json,
                "token_usage": response.token_usage,
                "token_entropy_stats": getattr(response, "token_entropy_stats", {}),
                "logprobs_path": logprob_sidecars["logprobs_path"],
                "logprobs_int16_path": logprob_sidecars["logprobs_int16_path"],
                "top_logprobs": logprob_sidecars["top_logprobs"],
                "int16_probability_scale": logprob_sidecars["int16_probability_scale"],
                "wall_time_sec": response.wall_time_sec,
                "sandbox_id": response.sandbox_id,
                "gateway_url": response.gateway_url,
                "artifact_manifest": artifact_manifest,
                "gateway_log_excerpt": response.gateway_log_excerpt,
                "workspace_snapshot_paths": response.workspace_snapshot_paths,
                "sandbox_metadata": response.sandbox_metadata,
                "system_prompt": response.system_prompt,
                "metadata": response_metadata,
                "finish_reason": getattr(response, "finish_reason", ""),
                "error": error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            record = _redact_for_persistence(record)
            record["score_status"] = infer_score_status(record)
            with self._write_lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            self._save_per_task_json(task.task_id, record)

    def _persist_artifacts(
        self,
        task: BenchmarkTask,
        response: AgentResponse,
        *,
        sample_index: int = 0,
        normalized_trajectory: list[dict[str, Any]] | None = None,
        normalized_reasoning_trajectory: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Write gateway logs, response JSON, and workspace files to disk."""
        manifest = dict(response.artifact_manifest)
        files = manifest.setdefault("files", {})

        # For multi-sample runs, nest artifacts under sample_<N> subdirectory.
        sample_prefix = Path(task.task_id) / f"sample_{sample_index}" if sample_index > 0 else Path(task.task_id)

        if response.gateway_log_excerpt:
            rel = sample_prefix / "agent" / "gateway.log"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_redact_text(response.gateway_log_excerpt), encoding="utf-8")
            files["gateway_log"] = str(rel)

        if response.response_json:
            rel = sample_prefix / "agent" / "response.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(_redact_for_persistence(response.response_json), indent=2),
                encoding="utf-8",
            )
            files["response_json"] = str(rel)

        if response.request_messages:
            rel = sample_prefix / "agent" / "request_messages.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(_redact_for_persistence(response.request_messages), indent=2),
                encoding="utf-8",
            )
            files["request_messages"] = str(rel)

        if response.sandbox_metadata:
            rel = sample_prefix / "sandbox" / "sandbox_meta.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(_redact_for_persistence(response.sandbox_metadata), indent=2),
                encoding="utf-8",
            )
            files["sandbox_metadata"] = str(rel)

        if response.system_prompt:
            rel = sample_prefix / "agent" / "system_prompt.txt"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_redact_text(response.system_prompt), encoding="utf-8")
            files["system_prompt"] = str(rel)

        retry_responses = response.metadata.get("retry_responses")
        if retry_responses:
            rel = sample_prefix / "agent" / "retry_responses.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(_redact_for_persistence(retry_responses), indent=2),
                encoding="utf-8",
            )
            files["retry_responses"] = str(rel)

        if response.workspace_file_contents:
            workspace_files: dict[str, str] = {}
            for remote_path, content in response.workspace_file_contents.items():
                normalized = remote_path.lstrip("/") or task.task_id
                rel = sample_prefix / "workspace" / normalized
                path = self.artifacts_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_redact_text(content), encoding="utf-8")
                workspace_files[remote_path] = str(rel)
            files["workspace_files"] = workspace_files
            self._resolve_workspace_artifact_refs(files, workspace_files)

        normalized_trace = build_normalized_trace(
            task_id=task.task_id,
            sample_index=sample_index,
            response=response,
            run_metadata=self._run_metadata,
            trajectory=normalized_trajectory,
            reasoning_trajectory=normalized_reasoning_trajectory,
            artifact_files=files,
        )
        rel = sample_prefix / "agent" / TRACE_ARTIFACT_NAME
        path = self.artifacts_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_redact_for_persistence(normalized_trace), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        files["normalized_trace"] = str(rel)

        artifact_root = self.artifacts_dir / sample_prefix
        if artifact_root.exists():
            manifest["local_artifact_root"] = str(sample_prefix)
        return manifest

    def _resolve_workspace_artifact_refs(
        self,
        files: dict[str, Any],
        workspace_files: dict[str, str],
    ) -> None:
        """Replace workspace file aliases in manifest entries with local relative paths."""
        basename_lookup: dict[str, list[str]] = {}
        for remote_path, rel_path in workspace_files.items():
            basename_lookup.setdefault(Path(remote_path).name, []).append(rel_path)

        def _resolve(value: Any) -> Any:
            if isinstance(value, str):
                direct = workspace_files.get(value)
                if direct:
                    return direct
                candidates = basename_lookup.get(Path(value).name, [])
                if len(candidates) == 1:
                    return candidates[0]
                return value
            if isinstance(value, list):
                return [_resolve(item) for item in value]
            if isinstance(value, dict):
                return {
                    key: _resolve(item)
                    for key, item in value.items()
                }
            return value

        for key, value in list(files.items()):
            if key == "workspace_files":
                continue
            files[key] = _resolve(value)

    def _save_per_task_json(self, task_id: str, record: dict) -> None:
        """Write/update a per-task JSON file under {run_id}/tasks/{task_id}.json.

        All samples for the same task_id are stored as a list in a single file.
        """
        with self._get_task_json_lock(task_id):
            tasks_dir = self.output_dir / self.run_id / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            path = tasks_dir / f"{task_id}.json"
            # Load existing records if any.
            existing: list[dict] = []
            if path.exists():
                try:
                    content = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(content, list):
                        existing = content
                    elif isinstance(content, dict):
                        # Migrate from old single-record format.
                        existing = [content]
                except (json.JSONDecodeError, OSError):
                    pass
            # Replace existing sample with same index, or append.
            sample_index = record.get("sample_index", 0)
            replaced = False
            for i, rec in enumerate(existing):
                if rec.get("sample_index", 0) == sample_index:
                    existing[i] = record
                    replaced = True
                    break
            if not replaced:
                existing.append(record)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    def _add_missing_artifact_file_refs(
        self,
        response: AgentResponse,
        file_refs: dict[str, str],
    ) -> None:
        """Add generated sidecar aliases without replacing existing manifest refs."""
        if not file_refs:
            return
        existing_files = {}
        if isinstance(response.artifact_manifest, dict):
            existing_files = response.artifact_manifest.get("files", {})
            if not isinstance(existing_files, dict):
                existing_files = {}
        refs_to_add = {
            key: value
            for key, value in file_refs.items()
            if key not in existing_files
        }
        if refs_to_add:
            response.artifact_manifest = add_artifact_file_refs(
                response.artifact_manifest,
                **refs_to_add,
            )

    def _logprobs_relative_path(self, task_id: str, sample_index: int = 0) -> Path:
        if sample_index <= 0:
            return Path("logprobs") / f"{task_id}.jsonl"
        return Path("logprobs") / task_id / f"sample_{sample_index}.jsonl"

    def _logprobs_int16_relative_path(self, task_id: str, sample_index: int = 0) -> Path:
        if sample_index <= 0:
            return Path("logprobs_int16") / f"{task_id}.jsonl"
        return Path("logprobs_int16") / task_id / f"sample_{sample_index}.jsonl"

    def write_logprobs_jsonl(
        self,
        task_id: str,
        records: list[dict],
        *,
        sample_index: int = 0,
    ) -> Path:
        """Write per-token logprob records to results/{run_id}/logprobs/.

        Single-sample runs keep the historic flat path
        `logprobs/{task_id}.jsonl`. Multi-sample runs use
        `logprobs/{task_id}/sample_<N>.jsonl` for sample indices > 0 so
        concurrent samples do not overwrite each other.
        """
        self._ensure_dirs()
        rel_path = self._logprobs_relative_path(task_id, sample_index)
        path = self.output_dir / self.run_id / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_task_json_lock(f"logprobs:{task_id}:{sample_index}"):
            with open(path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path

    def write_logprob_sidecars(
        self,
        task_id: str,
        response_metadata: dict,
        *,
        sample_index: int = 0,
    ) -> dict:
        """Write raw float and compact Int16 logprob sidecars for a task."""
        logprob_records = response_metadata.pop("logprob_records", None)
        logprob_int16_records = response_metadata.pop("logprob_int16_records", None)
        raw_records = logprob_records if isinstance(logprob_records, list) else []
        int16_records = logprob_int16_records if isinstance(logprob_int16_records, list) else []
        raw_record_count = len(raw_records)

        logprobs_path_rel = ""
        logprobs_int16_path_rel = ""
        artifact_file_refs: dict[str, str] = {}

        if raw_records:
            path = self.write_logprobs_jsonl(
                task_id,
                raw_records,
                sample_index=sample_index,
            )
            rel_path = path.relative_to(self.output_dir / self.run_id).as_posix()
            logprobs_path_rel = f"{self.run_id}/{rel_path}"
            artifact_file_refs["logprobs_float"] = rel_path
            response_metadata["logprob_records"] = raw_record_count

        if not int16_records and raw_records:
            int16_records = [
                raw_record_to_int16_record(record)
                for record in raw_records
            ]

        if int16_records:
            self._ensure_dirs()
            rel_path_obj = self._logprobs_int16_relative_path(task_id, sample_index)
            path = self.output_dir / self.run_id / rel_path_obj
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._get_task_json_lock(f"logprobs_int16:{task_id}:{sample_index}"):
                with open(path, "w", encoding="utf-8") as f:
                    for record in int16_records:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
            rel_path = rel_path_obj.as_posix()
            logprobs_int16_path_rel = f"{self.run_id}/{rel_path}"
            artifact_file_refs["logprobs_int16"] = rel_path

        has_int16_sidecar = bool(logprobs_int16_path_rel)
        return {
            "logprobs_path": logprobs_path_rel,
            "logprobs_int16_path": logprobs_int16_path_rel,
            "top_logprobs": 20 if has_int16_sidecar else 0,
            "int16_probability_scale": INT16_PROB_SCALE if has_int16_sidecar else 0,
            "artifact_file_refs": artifact_file_refs,
        }

    def completed_task_ids(self, scorer_name: str | None = None) -> set[str]:
        """Return task_ids of records that should NOT be retried.

        Only valid scored records count as completed. Legacy scored records
        without score_status remain valid when they have score/correct and no
        disqualifying failure metadata.
        """
        completed: set[str] = set()
        for record in self.load():
            if is_valid_completed_record(record, scorer_name=scorer_name):
                completed.add(record["task_id"])
        return completed

    def completed_sample_ids(self, scorer_name: str | None = None) -> set[tuple[str, int]]:
        """Return (task_id, sample_index) pairs that have been completed."""
        completed: set[tuple[str, int]] = set()
        for record in self.load():
            if is_valid_completed_record(record, scorer_name=scorer_name):
                completed.add((record["task_id"], record.get("sample_index", 0)))
        return completed

    def load(self) -> list[dict]:
        """Read and parse all JSONL lines, deduplicating by (task_id, sample_index).

        Malformed lines (e.g. from a process crash mid-write) are skipped
        with a warning rather than aborting the entire load.
        """
        if not os.path.exists(self.path):
            return []
        records: dict[tuple[str, int], dict] = {}
        with open(self.path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "Skipping malformed JSONL line %d in %s", line_num, self.path,
                    )
                    continue
                record = normalize_legacy_timeout_zero_record(record)
                record["score_status"] = infer_score_status(record)
                key = (record["task_id"], record.get("sample_index", 0))
                records[key] = record
        return list(records.values())

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to read run manifest: %s", self.manifest_path)
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _copy_tree_contents(src: Path, dst: Path) -> None:
        if not src.exists():
            return
        for path in src.rglob("*"):
            rel = path.relative_to(src)
            target = dst / rel
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

    @staticmethod
    def _record_score_rank(record: dict) -> int:
        if infer_score_status(record) == VALID_SCORE_STATUS:
            return 2
        if record.get("score") is not None and record.get("correct") is not None:
            return 1
        return 0

    @staticmethod
    def _annotate_shard_record(record: dict, *, target_run_id: str, shard_run_id: str, shard_index: int) -> dict:
        annotated = dict(record)
        annotated["run_id"] = target_run_id
        annotated["shard_run_id"] = shard_run_id
        annotated["shard_index"] = shard_index
        for path_key in ("logprobs_path", "logprobs_int16_path"):
            value = annotated.get(path_key)
            if isinstance(value, str) and value.startswith(f"{shard_run_id}/"):
                annotated[path_key] = f"{target_run_id}/{value[len(shard_run_id) + 1:]}"
        metadata = annotated.get("metadata")
        if isinstance(metadata, dict):
            metadata = dict(metadata)
        else:
            metadata = {}
        metadata.setdefault("shard_run_id", shard_run_id)
        metadata.setdefault("shard_index", shard_index)
        annotated["metadata"] = metadata
        annotated["score_status"] = infer_score_status(annotated)
        return annotated

    def _merge_task_json_record(
        self,
        *,
        target_path: Path,
        incoming: dict,
    ) -> None:
        existing: list[dict] = []
        if target_path.exists():
            try:
                payload = json.loads(target_path.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    existing = [item for item in payload if isinstance(item, dict)]
                elif isinstance(payload, dict):
                    existing = [payload]
            except (OSError, json.JSONDecodeError):
                existing = []
        sample_index = incoming.get("sample_index", 0)
        replaced = False
        for index, record in enumerate(existing):
            if record.get("sample_index", 0) == sample_index:
                if self._record_score_rank(incoming) >= self._record_score_rank(record):
                    existing[index] = incoming
                replaced = True
                break
        if not replaced:
            existing.append(incoming)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def merge_result_stores(
        self,
        *,
        target_run_id: str,
        shard_run_ids: list[str],
        expected_tasks: list[Any],
        num_samples: int,
        config_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Merge shard ResultStore outputs into this store's target run."""
        self._ensure_dirs()
        target_jsonl = self.output_dir / f"{target_run_id}.jsonl"
        target_run_dir = self.output_dir / target_run_id
        target_tasks_dir = target_run_dir / "tasks"
        target_jsonl.parent.mkdir(parents=True, exist_ok=True)
        target_run_dir.mkdir(parents=True, exist_ok=True)
        target_tasks_dir.mkdir(parents=True, exist_ok=True)

        records_by_key: dict[tuple[str, int], dict] = {}
        shard_manifests: dict[str, dict[str, Any]] = {}

        for shard_index, shard_run_id in enumerate(shard_run_ids):
            shard_jsonl = self.output_dir / f"{shard_run_id}.jsonl"
            shard_dir = self.output_dir / shard_run_id
            shard_manifest_path = shard_dir / "run_manifest.json"
            if shard_manifest_path.exists():
                try:
                    payload = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        shard_manifests[shard_run_id] = payload
                except (OSError, json.JSONDecodeError):
                    logger.warning("Failed to read shard manifest: %s", shard_manifest_path)

            if shard_jsonl.exists():
                with shard_jsonl.open("r", encoding="utf-8") as handle:
                    for line_num, raw in enumerate(handle, 1):
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed shard JSONL line %s:%d", shard_jsonl, line_num)
                            continue
                        if not isinstance(record, dict) or not record.get("task_id"):
                            continue
                        annotated = self._annotate_shard_record(
                            record,
                            target_run_id=target_run_id,
                            shard_run_id=shard_run_id,
                            shard_index=shard_index,
                        )
                        key = (str(annotated["task_id"]), int(annotated.get("sample_index", 0) or 0))
                        existing = records_by_key.get(key)
                        if existing is None or self._record_score_rank(annotated) >= self._record_score_rank(existing):
                            records_by_key[key] = annotated

            shard_tasks_dir = shard_dir / "tasks"
            if shard_tasks_dir.exists():
                for task_json in sorted(shard_tasks_dir.rglob("*.json")):
                    try:
                        payload = json.loads(task_json.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        logger.warning("Skipping unreadable shard task JSON: %s", task_json)
                        continue
                    rows = payload if isinstance(payload, list) else [payload]
                    target_path = target_tasks_dir / task_json.relative_to(shard_tasks_dir)
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        annotated = self._annotate_shard_record(
                            row,
                            target_run_id=target_run_id,
                            shard_run_id=shard_run_id,
                            shard_index=shard_index,
                        )
                        self._merge_task_json_record(target_path=target_path, incoming=annotated)

            for dirname in ("artifacts", "lifecycle", "status", "logprobs", "logprobs_int16"):
                self._copy_tree_contents(shard_dir / dirname, target_run_dir / dirname)

        ordered_records = sorted(
            records_by_key.values(),
            key=lambda record: (str(record.get("task_id", "")), int(record.get("sample_index", 0) or 0)),
        )
        tmp_jsonl = target_jsonl.with_suffix(target_jsonl.suffix + ".tmp")
        with tmp_jsonl.open("w", encoding="utf-8") as handle:
            for record in ordered_records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp_jsonl.replace(target_jsonl)

        expected_task_ids = [str(getattr(task, "task_id", "")) for task in expected_tasks]
        task_metadata_by_id = {
            str(getattr(task, "task_id", "")): getattr(task, "metadata", {})
            for task in expected_tasks
        }
        self.save_manifest(
            {
                "run_id": target_run_id,
                **self._run_metadata,
                "num_samples": num_samples,
                "expected_task_count": len(expected_task_ids),
                "expected_sample_count": len(expected_task_ids) * num_samples,
                "expected_task_ids": expected_task_ids,
                "task_metadata_by_id": task_metadata_by_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "config_metadata": config_metadata or {},
                "merged_from_shards": shard_run_ids,
                "shard_manifests": shard_manifests,
            }
        )
