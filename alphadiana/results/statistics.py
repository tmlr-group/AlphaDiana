"""Latest-attempt selection and canonical sample/coverage/reliability statistics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from alphadiana.results.status import (
    VALID_SCORE_STATUS,
    infer_score_status,
)

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig
ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}


def latest_samples(records: Iterable[dict]) -> list[dict]:
    """Last persisted attempt per (task, sample) within one run, never best score.

    Normalize legacy status on copies; callers retain their original attempt
    history. A result file/manifest represents one run, including rescoring.
    """
    from alphadiana.results.status import normalize_legacy_timeout_zero_record

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


def compute_reliability_summary(
    records: list[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Keep observed-valid and planned-sample accuracy as distinct views."""
    base = RunStatistics.from_records(records, manifest)
    n, expected, k = len(base.results), base.expected_sample_count, base.num_samples
    power_available = (
        k > 1
        and base.missing_samples == 0
        and all(len(base.by_task.get(t, [])) >= k for t in base.task_ids)
    )
    return {
        "data_view": "latest_samples",
        "selection": "last_persisted_attempt_per_task_sample",
        "denominators": {
            "observed_valid_accuracy": len(base.valid),
            "expected_sample_accuracy": expected,
            "coverage": expected,
            "pass_at_k": base.expected_task_count,
            "avg_at_k": base.expected_task_count,
            "pass_power_k": base.expected_task_count,
        },
        "num_samples": k,
        "expected_sample_count": expected,
        "written_records": n,
        "valid_scored": len(base.valid),
        "observed_valid_accuracy": (
            base.correct / len(base.valid) if base.valid else 0.0
        ),
        "expected_sample_accuracy": base.correct / expected if expected > 0 else 0.0,
        "coverage": n / expected if expected > 0 else 0.0,
        "missing_samples": base.missing_samples,
        "error_records": base.errors,
        "pass_at_k": base.pass_at_k,
        "avg_at_k": base.avg_at_k,
        "pass_power_k_available": power_available,
        "pass_power_k": (
            sum(rate**k for rate in base.task_rates) / base.expected_task_count
            if power_available and base.expected_task_count > 0
            else None
        ),
    }
