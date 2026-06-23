#!/usr/bin/env python3
"""Fig 5 (split) — one standalone HLE Sankey per (model, harness, outcome).

Splits the per-harness Sankey grid (plot_fig5_sankey_revised.py) into individual PDFs
so they can be tiled in a LaTeX subfigure grid by configuration (harness) x correctness
(success / wrong). Same bipartite Sankey encoding as the grid version: left = source
action, right = next action, ribbons sized by transition frequency and colored by the
source action (shared 6-action palette). No in-figure text; the action color key and the
check/cross meaning live in the caption.

Run:    python3 plot_fig5_sankey_split.py
Reads:  data/action_transitions_by_outcome_{qwen,gemma}.csv
Writes: revised/macro_chords/HLE_{Model}_{Harness}_{success,failure}.{pdf,png}
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from _style import apply_publication_style
# reuse the sankey machinery (matrix loader + renderer) from the grid version
from plot_fig5_sankey_revised import counts, draw_sankey, DATA, BENCH

# keep revised outputs inside the revision workspace; copy into the manuscript's
# figures/macro_chords/ when integrating. pdf for LaTeX, png alongside for preview.
OUTDIR = HERE / "revised" / "macro_chords"
PREVIEW = OUTDIR

# file-name model tokens match the LaTeX grid (HLE_Qwen_*, HLE_Gemma_*)
MODELS = [("Qwen", "action_transitions_by_outcome_qwen.csv"),
          ("Gemma", "action_transitions_by_outcome_gemma.csv")]
HARNESS_ORDER = ["DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw"]
# file suffix -> outcome set  (success ; failure == "wrong" = failure + unknown)
OUTCOMES = [("success", {"success"}), ("failure", {"failure", "unknown"})]


def render_one(path: Path, harness: str, oset: set, out_base: Path, preview_png: Path):
    fig, ax = plt.subplots(figsize=(2.4, 2.4))
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    draw_sankey(ax, counts(path, harness, oset))
    fig.savefig(out_base.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.02)
    fig.savefig(preview_png, dpi=150, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def main():
    apply_publication_style(font_size=10)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    PREVIEW.mkdir(parents=True, exist_ok=True)
    n = 0
    for model, fn in MODELS:
        path = DATA / fn
        for harness in HARNESS_ORDER:
            for suffix, oset in OUTCOMES:
                base = OUTDIR / f"{BENCH}_{model}_{harness}_{suffix}"
                render_one(path, harness, oset, base, PREVIEW / f"{base.name}.png")
                print("wrote", base.with_suffix(".pdf"))
                n += 1
    print(f"total {n} panels -> {OUTDIR}")


if __name__ == "__main__":
    main()
