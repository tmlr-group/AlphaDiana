#!/usr/bin/env python3
"""
plot_action_dag.py — DAG and verification context visualization.

Reads: analyze_tools/data/action_transition_data.csv
       analyze_tools/data/action_motif_data.csv
Writes: analyze_tools/figures/fig12_action_transition_dag.pdf
        analyze_tools/figures/fig13_verify_context.pdf

Run:
    python3 analyze_tools/plot_action_dag.py
"""

from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import networkx as nx
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ─── academic-plot setup ─────────────────────────────────────────────────
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

# ─── Color constants (consistent with plot_figures.py) ──────────────────
C_CORRECT = "#103778"
C_WRONG = "#e3371e"
C_OC = "#103778"
C_OCODE = "#8e375f"
C_ZC = "#555555"

FONT_SIZE = 12  # smaller for multi-panel

ACTION_LABELS = {
    "reason": "Reason",
    "answer": "Answer",
    "verify": "Verify",
    "plan": "Plan",
    "tool_use": "Tool",
    "recover": "Recover",
    "__START__": "Start",
    "__END__": "End",
}

ALL_NODES = ["reason", "answer", "verify", "plan", "tool_use", "recover", "__START__", "__END__"]

HARNESS_ORDER = ["openclaw", "opencode", "zeroclaw"]
HARNESS_LABELS = ["OpenClaw", "OpenCode", "ZeroClaw"]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


# ─── Registration helper (mirrors plot_figures.py) ─────────────────────

def _register_palettes() -> None:
    """Register academic colormaps for matplotlib 3.9+ compatibility."""
    from matplotlib.colors import LinearSegmentedColormap
    for name, colors in PALETTES.items():
        for suffix, cols in [("", colors), ("_r", colors[::-1])]:
            cmap = LinearSegmentedColormap.from_list(name + suffix, cols, N=1024)
            try:
                matplotlib.colormaps.register(cmap, name=name + suffix, force=False)
            except ValueError:
                pass


# ─── Fig 12: Action Transition DAG ─────────────────────────────────────

