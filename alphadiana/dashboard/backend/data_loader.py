"""Load evaluation results from JSONL files and YAML configs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from alphadiana.analysis.export import ReportGenerator
from alphadiana.results.statistics import RunStatistics
from alphadiana.results.store import ResultStore

from alphadiana.dashboard.backend.models import (
    CompareRunEntry,
    RunDetailResponse,
    RunSummaryResponse,
    TaskResult,
)

logger = logging.getLogger(__name__)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSON lines from a file, deduplicating by (task_id, sample_index).

    The last record for each (task_id, sample_index) pair wins, matching
    ResultStore.load() semantics so that retried tasks replace error records.
    """
    return ResultStore(str(path.parent), path.stem).load()


def _build_summary(run_id: str, records: list[dict[str, Any]],
                   manifest: dict | None = None) -> RunSummaryResponse:
    """Adapt canonical statistics while retaining legacy dashboard task counters."""
    core = ReportGenerator().summarize(records, manifest, run_id=run_id)
    base = RunStatistics.from_records(records, manifest)
    records = base.results
    completed = base.completed_tasks
    total = core.expected_task_count
    failed = max(total - completed, 0)

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
        accuracy=core.accuracy,
        accuracy_total=core.accuracy_total,
        mean_score=core.mean_score,
        mean_wall_time_sec=core.mean_wall_time_sec,
        total_tokens=core.total_tokens,
        per_category={k: v["pass_rate"] for k, v in core.per_category.items()},
        expected_sample_count=core.expected_sample_count,
        valid_scored=core.valid_scored,
        missing_samples=core.missing_samples,
        missing_tasks=core.missing_tasks,
        strict_report_issues=core.strict_report_issues,
        error_distribution=core.error_distribution,
        model=model,
        num_samples=core.num_samples,
        samples_independent=core.samples_independent,
        pass_at_k=core.pass_at_k,
        avg_at_k=core.avg_at_k,
        per_category_pass_at_k=core.per_category_pass_at_k,
        per_category_avg_at_k=core.per_category_avg_at_k,
        evaluation_metrics=core.evaluation_metrics,
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
        evaluation_metrics=record.get("evaluation_metrics", {}),
        evaluation_metric_errors=record.get("evaluation_metric_errors", {}),
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
            summary = _build_summary(run_id, records, ResultStore(str(self.results_dir), run_id).load_manifest())
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
        summary = _build_summary(run_id, records, ResultStore(str(self.results_dir), run_id).load_manifest())
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
            content = json.loads(task_path.read_text(encoding="utf-8"))
            if isinstance(content, list):
                if not content:
                    return None
                record = content[-1]
            elif isinstance(content, dict):
                record = content
            else:
                return None
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
