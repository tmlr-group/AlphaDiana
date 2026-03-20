"""Append-only JSONL storage for evaluation results."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.scorer.base import ScoreResult

logger = logging.getLogger(__name__)


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
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.path = str(self.output_dir / f"{run_id}.jsonl")
        self.artifacts_dir = self.output_dir / run_id / "artifacts"
        self._dirs_created = False
        self._write_lock = threading.Lock()
        # Per-key locks for artifact writes to avoid concurrent overwrites.
        self._artifact_locks: dict[str, threading.Lock] = {}
        self._artifact_locks_guard = threading.Lock()
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
        with self._get_artifact_lock(artifact_key):
            artifact_manifest = self._persist_artifacts(task, response, sample_index=sample_index)
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
                "trajectory": response.trajectory,
                "reasoning_trajectory": response.reasoning_trajectory,
                "raw_output": response.raw_output,
                "request_messages": response.request_messages,
                "response_json": response.response_json,
                "token_usage": response.token_usage,
                "wall_time_sec": response.wall_time_sec,
                "sandbox_id": response.sandbox_id,
                "gateway_url": response.gateway_url,
                "artifact_manifest": artifact_manifest,
                "gateway_log_excerpt": response.gateway_log_excerpt,
                "workspace_snapshot_paths": response.workspace_snapshot_paths,
                "sandbox_metadata": response.sandbox_metadata,
                "system_prompt": response.system_prompt,
                "finish_reason": getattr(response, "finish_reason", ""),
                "metadata": response.metadata,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
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
        with self._get_artifact_lock(artifact_key):
            artifact_manifest = self._persist_artifacts(task, response, sample_index=sample_index)
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
                "trajectory": response.trajectory,
                "reasoning_trajectory": response.reasoning_trajectory,
                "raw_output": response.raw_output,
                "request_messages": response.request_messages,
                "response_json": response.response_json,
                "token_usage": response.token_usage,
                "wall_time_sec": response.wall_time_sec,
                "sandbox_id": response.sandbox_id,
                "gateway_url": response.gateway_url,
                "artifact_manifest": artifact_manifest,
                "gateway_log_excerpt": response.gateway_log_excerpt,
                "workspace_snapshot_paths": response.workspace_snapshot_paths,
                "sandbox_metadata": response.sandbox_metadata,
                "system_prompt": response.system_prompt,
                "metadata": response.metadata,
                "finish_reason": getattr(response, "finish_reason", ""),
                "error": error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with self._write_lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            self._save_per_task_json(task.task_id, record)

    def _persist_artifacts(
        self, task: BenchmarkTask, response: AgentResponse, *, sample_index: int = 0,
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
            path.write_text(response.gateway_log_excerpt, encoding="utf-8")
            files["gateway_log"] = str(rel)

        if response.response_json:
            rel = sample_prefix / "agent" / "response.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(response.response_json, indent=2), encoding="utf-8")
            files["response_json"] = str(rel)

        if response.request_messages:
            rel = sample_prefix / "agent" / "request_messages.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(response.request_messages, indent=2), encoding="utf-8")
            files["request_messages"] = str(rel)

        if response.sandbox_metadata:
            rel = sample_prefix / "sandbox" / "sandbox_meta.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(response.sandbox_metadata, indent=2), encoding="utf-8")
            files["sandbox_metadata"] = str(rel)

        if response.system_prompt:
            rel = sample_prefix / "agent" / "system_prompt.txt"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(response.system_prompt, encoding="utf-8")
            files["system_prompt"] = str(rel)

        retry_responses = response.metadata.get("retry_responses")
        if retry_responses:
            rel = sample_prefix / "agent" / "retry_responses.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(retry_responses, indent=2), encoding="utf-8")
            files["retry_responses"] = str(rel)

        if response.workspace_file_contents:
            workspace_files: dict[str, str] = {}
            for remote_path, content in response.workspace_file_contents.items():
                normalized = remote_path.lstrip("/") or task.task_id
                rel = sample_prefix / "workspace" / normalized
                path = self.artifacts_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                workspace_files[remote_path] = str(rel)
            files["workspace_files"] = workspace_files

        artifact_root = self.artifacts_dir / sample_prefix
        if artifact_root.exists():
            manifest["local_artifact_root"] = str(sample_prefix)
        return manifest

    def _save_per_task_json(self, task_id: str, record: dict) -> None:
        """Write/update a per-task JSON file under {run_id}/tasks/{task_id}.json.

        All samples for the same task_id are stored as a list in a single file.
        """
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
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    def completed_task_ids(self) -> set[str]:
        """Return task_ids of records that should NOT be retried.

        A record is considered done if it has no error dict — this covers both
        scored results (correct=True/False) and old scorer records (correct=None,
        error=None).  Only records with a real error dict (agent crash, timeout,
        etc.) are eligible for retry when redo_all is False.
        """
        completed: set[str] = set()
        for record in self.load():
            if record.get("error") is None:
                completed.add(record["task_id"])
        return completed

    def completed_sample_ids(self) -> set[tuple[str, int]]:
        """Return (task_id, sample_index) pairs that have been completed."""
        completed: set[tuple[str, int]] = set()
        for record in self.load():
            if record.get("error") is None:
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
                key = (record["task_id"], record.get("sample_index", 0))
                records[key] = record
        return list(records.values())