def plot_fig12_action_transition_dag() -> None:
    """Two-panel DAG: correct (left) vs wrong (right), all harnesses."""
    rows = load_csv(DATA_DIR / "action_transition_data.csv")

    # Filter to "all" harness transitions with probability >= 0.05
    transitions: dict[str, list[dict]] = {"True": [], "False": []}
    for r in rows:
        if r["harness"] != "all":
            continue
        correct = r["correct"]
        if correct not in ("True", "False"):
            continue
        prob = float(r["probability"])
        if prob < 0.05:
            continue
        transitions[correct].append(r)

    # Count node frequencies (for node sizing)
    # Sum up all events for each action type per outcome group
    all_rows = load_csv(DATA_DIR / "action_transition_data.csv")
    node_counts: dict[str, dict[str, int]] = {"True": defaultdict(int), "False": defaultdict(int)}
    for r in all_rows:
        if r["harness"] != "all":
            continue
        correct = r["correct"]
        if correct not in ("True", "False"):
            continue
        from_a = r["from_action"]
        to_a = r["to_action"]
        n = int(r["n_transitions"])
        node_counts[correct][from_a] += n
        if to_a not in ("__START__",):
            node_counts[correct][to_a] += n

    fig, (axL, axR) = plt.subplots(1, 2, figsize=(14, 7))

    tab10 = matplotlib.colormaps["tab10"]
    node_colors = {}
    for i, node in enumerate(ALL_NODES):
        node_colors[node] = tab10(i % 10)

    for ax, correct, title, edge_color in [
        (axL, "True", "Correct", C_CORRECT),
        (axR, "False", "Wrong", C_WRONG),
    ]:
        G = nx.DiGraph()
        for node in ALL_NODES:
            G.add_node(node)

        for r in transitions[correct]:
            from_a = r["from_action"]
            to_a = r["to_action"]
            prob = float(r["probability"])
            count = int(r["n_transitions"])
            G.add_edge(from_a, to_a, weight=prob, count=count)

        # Layout: fix START top-left, END bottom-right
        pos = nx.spring_layout(G, k=1.2, iterations=50, seed=42)
        pos["__START__"] = np.array([-1.2, 1.0])
        pos["__END__"] = np.array([1.2, -1.0])

        # Fix reason at center-top to improve layout
        if "reason" in pos:
            pos["reason"] = np.array([0.0, 0.6])

        # Draw nodes
        node_sizes = []
        for node in ALL_NODES:
            count = node_counts[correct].get(node, 0)
            # Scale size: sqrt for reasonable proportions, clamp
            size = max(300, min(5000, np.sqrt(count) * 80))
            node_sizes.append(size)

        nx.draw_networkx_nodes(
            G, pos, ax=ax,
            nodelist=ALL_NODES,
            node_size=node_sizes,
            node_color=[node_colors[n] for n in ALL_NODES],
            alpha=0.85,
            edgecolors="#333333",
            linewidths=0.8,
        )

        # Draw edges with width proportional to probability
        edges = G.edges(data=True)
        edge_widths = [max(0.5, d["weight"] * 15) for _, _, d in edges]
        edge_alphas = [min(1.0, 0.3 + d["weight"] * 2) for _, _, d in edges]

        nx.draw_networkx_edges(
            G, pos, ax=ax,
            edgelist=[(u, v) for u, v, d in edges],
            width=edge_widths,
            alpha=edge_alphas,
            edge_color=edge_color,
            arrows=True,
            arrowsize=15,
            arrowstyle="-|>",
            connectionstyle="arc3,rad=0.12",
            min_source_margin=20,
            min_target_margin=20,
        )

        # Edge labels (probability as percentage, only >= 0.05)
        edge_labels = {}
        for u, v, d in edges:
            pct = d["weight"] * 100
            if pct >= 5.0:
                edge_labels[(u, v)] = f"{pct:.0f}%"

        nx.draw_networkx_edge_labels(
            G, pos, ax=ax,
            edge_labels=edge_labels,
            font_size=7,
            font_color="#444444",
            label_pos=0.5,
        )

        # Node labels (action names)
        labels = {n: ACTION_LABELS.get(n, n) for n in ALL_NODES}
        nx.draw_networkx_labels(
            G, pos, ax=ax,
            labels=labels,
            font_size=10,
            font_weight="bold",
        )

        ax.set_title(title, fontsize=13, fontweight="bold", pad=10)
        ax.axis("off")

        # Legend for action colors
        legend_elements = [
            mpatches.Patch(facecolor=node_colors[n], edgecolor="#333333",
                           linewidth=0.6, label=ACTION_LABELS.get(n, n))
            for n in ALL_NODES
        ]
        ax.legend(
            handles=legend_elements,
            loc="lower left",
            frameon=True,
            fontsize=6,
            title="Action",
            title_fontsize=7,
            ncol=2,
        )

    fig.suptitle("Action Transition DAG (GPQA, all harnesses)", fontsize=14, y=1.01)
    fig.tight_layout()

    out = FIG_DIR / "fig12_action_transition_dag.pdf"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out}")


# ─── Fig 13: Verification Context Profile ──────────────────────────────

def _build_verify_context_data(rows: list[dict]) -> dict:
    """Build structured verify context data from CSV rows."""
    data: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    # data[harness][correct][context][action] = {"n": int, "fraction": float}

    for r in rows:
        if r["type"] != "verify_context":
            continue
        harness = r["harness"]
        correct = r["correct"]
        context = r["context"]
        action = r["action"]
        n = int(r["n"])
        fraction = float(r["fraction"])
        total = int(r["total_verify_events"]) if r["total_verify_events"] else 0

        if context in ("pre_verify", "post_verify"):
            data[harness][correct][context][action] = {
                "n": n, "fraction": fraction, "total": total
            }
        elif context == "terminal_verify":
            data[harness][correct]["terminal"] = {"__END__": {
                "n": n, "fraction": fraction, "total": total
            }}
        elif context == "verify_to_recover":
            data[harness][correct]["verify_transition"]["recover"] = {
                "n": n, "fraction": fraction, "total": total
            }
        elif context == "verify_to_tool_use":
            data[harness][correct]["verify_transition"]["tool_use"] = {
                "n": n, "fraction": fraction, "total": total
            }
        elif context == "verify_to_answer":
            data[harness][correct]["verify_transition"]["answer"] = {
                "n": n, "fraction": fraction, "total": total
            }

    return data


