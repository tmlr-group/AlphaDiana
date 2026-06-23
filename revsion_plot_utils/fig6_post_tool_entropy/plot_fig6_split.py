#!/usr/bin/env python3
"""Fig 6 (split) — one standalone post-tool entropy panel per (dataset, model, harness).

Splits the revised small-multiples grid (plot_fig6_lines_revised.py) into individual
PDFs so they can be tiled in a LaTeX subfigure grid by dataset x configuration. Same
encoding as the grid version: correct (green) vs wrong (red) mean post-tool token
entropy with shaded standard-error bands, log-scaled post-tool token position on x.
Per-panel y-autoscale so within-setting entropy dynamics stay visible; x scale shared.

Reuses the revised module's aggregation cache and helpers, so it does not re-run the
token loop. No in-figure title/legend; the LaTeX \\subcap and caption carry those.

Run:    python3 plot_fig6_split.py        (needs revised/_fig6_agg_cache.pkl)
Writes: revised/macro_post_tool_analyze/{AIME,GPQA,HLE}_{Qwen,Gemma}_{OpenClaw,OpenCode}.{pdf,png}
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from _style import apply_publication_style, C_CORRECT, C_WRONG
import plot_fig6_lines_revised as fig6   # reuses pt setup, cache path, mean_se, global_x_max

# keep revised outputs inside the revision workspace; copy into the manuscript's
# figures/macro_post_tool_analyze/ when integrating. pdf for LaTeX, png for preview.
OUTDIR = HERE / "revised" / "macro_post_tool_analyze"
PREVIEW = OUTDIR

MODELS = ["Qwen", "Gemma"]
BENCH_ORDER = ["AIMEPass4", "GPQA", "HLE"]
BENCH_FILE = {"AIMEPass4": "AIME", "GPQA": "GPQA", "HLE": "HLE"}   # LaTeX file tokens
HARNESS_ORDER = ["OpenClaw", "OpenCode"]


def draw_compact(ax, agg, x_max):
    edges = agg["edges"]
    x = np.sqrt(edges[:-1] * edges[1:])     # geometric bin centers
    lo, hi = np.inf, -np.inf
    for cnt, s, sq, color in [
        (agg["c_cnt"], agg["c_sum"], agg["c_sq"], C_CORRECT),
        (agg["w_cnt"], agg["w_sum"], agg["w_sq"], C_WRONG),
    ]:
        mean, se = fig6.mean_se(s, sq, cnt)
        m = np.isfinite(mean)
        if not m.any():
            continue
        ax.fill_between(x[m], mean[m] - se[m], mean[m] + se[m], color=color, alpha=0.18, lw=0)
        ax.plot(x[m], mean[m], color=color, lw=1.6)
        lo = min(lo, float(np.nanmin(mean[m] - se[m])))
        hi = max(hi, float(np.nanmax(mean[m] + se[m])))
    ax.set_xscale("log")
    ax.set_xlim(1, x_max)
    if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
        pad = (hi - lo) * 0.08
        ax.set_ylim(max(0.0, lo - pad), hi + pad)
    ax.grid(True, alpha=0.2, lw=0.5)
    ax.tick_params(labelsize=8)


def render_one(agg, x_max, base: Path, preview: Path):
    fig, ax = plt.subplots(figsize=(2.4, 2.0))
    fig.subplots_adjust(left=0.17, right=0.97, top=0.97, bottom=0.16)
    if agg is None:
        ax.text(0.5, 0.5, "no post-tool data", ha="center", va="center",
                transform=ax.transAxes, fontsize=9, color="0.5")
        ax.set_xticks([]); ax.set_yticks([])
    else:
        draw_compact(ax, agg, x_max)
    fig.savefig(base.with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.02)
    fig.savefig(preview, dpi=150, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def main():
    apply_publication_style(font_size=10, axes_linewidth=0.8)
    if not fig6.CACHE.exists():
        sys.exit(f"No aggregation cache at {fig6.CACHE}; run plot_fig6_lines_revised.py first.")
    with open(fig6.CACHE, "rb") as f:
        by_model = pickle.load(f)
    x_max = fig6.global_x_max(by_model)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    PREVIEW.mkdir(parents=True, exist_ok=True)
    n = 0
    for model in MODELS:
        aggs = by_model.get(model, {})
        for bench in BENCH_ORDER:
            for harness in HARNESS_ORDER:
                agg = aggs.get((bench, harness))
                base = OUTDIR / f"{BENCH_FILE[bench]}_{model}_{harness}"
                render_one(agg, x_max, base, PREVIEW / f"{base.name}.png")
                print("wrote", base.with_suffix(".pdf"))
                n += 1
    print(f"total {n} panels -> {OUTDIR}")


if __name__ == "__main__":
    main()
