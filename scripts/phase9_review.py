#!/usr/bin/env python3
"""Phase 9 post-run Codex review generator.

Reads results/{run_id}.jsonl and produces a Markdown report at
results/{run_id}/codex_review.md containing overall accuracy, entropy
summary, and high/low-entropy error lists.

Usage:
    python scripts/phase9_review.py \
        --run-dir results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs

    # Or dump to stdout for piping:
    python scripts/phase9_review.py --run-dir <dir> --stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_records(run_dir: Path) -> list[dict]:
    """Locate and load the main JSONL for a run."""
    # run_dir is results/{run_id}; the JSONL lives at results/{run_id}.jsonl
    parent = run_dir.parent
    run_id = run_dir.name
    jsonl = parent / f"{run_id}.jsonl"
    if not jsonl.exists():
        return []
    records: list[dict] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(len(sorted_vals) * p)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def _format_error_row(rec: dict) -> str:
    stats = rec.get("token_entropy_stats") or {}
    return (
        f"- `{rec.get('task_id', '?')}` | "
        f"predicted=`{rec.get('predicted', '?')}` | "
        f"ground_truth=`{rec.get('ground_truth', '?')}` | "
        f"entropy_mean={stats.get('mean', 0.0):.3f} | "
        f"n_tokens={stats.get('n_tokens', 0)}"
    )


def generate_review(run_dir: Path) -> str:
    run_id = run_dir.name
    records = _load_records(run_dir)
    lines: list[str] = [f"# Phase 9 Review — {run_id}", ""]

    if not records:
        lines.append("**No results found.** Expected JSONL at "
                     f"`{run_dir.parent}/{run_id}.jsonl`.")
        return "\n".join(lines) + "\n"

    # --- Overall accuracy ---
    total = len(records)
    correct = sum(1 for r in records if r.get("correct") is True)
    acc_pct = (100.0 * correct / total) if total else 0.0
    lines += [
        "## Overall Accuracy",
        "",
        f"- Total records: **{total}**",
        f"- Correct: **{correct}** ({acc_pct:.2f}%)",
        f"- Errors: **{total - correct}**",
        "",
    ]

    # --- Entropy stats over records that have stats ---
    stats_records = [r for r in records
                     if r.get("token_entropy_stats") and
                     r["token_entropy_stats"].get("n_tokens", 0) > 0]
    means = [r["token_entropy_stats"]["mean"] for r in stats_records]
    p90s = [r["token_entropy_stats"]["p90"] for r in stats_records]
    lines += ["## Entropy Summary", ""]
    if means:
        sorted_means = sorted(means)
        sorted_p90s = sorted(p90s)
        hi_thresh = _percentile(sorted_means, 0.75)
        lo_thresh = _percentile(sorted_means, 0.25)
        lines += [
            f"- Records with logprobs: **{len(stats_records)}** / {total}",
            f"- Per-task entropy mean — "
            f"overall mean: {sum(means) / len(means):.3f} | "
            f"p25: {_percentile(sorted_means, 0.25):.3f} | "
            f"p50: {_percentile(sorted_means, 0.5):.3f} | "
            f"p75: {_percentile(sorted_means, 0.75):.3f}",
            f"- Per-task entropy p90 — "
            f"overall mean: {sum(p90s) / len(p90s):.3f} | "
            f"p75: {_percentile(sorted_p90s, 0.75):.3f}",
            "",
        ]
    else:
        hi_thresh = lo_thresh = 0.0
        lines += ["- No logprobs-enabled records found.", ""]

    # --- Error lists ---
    errors = [r for r in stats_records if r.get("correct") is False]
    high_e_errors = [r for r in errors
                     if r["token_entropy_stats"]["mean"] >= hi_thresh]
    low_e_errors = [r for r in errors
                    if r["token_entropy_stats"]["mean"] <= lo_thresh]

    lines += [
        f"## High-Entropy Errors (mean >= p75 = {hi_thresh:.3f})",
        "",
        f"_{len(high_e_errors)} tasks where the model was uncertain AND wrong._",
        "",
    ]
    lines += [_format_error_row(r) for r in high_e_errors] or ["- (none)"]
    lines += [
        "",
        f"## Low-Entropy Errors (mean <= p25 = {lo_thresh:.3f})",
        "",
        f"_{len(low_e_errors)} tasks where the model was confident AND wrong — likely systematic._",
        "",
    ]
    lines += [_format_error_row(r) for r in low_e_errors] or ["- (none)"]
    lines.append("")

    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True,
                   help="results/{run_id} directory (main JSONL read from parent)")
    p.add_argument("--stdout", action="store_true",
                   help="print markdown to stdout instead of writing codex_review.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    md = generate_review(args.run_dir)
    if args.stdout:
        sys.stdout.write(md)
        return 0
    out_path = args.run_dir / "codex_review.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    sys.stdout.write(f"Wrote {out_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
