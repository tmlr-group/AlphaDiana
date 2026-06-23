#!/usr/bin/env python3
"""Shared publication style + kept color theme for the figure revisions.

Self-contained replacement for the missing `academic_plot` package
(`/home/xxx/academic-plot/scripts` is empty on this machine). Import this
from each revised figure script instead of `from academic_plot import ...`.

Color policy (user decision): keep the figures' CURRENT main theme.
  - Action palette (Fig 4 + Fig 5): the project's existing 6-action colors.
  - Outcome colors (Fig 6 + Fig 7): the current green (correct) / red (wrong).
Legends are NOT drawn in-figure; the marker/color key goes in the caption.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap


# ── publication rcParams ──────────────────────────────────────────────────────

def apply_publication_style(font_size: float = 7, axes_linewidth: float = 0.8) -> None:
    """Nature-style rcParams with editable SVG/PDF text. Call once before plotting."""
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "svg.fonttype": "none",   # editable text in SVG
        "pdf.fonttype": 42,       # editable TrueType text in PDF
        "font.size": font_size,
        "axes.spines.right": False,
        "axes.spines.top": False,
        "axes.linewidth": axes_linewidth,
        "legend.frameon": False,
    })


# ── kept color theme ──────────────────────────────────────────────────────────

# Action palette — identical to plot_action_chords.py (shared by Fig 4 and Fig 5).
ACTION_LABELS = ["Understanding", "Planning", "Reasoning",
                 "Tool Use", "Verification", "Finalization"]
ACTION_COLORS = {
    "Understanding": "#e41a1c",
    "Planning":      "#377eb8",
    "Reasoning":     "#4daf4a",
    "Tool Use":      "#984ea3",
    "Verification":  "#ff7f00",
    "Finalization":  "#a65628",
}
# Old six-action names → publication names (matches plot_action_chords.OLD_TO_NEW).
OLD_TO_NEW = {
    "Problem Framing":    "Understanding",
    "Plan Formation":     "Planning",
    "Solution Execution": "Reasoning",
    "Tool Grounding":     "Tool Use",
    "Result Auditing":    "Verification",
    "Answer Delivery":    "Finalization",
}

# Outcome colors — current green/red theme (Fig 6 + Fig 7).
C_CORRECT = "#2ca02c"
C_WRONG   = "#d62728"


def graded_cmap(hex_color: str, name: str) -> LinearSegmentedColormap:
    """White -> hex_color colormap for filled KDE density."""
    return LinearSegmentedColormap.from_list(name, [(1, 1, 1), hex_color])


# ── small helpers ─────────────────────────────────────────────────────────────

def add_panel_label(ax, label: str, x: float = -0.08, y: float = 1.02,
                    fontsize: float = 10, fontweight: str = "bold") -> None:
    """Nature-style bold panel letter near the top-left of an axes."""
    ax.text(x, y, label, transform=ax.transAxes, fontsize=fontsize,
            fontweight=fontweight, ha="left", va="bottom")


def finalize_figure(fig, out_path_noext, dpi: int = 300,
                    formats=("pdf", "png", "svg")) -> list[str]:
    """Save fig as several formats (no extension on out_path_noext). Returns paths."""
    base = Path(out_path_noext)
    base.parent.mkdir(parents=True, exist_ok=True)
    saved = []
    for fmt in formats:
        fp = f"{base}.{fmt}"
        fig.savefig(fp, dpi=dpi, bbox_inches="tight")
        saved.append(fp)
    plt.close(fig)
    return saved