def _load_motif_deltas(rows: list[dict]) -> list[dict]:
    """Extract top motif deltas (wrong - correct) from motif data.

    Returns list of {motif: str, delta: float, favor: str} sorted by |delta|.
    """
    # Aggregate across all harnesses (we use per-harness motifs as-is)
    deltas: list[dict] = []
    seen: set = set()

    for r in rows:
        if r["type"] != "motif":
            continue
        ngram = r.get("ngram", "")
        if ngram and int(ngram) != 3:
            continue  # Only 3-gram motifs for the chart
        harness = r["harness"]
        motif = r["motif"]
        correct = r["correct"]
        delta_str = r.get("correct_vs_wrong_delta", "")

        if not delta_str or delta_str == "":
            continue
        delta = float(delta_str)
        if abs(delta) < 0.0001:
            continue

        key = (harness, motif)
        if key in seen:
            continue
        seen.add(key)

        favor = "correct" if delta < 0 else "wrong"
        deltas.append({
            "harness": harness,
            "motif": motif,
            "delta": delta * 100,  # convert to percentage points
            "favor": favor,
        })

    # Sort by absolute delta, take top 5 wrong-favoring and top 5 correct-favoring
    wrong_favor = sorted([d for d in deltas if d["favor"] == "wrong"],
                         key=lambda x: -abs(x["delta"]))[:5]
    correct_favor = sorted([d for d in deltas if d["favor"] == "correct"],
                           key=lambda x: -abs(x["delta"]))[:5]

    return wrong_favor + correct_favor


