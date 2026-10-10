"""Run report projections and declarative table/figure exports. Sample validity and selection belong to results.statistics."""

from __future__ import annotations

import json
import re
import textwrap
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alphadiana.results.statistics import ERROR_STATUSES, RunStatistics
from alphadiana.results.status import (
    INVALID_SCORE_STATUSES,
)

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig
    from alphadiana.results.store import ResultStore


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
        accuracy = base.metric_accuracy
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


def _safe_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError(f"Unsafe export name: {name!r}")
    return name


def export_report(
    report: dict, output: Path, *, formats: Sequence[str]
) -> dict[str, Any]:
    if not formats or any(f not in {"pdf", "svg", "png"} for f in formats):
        raise ValueError("Formats must be pdf, svg or png")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Use a new or empty export directory: {output}")
    # Matplotlib stays optional for users needing only execution/structured data.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    )
    exported, skipped = [], []
    style = {
        # Match the paper's compact gray/blue figures without requiring bundled fonts.
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Arimo", "DejaVu Sans"],
        "font.size": 8,
        "axes.titlesize": 9,
        "axes.labelsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "text.color": "#29333E",
        "axes.labelcolor": "#29333E",
        "axes.edgecolor": "#A7ADB5",
        "axes.linewidth": 0.55,
        "xtick.color": "#29333E",
        "ytick.color": "#29333E",
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "savefig.dpi": 300,
    }
    with plt.rc_context(style):
        for name, analysis in report["analyses"].items():
            _safe_name(name)
            for spec in analysis["figures"]:
                stem = f"{name}-{_safe_name(spec['name'])}"
                rows = analysis["tables"][spec["table"]]
                rows = [
                    r
                    for r in rows
                    if r.get(spec["x"]) is not None and r.get(spec["y"]) is not None
                ]
                if not rows:
                    skipped.append(
                        {
                            "figure": stem,
                            "reason": "No applicable measured observations",
                        }
                    )
                    continue
                fig = _render(plt, spec, rows)
                try:
                    for fmt in formats:
                        path = output / f"{stem}.{fmt}"
                        fig.savefig(path, bbox_inches="tight")
                        exported.append(path.name)
                    (output / f"{stem}.data.json").write_text(
                        json.dumps(rows, indent=2, allow_nan=False)
                    )
                finally:
                    plt.close(fig)
    receipt = {"files": exported, "skipped": skipped, "report": "report.json"}
    (output / "export.json").write_text(json.dumps(receipt, indent=2))
    return receipt


