"""Benchmark loader for DecodingTrust Agent Platform tasks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import register_benchmark

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DT_ROOT = REPO_ROOT / "ref" / "DecodingTrust-Agent"
DEFAULT_DT_COMMIT = "6586b3b695b19c6b4d0b591e1739135404f5cce4"


def resolve_dt_root(config: dict[str, Any] | None = None) -> Path:
    config = config or {}
    raw = config.get("dt_root") or str(DEFAULT_DT_ROOT)
    return Path(str(raw)).expanduser().resolve()


def ensure_dtap_on_path(dt_root: Path) -> None:
    if not dt_root.exists():
        hint = ""
        if dt_root == DEFAULT_DT_ROOT.resolve():
            hint = (
                " Run 'git submodule update --init --recursive "
                "ref/DecodingTrust-Agent' from the AlphaDiana repo root."
            )
        raise FileNotFoundError(f"DecodingTrust-Agent root not found: {dt_root}.{hint}")
    root = str(dt_root)
    if root not in sys.path:
        sys.path.insert(0, root)


def _dt_commit(dt_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(dt_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return DEFAULT_DT_COMMIT


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_num, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_num}: {exc}") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _load_task_config(task_dir: Path) -> dict[str, Any]:
    config_path = task_dir / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"DTAP task config not found: {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"DTAP task config must be a mapping: {config_path}")
    return data


def _build_task_dir(dt_root: Path, row: dict[str, Any]) -> Path:
    task_type = str(row.get("type") or "")
    domain = str(row.get("domain") or "")
    task_id = str(row.get("task_id") or "")
    risk_category = str(row.get("risk_category") or "")
    if not domain or not task_type or not task_id:
        raise ValueError(f"DTAP task row missing domain/type/task_id: {row!r}")
    dataset = dt_root / "dataset"
    if task_type == "benign":
        return (dataset / domain / "benign" / risk_category / task_id).resolve()
    threat_model = str(row.get("threat_model") or task_type)
    return (dataset / domain / "malicious" / threat_model / risk_category / task_id).resolve()


def _task_identifier(row: dict[str, Any]) -> str:
    task_type = str(row.get("type") or "")
    if task_type == "benign":
        return "/".join(
            [
                str(row.get("domain")),
                "benign",
                str(row.get("risk_category")),
                str(row.get("task_id")),
            ]
        )
    threat_model = str(row.get("threat_model") or task_type)
    return "/".join(
        [
            str(row.get("domain")),
            threat_model,
            str(row.get("risk_category")),
            str(row.get("task_id")),
        ]
    )


def _normalize_instruction(instruction: Any) -> str:
    if isinstance(instruction, list):
        return "\n\n".join(str(item).strip() for item in instruction if str(item).strip())
    return str(instruction or "").strip()


def _load_instruction(task_dir: Path, task_config: dict[str, Any], row: dict[str, Any]) -> str:
    task_section = task_config.get("Task", {}) if isinstance(task_config.get("Task"), dict) else {}
    attack_section = task_config.get("Attack", {}) if isinstance(task_config.get("Attack"), dict) else {}
    instruction = task_section.get("task_instruction", task_section.get("original_instruction", ""))

    if attack_section:
        try:
            from dt_arena.src.types.task import AttackConfig
            from utils import apply_prompt_injections
        except Exception as exc:
            raise RuntimeError(
                "Failed to import DecodingTrust prompt-injection helpers. "
                f"Check dt_root/PYTHONPATH for task {task_dir}."
            ) from exc

        attack_config = AttackConfig.from_yaml(str(task_dir / "config.yaml"))
        threat_model = str(
            row.get("threat_model")
            or attack_section.get("threat_model")
            or row.get("type")
            or ""
        ).lower()
        if threat_model == "direct":
            instruction = apply_prompt_injections(None, attack_config)
        else:
            instruction = apply_prompt_injections(instruction, attack_config)

    problem = _normalize_instruction(instruction)
    if not problem:
        raise ValueError(
            f"DTAP task instruction is empty after applying prompt injections: {task_dir}"
        )
    return problem


@register_benchmark("decodingtrust")
class DecodingTrustBenchmark(Benchmark):
    """Load DTAP task indices and expose them as AlphaDiana tasks."""

    name = "decodingtrust"

    def default_scorer(self) -> str:
        return "decodingtrust"

    def load_tasks(self, config: dict[str, Any]) -> list[BenchmarkTask]:
        dt_root = resolve_dt_root(config)
        ensure_dtap_on_path(dt_root)
        domain = str(config.get("domain", "finance"))
        task_types = config.get("task_types", config.get("task_type", ["benign", "direct", "indirect"]))
        if isinstance(task_types, str):
            task_types = [task_types]
        limit = int(config.get("limit", 0) or 0)
        task_ids_filter = {str(item) for item in config.get("task_ids", []) or []}
        risk_filter = {str(item) for item in config.get("risk_categories", []) or []}

        tasks: list[BenchmarkTask] = []
        commit = _dt_commit(dt_root)

        for task_type in task_types:
            index_name = str(task_type)
            jsonl_path = dt_root / "benchmark" / domain / f"{index_name}.jsonl"
            if not jsonl_path.exists():
                raise FileNotFoundError(f"DTAP task index not found: {jsonl_path}")
            for row in _read_jsonl(jsonl_path):
                task_id = _task_identifier(row)
                if task_ids_filter and str(row.get("task_id")) not in task_ids_filter and task_id not in task_ids_filter:
                    continue
                if risk_filter and str(row.get("risk_category")) not in risk_filter:
                    continue
                task_dir = _build_task_dir(dt_root, row)
                task_config = _load_task_config(task_dir)
                for required in ("setup.sh", "judge.py"):
                    if not (task_dir / required).exists():
                        raise FileNotFoundError(f"DTAP task missing {required}: {task_dir}")

                task_section = task_config.get("Task", {}) if isinstance(task_config.get("Task"), dict) else {}
                attack_section = task_config.get("Attack", {}) if isinstance(task_config.get("Attack"), dict) else {}
                problem = _load_instruction(task_dir, task_config, row)

                metadata = {
                    "dt_root": str(dt_root),
                    "dt_task_dir": str(task_dir),
                    "dt_task_id": task_section.get("task_id") or row.get("task_id"),
                    "domain": row.get("domain"),
                    "task_type": row.get("type"),
                    "threat_model": row.get("threat_model") or ("benign" if row.get("type") == "benign" else row.get("type")),
                    "risk_category": row.get("risk_category"),
                    "dt_commit": commit,
                    "dt_index_row": row,
                    "dt_config_task_id": task_section.get("task_id"),
                    "dt_agent_system_prompt": (
                        task_config.get("Agent", {}).get("system_prompt", "")
                        if isinstance(task_config.get("Agent"), dict)
                        else ""
                    ),
                    "dt_attack": attack_section,
                }
                tasks.append(
                    BenchmarkTask(
                        task_id=task_id,
                        problem=problem,
                        ground_truth=None,
                        metadata=metadata,
                    )
                )
                if limit and len(tasks) >= limit:
                    return tasks
        return tasks
