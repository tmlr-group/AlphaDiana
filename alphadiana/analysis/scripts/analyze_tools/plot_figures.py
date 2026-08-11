#!/usr/bin/env python3
"""
plot_figures.py — 生成所有分析图表（使用 academic-plot 风格）。

前置条件：先运行 extract_data.py 生成 data/ 下的 CSV。

运行方式：
    python3 analyze_tools/plot_figures.py

输出（analyze_tools/figures/）：
    fig1_entropy_calibration.pdf    — entropy bin vs accuracy 校准曲线
    fig2_openclaw_tokens.pdf        — OpenClaw token 数 × 答对/错分布
    fig3_tool_boundary_profile.pdf  — tool call 边界 ±15 token entropy profile
    fig4_baseline_vs_posttool.pdf   — baseline vs post-tool entropy 对比
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ─── 安装 academic-plot skill ──────────────────────────────────────────────────
SKILL_DIR = os.environ.get("ALPHADIANA_ACADEMIC_PLOT_DIR", "").strip()
if SKILL_DIR and SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from academic_plot import (
    register_academic_colormaps,
    set_academic_style,
    PALETTES,
)

DATA_DIR = Path(__file__).parent / "data"
FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)

# ─── Paths resolved against repo root ─────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[4]
_RESULTS_DIR = Path(os.environ.get("ALPHADIANA_RESULTS_DIR", _REPO_ROOT / "results")).expanduser()
DIRECTLLM_TASKS = _RESULTS_DIR / "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs/tasks"
OPENCODE_TASKS  = _RESULTS_DIR / "full_gpqa_v2_opencode_qwen35_27b_logprobs/tasks"
ZEROCLAW_TASKS  = _RESULTS_DIR / "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs/tasks"
ACTION_EVENTS_CSV = _RESULTS_DIR / "phase14_gpqa_trajectory_analysis/action_events.csv"

FONT_SIZE = 14

# ─── 调色板 ────────────────────────────────────────────────────────────────────
# contrast: #103778 (深蓝) / #8e375f (深玫红) / #e3371e (红)
C_CORRECT = "#103778"   # 答对
C_WRONG   = "#e3371e"   # 答错
C_OC      = "#103778"   # OpenClaw
C_OCODE   = "#8e375f"   # OpenCode
C_ALL     = "#555555"   # 全体


def load_csv(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="") as f:
        return list(csv.DictReader(f))


# ──────────────────────────────────────────────────────────────────────────────
# Fig 1: Entropy-binned accuracy (校准曲线)
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig1_entropy_calibration() -> None:
    rows = load_csv("entropy_calibration.csv")

    oc_bins, oc_acc, oc_n = [], [], []
    ocode_bins, ocode_acc, ocode_n = [], [], []
    for r in rows:
        b = float(r["entropy_bin"])
        acc = float(r["accuracy"])
        n = int(r["n"])
        if r["model"] == "OpenClaw":
            oc_bins.append(b); oc_acc.append(acc); oc_n.append(n)
        else:
            ocode_bins.append(b); ocode_acc.append(acc); ocode_n.append(n)

    fig, ax = plt.subplots(figsize=(7.5, 5))

    ax.plot(oc_bins, oc_acc, "o-", color=C_OC, linewidth=2.2, markersize=6, label="OpenClaw")
    ax.plot(ocode_bins, ocode_acc, "s-", color=C_OCODE, linewidth=2.2, markersize=6, label="OpenCode")

    # 标注样本量
    for b, a, n in zip(oc_bins, oc_acc, oc_n):
        ax.annotate(f"n={n}", (b, a), textcoords="offset points", xytext=(0, 8),
                    fontsize=9, color=C_OC, ha="center")
    for b, a, n in zip(ocode_bins, ocode_acc, ocode_n):
        ax.annotate(f"n={n}", (b, a), textcoords="offset points", xytext=(0, -14),
                    fontsize=9, color=C_OCODE, ha="center")

    # 突出 OpenClaw 的 <0.1 危险区
    ax.axvspan(-0.05, 0.1, alpha=0.08, color=C_WRONG, zorder=0)
    ax.text(0.025, 0.06, "\"confident\nwrong\"\nzone", fontsize=8.5, color=C_WRONG,
            ha="center", va="bottom", transform=ax.get_xaxis_transform())

    ax.set_xlabel("Per-task mean token entropy (nat)")
    ax.set_ylabel("Accuracy")
    ax.set_xlim(-0.05, 1.65)
    ax.set_ylim(0, 1.05)
    ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4])
    ax.grid(alpha=0.25, linestyle="--", linewidth=0.6)
    ax.legend(frameon=False)
    ax.set_title("Entropy-Binned Accuracy: OpenClaw vs OpenCode")

    out = FIG_DIR / "fig1_entropy_calibration.pdf"
    fig.savefig(out, dpi=300)
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 2: OpenClaw token count distribution — correct vs wrong
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig2_openclaw_tokens() -> None:
    rows = load_csv("token_count_by_outcome.csv")
    correct_tok = [int(r["n_tokens"]) for r in rows if r["correct"] == "1"]
    wrong_tok   = [int(r["n_tokens"]) for r in rows if r["correct"] == "0"]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))

    # 左：箱线图
    ax = axes[0]
    bp = ax.boxplot(
        [correct_tok, wrong_tok],
        tick_labels=["Correct", "Wrong"],
        patch_artist=True,
        widths=0.45,
        medianprops=dict(color="white", linewidth=2),
        flierprops=dict(marker=".", markersize=3, alpha=0.4),
    )
    for patch, color in zip(bp["boxes"], [C_CORRECT, C_WRONG]):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
    for whisker in bp["whiskers"]:
        whisker.set(color="#555555", linewidth=1.2)
    for cap in bp["caps"]:
        cap.set(color="#555555", linewidth=1.2)
    ax.set_ylabel("Output tokens per task")
    ax.set_title("Token count: Correct vs Wrong")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.6)

    # 中位数标注
    for i, (vals, color) in enumerate([(correct_tok, C_CORRECT), (wrong_tok, C_WRONG)], 1):
        med = np.median(vals)
        ax.text(i, med + 1500, f"med={int(med):,}", ha="center", fontsize=9.5, color=color)

    # 右：CDF
    ax2 = axes[1]
    for vals, color, label in [
        (sorted(correct_tok), C_CORRECT, f"Correct (n={len(correct_tok)})"),
        (sorted(wrong_tok),   C_WRONG,   f"Wrong   (n={len(wrong_tok)})"),
    ]:
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax2.plot(vals, cdf, color=color, linewidth=2.2, label=label)

    ax2.axvline(65536, color="#aaaaaa", linestyle=":", linewidth=1.2)
    ax2.text(65536 + 800, 0.15, "max_tokens\n= 65536", fontsize=8.5, color="#888888")
    ax2.set_xlabel("Output tokens per task")
    ax2.set_ylabel("CDF")
    ax2.set_title("Token count CDF")
    ax2.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x/1000)}k"))
    ax2.grid(alpha=0.25, linestyle="--", linewidth=0.6)
    ax2.legend(frameon=False, fontsize=11)

    fig.suptitle("OpenClaw: Token count by outcome", fontsize=FONT_SIZE + 1, y=1.01)
    fig.tight_layout()
    out = FIG_DIR / "fig2_openclaw_tokens.pdf"
    fig.savefig(out, dpi=300)
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 3: Tool call boundary entropy profile (±15 token)
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig3_tool_boundary_profile() -> None:
    rows = load_csv("tool_boundary_profile.csv")
    offsets = [int(r["offset"]) for r in rows]
    mean_all  = [float(r["mean_all"])     for r in rows]
    mean_corr = [float(r["mean_correct"]) for r in rows]
    mean_wrong = [float(r["mean_wrong"])  for r in rows]

    fig, ax = plt.subplots(figsize=(8.5, 5))

    ax.plot(offsets, mean_all,  color=C_ALL,     linewidth=1.6, linestyle="--", label="All",     alpha=0.7)
    ax.plot(offsets, mean_corr, color=C_CORRECT, linewidth=2.2, label="Correct")
    ax.plot(offsets, mean_wrong,color=C_WRONG,   linewidth=2.2, label="Wrong")

    # 边界线
    ax.axvline(0, color="#333333", linewidth=1.0, linestyle="-")
    ax.text(0.3, ax.get_ylim()[1] * 0.97 if ax.get_ylim()[1] > 0 else 0.85,
            "← tool call │ tool result →", fontsize=9, color="#333333", va="top")

    # 结构区着色（近零熵区 offset -5~-2）
    ax.axvspan(-5.5, -1.5, alpha=0.07, color="#888888", zorder=0)
    ax.text(-3.5, 0.02, "struct\ntokens", fontsize=7.5, color="#888888", ha="center", va="bottom")

    ax.set_xlabel("Token offset from tool call boundary")
    ax.set_ylabel("Mean token entropy (nat)")
    ax.set_title("Entropy Profile around Tool Call Boundary (OpenCode)")
    ax.set_xticks(range(-15, 16, 5))
    ax.grid(alpha=0.25, linestyle="--", linewidth=0.6)
    ax.legend(frameon=False)

    out = FIG_DIR / "fig3_tool_boundary_profile.pdf"
    fig.savefig(out, dpi=300)
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 4: Baseline vs post-tool entropy (grouped bar)
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig4_baseline_vs_posttool() -> None:
    rows = load_csv("baseline_vs_posttool.csv")

    # label order
    label_order = ["baseline", "after_tool_1+", "after_tool_2+", "after_tool_3+", "after_tool_4+"]
    label_display = ["Baseline\n(turn 0)", "After\ntool #1", "After\ntool #2", "After\ntool #3", "After\ntool #4+"]

    data: dict = {}
    for r in rows:
        key = (r["turn_label"], int(r["correct"]))
        data[key] = (float(r["mean_entropy"]), float(r["std_entropy"]), int(r["n"]))

    x = np.arange(len(label_order))
    width = 0.35

    fig, ax = plt.subplots(figsize=(9, 5))

    for shift, correct, color, label in [
        (-width / 2, 1, C_CORRECT, "Correct"),
        ( width / 2, 0, C_WRONG,   "Wrong"),
    ]:
        means, errs, ns = [], [], []
        for lbl in label_order:
            val = data.get((lbl, correct))
            if val:
                means.append(val[0])
                errs.append(val[1] / np.sqrt(val[2]))  # SE
                ns.append(val[2])
            else:
                means.append(float("nan"))
                errs.append(0)
                ns.append(0)

        bars = ax.bar(x + shift, means, width, yerr=errs, capsize=4,
                      color=color, alpha=0.78, label=label,
                      error_kw=dict(linewidth=1.2, ecolor="#333333"))

        # 标注 n
        for xi, (m, n) in enumerate(zip(means, ns)):
            if not np.isnan(m) and n > 0:
                ax.text(xi + shift, m + errs[xi] + 0.005, f"n={n}",
                        ha="center", va="bottom", fontsize=7.5, color=color)

    # baseline 参考线（答对 baseline = 0.312）
    baseline_correct = data.get(("baseline", 1), (0.312, 0, 0))[0]
    ax.axhline(baseline_correct, color=C_CORRECT, linewidth=1.0, linestyle=":", alpha=0.6)
    ax.text(len(label_order) - 0.4, baseline_correct + 0.005, f"baseline-correct\n({baseline_correct:.3f})",
            fontsize=8, color=C_CORRECT, va="bottom")

    ax.set_xticks(x)
    ax.set_xticklabels(label_display)
    ax.set_ylabel("Mean entropy of first 30 tokens (nat)")
    ax.set_title("Baseline vs Post-Tool Entropy by Outcome (OpenCode)")
    ax.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.6)
    ax.legend(frameon=False)
    ax.set_ylim(0, 0.65)

    out = FIG_DIR / "fig4_baseline_vs_posttool.pdf"
    fig.savefig(out, dpi=300)
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 5: OpenClaw — mean entropy vs token count scatter (correct/wrong)
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig5_openclaw_entropy_scatter() -> None:
    rows = load_csv("openclaw_entropy_by_outcome.csv")

    fig, ax = plt.subplots(figsize=(7, 5))

    for correct, color, label, marker in [(1, C_CORRECT, "Correct", "o"), (0, C_WRONG, "Wrong", "x")]:
        xs = [float(r["n_tokens"]) for r in rows if int(r["correct"]) == correct]
        ys = [float(r["mean_entropy"]) for r in rows if int(r["correct"]) == correct]
        ax.scatter(xs, ys, c=color, marker=marker, s=30, alpha=0.55, label=f"{label} (n={len(xs)})")

    ax.axvline(65536, color="#aaaaaa", linestyle=":", linewidth=1.2)
    ax.text(65536 + 500, 0.28, "max_tokens", fontsize=8, color="#888888", rotation=90, va="center")

    # "confident wrong" 区域标注
    ax.axvspan(20000, 90000, alpha=0.04, color=C_WRONG)
    ax.text(52000, 0.25, '"confident wrong"\n(long + low entropy)', fontsize=8,
            color=C_WRONG, ha="center", va="center", alpha=0.8)

    ax.set_xlabel("Output tokens per task")
    ax.set_ylabel("Mean token entropy (nat)")
    ax.set_title("OpenClaw: Token Count vs Mean Entropy")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x/1000)}k"))
    ax.grid(alpha=0.25, linestyle="--", linewidth=0.6)
    ax.legend(frameon=False)

    out = FIG_DIR / "fig5_openclaw_entropy_scatter.pdf"
    fig.savefig(out, dpi=300)
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 6 (Q1): Entropy-token quadrant heatmap
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig6_entropy_quadrant() -> None:
    rows = load_csv("entropy_token_quadrants.csv")

    data = {}
    for r in rows:
        data[r["bucket"]] = {
            "n": int(r["n"]),
            "wrong_rate": float(r["wrong_rate"]),
            "median_tokens": int(float(r["median_tokens"])),
            "median_entropy": float(r["median_entropy"]),
        }

    grid_keys = [
        ["low_entropy_short",    "higher_entropy_short"],
        ["low_entropy_long",     "higher_entropy_long"],
    ]
    col_labels = ["Low entropy\n" + r"($H \leq q_{25}$)", "Higher entropy\n" + r"($H > q_{25}$)"]
    row_labels  = ["Short\n" + r"(tokens $< q_{75}$)", "Long\n" + r"(tokens $\geq q_{75}$)"]

    wrong_rates = np.array([[data[k]["wrong_rate"] for k in row] for row in grid_keys])

    FS = 18
    fig, ax = plt.subplots(figsize=(10, 7.5))

    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("wr", ["#f7f7f7", "#fddbc7", "#d6604d", "#a50026"])
    im = ax.imshow(wrong_rates, cmap=cmap, vmin=0, vmax=1, aspect="auto")

    cbar = fig.colorbar(im, ax=ax, fraction=0.032, pad=0.05)
    cbar.set_label("Wrong rate", fontsize=FS)
    cbar.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
    cbar.set_ticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=FS - 2)

    ax.set_xticks([0, 1])
    ax.set_xticklabels(col_labels, fontsize=FS)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(row_labels, fontsize=FS)
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    ax.set_xlabel("Entropy level", fontsize=FS, labelpad=14)
    ax.set_ylabel("Trajectory length", fontsize=FS, labelpad=14)

    for ri, row in enumerate(grid_keys):
        for ci, key in enumerate(row):
            d = data[key]
            wr = d["wrong_rate"]
            text_color = "white" if wr > 0.55 else "#1a1a1a"
            # Two-line cell text: wrong rate bold on top, stats below
            ax.text(ci, ri - 0.14,
                    f"{wr*100:.0f}% wrong",
                    ha="center", va="center", fontsize=FS + 1,
                    color=text_color, fontweight="bold")
            ax.text(ci, ri + 0.22,
                    f"n={d['n']}   med. {d['median_tokens']:,} tok   H={d['median_entropy']:.3f}",
                    ha="center", va="center", fontsize=FS - 5,
                    color=text_color)

    fig.tight_layout(pad=1.5)
    out = FIG_DIR / "fig6_entropy_quadrant.pdf"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 7 (Q2): Post-tool entropy separation
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig7_posttool_separation() -> None:
    rows = load_csv("posttool_state_shift.csv")

    order = ["baseline", "after_tool_1+", "after_tool_2+", "after_tool_3+"]
    xlabels = ["Baseline\n(turn 0)", "After\ntool 1+", "After\ntool 2+", "After\ntool 3+"]

    idx_map = {r["turn_label"]: r for r in rows if r["turn_label"] in order}

    corr_h  = [float(idx_map[k]["correct_entropy"]) for k in order]
    wrong_h = [float(idx_map[k]["wrong_entropy"])   for k in order]
    sep     = [float(idx_map[k]["wrong_minus_correct_entropy"]) for k in order]

    x  = np.arange(len(order))
    FS = 18

    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
    fig.subplots_adjust(wspace=0.32)

    # ── left: entropy lines ────────────────────────────────────────────────────
    ax = axes[0]
    ax.plot(x, corr_h,  "o-", color=C_CORRECT, linewidth=2.5, markersize=9, label="Correct")
    ax.plot(x, wrong_h, "s-", color=C_WRONG,   linewidth=2.5, markersize=9, label="Wrong")
    ax.fill_between(x, corr_h, wrong_h,
                    where=[w >= c for c, w in zip(corr_h, wrong_h)],
                    alpha=0.12, color=C_WRONG)

    ax.set_xticks(x)
    ax.set_xticklabels(xlabels, fontsize=FS)
    ax.set_ylabel("Mean entropy (nat)", fontsize=FS, labelpad=10)
    ax.set_ylim(0.20, 0.68)
    ax.tick_params(axis="y", labelsize=FS - 2)
    ax.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.7)
    ax.legend(frameon=False, fontsize=FS - 1)

    # ── right: separation bar chart ────────────────────────────────────────────
    ax2 = axes[1]
    colors = ["#aaaaaa" if s < 0 else C_WRONG for s in sep]
    ax2.bar(x, sep, color=colors, alpha=0.82, width=0.55)

    # labels above/below bars with enough clearance
    for xi, s in enumerate(sep):
        if s >= 0:
            ax2.text(xi, s + 0.012, f"{s:+.3f}", ha="center", va="bottom",
                     fontsize=FS - 1, color="#1a1a1a",
                     fontweight="bold" if abs(s) > 0.10 else "normal")
        else:
            ax2.text(xi, s - 0.012, f"{s:+.3f}", ha="center", va="top",
                     fontsize=FS - 1, color="#555555")

    ax2.axhline(0, color="#333333", linewidth=0.9)
    ax2.set_xticks(x)
    ax2.set_xticklabels(xlabels, fontsize=FS)
    ax2.set_ylabel(r"$\Delta$ entropy (wrong $-$ correct)", fontsize=FS, labelpad=10)
    ax2.set_ylim(min(sep) - 0.08, max(sep) + 0.09)
    ax2.tick_params(axis="y", labelsize=FS - 2)
    ax2.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.7)

    fig.tight_layout(pad=1.8)
    out = FIG_DIR / "fig7_posttool_separation.pdf"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 8 (Q3): Verification conversion gap
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig8_verify_conversion() -> None:
    rows = load_csv("verification_conversion.csv")

    harnesses      = ["openclaw", "opencode", "zeroclaw"]
    harness_labels = ["OpenClaw", "OpenCode", "ZeroClaw"]
    metrics        = ["verify_rate", "verify_before_answer_rate", "post_verify_action_change_rate"]
    x_labels       = ["Verify rate", "Verify before answer\n(VBA)", "Post-verify action\nchange (PVAC)"]

    data: dict = {}
    for r in rows:
        key = (r["harness"], r["correct"] == "True" or r["correct"] == "1")
        data[key] = {m: float(r[m]) for m in metrics}

    FS = 18
    fig, axes = plt.subplots(1, 3, figsize=(17, 6.5), sharey=False)
    fig.subplots_adjust(wspace=0.30)

    for mi, (metric, xlabel) in enumerate(zip(metrics, x_labels)):
        ax = axes[mi]
        x     = np.arange(len(harnesses))
        width = 0.38

        wrong_vals   = [data.get((h, False), {}).get(metric, 0) for h in harnesses]
        correct_vals = [data.get((h, True),  {}).get(metric, 0) for h in harnesses]

        ax.bar(x - width/2, wrong_vals,   width, color=C_WRONG,   alpha=0.80,
               label="Wrong"   if mi == 0 else "")
        ax.bar(x + width/2, correct_vals, width, color=C_CORRECT, alpha=0.80,
               label="Correct" if mi == 0 else "")

        # percentage labels — keep clear of bar top
        top_max = max(wrong_vals + correct_vals)
        ax.set_ylim(0, top_max * 1.28)
        for vals, sign in [(wrong_vals, -width/2), (correct_vals, width/2)]:
            for xi, v in enumerate(vals):
                ax.text(xi + sign, v + top_max * 0.03,
                        f"{v*100:.0f}%", ha="center", va="bottom", fontsize=FS - 4)

        ax.set_xticks(x)
        ax.set_xticklabels(harness_labels, fontsize=FS)
        ax.tick_params(axis="y", labelsize=FS - 3)
        ax.set_xlabel(xlabel, fontsize=FS, labelpad=10)
        ax.set_ylabel("Rate" if mi == 0 else "", fontsize=FS, labelpad=8)
        ax.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.7)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v*100:.0f}%"))
        if mi == 0:
            ax.legend(frameon=False, fontsize=FS - 2, loc="upper right")

    fig.tight_layout(pad=1.8)
    out = FIG_DIR / "fig8_verify_conversion.pdf"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 9 (Q4): Deployable accuracy + paired net gain
# ──────────────────────────────────────────────────────────────────────────────

def plot_fig9_deployable_accuracy() -> None:
    tax_rows  = load_csv("operational_tax_adjusted_accuracy.csv")
    gain_rows = load_csv("paired_net_gain.csv")

    harness_order  = ["directllm", "openclaw", "opencode", "zeroclaw"]
    harness_labels = ["DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw"]

    tax_data  = {r["harness"]: r for r in tax_rows}
    gain_data = {r["harness"]: r for r in gain_rows}

    beh_acc = [float(tax_data[h]["behavioral_accuracy"]) for h in harness_order]
    op_tax  = [float(tax_data[h]["operational_tax"])     for h in harness_order]
    dep_acc = [float(tax_data[h]["deployable_accuracy"]) for h in harness_order]

    x  = np.arange(len(harness_order))
    FS = 18

    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    fig.subplots_adjust(wspace=0.32)

    # ── left: accuracy decomposition ──────────────────────────────────────────
    ax    = axes[0]
    width = 0.52

    ax.bar(x, dep_acc, width, color=C_CORRECT, alpha=0.85, label="Deployable accuracy")
    ax.bar(x, op_tax,  width, bottom=dep_acc,  color=C_WRONG, alpha=0.45, hatch="//",
           label="Operational tax")

    # DirectLLM reference line — label positioned at left edge to avoid overlap
    directllm_dep = dep_acc[0]
    ax.axhline(directllm_dep, color="#333333", linewidth=1.2, linestyle="--", alpha=0.6)
    ax.text(0.02, directllm_dep + 0.012,
            f"DirectLLM  {directllm_dep*100:.1f}%",
            fontsize=FS - 4, color="#333333", va="bottom",
            transform=ax.get_yaxis_transform())

    # deployable % inside bar (white bold); behavioral % above tax bar only when gap > 2pp
    for xi, (dep, beh, tax) in enumerate(zip(dep_acc, beh_acc, op_tax)):
        ax.text(xi, dep * 0.5, f"{dep*100:.1f}%",
                ha="center", va="center", fontsize=FS - 3,
                color="white", fontweight="bold")
        if tax > 0.02:
            ax.text(xi, dep + tax + 0.018, f"beh {beh*100:.1f}%",
                    ha="center", va="bottom", fontsize=FS - 5, color="#444444")

    ax.set_xticks(x)
    ax.set_xticklabels(harness_labels, fontsize=FS)
    ax.set_ylim(0, 1.10)
    ax.tick_params(axis="y", labelsize=FS - 3)
    ax.set_ylabel("Accuracy", fontsize=FS, labelpad=10)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v*100:.0f}%"))
    ax.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.7)
    ax.legend(frameon=False, fontsize=FS - 3, loc="upper right")

    # ── right: rescue / regression / net gain ─────────────────────────────────
    ax2           = axes[1]
    harness_agent = ["openclaw", "opencode", "zeroclaw"]
    agent_labels  = ["OpenClaw", "OpenCode", "ZeroClaw"]
    xa            = np.arange(len(harness_agent))

    rescues     = [int(gain_data[h]["rescue"])          for h in harness_agent]
    regressions = [int(gain_data[h]["regression"])      for h in harness_agent]
    net_gains   = [int(gain_data[h]["paired_net_gain"]) for h in harness_agent]

    bw = 0.26
    ax2.bar(xa - bw, rescues,     bw, color="#2c7bb6", alpha=0.85, label="Rescue")
    ax2.bar(xa,      regressions, bw, color=C_WRONG,   alpha=0.85, label="Regression")
    ax2.bar(xa + bw, net_gains,   bw,
            color=["#2c7bb6" if g > 0 else C_WRONG for g in net_gains],
            alpha=0.65, label="Net gain")

    # labels: fixed vertical gap based on axis range
    y_pad = 0.8
    for xi, (r, reg, ng) in enumerate(zip(rescues, regressions, net_gains)):
        ax2.text(xi - bw, r   + y_pad, str(r),   ha="center", va="bottom", fontsize=FS - 2)
        ax2.text(xi,      reg + y_pad, str(reg),  ha="center", va="bottom", fontsize=FS - 2)
        sign = "+" if ng >= 0 else ""
        col  = "#2c7bb6" if ng > 0 else C_WRONG
        if ng >= 0:
            ax2.text(xi + bw, ng + y_pad,   f"{sign}{ng}", ha="center", va="bottom",
                     fontsize=FS - 2, fontweight="bold", color=col)
        else:
            ax2.text(xi + bw, ng - y_pad*2, f"{sign}{ng}", ha="center", va="top",
                     fontsize=FS - 2, fontweight="bold", color=col)

    ax2.axhline(0, color="#333333", linewidth=0.9)
    ax2.set_xticks(xa)
    ax2.set_xticklabels(agent_labels, fontsize=FS)
    ax2.set_ylim(min(net_gains) - 6, max(regressions) + 8)
    ax2.tick_params(axis="y", labelsize=FS - 3)
    ax2.set_ylabel("Task count (vs DirectLLM)", fontsize=FS, labelpad=10)
    ax2.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.7)
    ax2.legend(frameon=False, fontsize=FS - 3, loc="upper right")

    fig.tight_layout(pad=1.8)
    out = FIG_DIR / "fig9_deployable_accuracy.pdf"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 10: 2x2 Harness × Outcome diagnostic grid
# ──────────────────────────────────────────────────────────────────────────────

def _load_task_records_from_dir(d: Path) -> list[dict]:
    """Glob gpqa_*.json, unwrap single-element lists, return per-task summary dicts.

    Returns list of {correct: int, mean_entropy: float, n_tokens: int, traj_steps: int|None}.
    Skips records missing token_entropy_stats.mean (prints a warning).
    """
    records = []
    skipped = 0
    for p in sorted(d.glob("gpqa_*.json")):
        try:
            raw = json.loads(p.read_text())
        except Exception as e:
            print(f"  warning: could not parse {p}: {e}")
            continue
        rec = raw[0] if isinstance(raw, list) else raw
        tes = rec.get("token_entropy_stats")
        if not tes or tes.get("mean") is None:
            skipped += 1
            continue
        traj_steps = len(rec.get("trajectory", [])) or None
        records.append({
            "correct": int(bool(rec.get("correct", False))),
            "mean_entropy": float(tes["mean"]),
            "n_tokens": int(tes.get("n_tokens", 0)),
            "traj_steps": traj_steps,
        })
    if skipped:
        print(f"  warning: skipped {skipped} records missing token_entropy_stats.mean in {d}")
    return records


def _load_openclaw_outcome_csv() -> list[dict]:
    """Read openclaw_entropy_by_outcome.csv and return same shape as _load_task_records_from_dir."""
    rows = load_csv("openclaw_entropy_by_outcome.csv")
    records = []
    for r in rows:
        traj_steps = None
        ts = r.get("traj_steps")
        if ts not in (None, "", "None"):
            try:
                traj_steps = int(float(ts))
            except (ValueError, TypeError):
                pass
        records.append({
            "correct": int(r["correct"]),
            "mean_entropy": float(r["mean_entropy"]),
            "n_tokens": int(float(r["n_tokens"])),
            "traj_steps": traj_steps,
        })
    return records


def plot_fig10_harness_outcome_2x2() -> None:
    """Three side-by-side 2×2 paired contingency tables (one per agent harness).

    Rows = DirectLLM outcome (Correct / Wrong)
    Cols = Harness outcome (Correct / Wrong)

    Cell semantics:
      (Direct ✓, Harness ✓) = both_correct   → neutral blue
      (Direct ✓, Harness ✗) = regression      → red
      (Direct ✗, Harness ✓) = rescue          → blue
      (Direct ✗, Harness ✗) = both_wrong      → neutral gray

    Below each grid: accuracy summary strip comparing DirectLLM vs harness
    (behavioral acc / deployable acc / operational tax).
    """
    gain_rows = load_csv("paired_net_gain.csv")
    tax_rows  = load_csv("operational_tax_adjusted_accuracy.csv")
    gain_data = {r["harness"]: r for r in gain_rows}
    tax_data  = {r["harness"]: r for r in tax_rows}

    DIRECT_BEH = float(tax_data["directllm"]["behavioral_accuracy"])
    DIRECT_DEP = float(tax_data["directllm"]["deployable_accuracy"])

    harnesses      = ["openclaw",  "opencode",  "zeroclaw"]
    harness_labels = ["OpenClaw",  "OpenCode",  "ZeroClaw"]

    CELL_COLORS = {
        (0, 0): "#c6dbef",   # both_correct  — light blue
        (0, 1): "#d6604d",   # regression    — red
        (1, 0): "#2c7bb6",   # rescue        — blue
        (1, 1): "#d9d9d9",   # both_wrong    — gray
    }
    CELL_TEXT_COLOR = {
        (0, 0): "#1a1a1a",
        (0, 1): "white",
        (1, 0): "white",
        (1, 1): "#1a1a1a",
    }
    CELL_LABEL = {
        (0, 0): "Both correct",
        (0, 1): "Regression\n(Direct ✓ / Harness ✗)",
        (1, 0): "Rescue\n(Direct ✗ / Harness ✓)",
        (1, 1): "Both wrong",
    }

    FS = 14
    # extra vertical space below each grid for the accuracy strip
    fig, axes = plt.subplots(1, 3, figsize=(15, 6.2))
    fig.subplots_adjust(wspace=0.12)

    for ax, harness, label in zip(axes, harnesses, harness_labels):
        d  = gain_data[harness]
        td = tax_data[harness]
        total = int(d["paired_valid_tasks"])
        cells = {
            (0, 0): int(d["both_correct"]),
            (0, 1): int(d["regression"]),
            (1, 0): int(d["rescue"]),
            (1, 1): int(d["both_wrong"]),
        }

        beh = float(td["behavioral_accuracy"])
        dep = float(td["deployable_accuracy"])
        tax = float(td["operational_tax"])

        # coordinate space: 2×2 grid occupies y=[0,2]; strip below at y=[-1.1, -0.05]
        ax.set_xlim(0, 2)
        ax.set_ylim(-1.15, 2)
        ax.set_aspect("equal")
        ax.axis("off")

        # ── 2×2 cells ─────────────────────────────────────────────────────────
        for (ri, ci), n in cells.items():
            x, y = ci, 1 - ri
            rect = plt.Rectangle((x, y), 1, 1,
                                  facecolor=CELL_COLORS[(ri, ci)],
                                  edgecolor="white", linewidth=2)
            ax.add_patch(rect)

            pct = n / total * 100
            tc  = CELL_TEXT_COLOR[(ri, ci)]
            ax.text(x + 0.5, y + 0.62, f"n = {n}",
                    ha="center", va="center",
                    fontsize=FS + 1, fontweight="bold", color=tc)
            ax.text(x + 0.5, y + 0.38, f"{pct:.1f}%",
                    ha="center", va="center",
                    fontsize=FS - 1, color=tc)
            ax.text(x + 0.5, y + 0.15, CELL_LABEL[(ri, ci)],
                    ha="center", va="center",
                    fontsize=FS - 5, color=tc, linespacing=1.3)

        # ── column / row headers ───────────────────────────────────────────────
        ax.text(0.5, 2.12, "Harness ✓", ha="center", va="bottom",
                fontsize=FS - 1, color="#2c7bb6", fontweight="bold")
        ax.text(1.5, 2.12, "Harness ✗", ha="center", va="bottom",
                fontsize=FS - 1, color="#d6604d", fontweight="bold")
        ax.text(-0.08, 1.5, "Direct ✓", ha="right", va="center",
                fontsize=FS - 1, rotation=90, color="#555555", fontweight="bold")
        ax.text(-0.08, 0.5, "Direct ✗", ha="right", va="center",
                fontsize=FS - 1, rotation=90, color="#555555", fontweight="bold")

        # ── title ─────────────────────────────────────────────────────────────
        net = int(d["paired_net_gain"])
        net_str = f"+{net}" if net >= 0 else str(net)
        net_col = "#2c7bb6" if net >= 0 else "#d6604d"
        ax.set_title(f"{label}  (n={total} paired)",
                     fontsize=FS, pad=28, color="#1a1a1a")

        # ── accuracy strip ─────────────────────────────────────────────────────
        # three columns: label | value | Δ vs Direct
        X_LABEL = 0.04   # left-align
        X_VALUE = 1.12   # center
        X_DELTA = 1.96   # right-align

        strip = plt.Rectangle((0, -1.10), 2, 1.0,
                               facecolor="#f4f4f4", edgecolor="#cccccc",
                               linewidth=0.8)
        ax.add_patch(strip)
        ax.plot([0, 2], [-0.10, -0.10], color="#cccccc", linewidth=0.8)

        beh_delta = beh - DIRECT_BEH
        dep_delta = dep - DIRECT_DEP
        beh_col   = "#2c7bb6" if beh_delta >= 0 else "#d6604d"
        dep_col   = "#2c7bb6" if dep_delta >= 0 else "#d6604d"
        tax_col   = "#d6604d" if tax > 0.05 else "#777777"

        def _sign(v):
            return f"+{v*100:.1f} pp" if v >= 0 else f"{v*100:.1f} pp"

        rows_strip = [
            (-0.30, "Behavioral acc",  f"{beh*100:.1f}%", _sign(beh_delta), beh_col),
            (-0.57, "Operational tax", f"{tax*100:.1f}%", "",               tax_col),
            (-0.84, "Deployable acc",  f"{dep*100:.1f}%", _sign(dep_delta), dep_col),
        ]
        for y_s, lbl, val, delta, col in rows_strip:
            ax.text(X_LABEL, y_s, lbl, ha="left", va="center",
                    fontsize=FS - 4, color="#555555")
            ax.text(X_VALUE, y_s, val, ha="center", va="center",
                    fontsize=FS - 3, color=col, fontweight="bold")
            if delta:
                ax.text(X_DELTA, y_s, delta, ha="right", va="center",
                        fontsize=FS - 4, color=col)

        ax.text(1.0, -1.08, f"net gain: {net_str}",
                ha="center", va="center", fontsize=FS - 2,
                color=net_col, fontweight="bold")

    # DirectLLM baseline footnote
    fig.text(0.5, -0.01,
             f"DirectLLM baseline — behavioral acc: {DIRECT_BEH*100:.1f}%   "
             f"deployable acc: {DIRECT_DEP*100:.1f}%   operational tax: 0.0%",
             ha="center", va="top", fontsize=FS - 3, color="#666666",
             style="italic")

    fig.suptitle(
        "Paired DirectLLM vs. Harness Outcome  ·  % of paired tasks",
        fontsize=FS + 2, y=1.04,
    )
    out = FIG_DIR / "fig10_harness_outcome_2x2.pdf"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out}")


# ──────────────────────────────────────────────────────────────────────────────
# Fig 11: Tool usage patterns (left: action rates; right: entropy-stratified)
# ──────────────────────────────────────────────────────────────────────────────

# Module-level dict populated by plot_fig11_tool_usage_patterns() so the numbers
# are accessible from other functions / REPL sessions without re-running the plot.
_FIG11_NUMBERS: dict = {}


def _load_action_events() -> list[dict]:
    """Load action_events.csv with field_size_limit guard for large text_span field."""
    csv.field_size_limit(sys.maxsize)
    with open(ACTION_EVENTS_CSV, newline="") as f:
        return list(csv.DictReader(f))


def plot_fig11_tool_usage_patterns() -> None:
    """Two-panel figure: left = action rates per harness×outcome grouped bar;
    right = entropy-stratified action composition stacked bar.

    Writes computed numbers to analyze_tools/data/fig11_tool_usage_numbers.json.
    """
    global _FIG11_NUMBERS

    HARNESSES = ["openclaw", "opencode", "zeroclaw", "directllm"]
    ACTIONS   = ["answer", "reason", "tool_use", "verify", "plan", "recover"]

    # ── read action events ────────────────────────────────────────────────────
    events = _load_action_events()

    # Build per-trajectory (harness, task_id, sample_index) → {actions set, correct, events list}
    traj_map: dict = {}  # key=(harness, task_id, sample_index) → {correct: str, actions: set, all_events: list}
    for row in events:
        h = row["harness"]
        tid = row["task_id"]
        si = row["sample_index"]
        key = (h, tid, si)
        if key not in traj_map:
            traj_map[key] = {"correct": row["correct"], "actions": set(), "all_events": []}
        traj_map[key]["actions"].add(row["canonical_action"])
        traj_map[key]["all_events"].append(row)

    # ── LEFT panel: action rates per harness × outcome ────────────────────────
    # correct field in action_events.csv uses "True"/"False" strings (not "1"/"0")
    action_rates: dict = {}  # {harness: {outcome: {action: rate, "n_trajectories": int}}}

    for h in HARNESSES:
        action_rates[h] = {}
        for outcome_label, outcome_val in [("correct", "True"), ("wrong", "False")]:
            bucket_keys = [k for k, v in traj_map.items() if k[0] == h and v["correct"] == outcome_val]
            n_traj = len(bucket_keys)
            rates: dict = {}
            for a in ACTIONS:
                n_with = sum(1 for k in bucket_keys if a in traj_map[k]["actions"])
                rates[a] = n_with / n_traj if n_traj > 0 else 0.0
            rates["n_trajectories"] = n_traj
            action_rates[h][outcome_label] = rates

    # ── RIGHT panel: entropy-stratified action composition ────────────────────
    # Collect mean_entropy per (harness, task_id)
    harness_entropy: dict = {h: {} for h in HARNESSES}

    # directllm
    for rec in _load_task_records_from_dir(DIRECTLLM_TASKS):
        # task_id not tracked in helper; we need to re-read to get task_id
        pass
    # Reload with task_ids for entropy lookup
    def _load_entropy_with_ids(d: Path) -> dict:
        """Return {task_id: mean_entropy}."""
        out = {}
        for p in sorted(d.glob("gpqa_*.json")):
            try:
                raw = json.loads(p.read_text())
            except Exception:
                continue
            rec = raw[0] if isinstance(raw, list) else raw
            tes = rec.get("token_entropy_stats")
            if not tes or tes.get("mean") is None:
                continue
            tid = rec.get("task_id", p.stem)
            out[tid] = float(tes["mean"])
        return out

    harness_entropy["directllm"] = _load_entropy_with_ids(DIRECTLLM_TASKS)
    harness_entropy["opencode"]  = _load_entropy_with_ids(OPENCODE_TASKS)
    harness_entropy["zeroclaw"]  = _load_entropy_with_ids(ZEROCLAW_TASKS)

    # openclaw from CSV
    oc_rows = load_csv("openclaw_entropy_by_outcome.csv")
    harness_entropy["openclaw"] = {r["task_id"]: float(r["mean_entropy"]) for r in oc_rows}

    entropy_stratified: dict = {}
    for h in HARNESSES:
        h_ent = harness_entropy[h]
        if not h_ent:
            entropy_stratified[h] = {}
            continue
        ent_vals = list(h_ent.values())
        median_h = float(np.median(ent_vals))

        high_tids = {tid for tid, e in h_ent.items() if e > median_h}
        low_tids  = {tid for tid, e in h_ent.items() if e <= median_h}

        def _stratum_stats(tids: set) -> dict:
            ev = [e for e in events if e["harness"] == h and e["task_id"] in tids]
            n_traj_keys = {(e["task_id"], e["sample_index"]) for e in ev}
            n_traj = len(n_traj_keys)
            total_ev = len(ev)
            fracs: dict = {}
            for a in ACTIONS:
                fracs[a] = sum(1 for e in ev if e["canonical_action"] == a) / total_ev if total_ev > 0 else 0.0
            fracs["n_trajectories"] = n_traj
            fracs["n_events"] = total_ev
            return fracs

        entropy_stratified[h] = {
            "median_mean_entropy": median_h,
            "high": _stratum_stats(high_tids),
            "low":  _stratum_stats(low_tids),
        }

    _FIG11_NUMBERS = {
        "action_rates": action_rates,
        "entropy_stratified": entropy_stratified,
    }

    # Persist numbers
    out_json = DATA_DIR / "fig11_tool_usage_numbers.json"
    with open(out_json, "w") as f:
        json.dump(_FIG11_NUMBERS, f, indent=2)
    print(f"  saved {out_json}")

    # ── plotting ──────────────────────────────────────────────────────────────
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(14, 5.5))

    # LEFT panel — grouped bar chart
    # Layout: 6 action groups; within each group 8 bars (4 harnesses × 2 outcomes)
    # Order: openclaw-correct, openclaw-wrong, opencode-correct, opencode-wrong,
    #        zeroclaw-correct, zeroclaw-wrong, directllm-correct, directllm-wrong
    # Color = outcome (C_CORRECT / C_WRONG); hatch = harness
    harness_hatches = {"openclaw": "", "opencode": "//", "zeroclaw": "\\\\", "directllm": "xx"}
    harness_edge    = {"openclaw": "#103778", "opencode": "#8e375f", "zeroclaw": "#555555", "directllm": "#222222"}

    bar_width = 0.09
    group_gap = 0.85  # x-distance between action group centers

    bar_order = []
    for h in HARNESSES:
        for outcome in ["correct", "wrong"]:
            bar_order.append((h, outcome))

    n_bars = len(bar_order)  # 8
    offsets = np.linspace(-(n_bars - 1) * bar_width / 2,
                          (n_bars - 1) * bar_width / 2,
                          n_bars)

    x_centers = np.arange(len(ACTIONS)) * group_gap

    legend_patches_outcome = [
        mpatches.Patch(color=C_CORRECT, alpha=0.8, label="Correct"),
        mpatches.Patch(color=C_WRONG,   alpha=0.8, label="Wrong"),
    ]
    legend_patches_harness = [
        mpatches.Patch(facecolor="#cccccc", edgecolor=harness_edge[h],
                       hatch=harness_hatches[h], label=h.capitalize())
        for h in HARNESSES
    ]

    for bi, (h, outcome) in enumerate(bar_order):
        rates = action_rates[h][outcome]
        vals = [rates.get(a, 0.0) for a in ACTIONS]
        color = C_CORRECT if outcome == "correct" else C_WRONG
        xs = x_centers + offsets[bi]
        axL.bar(xs, vals, bar_width,
                color=color, alpha=0.78,
                hatch=harness_hatches[h],
                edgecolor=harness_edge[h],
                linewidth=0.6)

    axL.set_xticks(x_centers)
    axL.set_xticklabels(ACTIONS, fontsize=10)
    axL.set_ylabel("Rate (fraction of trajectories with action)", fontsize=10)
    axL.set_ylim(0, 1.08)
    axL.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v*100:.0f}%"))
    axL.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.6)
    axL.set_title("Action Rates by Harness × Outcome", fontsize=11)

    leg1 = axL.legend(handles=legend_patches_outcome, loc="upper left",
                      frameon=False, fontsize=8, title="Outcome", title_fontsize=8)
    axL.add_artist(leg1)
    axL.legend(handles=legend_patches_harness, loc="upper right",
               frameon=False, fontsize=8, title="Harness", title_fontsize=8)

    # RIGHT panel — entropy-stratified stacked bars
    # x positions: [0,0.6, 1.6,2.2, 3.2,3.8, 4.8,5.4] for HIGH/LOW pairs per harness
    x_high_low = []
    x_ticks = []
    x_ticklabels = []
    harness_label_x = []
    base = 0.0
    pair_gap = 0.6
    group_gap_r = 1.0
    bar_w = 0.5

    x_positions = []
    for hi, h in enumerate(HARNESSES):
        x_h = base
        x_l = base + pair_gap
        x_positions.append((h, "high", x_h))
        x_positions.append((h, "low",  x_l))
        harness_label_x.append((h, (x_h + x_l) / 2))
        base = x_l + pair_gap + group_gap_r

    import matplotlib
    tab10 = matplotlib.colormaps["tab10"]
    action_colors = {a: tab10(i) for i, a in enumerate(ACTIONS)}

    # Stacked bars
    cum = defaultdict(float)  # x_pos → cumulative height
    for h, stratum, xp in x_positions:
        hdata = entropy_stratified.get(h, {})
        sdata = hdata.get(stratum, {})
        if not sdata:
            continue
        bottom = 0.0
        for a in ACTIONS:
            frac = sdata.get(a, 0.0)
            axR.bar(xp, frac, bar_w, bottom=bottom,
                    color=action_colors[a], alpha=0.85,
                    label=a if (h == HARNESSES[0] and stratum == "high") else "")
            bottom += frac

    # x-axis labels: H/L under each bar, harness name centered above
    all_xps = [xp for _, _, xp in x_positions]
    stratum_labels = [stratum for _, stratum, _ in x_positions]
    axR.set_xticks(all_xps)
    axR.set_xticklabels(["HIGH" if s == "high" else "LOW" for s in stratum_labels], fontsize=8)
    axR.set_ylim(0, 1.15)
    axR.set_ylabel("Action fraction (within harness×stratum events)", fontsize=9)
    axR.set_title("Entropy-Stratified Action Composition", fontsize=11)
    axR.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.6)

    for h, cx in harness_label_x:
        axR.text(cx, 1.06, h.capitalize(), ha="center", va="bottom", fontsize=9, fontweight="bold")

    axR.legend(loc="upper right", frameon=False, fontsize=8,
               title="Action type", title_fontsize=8, ncol=2)

    fig.tight_layout(pad=1.5)
    out = FIG_DIR / "fig11_tool_usage_patterns.pdf"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out}")


# ─── main ─────────────────────────────────────────────────────────────────────

def _register_palettes() -> None:
    """register_cmap was removed in matplotlib 3.9+; use colormaps.register instead."""
    from matplotlib.colors import LinearSegmentedColormap
    import matplotlib
    for name, colors in PALETTES.items():
        for suffix, cols in [("", colors), ("_r", colors[::-1])]:
            cmap = LinearSegmentedColormap.from_list(name + suffix, cols, N=1024)
            try:
                matplotlib.colormaps.register(cmap, name=name + suffix, force=False)
            except ValueError:
                pass


if __name__ == "__main__":
    _register_palettes()
    set_academic_style(font_size=FONT_SIZE)

    print("Generating figures...")
    plot_fig1_entropy_calibration()
    plot_fig2_openclaw_tokens()
    plot_fig3_tool_boundary_profile()
    plot_fig4_baseline_vs_posttool()
    plot_fig5_openclaw_entropy_scatter()
    plot_fig6_entropy_quadrant()
    plot_fig7_posttool_separation()
    plot_fig8_verify_conversion()
    plot_fig9_deployable_accuracy()
    plot_fig10_harness_outcome_2x2()
    plot_fig11_tool_usage_patterns()
    print(f"\nAll figures saved to {FIG_DIR}/")
