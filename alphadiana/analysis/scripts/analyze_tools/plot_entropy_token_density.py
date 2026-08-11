#!/usr/bin/env python3
"""
plot_entropy_token_density.py — 2D density contour figures for entropy vs token count.

Produces:
    figures/fig_entropy_token_density.pdf      — GPQA cross-harness density (4 harnesses)
    figures/fig_entropy_token_density_hle.pdf  — HLE cross-harness density (3 harnesses)

Run:
    python3 analyze_tools/plot_entropy_token_density.py
    (from repo root)
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib import gridspec

# Academic plot skill (same pattern as plot_figures.py)
SKILL_DIR = os.environ.get("ALPHADIANA_ACADEMIC_PLOT_DIR", "").strip()
if SKILL_DIR and SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from academic_plot import (
    set_academic_style,
    PALETTES,
)

DATA_DIR = Path(__file__).parent / "data"
FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)

# Colour constants (consistent with plot_figures.py)
C_CORRECT = PALETTES["contrast"][0]   # "#103778"  (blue)
C_WRONG   = PALETTES["contrast"][2]   # "#e3371e"  (red)
C_OC      = "#103778"                 # OpenClaw
C_OCODE   = "#8e375f"                 # OpenCode
C_ZC      = "#33BBEE"                 # ZeroClaw
C_DLLM    = "#CC3311"                 # DirectLLM

HARNESS_META: dict[str, dict] = {
    "openclaw":  {"color": C_OC,    "ls": "-",  "label": "OpenClaw"},
    "opencode":  {"color": C_OCODE, "ls": "--", "label": "OpenCode"},
    "zeroclaw":  {"color": C_ZC,    "ls": ":",  "label": "ZeroClaw"},
    "directllm": {"color": C_DLLM,  "ls": "-.", "label": "DirectLLM"},
}

ALL_HARNESSES = ["openclaw", "opencode", "zeroclaw", "directllm"]
GPQA_CSV = DATA_DIR / "gpqa_entropy_by_harness.csv"
HLE_CSV  = DATA_DIR / "hle_entropy_by_outcome.csv"
GRID_SIZE = 100


# ─── helpers ────────────────────────────────────────────────────────────────

def load_csv(path: Path) -> list[dict[str, str]]:
    """Load a CSV file, returning list of dicts."""
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def split_by_outcome(rows: list[dict], harness: str | None = None):
    """Split rows into correct (1) / wrong (0) numpy arrays.

    Returns (correct_x, correct_y, wrong_x, wrong_y) where
    x = log10(n_tokens) and y = mean_entropy.
    """
    correct_x, correct_y = [], []
    wrong_x, wrong_y = [], []

    for r in rows:
        if harness is not None and r["harness"] != harness:
            continue
        n_tokens = float(r["n_tokens"])
        entropy = float(r["mean_entropy"])
        x = np.log10(max(n_tokens, 1))
        correct = int(r["correct"])
        if correct == 1:
            correct_x.append(x)
            correct_y.append(entropy)
        else:
            wrong_x.append(x)
            wrong_y.append(entropy)

    def _to_arr(lst):
        return np.array(lst, dtype=float)

    return (_to_arr(correct_x), _to_arr(correct_y),
            _to_arr(wrong_x), _to_arr(wrong_y))


def compute_kde(x: np.ndarray, y: np.ndarray,
                x_lim: tuple[float, float], y_lim: tuple[float, float]):
    """Compute 2D KDE on a regular grid.

    Returns (xi, yi, Z) or (None, None, None) on failure.
    """
    n = len(x)
    if n < 5:
        return None, None, None

    # Small jitter to avoid duplicate points
    np.random.seed(42)
    x_jitter = x + np.random.uniform(0, 0.005, size=n)

    try:
        from scipy.stats import gaussian_kde
        kde = gaussian_kde(np.vstack([x_jitter, y]))
    except Exception:
        return None, None, None

    xi = np.linspace(x_lim[0], x_lim[1], GRID_SIZE)
    yi = np.linspace(y_lim[0], y_lim[1], GRID_SIZE)
    X, Y = np.meshgrid(xi, yi)
    positions = np.vstack([X.ravel(), Y.ravel()])
    try:
        Z = kde(positions).reshape(X.shape)
    except Exception:
        return None, None, None
    return xi, yi, Z


def compute_hpd_level(Z: np.ndarray, alpha: float = 0.5) -> float:
    """Find the contour level that contains *alpha* fraction of probability mass."""
    z_flat = Z.ravel()
    order = np.argsort(z_flat)[::-1]          # highest density first
    z_sorted = z_flat[order]
    z_norm = z_sorted / z_sorted.sum()
    cumsum = np.cumsum(z_norm)
    idx = int(np.searchsorted(cumsum, alpha, side="left"))
    idx = min(idx, len(z_sorted) - 1)
    return z_sorted[idx]


# ─── data extent (consistent axis limits) ──────────────────────────────────

def compute_limits(rows: list[dict]):
    """Compute global axis limits from a dataset."""
    all_log_tokens = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in rows])
    all_entropy = np.array([float(r["mean_entropy"]) for r in rows])
    x_lim = (max(0.5, all_log_tokens.min() - 0.3), min(6.0, all_log_tokens.max() + 0.3))
    y_lim = (-0.02, min(1.0, all_entropy.max() + 0.05))
    return x_lim, y_lim


# ─── plotting ───────────────────────────────────────────────────────────────

def plot_density_figure(rows: list[dict], harnesses: list[str],
                        out_name: str, title_prefix: str):
    """Create the 2D density contour figure.

    Panel A: 2x2 per-harness subplots with correct/wrong density overlay.
    Panel B: Cross-harness overlay with 50 % HPD contour lines.
    """
    x_lim, y_lim = compute_limits(rows)

    fig = plt.figure(figsize=(14, 9))
    gs = gridspec.GridSpec(2, 3, width_ratios=[1, 1, 1.25], hspace=0.30, wspace=0.30)

    # Panel A: 2x2 per-harness subplots
    ax_cells: dict[str, plt.Axes] = {}
    pos = [("openclaw", 0, 0), ("opencode", 0, 1),
           ("zeroclaw", 1, 0), ("directllm", 1, 1)]

    for harness, row, col in pos:
        ax = fig.add_subplot(gs[row, col])
        ax_cells[harness] = ax

        meta = HARNESS_META[harness]
        correct_x, correct_y, wrong_x, wrong_y = split_by_outcome(rows, harness)

        # Correct trajectory density
        xi_c, yi_c, Z_c = compute_kde(correct_x, correct_y, x_lim, y_lim)
        if Z_c is not None:
            ax.contourf(xi_c, yi_c, Z_c, levels=8, alpha=0.5,
                        colors=[C_CORRECT])
        elif len(correct_x) > 0:
            ax.scatter(10**correct_x, correct_y, s=8, color=C_CORRECT,
                       alpha=0.6, zorder=3)

        # Wrong trajectory density
        xi_w, yi_w, Z_w = compute_kde(wrong_x, wrong_y, x_lim, y_lim)
        if Z_w is not None:
            ax.contourf(xi_w, yi_w, Z_w, levels=8, alpha=0.5,
                        colors=[C_WRONG])
        elif len(wrong_x) > 0:
            ax.scatter(10**wrong_x, wrong_y, s=8, color=C_WRONG,
                       alpha=0.6, zorder=3)

        # Labelling and styling
        ax.set_title(meta["label"], fontsize=14, fontweight="bold")
        ax.set_xscale("log")
        ax.set_xlim(10**x_lim[0], 10**x_lim[1])
        ax.set_ylim(y_lim[0], y_lim[1])
        ax.tick_params(axis="both", labelsize=11)
        ax.grid(True, alpha=0.3)

        # Only label left-column y-axis and bottom-row x-axis
        if col == 0:
            ax.set_ylabel("Mean token entropy (nat)", fontsize=12)
        if row == 1:
            ax.set_xlabel("Output tokens per task (log scale)", fontsize=12)

        # Legend for first subplot only
        if harness == "openclaw":
            from matplotlib.patches import Patch
            legend_elements = [
                Patch(facecolor=C_CORRECT, alpha=0.5, label="Correct"),
                Patch(facecolor=C_WRONG, alpha=0.5, label="Wrong"),
            ]
            ax.legend(handles=legend_elements, loc="upper right",
                      fontsize=10, framealpha=0.85)

    # If a harness has no data (e.g. missing from dataset), note it
    for harness in harnesses:
        if harness not in [p[0] for p in pos]:
            continue
        actual_rows = [r for r in rows if r["harness"] == harness]
        if len(actual_rows) == 0 and harness in ax_cells:
            ax_cells[harness].text(0.5, 0.5, "No data",
                                   transform=ax_cells[harness].transAxes,
                                   ha="center", va="center", fontsize=12,
                                   color="gray", fontstyle="italic")

    # Panel B: cross-harness overlay with 50 % HPD contour
    ax_overlay = fig.add_subplot(gs[:, 2])
    ax_overlay.set_title("Cross-harness overlay (50 % density)", fontsize=14,
                         fontweight="bold")
    ax_overlay.set_xscale("log")
    ax_overlay.set_xlim(10**x_lim[0], 10**x_lim[1])
    ax_overlay.set_ylim(y_lim[0], y_lim[1])
    ax_overlay.set_xlabel("Output tokens per task (log scale)", fontsize=12)
    ax_overlay.set_ylabel("Mean token entropy (nat)", fontsize=12)
    ax_overlay.tick_params(axis="both", labelsize=11)
    ax_overlay.grid(True, alpha=0.3)

    for harness in harnesses:
        meta = HARNESS_META[harness]
        hx = np.array([np.log10(max(float(r["n_tokens"]), 1))
                       for r in rows if r["harness"] == harness])
        hy = np.array([float(r["mean_entropy"])
                       for r in rows if r["harness"] == harness])

        if len(hx) < 5:
            continue

        xi, yi, Z = compute_kde(hx, hy, x_lim, y_lim)
        if Z is None:
            continue

        level_50 = compute_hpd_level(Z)
        ax_overlay.contour(xi, yi, Z, levels=[level_50],
                           colors=[meta["color"]],
                           linestyles=[meta["ls"]], linewidths=2)

    ax_overlay.legend(
        handles=[
            plt.Line2D([0], [0], color=HARNESS_META[h]["color"],
                       ls=HARNESS_META[h]["ls"], lw=2, label=HARNESS_META[h]["label"])
            for h in harnesses
        ],
        loc="upper right", fontsize=10, framealpha=0.85,
    )

    # Super title
    fig.suptitle(f"{title_prefix}: Entropy-Token Density by Harness and Outcome",
                 fontsize=15, y=0.98)

    out_path = FIG_DIR / out_name
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path}")


# ─── main ───────────────────────────────────────────────────────────────────

def main() -> None:
    # register_academic_colormaps() is omitted because newer matplotlib
    # removed cm.register_cmap; we use direct colour strings so no colormap
    # registration is needed.
    set_academic_style(font_size=12)

    # ---- GPQA figure ----
    print("GPQA density figure ...")
    gpqa_rows = load_csv(GPQA_CSV)
    plot_density_figure(gpqa_rows, ALL_HARNESSES,
                        "fig_entropy_token_density.pdf",
                        "GPQA-Diamond")

    # ---- HLE figure (if data available) ----
    if HLE_CSV.exists():
        print("HLE density figure ...")
        hle_rows = load_csv(HLE_CSV)
        hle_harnesses = sorted(set(r["harness"] for r in hle_rows))
        print(f"  HLE harnesses found: {hle_harnesses}")
        plot_density_figure(hle_rows, hle_harnesses,
                            "fig_entropy_token_density_hle.pdf",
                            "HLE")
    else:
        print("HLE data not found at {HLE_CSV}, skipping HLE figure.")

    print("Done.")


if __name__ == "__main__":
    main()
