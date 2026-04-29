"""Readers for persisted ResultStore run artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunBundle:
    """Persisted records and manifest data for one ResultStore run."""

    results_dir: Path
    run_id: str
    jsonl_path: Path
    run_dir: Path
    manifest: dict[str, Any]
    records: list[dict[str, Any]]
    task_records: dict[str, list[dict[str, Any]]]


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Load dict records from JSONL, skipping blank or malformed lines."""
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_task_record_list(path: Path) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        for record in parsed:
            if isinstance(record, dict):
                return [record]
        return []
    return []


def load_run_bundle(results_dir: Path, run_id: str) -> RunBundle:
    """Load JSONL, manifest, and task JSON files for a persisted run."""
    jsonl_path = results_dir / f"{run_id}.jsonl"
    run_dir = results_dir / run_id
    manifest = _load_json_dict(run_dir / "run_manifest.json")
    task_records: dict[str, list[dict[str, Any]]] = {}
    tasks_dir = run_dir / "tasks"
    if tasks_dir.exists():
        for path in sorted(tasks_dir.glob("*.json")):
            task_records[path.stem] = _load_task_record_list(path)

    return RunBundle(
        results_dir=results_dir,
        run_id=run_id,
        jsonl_path=jsonl_path,
        run_dir=run_dir,
        manifest=manifest,
        records=load_jsonl_records(jsonl_path),
        task_records=task_records,
    )


def resolve_run_relative_path(results_dir: Path, rel_path: str) -> Path:
    """Resolve a ResultStore artifact reference under a results directory."""
    path = Path(rel_path)
    if path.is_absolute():
        raise ValueError("absolute paths are not allowed in ResultStore artifact refs")
    return results_dir / path
