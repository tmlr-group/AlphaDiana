"""Report rendering and artifact writers for action-space analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from alphadiana.results.reader import RunBundle
from alphadiana.results.statistics import ERROR_STATUSES, compute_reliability_summary
from alphadiana.results.status import VALID_SCORE_STATUS, infer_score_status


def _field_order(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    ordered = list(rows[0])
    extras = sorted({key for row in rows for key in row if key not in ordered})
    return ordered + extras


def write_feature_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write action feature rows with deterministic columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = _field_order(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(payload: dict[str, Any], path: Path) -> None:
    """Write stable JSON for machine-readable action-space artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _fmt_ratio(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _record_counts(bundle: RunBundle) -> tuple[int, int]:
    statuses = [infer_score_status(record) for record in bundle.records]
    valid_scored = sum(1 for status in statuses if status == VALID_SCORE_STATUS)
    error_records = sum(1 for status in statuses if status in ERROR_STATUSES)
    return valid_scored, error_records


def _expected_samples(bundle: RunBundle, reliability: dict[str, Any]) -> int:
    """Keep the legacy export while using the canonical planned sample count."""
    if "expected_sample_count" in reliability:
        return int(reliability["expected_sample_count"])
    return int(
        compute_reliability_summary(bundle.records, bundle.manifest)[
            "expected_sample_count"
        ]
    )


def _cluster_lines(view_name: str, view: dict[str, Any]) -> list[str]:
    lines = [
        f"### {view_name}",
        "",
        "- Data view: `all_attempts` (includes superseded attempts)",
        f"- Method: `{view.get('method', '')}`",
        f"- Distance: `{view.get('distance', '')}`",
        f"- Rows: {view.get('n_rows', 0)}",
        f"- Denominator: {view.get('n_rows', 0)} persisted attempts in this cluster view",
        f"- Feature Columns: {', '.join(str(column) for column in view.get('feature_columns', []))}",
        "",
        "| Cluster | Rows | Score Status | Correctness | Entropy Mean Avg | Exemplars |",
        "| --- | ---: | --- | --- | ---: | --- |",
    ]
    clusters = view.get("clusters") if isinstance(view.get("clusters"), list) else []
    if not clusters:
        lines.append("| n/a | 0 | n/a | n/a | 0.0000 | n/a |")
        lines.append("")
        return lines

    for cluster in clusters:
        score_counts = (
            ", ".join(
                f"{key}={value}"
                for key, value in (cluster.get("score_status_counts") or {}).items()
            )
            or "n/a"
        )
        correct_counts = (
            ", ".join(
                f"{key}={value}"
                for key, value in (cluster.get("correct_counts") or {}).items()
            )
            or "n/a"
        )
        exemplars = (
            ", ".join(
                f"`{task_id}`" for task_id in cluster.get("exemplar_task_ids", [])
            )
            or "n/a"
        )
        lines.append(
            f"| {cluster.get('cluster_id')} | {cluster.get('n', 0)} | "
            f"{score_counts} | {correct_counts} | {_fmt_ratio(cluster.get('entropy_mean_avg'))} | {exemplars} |"
        )
    lines.append("")
    return lines


def render_markdown_report(
    bundle: RunBundle,
    reliability: dict[str, Any],
    clusters: dict[str, Any],
    output_files: dict[str, str],
) -> str:
    """Render a reviewer-readable action-space analysis report."""
    expected_samples = _expected_samples(bundle, reliability)
    written_records = len(bundle.records)
    valid_scored, error_records = _record_counts(bundle)
    missing_samples = int(reliability.get("missing_samples") or 0)
    denominators = reliability.get("denominators")
    if denominators is None:
        denominators = compute_reliability_summary(bundle.records, bundle.manifest)[
            "denominators"
        ]

    lines = [
        f"# Action Space Analysis: {bundle.run_id}",
        "",
        "## Dataset Caveat",
        "",
        "| Field | Value |",
        "| --- | ---: |",
        f"| Run ID | `{bundle.run_id}` |",
        f"| Expected Samples | {expected_samples} |",
        f"| Missing Samples | {missing_samples} |",
        "",
        "All attempts retains every persisted record, including superseded retries. "
        "Latest samples selects the last persisted attempt per `(task_id, sample_index)`, never the best score.",
        "",
        "| Count | All attempts | Latest samples |",
        "| --- | ---: | ---: |",
        f"| Written Records | {written_records} | {reliability['written_records']} |",
        f"| Valid Scored | {valid_scored} | {reliability['valid_scored']} |",
        f"| Error Records | {error_records} | {reliability['error_records']} |",
        "",
    ]
    if missing_samples > 0:
        lines.extend(
            [
                "This run is incomplete; missing samples are reported separately and are not silently converted into model failures.",
                "Coverage and missing counts use latest samples; historical retries cannot fill missing samples.",
                "",
            ]
        )
    else:
        lines.extend(["No missing samples were detected from the run manifest.", ""])

    lines.extend(
        [
            "## Reliability Metrics (latest samples)",
            "",
            f"k = {reliability['num_samples']}. Avg@k averages correct/k over planned tasks; Pass^k averages (correct/k)^k when available.",
            "",
            "| Metric | Value | Denominator |",
            "| --- | ---: | --- |",
            f"| Observed Valid Accuracy | {_fmt_ratio(reliability.get('observed_valid_accuracy'))} | {denominators['observed_valid_accuracy']} valid scored samples |",
            f"| Expected-Sample Accuracy | {_fmt_ratio(reliability.get('expected_sample_accuracy'))} | {denominators['expected_sample_accuracy']} planned samples |",
            f"| Coverage | {_fmt_ratio(reliability.get('coverage'))} | {denominators['coverage']} planned samples |",
            f"| Pass@k | {_fmt_ratio(reliability.get('pass_at_k'))} | {denominators['pass_at_k']} planned tasks |",
            f"| Avg@k | {_fmt_ratio(reliability.get('avg_at_k'))} | {denominators['avg_at_k']} planned tasks |",
            f"| Pass^k Available | {bool(reliability.get('pass_power_k_available'))} | n/a |",
            f"| Pass^k | {_fmt_ratio(reliability.get('pass_power_k'))} | {denominators['pass_power_k']} planned tasks |",
            "",
            "## Cluster Views (all attempts)",
            "",
            "Feature CSV and clustering retain historical attempts. Each cluster's counts refer to its selected attempt rows, not the latest-sample reliability denominator.",
            "",
        ]
    )
    for view_name in ("valid_scored_only", "all_records_status"):
        view = clusters.get(view_name, {}) if isinstance(clusters, dict) else {}
        lines.extend(_cluster_lines(view_name, view))

    lines.extend(
        [
            "## Output Files",
            "",
            "| Artifact | Path |",
            "| --- | --- |",
        ]
    )
    for name in sorted(output_files):
        lines.append(f"| {name} | `{output_files[name]}` |")
    lines.append("")
    return "\n".join(lines)
