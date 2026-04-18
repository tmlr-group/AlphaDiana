"""Load evaluation results from JSONL files and YAML configs."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from alphadiana.dashboard.backend.models import (
    CompareRunEntry,
    RunDetailResponse,
    RunSummaryResponse,
    TaskResult,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSON lines from a file, deduplicating by (task_id, sample_index).

    The last record for each (task_id, sample_index) pair wins, matching
    ResultStore.load() semantics so that retried tasks replace error records.
    """
    records: dict[tuple[str, int], dict[str, Any]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                record = json.loads(line)
                key = (record.get("task_id", ""), record.get("sample_index", 0))
                records[key] = record
    return list(records.values())


def _get_category(r: dict[str, Any]) -> str:
    """Extract category from a result record."""
    task_meta = r.get("task_metadata", {})
    resp_meta = r.get("metadata", {})
    if isinstance(task_meta, dict) and task_meta.get("category"):
        return task_meta["category"]
    if isinstance(resp_meta, dict) and resp_meta.get("category"):
        return resp_meta["category"]
    return "default"


def _build_summary(run_id: str, records: list[dict[str, Any]]) -> RunSummaryResponse:
    """Compute summary statistics from a list of result records."""
    # Count unique tasks (by task_id), not raw JSONL records.
    # For multi-sample runs, each task may have multiple records.
    unique_task_ids = {r.get("task_id", "") for r in records}
    total = len(unique_task_ids)

    # A task is "completed" if at least one sample has a score.
    completed_task_ids: set[str] = set()
    for r in records:
        if r.get("score") is not None:
            completed_task_ids.add(r.get("task_id", ""))
    completed_records = [r for r in records if r.get("score") is not None]
    completed = len(completed_task_ids)
    failed = total - completed

    # For accuracy, count unique tasks with at least one correct sample.
    correct_task_ids: set[str] = set()
    for r in completed_records:
        if r.get("correct", False):
            correct_task_ids.add(r.get("task_id", ""))
    correct_count = len(correct_task_ids)
    accuracy = correct_count / completed if completed > 0 else 0.0
    accuracy_total = correct_count / total if total > 0 else 0.0

    scores = [r.get("score", 0.0) for r in completed_records]
    mean_score = sum(scores) / len(scores) if scores else 0.0

    wall_times = [r.get("wall_time_sec", 0.0) for r in completed_records]
    mean_wall_time = sum(wall_times) / len(wall_times) if wall_times else 0.0

    total_prompt = 0
    total_completion = 0
    for r in records:
        usage = r.get("token_usage", {})
        total_prompt += usage.get("prompt_tokens", 0)
        total_completion += usage.get("completion_tokens", 0)

    # Per-category accuracy
    cat_correct: dict[str, int] = {}
    cat_total: dict[str, int] = {}
    for r in completed_records:
        cat = _get_category(r)
        cat_total[cat] = cat_total.get(cat, 0) + 1
        if r.get("correct", False):
            cat_correct[cat] = cat_correct.get(cat, 0) + 1
    per_category = {
        cat: cat_correct.get(cat, 0) / cat_total[cat] for cat in cat_total
    }

    # Error distribution
    error_dist: dict[str, int] = {}
    for r in records:
        err = r.get("error")
        if isinstance(err, dict):
            etype = err.get("error_type", "unknown")
            error_dist[etype] = error_dist.get(etype, 0) + 1

    # Group results by task_id for pass@k / avg@k computation.
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_task[r.get("task_id", "")].append(r)

    # Compute actual max samples per task from data — this is the ground truth.
    actual_max_samples = max((len(samples) for samples in by_task.values()), default=1)
    # Stored num_samples can be wrong (e.g. set to 1 in records that actually have 32 samples).
    # Always take the larger of stored value vs observed data.
    num_samples_raw = next((r.get("num_samples") for r in records if r.get("num_samples") is not None), None)
    num_samples = max(int(num_samples_raw), actual_max_samples) if num_samples_raw is not None else actual_max_samples

    # Pass@K: fraction of unique tasks where at least 1 sample is correct.
    num_unique_tasks = len(by_task)
    tasks_passed = sum(
        1 for samples in by_task.values()
        if any(s.get("correct", False) for s in samples)
    )
    pass_at_k = tasks_passed / num_unique_tasks if num_unique_tasks > 0 else 0.0

    # Per-category pass@k
    cat_tasks_total: dict[str, set[str]] = defaultdict(set)
    cat_tasks_passed: dict[str, set[str]] = defaultdict(set)
    for task_id, samples in by_task.items():
        cat = _get_category(samples[0])
        cat_tasks_total[cat].add(task_id)
        if any(s.get("correct", False) for s in samples):
            cat_tasks_passed[cat].add(task_id)
    per_category_pass_at_k = {
        cat: len(cat_tasks_passed.get(cat, set())) / len(task_ids)
        for cat, task_ids in cat_tasks_total.items()
    }

    # Avg@K: per-task average correctness rate, then averaged across tasks.
    task_avg_scores: list[float] = []
    for samples in by_task.values():
        n_total = len(samples)
        n_correct = sum(1 for s in samples if s.get("correct", False))
        task_avg_scores.append(n_correct / n_total if n_total > 0 else 0.0)
    avg_at_k = sum(task_avg_scores) / len(task_avg_scores) if task_avg_scores else 0.0

    # Per-category avg@k
    cat_task_avgs: dict[str, list[float]] = defaultdict(list)
    for task_id, samples in by_task.items():
        cat = _get_category(samples[0])
        n_total = len(samples)
        n_correct = sum(1 for s in samples if s.get("correct", False))
        cat_task_avgs[cat].append(n_correct / n_total if n_total > 0 else 0.0)
    per_category_avg_at_k = {
        cat: sum(avgs) / len(avgs) if avgs else 0.0
        for cat, avgs in cat_task_avgs.items()
    }

    # Infer agent/benchmark from record-level fields first, then metadata fallback.
    agent = ""
    agent_version = ""
    benchmark = ""
    if records:
        agent = next((r.get("agent_name") for r in records if r.get("agent_name")), "")
        agent_version = next((r.get("agent_version") for r in records if r.get("agent_version")), "")
        # Prefer task_metadata.source: it is the actual dataset used (e.g. MathArena/hmmt_feb_2026),
        # which is more specific than benchmark_name (the registry plugin key, e.g. "aime").
        benchmark = next(
            (r.get("task_metadata", {}).get("source")
             for r in records
             if isinstance(r.get("task_metadata"), dict) and r.get("task_metadata", {}).get("source")),
            None,
        ) or next((r.get("benchmark_name") for r in records if r.get("benchmark_name")), "")
        # Legacy fallback: read from nested metadata/task_metadata.
        if not agent or not agent_version:
            meta = records[0].get("metadata", {})
            if isinstance(meta, dict):
                agent = agent or meta.get("agent", "")
                agent_version = agent_version or meta.get("agent_version", meta.get("model", ""))

    # Extract model name from record-level fields.
    model = ""
    if records:
        # Try response_json.model (present in direct_llm runs)
        for r in records:
            resp = r.get("response_json", {})
            if isinstance(resp, dict) and resp.get("model"):
                model = resp["model"]
                break
        # Try metadata.model_name
        if not model:
            model = next(
                (r.get("metadata", {}).get("model_name", "")
                 for r in records
                 if isinstance(r.get("metadata"), dict) and r.get("metadata", {}).get("model_name")),
                "",
            )

    timestamp = ""
    if records:
        timestamp = records[-1].get("timestamp", "")

    return RunSummaryResponse(
        run_id=run_id,
        agent=agent,
        agent_version=agent_version,
        benchmark=benchmark,
        total_tasks=total,
        completed=completed,
        failed=failed,
        accuracy=accuracy,
        accuracy_total=accuracy_total,
        mean_score=mean_score,
        mean_wall_time_sec=mean_wall_time,
        total_tokens={
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
        },
        per_category=per_category,
        error_distribution=error_dist,
        model=model,
        num_samples=num_samples,
        pass_at_k=pass_at_k,
        avg_at_k=avg_at_k,
        per_category_pass_at_k=per_category_pass_at_k,
        per_category_avg_at_k=per_category_avg_at_k,
        timestamp=timestamp,
    )


def _record_to_task_result(record: dict[str, Any]) -> TaskResult:
    """Convert a raw JSONL record to a TaskResult."""
    return TaskResult(
        task_id=record.get("task_id", ""),
        sample_index=record.get("sample_index", 0),
        problem=record.get("problem", ""),
        ground_truth=record.get("ground_truth"),
        predicted=record.get("predicted"),
        correct=record.get("correct"),
        score=record.get("score"),
        rationale=record.get("rationale", ""),
        wall_time_sec=record.get("wall_time_sec", 0.0),
        token_usage=record.get("token_usage", {}),
        trajectory=record.get("trajectory", []),
        raw_output=record.get("raw_output", ""),
        timestamp=record.get("timestamp", ""),
        error=record.get("error"),
        task_metadata=record.get("task_metadata", {}),
        finish_reason=record.get("finish_reason", ""),
    )


class DataLoader:
    """Reads JSONL result files and YAML configs from the results directory."""

    def __init__(self, results_dir: str, configs_dir: str | None = None) -> None:
        self.results_dir = Path(results_dir)
        self.configs_dir = Path(configs_dir) if configs_dir else None

    def list_runs(self) -> list[RunSummaryResponse]:
        """Return summary for every JSONL file in the results directory."""
        summaries: list[RunSummaryResponse] = []
        if not self.results_dir.exists():
            return summaries
        for jsonl_path in sorted(self.results_dir.glob("*.jsonl")):
            # Skip backup files
            if jsonl_path.suffixes[-1] != ".jsonl":
                continue
            run_id = jsonl_path.stem
            records = _load_jsonl(jsonl_path)
            if not records:
                continue
            summary = _build_summary(run_id, records)
            # Try to enrich from YAML config
            config = self._load_config(run_id)
            if config:
                agent_cfg = config.get("agent", {})
                summary.agent = agent_cfg.get("name", summary.agent)
                summary.agent_version = agent_cfg.get("version", summary.agent_version)
                bench_cfg = config.get("benchmark", {})
                # Prefer benchmark.config.dataset (specific dataset) over benchmark.name (plugin key).
                bench_dataset = bench_cfg.get("config", {}).get("dataset", "")
                summary.benchmark = bench_dataset or bench_cfg.get("name", summary.benchmark)
                # Model: agent.config.model (direct_llm) or sandbox.config.model_name (openclaw)
                if not summary.model:
                    summary.model = (
                        agent_cfg.get("config", {}).get("model", "")
                        or config.get("sandbox", {}).get("config", {}).get("model_name", "")
                    )
            summaries.append(summary)
        return summaries

    def _safe_path(self, *parts: str) -> Path | None:
        """Join parts under results_dir and return None if the result escapes it."""
        resolved = (self.results_dir / Path(*parts)).resolve()
        if not resolved.is_relative_to(self.results_dir.resolve()):
            return None
        return resolved

    def get_run(self, run_id: str) -> RunDetailResponse | None:
        """Return full detail for a single run."""
        jsonl_path = self._safe_path(f"{run_id}.jsonl")
        if jsonl_path is None or not jsonl_path.exists():
            return None
        records = _load_jsonl(jsonl_path)
        summary = _build_summary(run_id, records)
        config = self._load_config(run_id)
        if config:
            agent_cfg = config.get("agent", {})
            summary.agent = agent_cfg.get("name", summary.agent)
            summary.agent_version = agent_cfg.get("version", summary.agent_version)
            bench_cfg = config.get("benchmark", {})
            summary.benchmark = bench_cfg.get("name", summary.benchmark)
            if not summary.model:
                summary.model = (
                    agent_cfg.get("config", {}).get("model", "")
                    or config.get("sandbox", {}).get("config", {}).get("model_name", "")
                )
        results = [_record_to_task_result(r) for r in records]
        return RunDetailResponse(summary=summary, config=config, results=results)

    def get_task(self, run_id: str, task_id: str) -> TaskResult | None:
        """Return a single task result."""
        # Try per-task JSON first
        task_path = self._safe_path(run_id, "tasks", f"{task_id}.json")
        if task_path is not None and task_path.exists():
            record = json.loads(task_path.read_text(encoding="utf-8"))
            return _record_to_task_result(record)
        # Fallback: scan JSONL
        jsonl_path = self._safe_path(f"{run_id}.jsonl")
        if jsonl_path is None or not jsonl_path.exists():
            return None
        for record in _load_jsonl(jsonl_path):
            if record.get("task_id") == task_id:
                return _record_to_task_result(record)
        return None

    def compare_runs(self, run_ids: list[str]) -> list[CompareRunEntry]:
        """Return comparison data for multiple runs."""
        entries: list[CompareRunEntry] = []
        for run_id in run_ids:
            detail = self.get_run(run_id)
            if detail is None:
                continue
            results_by_task = {r.task_id: r for r in detail.results}
            entries.append(
                CompareRunEntry(
                    run_id=run_id,
                    summary=detail.summary,
                    results_by_task=results_by_task,
                )
            )
        return entries

    def _load_config(self, run_id: str) -> dict[str, Any] | None:
        """Try to find and load a YAML config matching the run_id."""
        if not self.configs_dir or not self.configs_dir.exists():
            return None
        # Try exact match and common naming patterns
        candidates = [
            self.configs_dir / f"{run_id}.yaml",
            self.configs_dir / f"{run_id}.yml",
        ]
        # Also try with underscores instead of hyphens
        slug = run_id.replace("-", "_")
        candidates.append(self.configs_dir / f"{slug}.yaml")
        candidates.append(self.configs_dir / f"{slug}.yml")
        for path in candidates:
            if path.exists():
                return yaml.safe_load(path.read_text(encoding="utf-8"))
        return None
