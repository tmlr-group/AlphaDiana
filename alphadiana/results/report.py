"""Report generation from stored evaluation results."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from alphadiana.results.status import INVALID_SCORE_STATUSES, VALID_SCORE_STATUS, infer_score_status

if TYPE_CHECKING:
    from alphadiana.config.experiment_config import ExperimentConfig
    from alphadiana.results.result_store import ResultStore


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
        results = result_store.load()
        manifest = result_store.load_manifest()
        written_records = len(results)

        # Infer run-level metadata from data; fall back to config.
        _cfg = config  # may be None
        run_id = self._infer_from_results(results, "run_id") or (
            getattr(_cfg, "run_id", "") if _cfg else "") or result_store.run_id
        agent_name = self._infer_from_results(results, "agent_name") or (
            getattr(_cfg, "agent_name", "") if _cfg else "unknown")
        agent_version = self._infer_from_results(results, "agent_version") or (
            getattr(_cfg, "agent_version", "") if _cfg else "unknown")
        benchmark_name = self._infer_from_results(results, "benchmark_name") or (
            getattr(_cfg, "benchmark_name", "") if _cfg else "unknown")

        # num_samples: prefer manifest, then data, then config, then infer.
        manifest_num_samples = manifest.get("num_samples")
        num_samples_raw = self._infer_from_results(results, "num_samples")
        if manifest_num_samples:
            num_samples = int(manifest_num_samples)
        elif num_samples_raw:
            num_samples = int(num_samples_raw)
        elif _cfg is not None:
            num_samples = getattr(_cfg, "num_samples", 1)
        else:
            by_task_tmp: dict[str, int] = {}
            for r in results:
                tid = r.get("task_id", "")
                by_task_tmp[tid] = by_task_tmp.get(tid, 0) + 1
            num_samples = max(by_task_tmp.values(), default=1)

        expected_task_ids = manifest.get("expected_task_ids")
        if isinstance(expected_task_ids, list) and expected_task_ids:
            ordered_expected_task_ids = [str(task_id) for task_id in expected_task_ids]
        else:
            ordered_expected_task_ids = list(dict.fromkeys(r.get("task_id", "") for r in results if r.get("task_id")))

        expected_task_count = int(
            manifest.get("expected_task_count", len(ordered_expected_task_ids))
        )
        if manifest.get("expected_sample_count") is not None:
            expected_sample_count = int(manifest["expected_sample_count"])
        elif expected_task_count and num_samples:
            expected_sample_count = expected_task_count * num_samples
        else:
            expected_sample_count = written_records

        status_by_key = {
            (r["task_id"], r.get("sample_index", 0)): infer_score_status(r)
            for r in results
        }
        valid_results = [
            r for r in results
            if status_by_key[(r["task_id"], r.get("sample_index", 0))] == VALID_SCORE_STATUS
        ]
        valid_scored = len(valid_results)
        error_statuses = {"agent_error", "provider_error", "runtime_error", "scorer_error"}
        error_records = sum(
            1 for status in status_by_key.values()
            if status in error_statuses
        )
        invalid_scored = sum(
            1 for status in status_by_key.values()
            if status in INVALID_SCORE_STATUSES and status not in error_statuses
        )
        completed = valid_scored
        failed = max(expected_sample_count - completed, 0)

        correct_count = sum(1 for r in valid_results if r.get("correct", False))
        accuracy = correct_count / completed if completed > 0 else 0.0
        accuracy_total = correct_count / expected_sample_count if expected_sample_count > 0 else 0.0

        scores = [r.get("score", 0.0) for r in valid_results]
        mean_score = sum(scores) / len(scores) if scores else 0.0

        wall_times = [r.get("wall_time_sec", 0.0) for r in valid_results]
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

        # Group results by task_id for per-category and pass@k computation.
        by_task: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            by_task[r["task_id"]].append(r)

        observed_task_ids = {task_id for task_id in by_task}
        if ordered_expected_task_ids:
            missing_tasks = sum(1 for task_id in ordered_expected_task_ids if task_id not in observed_task_ids)
            expected_sample_keys = {
                (task_id, sample_index)
                for task_id in ordered_expected_task_ids
                for sample_index in range(num_samples)
            }
            observed_sample_keys = {
                (r["task_id"], r.get("sample_index", 0))
                for r in results
                if r.get("task_id") in set(ordered_expected_task_ids)
            }
            missing_samples = len(expected_sample_keys - observed_sample_keys)
        else:
            missing_tasks = max(expected_task_count - len(observed_task_ids), 0)
            missing_samples = max(expected_sample_count - written_records, 0)

        # Per-category accuracy (based on task metadata "category" field).
        category_correct: dict[str, int] = {}
        category_total: dict[str, int] = {}
        category_sample_totals: dict[str, int] = {}
        for r in results:
            cat = _get_category(r)
            category_sample_totals[cat] = category_sample_totals.get(cat, 0) + 1
        for r in valid_results:
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
                etype = err.get("error_type", "unknown") if isinstance(err, dict) else status
                error_dist[etype] = error_dist.get(etype, 0) + 1

        # Pass@K: fraction of unique tasks where at least 1 sample is correct.
        task_ids_for_summary = ordered_expected_task_ids or list(by_task.keys())
        num_unique_tasks = expected_task_count or len(task_ids_for_summary)
        tasks_passed = sum(
            1 for task_id in task_ids_for_summary
            if any(
                status_by_key[(s["task_id"], s.get("sample_index", 0))] == VALID_SCORE_STATUS
                and s.get("correct", False)
                for s in by_task.get(task_id, [])
            )
        )
        pass_at_k = tasks_passed / num_unique_tasks if num_unique_tasks > 0 else 0.0

        # Per-category pass@k
        cat_tasks_total: dict[str, set[str]] = defaultdict(set)
        cat_tasks_passed: dict[str, set[str]] = defaultdict(set)
        for task_id in task_ids_for_summary:
            samples = by_task.get(task_id, [])
            cat = _manifest_category(task_id, manifest)
            if cat == "default" and samples:
                cat = _get_category(samples[0])
            cat_tasks_total[cat].add(task_id)
            if any(
                status_by_key[(s["task_id"], s.get("sample_index", 0))] == VALID_SCORE_STATUS
                and s.get("correct", False)
                for s in samples
            ):
                cat_tasks_passed[cat].add(task_id)

        per_category_pass_at_k = {
            cat: len(cat_tasks_passed.get(cat, set())) / len(task_ids)
            for cat, task_ids in cat_tasks_total.items()
        }

        # Avg@K: per-task average correctness rate, then averaged across tasks.
        # For each task, compute (number of correct samples) / (number of total samples).
        task_avg_scores: list[float] = []
        for task_id in task_ids_for_summary:
            samples = by_task.get(task_id, [])
            n_correct = sum(
                1 for s in samples
                if status_by_key[(s["task_id"], s.get("sample_index", 0))] == VALID_SCORE_STATUS
                and s.get("correct", False)
            )
            task_avg_scores.append(n_correct / num_samples if num_samples > 0 else 0.0)
        avg_at_k = sum(task_avg_scores) / len(task_avg_scores) if task_avg_scores else 0.0

        # Per-category avg@k
        cat_task_avgs: dict[str, list[float]] = defaultdict(list)
        for task_id in task_ids_for_summary:
            samples = by_task.get(task_id, [])
            cat = _manifest_category(task_id, manifest)
            if cat == "default" and samples:
                cat = _get_category(samples[0])
            n_correct = sum(
                1 for s in samples
                if status_by_key[(s["task_id"], s.get("sample_index", 0))] == VALID_SCORE_STATUS
                and s.get("correct", False)
            )
            cat_task_avgs[cat].append(n_correct / num_samples if num_samples > 0 else 0.0)

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
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _get_category(r: dict) -> str:
        return _get_category(r)

    def to_markdown(self, summary: RunSummary) -> str:
        """Generate a markdown report string with a summary table."""
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
            *(
                [f"| Resolve Rate | {summary.accuracy_total:.4f} |"]
                if summary.benchmark == "swebench_pro_os"
                else []
            ),
            f"| Pass@{summary.num_samples} | {summary.pass_at_k:.4f} |",
            f"| Avg@{summary.num_samples} | {summary.avg_at_k:.4f} |",
            f"| Mean Score | {summary.mean_score:.4f} |",
            f"| Mean Wall Time (s) | {summary.mean_wall_time_sec:.2f} |",
            f"| Prompt Tokens | {summary.total_tokens.get('prompt_tokens', 0)} |",
            f"| Completion Tokens | {summary.total_tokens.get('completion_tokens', 0)} |",
            "",
        ]

        if summary.per_category:
            lines.extend([
                "## Per-Category Accuracy",
                "",
                "| Category | Accuracy |",
                "|----------|----------|",
            ])
            for cat, stats in sorted(summary.per_category.items()):
                lines.append(f"| {cat} | {float(stats['pass_rate']):.4f} |")
            lines.append("")

        if summary.num_samples > 1 and summary.per_category_pass_at_k:
            lines.extend([
                f"## Per-Category Pass@{summary.num_samples} / Avg@{summary.num_samples}",
                "",
                f"| Category | Pass@{summary.num_samples} | Avg@{summary.num_samples} |",
                "|----------|----------|----------|",
            ])
            for cat in sorted(summary.per_category_pass_at_k.keys()):
                pk = summary.per_category_pass_at_k.get(cat, 0.0)
                ak = summary.per_category_avg_at_k.get(cat, 0.0)
                lines.append(f"| {cat} | {pk:.4f} | {ak:.4f} |")
            lines.append("")

        if summary.error_distribution:
            lines.extend([
                "## Error Distribution",
                "",
                "| Error Type | Count |",
                "|------------|-------|",
            ])
            for error_type, count in sorted(summary.error_distribution.items()):
                lines.append(f"| {error_type} | {count} |")
            lines.append("")

        return "\n".join(lines)