def _render(plt, spec: dict, rows: list[dict]):
    groups = (
        list(dict.fromkeys(str(r.get(spec["group"], "")) for r in rows))
        if spec.get("group")
        else [""]
    )
    x, y = spec["x"], spec["y"]
    colors = ["#527DA5", "#0A366B", "#A2A6AC", "#B7CDDE", "#357451", "#B25353"]
    # Presentation identities only: unknown plugin series use the same color cycle.
    paper_colors = {
        "direct": "#A2A6AC",
        "direct_llm": "#A2A6AC",
        "swe-agent": "#A2A6AC",
        "sweagent": "#A2A6AC",
        "terminus2": "#A2A6AC",
        "openclaw": "#B7CDDE",
        "zeroclaw": "#527DA5",
        "opencode": "#0A366B",
        "understanding": "#E41A1C",
        "planning": "#377EB8",
        "reasoning": "#4DAF4A",
        "tool use": "#984EA3",
        "verification": "#FF7F00",
        "finalization": "#A65628",
    }

    def series_color(label, index):
        name = str(label).split(" / ", 1)[0].casefold()
        return paper_colors.get(name, colors[index % len(colors)])

    if spec["kind"] == "matrix":
        xlabels = list(dict.fromkeys(str(r[x]) for r in rows))
        ylabels = list(dict.fromkeys(str(r[y]) for r in rows))
        value = spec.get("value", "count")
        cells = {}
        for row in rows:
            key = str(row[x]), str(row[y])
            if key in cells:
                raise ValueError("Matrix requires one measured value per cell")
            cells[key] = row[value]
        matrix = [[cells.get((a, b), float("nan")) for a in xlabels] for b in ylabels]
        fig, ax = plt.subplots(
            figsize=(max(5.5, len(xlabels) * 0.6 + 3), max(2.2, len(ylabels) * 0.28)),
            layout="constrained",
        )
        limits = spec.get("color_limits") or (None, None)
        im = ax.imshow(
            matrix, cmap="Blues", aspect="auto", vmin=limits[0], vmax=limits[1]
        )
        ax.set_xticks(
            range(len(xlabels)),
            [textwrap.fill(v, 18) for v in xlabels],
            rotation=30,
            ha="right",
        )
        ax.set_yticks(range(len(ylabels)), [textwrap.fill(v, 28) for v in ylabels])
        ax.set(title=spec["title"], xlabel=spec["xlabel"], ylabel=spec["ylabel"])
        fig.colorbar(im, ax=ax, label=spec.get("value_label", value))
        return fig
    if spec["kind"] == "heatmap":
        import math

        columns = min(2, len(groups))
        rows_count = math.ceil(len(groups) / columns)
        fig, axes = plt.subplots(
            rows_count,
            columns,
            figsize=(4.4 * columns, 3.5 * rows_count),
            squeeze=False,
            layout="constrained",
        )
        vmax = max(float(r.get("count", 0)) for r in rows) or 1
        for group, ax in zip(groups, axes.flat):
            subset = [
                r
                for r in rows
                if not spec.get("group") or str(r.get(spec["group"], "")) == group
            ]
            labels = sorted({str(r[x]) for r in subset} | {str(r[y]) for r in subset})
            matrix = [
                [
                    sum(
                        float(r.get("count", 0))
                        for r in subset
                        if str(r[y]) == a and str(r[x]) == b
                    )
                    for b in labels
                ]
                for a in labels
            ]
            im = ax.imshow(matrix, cmap="Blues", vmin=0, vmax=vmax)
            ax.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
            ax.set_yticks(range(len(labels)), labels)
            ax.set(
                title="\n".join(textwrap.wrap(group or spec["title"], width=32)),
                xlabel=spec["xlabel"],
                ylabel=spec["ylabel"],
            )
            fig.colorbar(im, ax=ax, label="Transitions", shrink=0.75)
        for ax in list(axes.flat)[len(groups) :]:
            ax.set_visible(False)
        return fig
    categories = list(dict.fromkeys(str(r[x]) for r in rows))
    # Long or numerous run labels collide on a shared horizontal category axis.
    # Reuse the horizontal renderer without changing saved measurements/specs.
    if spec["kind"] == "bar" and (
        len(categories) > 8 or any(len(label) > 18 for label in categories)
    ):
        return _render(plt, {**spec, "kind": "barh"}, rows)
    category_labels = [
        textwrap.fill(c, 24)
        if spec["kind"] == "stacked_barh"
        else textwrap.fill(c.replace("_", " "), width=18)
        for c in categories
    ]
    # Wrapped run names need enough vertical space between neighboring ticks.
    height = (
        max(
            2.1,
            0.8
            + len(categories)
            * max(
                0.38 * (len(groups) if spec["kind"] == "barh" else 1),
                0.16 * max(len(label.splitlines()) for label in category_labels) + 0.08,
            ),
        )
        if spec["kind"] in {"barh", "stacked_barh"}
        else 2.6
    )
    fig, ax = plt.subplots(figsize=(5.5, height), layout="constrained")
    stacked = [0.0] * len(categories)
    for index, group in enumerate(groups):
        subset = [
            r
            for r in rows
            if not spec.get("group") or str(r.get(spec["group"], "")) == group
        ]
        color = series_color(group, index)
        if spec["kind"] == "stacked_barh":
            values = {str(row[x]): row[y] for row in subset}
            if len(values) != len(subset):
                raise ValueError("Stacked bars require one value per category/series")
            heights = [values.get(category, 0) for category in categories]
            if any(value < 0 for value in heights):
                raise ValueError("Stacked composition requires nonnegative values")
            ax.barh(
                range(len(categories)),
                heights,
                left=stacked,
                height=0.7,
                color=color,
                edgecolor="white",
                linewidth=0.4,
                label=group,
            )
            stacked = [left + value for left, value in zip(stacked, heights)]
            ax.set_yticks(range(len(categories)), category_labels)
        elif spec["kind"] in {"bar", "barh"}:
            width = 0.8 / len(groups)
            positions = [
                categories.index(str(r[x])) - 0.4 + width * (index + 0.5)
                for r in subset
            ]
            values = [r[y] for r in subset]
            bar_colors = (
                color
                if spec.get("group")
                else [
                    series_color(r.get("agent", r[x]), categories.index(str(r[x])))
                    for r in subset
                ]
            )
            draw = ax.barh if spec["kind"] == "barh" else ax.bar
            bars = draw(
                positions,
                values,
                width,
                color=bar_colors,
                label=group or None,
                edgecolor="white",
                linewidth=0.5,
            )
            labels = category_labels
            if spec["kind"] == "barh":
                ax.set_yticks(range(len(categories)), labels)
                if not spec.get("group"):
                    ax.bar_label(bars, fmt="%.2g", padding=3, fontsize=8)
            else:
                ax.set_xticks(range(len(categories)), labels, rotation=0)
        elif spec["kind"] in {"scatter", "line"}:
            if spec["kind"] == "scatter":
                ax.scatter(
                    [r[x] for r in subset],
                    [r[y] for r in subset],
                    label=group or None,
                    color=color,
                    marker=("o", "s", "^", "D")[index % 4],
                    alpha=0.65,
                    s=12,
                )
                if spec.get("ellipse") and len(subset) >= 10:
                    import numpy as np
                    from matplotlib.patches import Ellipse

                    points = np.array([[r[x], r[y]] for r in subset], dtype=float)
                    covariance = np.cov(points, rowvar=False)
                    if np.isfinite(covariance).all():
                        values, vectors = np.linalg.eigh(covariance)
                        order = values.argsort()[::-1]
                        values, vectors = values[order], vectors[:, order]
                        angle = np.degrees(np.arctan2(vectors[1, 0], vectors[0, 0]))
                        center = points.mean(axis=0)
                        axes = 4 * np.sqrt(np.maximum(values, 0))
                        ax.add_patch(
                            Ellipse(
                                center,
                                axes[0],
                                axes[1],
                                angle=angle,
                                facecolor=color,
                                edgecolor=color,
                                alpha=0.15,
                            )
                        )
                        ax.autoscale_view()

            else:
                if spec.get("lower") and spec.get("upper"):
                    ax.fill_between(
                        [r[x] for r in subset],
                        [r[spec["lower"]] for r in subset],
                        [r[spec["upper"]] for r in subset],
                        color=color,
                        alpha=0.18,
                        linewidth=0,
                    )
                ax.plot(
                    [r[x] for r in subset],
                    [r[y] for r in subset],
                    label=group or None,
                    color=color,
                    marker=("o", "s", "^", "D")[index % 4],
                    markersize=2.5,
                    linewidth=1.0,
                    linestyle=("-", "--", "-.", ":")[index % 4],
                    # Bound marker density for long series, including log axes.
                    # Every measured point remains in the line and data export.
                    markevery=max(1, (len(subset) + 23) // 24),
                )
        else:
            plt.close(fig)
            raise ValueError(f"Unsupported plot kind: {spec['kind']}")
    if spec["kind"] in {"barh", "stacked_barh"}:
        ax.margins(x=0.15)
        ax.set_xlim(left=min(0, min(r[y] for r in rows) * 1.15))
        ax.invert_yaxis()
        ax.set(xlabel=spec["ylabel"], ylabel=spec["xlabel"])
    else:
        if spec["kind"] == "bar":
            # Keep autoscaling active until every group's bars have been added.
            ax.set_ylim(bottom=min(0, min(r[y] for r in rows) * 1.05))
        else:
            ax.set_xscale(spec.get("xscale", "linear"))
        ax.set(xlabel=spec["xlabel"], ylabel=spec["ylabel"])
    ax.set_title(spec["title"], loc="left", pad=8)
    ax.grid(
        axis="x" if spec["kind"] in {"barh", "stacked_barh"} else "y",
        color="#E9EDF1",
        linewidth=0.5,
    )
    ax.tick_params(length=2, width=0.5)
    ax.set_axisbelow(True)
    if spec.get("group"):
        handles, labels = ax.get_legend_handles_labels()
        fig.legend(
            handles,
            [textwrap.fill(label, 30) for label in labels],
            loc="outside upper center",
            frameon=False,
            fontsize=8,
            ncol=min(len(groups), 4 if max(map(len, labels)) <= 16 else 3),
            handlelength=1.5,
            columnspacing=1.2,
        )
    return fig
