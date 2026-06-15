"""Evaluation suitcase writer for host-controlled MoltBench episodes."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from alphadiana.benchmarks.moltbench.qjl_contract import build_score_vector

REQUIRED_JSONL = (
    "attempts.jsonl",
    "events.jsonl",
    "verifier_results.jsonl",
    "tool_calls.jsonl",
    "model_calls.jsonl",
    "file_diffs.jsonl",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


class EvaluationSuitcase:
    """Append-only episode artifacts owned by the host harness."""

    def __init__(
        self,
        root: Path,
        *,
        execution_id: str,
        task_id: str,
        task_payload: dict[str, Any],
        agent_type: str,
        runtime_config: dict[str, Any] | None = None,
    ) -> None:
        self.root = root
        self.execution_id = execution_id
        self.task_id = task_id
        self.agent_type = agent_type
        self._trajectory_cost = {
            "model_calls": 0,
            "tool_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "cost_reported": False,
        }
        self.root.mkdir(parents=True, exist_ok=False)
        self.root.chmod(0o750)
        for name in REQUIRED_JSONL:
            (self.root / name).touch()
        (self.root / "logs").mkdir()
        (self.root / "initial_workspace_snapshot").mkdir()
        (self.root / "final_workspace_snapshot").mkdir()
        (self.root / "best_workspace_snapshot").mkdir()
        (self.root / "task.json").write_text(
            json.dumps(task_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.root / "task.json").chmod(0o640)
        config_payload = {
            "execution_id": execution_id,
            "task_id": task_id,
            "agent_type": agent_type,
            "runtime": dict(runtime_config or {}),
        }
        (self.root / "config.json").write_text(
            json.dumps(config_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.root / "config.json").chmod(0o640)
        (self.root / "config.yaml").write_text(
            yaml.safe_dump(config_payload, sort_keys=True),
            encoding="utf-8",
        )
        (self.root / "config.yaml").chmod(0o640)
        self.write_verifier_config({})
        self.event("sandbox_start", {"owner": "host"})

    def write_verifier_config(self, payload: dict[str, Any]) -> None:
        """Persist host-only verifier inputs needed to reproduce the episode."""
        (self.root / "verifier_config.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.root / "verifier_config.json").chmod(0o600)

    def event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        stage_id: str = "",
        attempt_id: str = "",
    ) -> None:
        record = {
            "event_id": hashlib.sha256(
                f"{self.execution_id}:{utc_now()}:{event_type}".encode()
            ).hexdigest()[:24],
            "run_id": self.execution_id,
            "episode_id": self.execution_id,
            "stage_id": stage_id,
            "attempt_id": attempt_id,
            "agent_id": self.agent_type,
            "event_type": event_type,
            "timestamp": utc_now(),
            "payload": payload,
            "content_hash": hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "parent_event_id": None,
            "visibility": "host",
        }
        append_jsonl(self.root / "events.jsonl", record)

    def copy_snapshot(self, source: Path, destination_name: str) -> None:
        destination = self.root / destination_name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)

    def write_candidate_payload(
        self,
        snapshot: Path,
        filename: str = "final_candidate.json",
    ) -> Path:
        files: dict[str, str] = {}
        for path in sorted(snapshot.rglob("*.cu")):
            if path.parent != snapshot:
                raise ValueError(
                    f"candidate snapshot contains nested CUDA source: {path}"
                )
            if path.is_symlink():
                raise ValueError(f"candidate snapshot contains symlink: {path.name}")
            relative = path.relative_to(snapshot).as_posix()
            files[f"qjl_kernel/csrc/{relative}"] = path.read_text(
                encoding="utf-8",
                errors="strict",
            )
        candidate_path = self.root / filename
        candidate_path.write_text(
            json.dumps({"files": files}, sort_keys=True),
            encoding="utf-8",
        )
        return candidate_path

    def record_trajectory(self, trajectory: Iterable[dict[str, Any]]) -> None:
        for record in trajectory:
            event_type = str(record.get("type", record.get("event_type", ""))).lower()
            role = str(record.get("role", record.get("message", {}).get("role", ""))).lower()
            tool_calls = record.get("tool_calls")
            if not isinstance(tool_calls, list):
                content = record.get("message", {}).get("content", [])
                if isinstance(content, list):
                    tool_calls = [
                        item
                        for item in content
                        if isinstance(item, dict)
                        and str(item.get("type", "")).lower()
                        in {"tool_use", "tool_call", "toolcall"}
                    ]
            is_tool = (
                "tool" in event_type
                or role == "tool"
                or isinstance(tool_calls, list)
            )
            is_model = (
                role == "assistant"
                or "model" in event_type
                or "assistant" in event_type
                or "llm_" in event_type
                or isinstance(record.get("usage"), dict)
                or isinstance(record.get("message", {}).get("usage"), dict)
            )
            if is_model:
                append_jsonl(self.root / "model_calls.jsonl", dict(record))
                self._trajectory_cost["model_calls"] += 1
                self.event("model_call", {"record_type": event_type or role})
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if isinstance(tool_call, dict):
                        append_jsonl(self.root / "tool_calls.jsonl", tool_call)
                        self._trajectory_cost["tool_calls"] += 1
                        self.event(
                            "tool_call",
                            {"tool": tool_call.get("name") or tool_call.get("tool")},
                        )
            elif is_tool:
                append_jsonl(self.root / "tool_calls.jsonl", dict(record))
                self._trajectory_cost["tool_calls"] += 1
                self.event("tool_call", {"record_type": event_type or role})
            self._accumulate_usage(record)

    def _accumulate_usage(self, payload: Any) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                normalized = key.lower()
                if isinstance(value, (int, float)):
                    if normalized in {"input_tokens", "prompt_tokens"}:
                        self._trajectory_cost["input_tokens"] += int(value)
                    elif normalized in {"output_tokens", "completion_tokens"}:
                        self._trajectory_cost["output_tokens"] += int(value)
                    elif normalized in {"cost", "cost_usd", "total_cost_usd"}:
                        self._trajectory_cost["cost_usd"] += float(value)
                        self._trajectory_cost["cost_reported"] = True
                elif isinstance(value, (dict, list)):
                    self._accumulate_usage(value)
        elif isinstance(payload, list):
            for item in payload:
                self._accumulate_usage(item)

    def trajectory_cost(self) -> dict[str, Any]:
        return dict(self._trajectory_cost)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _artifact_validation(self) -> dict[str, Any]:
        snapshots = (
            "initial_workspace_snapshot",
            "final_workspace_snapshot",
            "best_workspace_snapshot",
        )
        snapshot_status = {
            name: any(
                path.is_file()
                and not path.is_symlink()
                and path.parent == self.root / name
                for path in (self.root / name).glob("*.cu")
            )
            for name in snapshots
        }
        required_files = (
            "task.json",
            "config.json",
            "config.yaml",
            "verifier_config.json",
            "baseline_eval.json",
            "attempts.jsonl",
            "events.jsonl",
            "verifier_results.jsonl",
            "tool_calls.jsonl",
            "model_calls.jsonl",
            "file_diffs.jsonl",
            "patch.diff",
            "changed_files.json",
            "official_eval.json",
            "score.json",
            "audit_report.md",
        )
        missing = [name for name in required_files if not (self.root / name).exists()]
        return {
            "complete": not missing and all(snapshot_status.values()),
            "missing": missing,
            "snapshots": snapshot_status,
        }

    def _artifact_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if path.name == "manifest.json":
                continue
            relative = path.relative_to(self.root).as_posix()
            if path.is_symlink():
                hashes[relative] = hashlib.sha256(
                    f"SYMLINK:{os.readlink(path)}".encode("utf-8")
                ).hexdigest()
            elif path.is_file():
                hashes[relative] = self._sha256_file(path)
        return hashes

    def finalize_diff(self) -> list[str]:
        initial = self.root / "initial_workspace_snapshot"
        final = self.root / "final_workspace_snapshot"
        changed: list[str] = []
        patch_parts: list[str] = []
        paths = {
            path.relative_to(initial).as_posix()
            for path in initial.rglob("*")
            if path.is_file() or path.is_symlink()
        } | {
            path.relative_to(final).as_posix()
            for path in final.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        for relative in sorted(paths):
            old_path = initial / relative
            new_path = final / relative
            old = self._diff_lines(old_path)
            new = self._diff_lines(new_path)
            if old == new:
                continue
            changed.append(f"qjl_kernel/csrc/{relative}")
            diff = "".join(
                difflib.unified_diff(
                    old,
                    new,
                    fromfile=f"a/qjl_kernel/csrc/{relative}",
                    tofile=f"b/qjl_kernel/csrc/{relative}",
                )
            )
            patch_parts.append(diff)
            append_jsonl(
                self.root / "file_diffs.jsonl",
                {"path": f"qjl_kernel/csrc/{relative}", "diff": diff},
            )
        (self.root / "patch.diff").write_text("".join(patch_parts), encoding="utf-8")
        (self.root / "changed_files.json").write_text(
            json.dumps(changed, indent=2),
            encoding="utf-8",
        )
        return changed

    @staticmethod
    def _diff_lines(path: Path) -> list[str]:
        if path.is_symlink():
            return [f"<SYMLINK {os.readlink(path)}>\n"]
        if not path.exists():
            return []
        return path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines(True)

    def finalize(
        self,
        *,
        official_result: dict[str, Any],
        baseline_result: dict[str, Any] | None,
        baseline_runtime_ms: float,
        retention_threshold: float,
        cost: dict[str, Any],
        exit_status: str,
    ) -> dict[str, Any]:
        attempts = load_jsonl(self.root / "verifier_results.jsonl")
        score = build_score_vector(
            official_result=official_result,
            attempt_results=attempts,
            baseline_runtime_ms=baseline_runtime_ms,
            retention_threshold=retention_threshold,
            cost=cost,
            baseline_result=baseline_result,
        )
        self.event("verifier_result", {"mode": "official_eval", "result": official_result})
        self.event("budget_event", cost)
        (self.root / "official_eval.json").write_text(
            json.dumps(official_result, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.root / "score.json").write_text(
            json.dumps(score, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        append_jsonl(
            self.root / "verifier_results.jsonl",
            {
                **official_result,
                "mode": "official_eval",
                "stage_id": "stage_02_improvement",
            },
        )
        changed_files = self.finalize_diff()
        for path in changed_files:
            self.event("file_change", {"path": path})
        self.event("sandbox_end", {"exit_status": exit_status})
        self.event("rollback_event", {"performed": False})
        self.event("artifact_event", {"manifest": "manifest.json"})
        (self.root / "audit_report.md").write_text(
            "\n".join(
                [
                    f"# QJL Evaluation Audit: {self.execution_id}",
                    "",
                    f"- Exit status: `{exit_status}`",
                    f"- Official verifier: `{official_result.get('verifier_status', 'ERROR')}`",
                    f"- C: `{score['C']['value']}`",
                    f"- G: `{score['G']['value']:.6f}`",
                    f"- S: `{score['S']['value']:.6f}`",
                    "- M: `N/A`",
                    f"- Changed files: `{len(changed_files)}`",
                    "",
                    "The score was computed from host-owned `official_eval.json`; "
                    "agent-written result files were ignored.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        validation = self._artifact_validation()
        manifest = {
            "schema_version": "moltbench.suitcase.v3",
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "agent_type": self.agent_type,
            "created_at": utc_now(),
            "exit_status": exit_status,
            "changed_files": changed_files,
            "host_owned": True,
            "artifact_validation": validation,
            "required_artifacts": [
                "task.json",
                "config.json",
                "config.yaml",
                "verifier_config.json",
                "baseline_eval.json",
                "initial_workspace_snapshot",
                "final_workspace_snapshot",
                "best_workspace_snapshot",
                "logs",
                *REQUIRED_JSONL,
                "patch.diff",
                "changed_files.json",
                "official_eval.json",
                "score.json",
                "audit_report.md",
            ],
            "artifact_hashes": self._artifact_hashes(),
        }
        (self.root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return score
