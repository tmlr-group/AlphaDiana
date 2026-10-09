"""Report generation from stored evaluation results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from alphadiana.analysis.io.status import (
    INVALID_SCORE_STATUSES,
    VALID_SCORE_STATUS,
    infer_score_status,
)

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig
    from alphadiana.analysis.io.result_store import ResultStore


ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}


def latest_samples(records: Iterable[dict]) -> list[dict]:
    """Last persisted attempt per (task, sample) within one run, never best score.

    Normalize legacy status on copies; callers retain their original attempt
    history. A result file/manifest represents one run, including rescoring.
    """
    from alphadiana.analysis.io.status import normalize_legacy_timeout_zero_record

    selected = {}
    for source in records:
        record = dict(normalize_legacy_timeout_zero_record(source))
        record["score_status"] = infer_score_status(record)
        selected[(record["task_id"], record.get("sample_index", 0))] = record
    return list(selected.values())


@dataclass
class RunStatistics:
    """Shared sample view; renderers keep their distinct units and denominators.

    ``valid`` excludes unmeasured failures. ``metric_results`` also includes
    explicitly zero-contributing empty answers used by the core score report.
    Resource coverage counts selected samples regardless of scoring status.
    Raw attempts are retained in ``history`` and never rewritten on disk.
    """

    history: list[dict]
    results: list[dict]
    manifest: dict
    num_samples: int
    task_ids: list[str]
    expected_task_count: int
    expected_sample_count: int
    by_task: dict[str, list[dict]]
    valid: list[dict]
    metric_results: list[dict]
    correct_by_task: dict[str, int]
    missing_tasks: int
    missing_samples: int

    @classmethod
    def from_records(
        cls,
        records: list[dict],
        manifest: dict | None = None,
        config: "ExperimentConfig | None" = None,
    ) -> "RunStatistics":
        manifest = manifest or {}
        results = latest_samples(records)
        by_task = defaultdict(list)
        for record in results:
            by_task[record["task_id"]].append(record)
        record_k = next(
            (
                r["num_samples"]
                for r in results
                if r.get("num_samples") not in (None, "")
            ),
            None,
        )
        num_samples = int(
            manifest.get("num_samples")
            or record_k
            or (
                getattr(config, "num_samples", 1)
                if config is not None
                else max((len(rows) for rows in by_task.values()), default=1)
            )
        )
        declared_ids = manifest.get("expected_task_ids")
        task_ids = (
            [str(t) for t in declared_ids]
            if isinstance(declared_ids, list) and declared_ids
            else list(by_task)
        )
        expected_tasks = int(manifest.get("expected_task_count", len(task_ids)))
        expected_samples = manifest.get("expected_sample_count")
        if expected_samples is None:
            expected_samples = (
                expected_tasks * num_samples
                if expected_tasks and num_samples
                else len(results)
            )
        expected_samples = int(expected_samples)
        valid = [r for r in results if r["score_status"] == VALID_SCORE_STATUS]
        metric_zeros = [
            r
            for r in results
            if r["score_status"] in {"agent_empty_output", "no_answer"}
            and isinstance(r.get("metadata"), dict)
            and r["metadata"].get("metric_contribution") == 0
            and r.get("score") is not None
            and r.get("correct") is not None
        ]
        correct_by_task = {
            t: sum(
                r["score_status"] == VALID_SCORE_STATUS and r.get("correct") is True
                for r in rows
            )
            for t, rows in by_task.items()
        }
        unknown_tasks = max(expected_tasks - len(task_ids), 0)
        missing_tasks = sum(t not in by_task for t in task_ids) + unknown_tasks
        expected_keys = {(t, i) for t in task_ids for i in range(num_samples)}
        observed_keys = {(r["task_id"], r.get("sample_index", 0)) for r in results}
        # Legacy manifests may declare only a total, without enough task IDs or
        # per-task counts to enumerate all planned keys. Keep that lower bound
        # as well as key-level gaps, which out-of-plan records cannot fill.
        missing_samples = max(
            len(expected_keys - observed_keys) + unknown_tasks * num_samples,
            expected_samples - len(results),
            0,
        )
        return cls(
            list(records),
            results,
            manifest,
            num_samples,
            task_ids,
            expected_tasks,
            expected_samples,
            dict(by_task),
            valid,
            valid + metric_zeros,
            correct_by_task,
            missing_tasks,
            missing_samples,
        )

    @property
    def completed_tasks(self) -> int:
        return len({r["task_id"] for r in self.valid})

    @property
    def correct(self) -> int:
        return sum(self.correct_by_task.values())

    @property
    def errors(self) -> int:
        return sum(r["score_status"] in ERROR_STATUSES for r in self.results)

    @property
    def task_rates(self) -> list[float]:
        return [
            (
                self.correct_by_task.get(t, 0) / self.num_samples
                if self.num_samples > 0
                else 0.0
            )
            for t in self.task_ids
        ]

    @property
    def pass_at_k(self) -> float:
        return (
            sum(self.correct_by_task.get(t, 0) > 0 for t in self.task_ids)
            / self.expected_task_count
            if self.expected_task_count > 0
            else 0.0
        )

    @property
    def avg_at_k(self) -> float:
        # A count-only manifest can declare tasks without IDs. Those missing
        # tasks contribute zero, just like explicitly listed missing tasks.
        return (
            sum(self.task_rates) / self.expected_task_count
            if self.expected_task_count > 0
            else 0.0
        )


@dataclass
class RunSummary:
    """Summary statistics for a single evaluation run."""

    run_id: str
    agent: str
    agent_version: str
    benchmark: str
    total_tasks: int
    completed: int
    failed: int
    accuracy: float
    accuracy_total: float
    mean_score: float
    mean_wall_time_sec: float
    total_tokens: dict
    per_category: dict[str, dict[str, float | int]]
    error_distribution: dict[str, int] = field(default_factory=dict)
    num_samples: int = 1
    pass_at_k: float = 0.0
    avg_at_k: float = 0.0
    per_category_pass_at_k: dict[str, float] = field(default_factory=dict)
    per_category_avg_at_k: dict[str, float] = field(default_factory=dict)
    samples_independent: bool = True
    expected_task_count: int = 0
    expected_sample_count: int = 0
    written_records: int = 0
    valid_scored: int = 0
    invalid_scored: int = 0
    error_records: int = 0
    missing_samples: int = 0
    missing_tasks: int = 0
    strict_report_failed: bool = False
    strict_report_issues: list[str] = field(default_factory=list)
    evaluation_metrics: dict[str, dict] = field(default_factory=dict)
    timestamp: str = ""


def _get_category(r: dict) -> str:
    """Extract category from a result record."""
    task_meta = r.get("task_metadata", {})
    resp_meta = r.get("metadata", {})
    if isinstance(task_meta, dict) and task_meta.get("category"):
        return task_meta["category"]
    if isinstance(resp_meta, dict) and resp_meta.get("category"):
        return resp_meta["category"]
    return "default"


def _manifest_category(task_id: str, manifest: dict) -> str:
    task_meta = manifest.get("task_metadata_by_id", {})
    if isinstance(task_meta, dict):
        task_data = task_meta.get(task_id, {})
        if isinstance(task_data, dict) and task_data.get("category"):
            return str(task_data["category"])
    return "default"


class ReportGenerator:
    """Generates summary reports from stored evaluation results."""

    @staticmethod
    def _infer_from_results(results: list[dict], key: str, fallback: str = "") -> str:
        """Extract a run-level field from the first record that has it."""
        for r in results:
            val = r.get(key)
            if val is not None and val != "":
                return str(val)
        return fallback

    def generate(
        self,
        result_store: "ResultStore",
        config: "ExperimentConfig | None" = None,
    ) -> RunSummary:
        """Load results from the store and compute summary statistics.

        Run-level metadata (run_id, agent_name, etc.) is read from the
        JSONL records first.  *config* is an optional fallback for legacy
        files that lack these fields.
        """
        return self.summarize(
            result_store.load(),
            result_store.load_manifest(),
            run_id=result_store.run_id,
            config=config,
        )

    def summarize(
        self,
        records: list[dict],
        manifest: dict | None = None,
        *,
        run_id: str = "",
        config: "ExperimentConfig | None" = None,
    ) -> RunSummary:
        """Aggregate the latest attempt per sample; retain source history unchanged."""
        base = RunStatistics.from_records(records, manifest, config)
        results, manifest = base.results, base.manifest
        written_records = len(results)

        # Infer run-level metadata from data; fall back to config.
        _cfg = config  # may be None
        run_id = (
            self._infer_from_results(results, "run_id")
            or (getattr(_cfg, "run_id", "") if _cfg else "")
            or run_id
        )
        agent_name = self._infer_from_results(results, "agent_name") or (
            getattr(_cfg, "agent_name", "") if _cfg else "unknown"
        )
        agent_version = self._infer_from_results(results, "agent_version") or (
            getattr(_cfg, "agent_version", "") if _cfg else "unknown"
        )
        benchmark_name = self._infer_from_results(results, "benchmark_name") or (
            getattr(_cfg, "benchmark_name", "") if _cfg else "unknown"
        )

        num_samples = base.num_samples
        samples_independent = bool(manifest.get("samples_independent", True))
        ordered_expected_task_ids = base.task_ids
        expected_task_count, expected_sample_count = (
            base.expected_task_count,
            base.expected_sample_count,
        )
        status_by_key = {
            (r["task_id"], r.get("sample_index", 0)): r["score_status"] for r in results
        }
        metric_results = base.metric_results
        valid_scored = len(base.valid)
        error_statuses = ERROR_STATUSES
        error_records = base.errors
        invalid_scored = sum(
            status in INVALID_SCORE_STATUSES and status not in ERROR_STATUSES
            for status in status_by_key.values()
        )
        completed = valid_scored
        failed = max(expected_sample_count - completed, 0)

        correct_count = sum(1 for r in metric_results if r.get("correct", False))
        accuracy_denominator = len(metric_results)
        accuracy = (
            correct_count / accuracy_denominator if accuracy_denominator > 0 else 0.0
        )
        accuracy_total = (
            correct_count / expected_sample_count if expected_sample_count > 0 else 0.0
        )

        scores = [r.get("score", 0.0) for r in metric_results]
        mean_score = sum(scores) / len(scores) if scores else 0.0

        wall_times = [r.get("wall_time_sec", 0.0) for r in metric_results]
        mean_wall_time = sum(wall_times) / len(wall_times) if wall_times else 0.0

        # Aggregate token usage across all results.
        total_prompt = 0
        total_completion = 0
        for r in results:
            usage = r.get("token_usage", {})
            total_prompt += usage.get("prompt_tokens", 0)
            total_completion += usage.get("completion_tokens", 0)
        total_tokens = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
        }

        # Aggregate optional observations independently of benchmark score.
        metric_values: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for result in results:
            observations = result.get("evaluation_metrics", {})
            if not isinstance(observations, dict):
                continue
            for name, observation in observations.items():
                if not isinstance(observation, dict):
                    continue
                value = observation.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                metric_values[str(name)].append(
                    (float(value), str(observation.get("unit", "")))
                )
        evaluation_metrics = {}
        for name, values_and_units in sorted(metric_values.items()):
            values = [value for value, _ in values_and_units]
            units = {unit for _, unit in values_and_units if unit}
            evaluation_metrics[name] = {
                "count": len(values),
                "mean": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
                "unit": next(iter(units)) if len(units) == 1 else "mixed",
            }

        by_task = base.by_task
        missing_tasks, missing_samples = base.missing_tasks, base.missing_samples

        # Per-category accuracy (based on task metadata "category" field).
        category_correct: dict[str, int] = {}
        category_total: dict[str, int] = {}
        category_sample_totals: dict[str, int] = {}
        for r in results:
            cat = _get_category(r)
            category_sample_totals[cat] = category_sample_totals.get(cat, 0) + 1
        for r in metric_results:
            cat = _manifest_category(r["task_id"], manifest)
            if cat == "default":
                cat = _get_category(r)
            category_total[cat] = category_total.get(cat, 0) + 1
            if r.get("correct", False):
                category_correct[cat] = category_correct.get(cat, 0) + 1

        per_category = {
            cat: {
                "pass_rate": category_correct.get(cat, 0) / category_total[cat],
                "numerator": category_correct.get(cat, 0),
                "denominator": category_total[cat],
                "total_samples": category_sample_totals.get(cat, 0),
            }
            for cat in category_total
        }

        # Compute error distribution from failed tasks
        error_dist: dict[str, int] = {}
        for r in results:
            status = status_by_key[(r["task_id"], r.get("sample_index", 0))]
            if status in error_statuses:
                err = r.get("error")
                etype = (
                    err.get("error_type", "unknown")
                    if isinstance(err, dict)
                    else status
                )
                error_dist[etype] = error_dist.get(etype, 0) + 1

        # Pass@K: fraction of unique tasks where at least 1 sample is correct.
        task_ids_for_summary = ordered_expected_task_ids or list(by_task.keys())
        pass_at_k = base.pass_at_k

        # Per-category pass@k
        cat_tasks_total: dict[str, set[str]] = defaultdict(set)
        cat_tasks_passed: dict[str, set[str]] = defaultdict(set)
        for task_id in task_ids_for_summary:
            samples = by_task.get(task_id, [])
            cat = _manifest_category(task_id, manifest)
            if cat == "default" and samples:
                cat = _get_category(samples[0])
            cat_tasks_total[cat].add(task_id)
            if base.correct_by_task.get(task_id, 0):
                cat_tasks_passed[cat].add(task_id)

        per_category_pass_at_k = {
            cat: len(cat_tasks_passed.get(cat, set())) / len(task_ids)
            for cat, task_ids in cat_tasks_total.items()
        }

        avg_at_k = base.avg_at_k

        # Per-category avg@k
        cat_task_avgs: dict[str, list[float]] = defaultdict(list)
        for task_id in task_ids_for_summary:
            samples = by_task.get(task_id, [])
            cat = _manifest_category(task_id, manifest)
            if cat == "default" and samples:
                cat = _get_category(samples[0])
            n_correct = base.correct_by_task.get(task_id, 0)
            cat_task_avgs[cat].append(
                n_correct / num_samples if num_samples > 0 else 0.0
            )

        per_category_avg_at_k = {
            cat: sum(avgs) / len(avgs) if avgs else 0.0
            for cat, avgs in cat_task_avgs.items()
        }

        strict_report_issues: list[str] = []
        if missing_samples > 0:
            strict_report_issues.append(f"missing_samples={missing_samples}")
        if invalid_scored > 0:
            strict_report_issues.append(f"invalid_scored={invalid_scored}")
        if error_records > 0:
            strict_report_issues.append(f"error_records={error_records}")

        return RunSummary(
            run_id=run_id,
            agent=agent_name,
            agent_version=agent_version,
            benchmark=benchmark_name,
            total_tasks=expected_sample_count,
            completed=completed,
            failed=failed,
            accuracy=accuracy,
            accuracy_total=accuracy_total,
            mean_score=mean_score,
            mean_wall_time_sec=mean_wall_time,
            total_tokens=total_tokens,
            per_category=per_category,
            error_distribution=error_dist,
            num_samples=num_samples,
            pass_at_k=pass_at_k,
            avg_at_k=avg_at_k,
            per_category_pass_at_k=per_category_pass_at_k,
            per_category_avg_at_k=per_category_avg_at_k,
            samples_independent=samples_independent,
            expected_task_count=expected_task_count,
            expected_sample_count=expected_sample_count,
            written_records=written_records,
            valid_scored=valid_scored,
            invalid_scored=invalid_scored,
            error_records=error_records,
            missing_samples=missing_samples,
            missing_tasks=missing_tasks,
            strict_report_failed=bool(strict_report_issues),
            strict_report_issues=strict_report_issues,
            evaluation_metrics=evaluation_metrics,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _get_category(r: dict) -> str:
        return _get_category(r)

    def to_markdown(self, summary: RunSummary) -> str:
        """Generate a markdown report string with a summary table."""
        pass_label = (
            f"Pass@{summary.num_samples}"
            if summary.samples_independent
            else f"Sequential Any@{summary.num_samples}"
        )
        avg_label = (
            f"Avg@{summary.num_samples}"
            if summary.samples_independent
            else f"Sequential Mean@{summary.num_samples}"
        )
        lines = [
            f"# Evaluation Report: {summary.run_id}",
            "",
            f"**Timestamp:** {summary.timestamp}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Agent | {summary.agent} |",
            f"| Agent Version | {summary.agent_version} |",
            f"| Benchmark | {summary.benchmark} |",
            f"| Total Tasks | {summary.total_tasks} |",
            f"| Expected Tasks | {summary.expected_task_count} |",
            f"| Expected Samples | {summary.expected_sample_count} |",
            f"| Written Records | {summary.written_records} |",
            f"| Completed | {summary.completed} |",
            f"| Failed | {summary.failed} |",
            f"| Valid Scored | {summary.valid_scored} |",
            f"| Invalid Scored | {summary.invalid_scored} |",
            f"| Error Records | {summary.error_records} |",
            f"| Missing Tasks | {summary.missing_tasks} |",
            f"| Missing Samples | {summary.missing_samples} |",
            f"| Num Samples (k) | {summary.num_samples} |",
            f"| Accuracy (completed) | {summary.accuracy:.4f} |",
            f"| Accuracy (total) | {summary.accuracy_total:.4f} |",
            f"| {pass_label} | {summary.pass_at_k:.4f} |",
            f"| {avg_label} | {summary.avg_at_k:.4f} |",
            f"| Mean Score | {summary.mean_score:.4f} |",
            f"| Mean Wall Time (s) | {summary.mean_wall_time_sec:.2f} |",
            f"| Prompt Tokens | {summary.total_tokens.get('prompt_tokens', 0)} |",
            f"| Completion Tokens | {summary.total_tokens.get('completion_tokens', 0)} |",
            "",
        ]

        if summary.per_category:
            lines.extend(
                [
                    "## Per-Category Accuracy",
                    "",
                    "| Category | Accuracy |",
                    "|----------|----------|",
                ]
            )
            for cat, stats in sorted(summary.per_category.items()):
                lines.append(f"| {cat} | {float(stats['pass_rate']):.4f} |")
            lines.append("")

        if summary.evaluation_metrics:
            lines.extend(
                [
                    "## Observational Metrics",
                    "",
                    "These post-run measurements do not affect benchmark score.",
                    "",
                    "| Metric | Count | Mean | Min | Max | Unit |",
                    "|--------|------:|-----:|----:|----:|------|",
                ]
            )
            for name, metric in sorted(summary.evaluation_metrics.items()):
                lines.append(
                    f"| {name} | {metric['count']} | {metric['mean']:.3f} | "
                    f"{metric['min']:.3f} | {metric['max']:.3f} | {metric['unit']} |"
                )
            lines.append("")

        if summary.num_samples > 1 and summary.per_category_pass_at_k:
            lines.extend(
                [
                    f"## Per-Category {pass_label} / {avg_label}",
                    "",
                    f"| Category | {pass_label} | {avg_label} |",
                    "|----------|----------|----------|",
                ]
            )
            for cat in sorted(summary.per_category_pass_at_k.keys()):
                pk = summary.per_category_pass_at_k.get(cat, 0.0)
                ak = summary.per_category_avg_at_k.get(cat, 0.0)
                lines.append(f"| {cat} | {pk:.4f} | {ak:.4f} |")
            lines.append("")

        if summary.error_distribution:
            lines.extend(
                [
                    "## Error Distribution",
                    "",
                    "| Error Type | Count |",
                    "|------------|-------|",
                ]
            )
            for error_type, count in sorted(summary.error_distribution.items()):
                lines.append(f"| {error_type} | {count} |")
            lines.append("")

        return "\n".join(lines)
