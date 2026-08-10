#!/usr/bin/env python3
"""Offline action-space analysis CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alphadiana.analysis.action_features import build_record_feature_rows
from alphadiana.analysis.clustering import cluster_analysis_views
from alphadiana.analysis.reliability import compute_reliability_summary
from alphadiana.analysis.reports import render_markdown_report, write_feature_csv, write_json
from alphadiana.analysis.result_reader import load_run_bundle


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-clusters", type=int, default=4)
    parser.add_argument("--stdout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    bundle = load_run_bundle(args.results_dir, args.run_id)
    rows = build_record_feature_rows(bundle)
    reliability = compute_reliability_summary(bundle.records, bundle.manifest)
    clusters = cluster_analysis_views(rows, max_clusters=args.max_clusters)

    run_dir = args.results_dir / args.run_id
    output_paths = {
        "features_csv": run_dir / "action_space_features.csv",
        "clusters_json": run_dir / "action_space_clusters.json",
        "reliability_json": run_dir / "action_space_reliability.json",
        "report_md": run_dir / "action_space_report.md",
    }
    output_files = {name: f"{args.run_id}/{path.name}" for name, path in output_paths.items()}
    cluster_payload = {
        "run_id": args.run_id,
        "feature_level": "record",
        "views": clusters,
    }
    reliability_payload = {
        "run_id": args.run_id,
        "metrics": reliability,
    }
    markdown = render_markdown_report(bundle, reliability, clusters, output_files)

    write_feature_csv(rows, output_paths["features_csv"])
    write_json(cluster_payload, output_paths["clusters_json"])
    write_json(reliability_payload, output_paths["reliability_json"])
    output_paths["report_md"].parent.mkdir(parents=True, exist_ok=True)
    output_paths["report_md"].write_text(markdown, encoding="utf-8")

    if args.stdout:
        sys.stdout.write(markdown)
        if not markdown.endswith("\n"):
            sys.stdout.write("\n")

    for path in output_paths.values():
        sys.stdout.write(f"Wrote {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
