#!/usr/bin/env python3
"""Offline Phase 15 behavior insight analysis CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from alphadiana.analysis.action_events import extract_action_event_rows
from alphadiana.analysis.insight_cases import select_insight_case_anchors
from alphadiana.analysis.insight_corpus import (
    ENV_CORPUS_VARS,
    build_denominator_ledger,
    load_phase15_corpus_specs,
    load_selected_task_records,
)
from alphadiana.analysis.insight_measurements import build_insight_claims
from alphadiana.analysis.insight_reports import write_phase15_outputs
from alphadiana.analysis.result_reader import load_run_bundle
from alphadiana.analysis.trajectory_metrics import compute_outcome_conditioned_metrics

DEFAULT_OUTPUT_DIR = Path("results/phase15_behavior_insights")
DEFAULT_MEASUREMENT_SUMMARY = Path("analyze_tools/data/measurement_summary.json")
PHASE15_INPUT_ENV_VARS = (
    "PHASE15_HLE_OPENCODE_LOGPROBS",
    "PHASE15_HLE_ZEROCLAW_LOGPROBS",
    "PHASE15_COLLAB_RESULTS_ROOT",
)

assert PHASE15_INPUT_ENV_VARS == ENV_CORPUS_VARS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--measurement-summary",
        type=Path,
        default=DEFAULT_MEASUREMENT_SUMMARY,
        help="Optional analyze_tools measurement_summary.json to fold into insight measurements.",
    )
    parser.add_argument("--include-hf-synced", dest="include_hf_synced", action="store_true", default=True)
    parser.add_argument("--no-include-hf-synced", dest="include_hf_synced", action="store_false")
    parser.add_argument("--stdout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    measurement_summary = _load_measurement_summary(args.measurement_summary)
    specs = load_phase15_corpus_specs(args.results_dir, include_hf_synced=args.include_hf_synced)
    denominator_rows = build_denominator_ledger(specs)
    denominator_by_label = {str(row.get("corpus_label") or ""): row for row in denominator_rows}

    selected_records: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    for spec in specs:
        row = denominator_by_label.get(spec.label)
        if not row or row.get("status") != "validated_records":
            continue
        bundle = load_run_bundle(spec.results_dir, spec.run_id)
        spec_records = [_record_with_spec_defaults(record, spec) for record in load_selected_task_records(bundle)]
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
    output_paths = write_phase15_outputs(
        args.output_dir,
        denominator_rows=denominator_rows,
        event_rows=event_rows,
        insight_claims=insight_claims,
        case_anchors=case_anchors,
    )

    if args.stdout:
        sys.stdout.write(output_paths["markdown"].read_text(encoding="utf-8"))
        sys.stdout.write("\n\n")
        sys.stdout.write(output_paths["markdown_zh"].read_text(encoding="utf-8"))
        sys.stdout.write("\n")

    for path in output_paths.values():
        sys.stdout.write(f"Wrote {path}\n")
    return 0


def _load_measurement_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _record_with_spec_defaults(record: Mapping[str, Any], spec) -> dict[str, Any]:
    enriched = dict(record)
    enriched.setdefault("run_id", spec.run_id)
    enriched.setdefault("harness", spec.harness)
    return enriched


if __name__ == "__main__":
    raise SystemExit(main())