def plot_fig13_verify_context() -> None:
    """Three-panel figure: pre-verify, post-verify, motif differences."""
    rows = load_csv(DATA_DIR / "action_motif_data.csv")
    vc_data = _build_verify_context_data(rows)
    motif_deltas = _load_motif_deltas(rows)

    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    fig.subplots_adjust(wspace=0.35)

    # ── Panel A (left): Pre-verify action distribution ───────────────────
    # Stacked bars: for each (harness, outcome), show the fraction split across pre-verify actions
    ax = axes[0]
    pre_actions = ["reason", "answer", "plan", "tool_use", "recover", "__START__"]
    pre_colors = [matplotlib.colormaps["tab10"](i) for i in range(len(pre_actions))]

    x = np.arange(len(HARNESS_ORDER))
    width = 0.35

    for correct_idx, (correct_val, outcome_color) in enumerate([("True", C_CORRECT), ("False", C_WRONG)]):
        for hi, (harness, h_label) in enumerate(zip(HARNESS_ORDER, HARNESS_LABELS)):
            x_pos = x[hi] + (correct_idx - 0.5) * width * 1.1
            bottom = 0.0
            for ai, (pre_a, pre_c) in enumerate(zip(pre_actions, pre_colors)):
                fraction = vc_data.get(harness, {}).get(correct_val, {}).get("pre_verify", {}).get(pre_a, {}).get("fraction", 0)
                if fraction > 0:
                    ax.bar(x_pos, fraction, width * 0.9, bottom=bottom,
                           color=pre_c, alpha=0.85,
                           label=pre_a.capitalize() if (hi == 0 and correct_idx == 0) else "")
                    bottom += fraction

    # X-axis labels under each pair of bars
    ax.set_xticks(x)
    ax.set_xticklabels(HARNESS_LABELS, fontsize=10)
    ax.set_ylabel("Fraction of verify events", fontsize=10)
    ax.set_title("A: Pre-Verify Action\nDistribution", fontsize=11, fontweight="bold")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v*100:.0f}%"))
    ax.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.6)
    # Add correct/wrong labels under each bar
    for hi in range(len(HARNESS_ORDER)):
        ax.text(x[hi] - width * 0.55, -0.06, "C", ha="center", va="top", fontsize=8,
                color=C_CORRECT, fontweight="bold")
        ax.text(x[hi] + width * 0.55, -0.06, "W", ha="center", va="top", fontsize=8,
                color=C_WRONG, fontweight="bold")

    # ── Panel B (middle): Post-verify action distribution ────────────────
    ax = axes[1]
    post_actions = ["reason", "answer", "verify", "plan", "tool_use", "recover", "__END__"]
    post_colors = [matplotlib.colormaps["tab10"](i) for i in range(len(post_actions))]

    for correct_idx, (correct_val, outcome_color) in enumerate([("True", C_CORRECT), ("False", C_WRONG)]):
        for hi, (harness, h_label) in enumerate(zip(HARNESS_ORDER, HARNESS_LABELS)):
            x_pos = x[hi] + (correct_idx - 0.5) * width * 1.1
            bottom = 0.0
            for ai, (post_a, post_c) in enumerate(zip(post_actions, post_colors)):
                fraction = vc_data.get(harness, {}).get(correct_val, {}).get("post_verify", {}).get(post_a, {}).get("fraction", 0)
                if fraction > 0:
                    ax.bar(x_pos, fraction, width * 0.9, bottom=bottom,
                           color=post_c, alpha=0.85,
                           label=post_a.capitalize() if (hi == 0 and correct_idx == 0) else "")
                    bottom += fraction

    ax.set_xticks(x)
    ax.set_xticklabels(HARNESS_LABELS, fontsize=10)
    ax.set_ylabel("Fraction of verify events", fontsize=10)
    ax.set_title("B: Post-Verify Action\nDistribution", fontsize=11, fontweight="bold")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v*100:.0f}%"))
    ax.grid(axis="y", alpha=0.25, linestyle="--", linewidth=0.6)
    for hi in range(len(HARNESS_ORDER)):
        ax.text(x[hi] - width * 0.55, -0.06, "C", ha="center", va="top", fontsize=8,
                color=C_CORRECT, fontweight="bold")
        ax.text(x[hi] + width * 0.55, -0.06, "W", ha="center", va="top", fontsize=8,
                color=C_WRONG, fontweight="bold")

    # ── Panel C (right): Motif difference bar chart ─────────────────────
    ax = axes[2]

    if motif_deltas:
        # Combine wrong-favoring (top 5) and correct-favoring (top 5)
        wrong_favor = [d for d in motif_deltas if d["favor"] == "wrong"][:5]
        correct_favor = [d for d in motif_deltas if d["favor"] == "correct"][:5]
        top_deltas = wrong_favor + correct_favor

        if top_deltas:
            motifs = [d["motif"] for d in top_deltas]
            deltas = [d["delta"] for d in top_deltas]
            colors = [C_WRONG if d["favor"] == "wrong" else C_CORRECT for d in top_deltas]

            y_pos = np.arange(len(motifs))
            ax.barh(y_pos, deltas, color=colors, alpha=0.82, height=0.6)

            for yi, (m, d, c) in enumerate(zip(motifs, deltas, colors)):
                if d >= 0:
                    ax.text(d + 0.05, yi, f"+{d:.2f}pp", va="center", fontsize=8, color=c)
                else:
                    ax.text(d - 0.05, yi, f"{d:.2f}pp", va="center", fontsize=8, color=c,
                            ha="right")

            ax.set_yticks(y_pos)
            ax.set_yticklabels(motifs, fontsize=8)
            ax.axvline(0, color="#333333", linewidth=0.8)
            ax.set_xlabel("Delta (wrong - correct, pp)", fontsize=10)
        else:
            ax.text(0.5, 0.5, "No motif deltas available", ha="center", va="center",
                    transform=ax.transAxes, fontsize=10, color="#888888")
    else:
        ax.text(0.5, 0.5, "No motif deltas available", ha="center", va="center",
                transform=ax.transAxes, fontsize=10, color="#888888")

    ax.set_title("C: Motif Outcome\nDifferences", fontsize=11, fontweight="bold")
    ax.grid(axis="x", alpha=0.25, linestyle="--", linewidth=0.6)

    # Legend for action type colors (show on Panel A)
    pre_legend = [
        mpatches.Patch(color=pre_colors[i], alpha=0.85,
                       label=pre_lbl)
        for i, pre_lbl in enumerate(["Reason", "Answer", "Plan", "Tool", "Recover", "Start"])
    ]
    axes[0].legend(handles=pre_legend, loc="upper right", frameon=True,
                   fontsize=6, title="Pre-verify action", title_fontsize=7, ncol=1)

    # Panel B legend for post-verify
    post_legend = [
        mpatches.Patch(color=post_colors[i], alpha=0.85,
                       label=post_lbl)
        for i, post_lbl in enumerate(["Reason", "Answer", "Verify", "Plan", "Tool", "Recover", "End"])
    ]
    axes[1].legend(handles=post_legend, loc="upper right", frameon=True,
                   fontsize=6, title="Post-verify action", title_fontsize=7, ncol=1)

    fig.suptitle("Verification Context Profile (GPQA)", fontsize=13, y=1.02)
    fig.tight_layout()

    out = FIG_DIR / "fig13_verify_context.pdf"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out}")


# ─── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    _register_palettes()
    set_academic_style(font_size=FONT_SIZE)

    print("Generating action DAG and verification figures...")
    plot_fig12_action_transition_dag()
    plot_fig13_verify_context()
    print(f"\nAll figures saved to {FIG_DIR}/")


if __name__ == "__main__":
    main()
