#!/usr/bin/env python3
"""Offline behavior insight analysis CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from alphadiana.analysis.actions.keyword_baseline import extract_action_event_rows
from alphadiana.analysis.actions.trajectories import compute_outcome_conditioned_metrics
from alphadiana.analysis.insights.cases import select_insight_case_anchors
from alphadiana.analysis.insights.claims import build_insight_claims
from alphadiana.analysis.insights.corpus import (
    build_denominator_ledger,
    load_insight_corpus_specs,
    load_selected_task_records,
)
from alphadiana.analysis.insights.reports import write_behavior_insight_outputs
from alphadiana.results.reader import load_run_bundle

DEFAULT_OUTPUT_DIR = Path("results/phase15_behavior_insights")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--measurement-summary",
        type=Path,
        default=None,
        help="Optional analyze_tools measurement_summary.json to fold into insight measurements.",
    )
    parser.add_argument(
        "--include-hf-synced",
        dest="include_hf_synced",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-include-hf-synced", dest="include_hf_synced", action="store_false"
    )
    parser.add_argument("--stdout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    measurement_summary = _load_measurement_summary(args.measurement_summary)
    specs = load_insight_corpus_specs(
        args.results_dir, include_hf_synced=args.include_hf_synced
    )
    denominator_rows = build_denominator_ledger(specs)
    denominator_by_label = {
        str(row.get("corpus_label") or ""): row for row in denominator_rows
    }

    selected_records: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    for spec in specs:
        row = denominator_by_label.get(spec.label)
        if not row or row.get("status") != "validated_records":
            continue
        bundle = load_run_bundle(spec.results_dir, spec.run_id)
        spec_records = [
            _record_with_spec_defaults(record, spec)
            for record in load_selected_task_records(bundle)
        ]
        selected_records.extend(spec_records)
        event_rows.extend(extract_action_event_rows(bundle, harness=spec.harness))

    metrics = compute_outcome_conditioned_metrics(
        event_rows,
        inventory_rows=denominator_rows,
        measurement_summary=measurement_summary,
    )
    case_anchors = select_insight_case_anchors(
        event_rows=event_rows,
        selected_records=selected_records,
        measurement_summary=measurement_summary,
    )
    insight_claims = build_insight_claims(
        denominator_rows=denominator_rows,
        metrics=metrics,
        measurement_summary=measurement_summary,
        case_anchors=case_anchors,
    )
    output_paths = write_behavior_insight_outputs(
        args.output_dir,
        denominator_rows=denominator_rows,
        event_rows=event_rows,
        insight_claims=insight_claims,
        case_anchors=case_anchors,
    )

    if args.stdout:
        sys.stdout.write(output_paths["markdown"].read_text(encoding="utf-8"))
        sys.stdout.write("\n")

    for path in output_paths.values():
        sys.stdout.write(f"Wrote {path}\n")
    return 0


def _load_measurement_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _record_with_spec_defaults(record: Mapping[str, Any], spec) -> dict[str, Any]:
    enriched = dict(record)
    enriched.setdefault("run_id", spec.run_id)
    enriched.setdefault("harness", spec.harness)
    return enriched


if __name__ == "__main__":
    raise SystemExit(main())
