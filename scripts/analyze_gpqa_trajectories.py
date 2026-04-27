#!/usr/bin/env python3
"""Offline Phase 14 GPQA trajectory analysis CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from alphadiana.analysis.action_events import extract_action_event_rows, inventory_corpus, select_directllm_baseline
from alphadiana.analysis.case_studies import select_case_studies
from alphadiana.analysis.result_reader import load_run_bundle
from alphadiana.analysis.trajectory_metrics import compute_outcome_conditioned_metrics
from alphadiana.analysis.trajectory_reports import write_phase14_outputs

DEFAULT_OUTPUT_DIR = Path("results/phase14_gpqa_trajectory_analysis")
DEFAULT_PRIMARY_RUNS = {
    "openclaw": "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
    "opencode": "full_gpqa_v2_opencode_qwen35_27b_logprobs",
    "zeroclaw": "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--stdout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    results_dir = args.results_dir
    corpus_specs = dict(DEFAULT_PRIMARY_RUNS)

    directllm = select_directllm_baseline(results_dir)
    direct_bundle = directllm.get("bundle")
    if direct_bundle is not None and directllm.get("run_id"):
        corpus_specs = {"directllm": str(directllm["run_id"]), **corpus_specs}

    inventory_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    for harness, run_id in corpus_specs.items():
        inventory_rows.append(inventory_corpus(results_dir, run_id, harness))
        bundle = direct_bundle if harness == "directllm" and direct_bundle is not None else load_run_bundle(results_dir, run_id)
        event_rows.extend(extract_action_event_rows(bundle, harness=harness))

    inventory = {
        "analysis": "phase14_gpqa_trajectory_analysis",
        "corpora": inventory_rows,
        "directllm_selection": {
            key: value
            for key, value in directllm.items()
            if key in {"candidate", "run_id", "valid_scored", "task_records", "jsonl_records"}
        },
    }
    metrics = compute_outcome_conditioned_metrics(event_rows, inventory_rows=inventory_rows)
    case_studies = select_case_studies(event_rows)
    output_paths = write_phase14_outputs(
        args.output_dir,
        inventory=inventory,
        event_rows=event_rows,
        metrics=metrics,
        case_studies=case_studies,
    )

    if args.stdout:
        sys.stdout.write((output_paths["latex_appendix"]).read_text(encoding="utf-8"))
        sys.stdout.write("\n\n")
        sys.stdout.write((output_paths["chinese_discussion"]).read_text(encoding="utf-8"))
        sys.stdout.write("\n")

    for path in output_paths.values():
        sys.stdout.write(f"Wrote {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
