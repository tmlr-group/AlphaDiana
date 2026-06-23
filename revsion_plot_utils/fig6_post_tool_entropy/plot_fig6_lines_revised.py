#!/usr/bin/env python3
"""Fig 6 (revised) — post-tool token entropy as lines + SE bands.

Replaces the interleaved green/red bar comb with two lines (correct vs wrong) and
shaded standard-error bands over post-tool token position, consolidated into
small-multiples (rows = benchmark, cols = tool harness), one figure per model.
Keeps the current green/red theme. No in-figure legend (curves get direct
end-labels; the full key goes in the caption).

This reuses the original module's harness-specific aggregation helpers
(`code/plot_entropy_post_tool.py`) but re-runs the per-token loop while also
accumulating sum-of-squares, so a per-bin standard error can be computed (the
original kept only sum + count).

Run:    python3 plot_fig6_lines_revised.py
Writes: revised/fig6_post_tool_entropy_{Qwen,Gemma}.{pdf,png,svg}
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # revsion_plot_utils/ for _style
sys.path.insert(0, str(HERE / "code"))        # original module
from _style import (apply_publication_style, add_panel_label, finalize_figure,
                    C_CORRECT, C_WRONG)
import plot_entropy_post_tool as pt            # reuse aggregation helpers

# The module was copied into this staging folder, so its ROOT (= __file__.parent.parent)
# points here, not at the repo. Re-point it so ROOT-relative run_dirs / metric files
# resolve against the real repo root, and remap the already-built SETTINGS.
REPO = HERE.parents[1]
_WRONG_ROOT = pt.ROOT
pt.ROOT = REPO
pt.TOOL_METRIC_FILES = {
    "Qwen": REPO / "analyze_tools/data/six_action_statistics/trajectory_metrics.csv",
    "Gemma": REPO / "analyze_tools/data/six_action_statistics_gemma/trajectory_metrics.csv",
}


def _fix_root(s):
    try:
        return pt.Setting(s.benchmark, s.model, s.harness,
                          REPO / s.run_dir.relative_to(_WRONG_ROOT))
    except ValueError:
        return s          # absolute path, leave as-is


pt.SETTINGS = [_fix_root(s) for s in pt.SETTINGS]

OUT_DIR = HERE / "revised"
MODELS = ["Qwen", "Gemma"]
BENCH_ORDER = ["HLE", "GPQA", "AIMEPass4"]
BENCH_LABEL = {"HLE": "HLE", "GPQA": "GPQA-Diamond", "AIMEPass4": "AIME"}
HARNESS_ORDER = ["OpenClaw", "OpenCode"]
MIN_PLOT_COUNT = 5     # bins with fewer post-tool tokens are dropped from the line


def aggregate(setting) -> dict | None:
    """Re-run the original per-token loop, also accumulating sum-of-squares."""
    files = pt.collect_logprob_files(setting.run_dir)
    edges = pt.token_position_edges()
    n_bins = len(edges) - 1
    acc = {k: np.zeros(n_bins, dtype=float) for k in
           ("c_sum", "c_sq", "w_sum", "w_sq")}
    c_cnt = np.zeros(n_bins, dtype=int)
    w_cnt = np.zeros(n_bins, dtype=int)
    c_files = w_files = 0

    for lp_path in files:
        task_id, sample_index = pt.task_and_sample_from_logprob_path(lp_path)
        tool_key = (setting.model, setting.benchmark, setting.harness, task_id, sample_index)
        if pt.TOOL_CALL_LOOKUP.get(tool_key) is False:
            continue
        correct = pt.load_correct_label(setting.run_dir, task_id, sample_index)
        if correct is None:
            continue
        tokens = pt.load_token_records(lp_path)
        if not tokens:
            continue
        spans = pt.post_tool_spans(setting, lp_path, tokens)
        if not spans:
            continue
        c_files += int(bool(correct))
        w_files += int(not correct)
        for start, end in spans:
            for rel_idx, item in enumerate(tokens[start:end], start=1):
                e = pt.token_entropy(item)
                if e is None or not math.isfinite(e):
                    continue
                b = int(np.searchsorted(edges, rel_idx, side="right") - 1)
                if not (0 <= b < n_bins):
                    continue
                if correct:
                    acc["c_sum"][b] += e; acc["c_sq"][b] += e * e; c_cnt[b] += 1
                else:
                    acc["w_sum"][b] += e; acc["w_sq"][b] += e * e; w_cnt[b] += 1

    if c_cnt.sum() == 0 and w_cnt.sum() == 0:
        return None
    return dict(edges=edges, c_cnt=c_cnt, w_cnt=w_cnt,
                c_files=c_files, w_files=w_files, **acc)


def mean_se(s, sq, n):
    """Per-bin mean and standard error, NaN where n < MIN_PLOT_COUNT."""
    mean = np.full(len(n), np.nan)
    se = np.full(len(n), np.nan)
    ok = n >= MIN_PLOT_COUNT
    mean[ok] = s[ok] / n[ok]
    var = np.clip(sq[ok] / n[ok] - mean[ok] ** 2, 0, None)
    se[ok] = np.sqrt(var / n[ok])
    return mean, se


def draw_panel(ax, agg, x_max):
    edges = agg["edges"]
    x = np.sqrt(edges[:-1] * edges[1:])     # geometric bin centers
    ends = []
    for cnt, s, sq, color, lab in [
        (agg["c_cnt"], agg["c_sum"], agg["c_sq"], C_CORRECT, "Correct"),
        (agg["w_cnt"], agg["w_sum"], agg["w_sq"], C_WRONG, "Wrong"),
    ]:
        mean, se = mean_se(s, sq, cnt)
        m = np.isfinite(mean)
        if not m.any():
            continue
        ax.fill_between(x[m], mean[m] - se[m], mean[m] + se[m],
                        color=color, alpha=0.18, lw=0)
        ax.plot(x[m], mean[m], color=color, lw=1.4)
        ends.append([x[m][-1], float(mean[m][-1]), color, lab])
    # End-labels pinned to the right edge (fixed x in axes coords) at separated y,
    # so they never overlap each other or the x-axis regardless of where curves end.
    if len(ends) == 2:
        ends.sort(key=lambda e: e[1])               # lower curve first
        sep = 0.12
        if ends[1][1] - ends[0][1] < sep:
            mid = (ends[0][1] + ends[1][1]) / 2
            ends[0][1], ends[1][1] = mid - sep / 2, mid + sep / 2
        if ends[0][1] < 0.07:                        # clamp, preserving separation
            d = 0.07 - ends[0][1]; ends[0][1] += d; ends[1][1] += d
        if ends[1][1] > 0.70:
            d = ends[1][1] - 0.70; ends[0][1] -= d; ends[1][1] -= d
    for xe, ye, color, lab in ends:
        ax.text(0.985, ye, lab, color=color, fontsize=8, transform=ax.get_yaxis_transform(),
                va="center", ha="right", fontweight="bold")
    ax.set_xscale("log")
    ax.set_xlim(1, x_max)
    ax.set_ylim(0, 0.75)
    ax.grid(True, alpha=0.2, lw=0.5)


def plot_model(model, aggs, x_max):
    """One figure per model: bench rows x tool-harness cols. x_max is shared across
    both model figures so their axes align in scale."""
    apply_publication_style(font_size=11, axes_linewidth=0.9)
    nb, nh = len(BENCH_ORDER), len(HARNESS_ORDER)
    fig, axes = plt.subplots(nb, nh, figsize=(7.2, 7.2))
    fig.subplots_adjust(left=0.135, right=0.965, top=0.955, bottom=0.075,
                        hspace=0.13, wspace=0.08)
    for bi, bench in enumerate(BENCH_ORDER):
        for hi, harness in enumerate(HARNESS_ORDER):
            ax = axes[bi, hi]
            agg = aggs.get((bench, harness))
            if bi == 0:
                ax.set_title(harness, fontsize=13, fontweight="bold")
            if agg is None:
                ax.text(0.5, 0.5, "no post-tool data", transform=ax.transAxes,
                        ha="center", va="center", fontsize=10, color="0.5")
                ax.set_xticks([]); ax.set_yticks([])
                continue
            draw_panel(ax, agg, x_max)
            if hi != 0:
                ax.set_yticklabels([])
            if bi != nb - 1:
                ax.set_xticklabels([])
        axes[bi, 0].set_ylabel(BENCH_LABEL[bench], fontsize=12, fontweight="bold")
    fig.text(0.035, 0.51, "Mean token entropy (nat)", va="center",
             rotation="vertical", fontsize=12)
    fig.text(0.55, 0.025, "Post-tool token position (log scale)", ha="center", fontsize=12)
    saved = finalize_figure(fig, OUT_DIR / f"fig6_post_tool_entropy_{model}", dpi=300)
    for p in saved:
        print("wrote", p)


def global_x_max(by_model):
    xm = 10.0
    for aggs in by_model.values():
        for agg in aggs.values():
            if agg is not None:
                xm = max(xm, pt.max_position_with_data(
                    agg["c_cnt"] + agg["w_cnt"], agg["edges"]))
    return xm


CACHE = HERE / "revised" / "_fig6_agg_cache.pkl"


def main():
    import pickle
    if CACHE.exists() and "--fresh" not in sys.argv:
        print(f"Loading cached aggregation {CACHE} (pass --fresh to re-aggregate)")
        with open(CACHE, "rb") as f:
            by_model = pickle.load(f)
    else:
        pt.TOOL_CALL_LOOKUP = pt.load_tool_call_lookup()
        by_model = {m: {} for m in MODELS}
        for setting in pt.SETTINGS:
            if setting.harness not in HARNESS_ORDER or setting.model not in MODELS:
                continue
            if not setting.run_dir.exists():
                print(f"SKIP missing {setting.benchmark}/{setting.model}/{setting.harness}")
                continue
            print(f"Aggregating {setting.benchmark}/{setting.model}/{setting.harness}", flush=True)
            by_model[setting.model][(setting.benchmark, setting.harness)] = aggregate(setting)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE, "wb") as f:
            pickle.dump(by_model, f)
    x_max = global_x_max(by_model)
    for model in MODELS:
        plot_model(model, by_model[model], x_max)


if __name__ == "__main__":
    main()
