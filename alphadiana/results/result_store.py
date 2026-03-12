"""Append-only JSONL storage for evaluation results."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.scorer.base import ScoreResult


class ResultStore:
    """Append-only JSONL result storage.

    Each line in the file is a JSON object representing the outcome of
    evaluating one benchmark task.
    """

    def __init__(self, output_dir: str, run_id: str) -> None:
        os.makedirs(output_dir, exist_ok=True)
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.path = str(self.output_dir / f"{run_id}.jsonl")
        self.artifacts_dir = self.output_dir / run_id / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        task: BenchmarkTask,
        response: AgentResponse,
        score: ScoreResult,
    ) -> None:
        """Append a single result record to the JSONL file."""
        artifact_manifest = self._persist_artifacts(task, response)
        record = {
            "task_id": task.task_id,
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
            "metadata": response.metadata,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def append_error(
        self,
        task: BenchmarkTask,
        *,
        error: dict,
        response: AgentResponse | None = None,
    ) -> None:
        """Append a failed task attempt with preserved runtime artifacts."""
        response = response or AgentResponse(answer=None)
        artifact_manifest = self._persist_artifacts(task, response)
        record = {
            "task_id": task.task_id,
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
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def _persist_artifacts(self, task: BenchmarkTask, response: AgentResponse) -> dict:
        """Write gateway logs, response JSON, and workspace files to disk."""
        manifest = dict(response.artifact_manifest)
        files = manifest.setdefault("files", {})

        if response.gateway_log_excerpt:
            rel = Path(task.task_id) / "openclaw" / "gateway.log"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(response.gateway_log_excerpt, encoding="utf-8")
            files["gateway_log"] = str(rel)

        if response.response_json:
            rel = Path(task.task_id) / "openclaw" / "response.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(response.response_json, indent=2), encoding="utf-8")
            files["response_json"] = str(rel)

        if response.request_messages:
            rel = Path(task.task_id) / "openclaw" / "request_messages.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(response.request_messages, indent=2), encoding="utf-8")
            files["request_messages"] = str(rel)

        if response.sandbox_metadata:
            rel = Path(task.task_id) / "sandbox" / "sandbox_meta.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(response.sandbox_metadata, indent=2), encoding="utf-8")
            files["sandbox_metadata"] = str(rel)

        if response.system_prompt:
            rel = Path(task.task_id) / "openclaw" / "system_prompt.txt"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(response.system_prompt, encoding="utf-8")
            files["system_prompt"] = str(rel)

        retry_responses = response.metadata.get("retry_responses")
        if retry_responses:
            rel = Path(task.task_id) / "openclaw" / "retry_responses.json"
            path = self.artifacts_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(retry_responses, indent=2), encoding="utf-8")
            files["retry_responses"] = str(rel)

        if response.workspace_file_contents:
            workspace_files: dict[str, str] = {}
            for remote_path, content in response.workspace_file_contents.items():
                normalized = remote_path.lstrip("/") or task.task_id
                rel = Path(task.task_id) / "workspace" / normalized
                path = self.artifacts_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                workspace_files[remote_path] = str(rel)
            files["workspace_files"] = workspace_files

        task_dir = self.artifacts_dir / task.task_id
        if task_dir.exists():
            manifest["local_artifact_root"] = str(Path(task.task_id))
        return manifest

    def load(self) -> list[dict]:
        """Read and parse all JSONL lines from the results file."""
        if not os.path.exists(self.path):
            return []
        results: list[dict] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
        return results
