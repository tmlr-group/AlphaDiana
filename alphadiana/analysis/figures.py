"""Publication renderers for explicit measurement tables (no metric computation)."""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _safe_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError(f"Unsafe export name: {name!r}")
    return name


def export_report(
    report: dict, output: Path, *, formats: Sequence[str]
) -> dict[str, Any]:
    if not formats or any(f not in {"pdf", "svg", "png"} for f in formats):
        raise ValueError("Formats must be pdf, svg or png")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Use a new or empty export directory: {output}")
    # Matplotlib stays optional for users needing only execution/structured data.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    )
    exported, skipped = [], []
    style = {
        # Match the paper's compact gray/blue figures without requiring bundled fonts.
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Arimo", "DejaVu Sans"],
        "font.size": 8,
        "axes.titlesize": 9,
        "axes.labelsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "text.color": "#29333E",
        "axes.labelcolor": "#29333E",
        "axes.edgecolor": "#A7ADB5",
        "axes.linewidth": 0.55,
        "xtick.color": "#29333E",
        "ytick.color": "#29333E",
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "savefig.dpi": 300,
    }
    with plt.rc_context(style):
        for name, analysis in report["analyses"].items():
            _safe_name(name)
            for spec in analysis["figures"]:
                stem = f"{name}-{_safe_name(spec['name'])}"
                rows = analysis["tables"][spec["table"]]
                rows = [
                    r
                    for r in rows
                    if r.get(spec["x"]) is not None and r.get(spec["y"]) is not None
                ]
                if not rows:
                    skipped.append(
                        {
                            "figure": stem,
                            "reason": "No applicable measured observations",
                        }
                    )
                    continue
                fig = _render(plt, spec, rows)
                try:
                    for fmt in formats:
                        path = output / f"{stem}.{fmt}"
                        fig.savefig(path, bbox_inches="tight")
                        exported.append(path.name)
                    (output / f"{stem}.data.json").write_text(
                        json.dumps(rows, indent=2, allow_nan=False)
                    )
                finally:
                    plt.close(fig)
    receipt = {"files": exported, "skipped": skipped, "report": "report.json"}
    (output / "export.json").write_text(json.dumps(receipt, indent=2))
    return receipt


def _render(plt, spec: dict, rows: list[dict]):
    groups = (
        list(dict.fromkeys(str(r.get(spec["group"], "")) for r in rows))
        if spec.get("group")
        else [""]
    )
    x, y = spec["x"], spec["y"]
    colors = ["#527DA5", "#0A366B", "#A2A6AC", "#B7CDDE", "#357451", "#B25353"]
    # Presentation identities only: unknown plugin series use the same color cycle.
    paper_colors = {
        "direct": "#A2A6AC",
        "direct_llm": "#A2A6AC",
        "swe-agent": "#A2A6AC",
        "sweagent": "#A2A6AC",
        "terminus2": "#A2A6AC",
        "openclaw": "#B7CDDE",
        "zeroclaw": "#527DA5",
        "opencode": "#0A366B",
        "understanding": "#E41A1C",
        "planning": "#377EB8",
        "reasoning": "#4DAF4A",
        "tool use": "#984EA3",
        "verification": "#FF7F00",
        "finalization": "#A65628",
    }

    def series_color(label, index):
        name = str(label).split(" / ", 1)[0].casefold()
        return paper_colors.get(name, colors[index % len(colors)])

    if spec["kind"] == "matrix":
        xlabels = list(dict.fromkeys(str(r[x]) for r in rows))
        ylabels = list(dict.fromkeys(str(r[y]) for r in rows))
        value = spec.get("value", "count")
        cells = {}
        for row in rows:
            key = str(row[x]), str(row[y])
            if key in cells:
                raise ValueError("Matrix requires one measured value per cell")
            cells[key] = row[value]
        matrix = [[cells.get((a, b), float("nan")) for a in xlabels] for b in ylabels]
        fig, ax = plt.subplots(
            figsize=(max(5.5, len(xlabels) * 0.6 + 3), max(2.2, len(ylabels) * 0.28)),
            layout="constrained",
        )
        limits = spec.get("color_limits") or (None, None)
        im = ax.imshow(
            matrix, cmap="Blues", aspect="auto", vmin=limits[0], vmax=limits[1]
        )
        ax.set_xticks(
            range(len(xlabels)),
            [textwrap.fill(v, 18) for v in xlabels],
            rotation=30,
            ha="right",
        )
        ax.set_yticks(range(len(ylabels)), [textwrap.fill(v, 28) for v in ylabels])
        ax.set(title=spec["title"], xlabel=spec["xlabel"], ylabel=spec["ylabel"])
        fig.colorbar(im, ax=ax, label=spec.get("value_label", value))
        return fig
    if spec["kind"] == "heatmap":
        import math

        columns = min(2, len(groups))
        rows_count = math.ceil(len(groups) / columns)
        fig, axes = plt.subplots(
            rows_count,
            columns,
            figsize=(4.4 * columns, 3.5 * rows_count),
            squeeze=False,
            layout="constrained",
        )
        vmax = max(float(r.get("count", 0)) for r in rows) or 1
        for group, ax in zip(groups, axes.flat):
            subset = [
                r
                for r in rows
                if not spec.get("group") or str(r.get(spec["group"], "")) == group
            ]
            labels = sorted({str(r[x]) for r in subset} | {str(r[y]) for r in subset})
            matrix = [
                [
                    sum(
                        float(r.get("count", 0))
                        for r in subset
                        if str(r[y]) == a and str(r[x]) == b
                    )
                    for b in labels
                ]
                for a in labels
            ]
            im = ax.imshow(matrix, cmap="Blues", vmin=0, vmax=vmax)
            ax.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
            ax.set_yticks(range(len(labels)), labels)
            ax.set(
                title="\n".join(textwrap.wrap(group or spec["title"], width=32)),
                xlabel=spec["xlabel"],
                ylabel=spec["ylabel"],
            )
            fig.colorbar(im, ax=ax, label="Transitions", shrink=0.75)
        for ax in list(axes.flat)[len(groups) :]:
            ax.set_visible(False)
        return fig
    categories = list(dict.fromkeys(str(r[x]) for r in rows))
    category_labels = [
        textwrap.fill(c, 24)
        if spec["kind"] == "stacked_barh"
        else textwrap.fill(c.replace("_", " "), width=18)
        for c in categories
    ]
    # Wrapped run names need enough vertical space between neighboring ticks.
    height = (
        max(
            2.1,
            0.8 + len(categories) * max(
                0.38 * (len(groups) if spec["kind"] == "barh" else 1),
                0.16 * max(len(label.splitlines()) for label in category_labels) + 0.08,
            ),
        )
        if spec["kind"] in {"barh", "stacked_barh"}
        else 2.6
    )
    fig, ax = plt.subplots(figsize=(5.5, height), layout="constrained")
    stacked = [0.0] * len(categories)
    for index, group in enumerate(groups):
        subset = [
            r
            for r in rows
            if not spec.get("group") or str(r.get(spec["group"], "")) == group
        ]
        color = series_color(group, index)
        if spec["kind"] == "stacked_barh":
            values = {str(row[x]): row[y] for row in subset}
            if len(values) != len(subset):
                raise ValueError("Stacked bars require one value per category/series")
            heights = [values.get(category, 0) for category in categories]
            if any(value < 0 for value in heights):
                raise ValueError("Stacked composition requires nonnegative values")
            ax.barh(
                range(len(categories)),
                heights,
                left=stacked,
                height=0.7,
                color=color,
                edgecolor="white",
                linewidth=0.4,
                label=group,
            )
            stacked = [left + value for left, value in zip(stacked, heights)]
            ax.set_yticks(range(len(categories)), category_labels)
        elif spec["kind"] in {"bar", "barh"}:
            width = 0.8 / len(groups)
            positions = [
                categories.index(str(r[x])) - 0.4 + width * (index + 0.5)
                for r in subset
            ]
            values = [r[y] for r in subset]
            bar_colors = (
                color
                if spec.get("group")
                else [
                    series_color(r.get("agent", r[x]), categories.index(str(r[x])))
                    for r in subset
                ]
            )
            draw = ax.barh if spec["kind"] == "barh" else ax.bar
            bars = draw(
                positions,
                values,
                width,
                color=bar_colors,
                label=group or None,
                edgecolor="white",
                linewidth=0.5,
            )
            labels = category_labels
            if spec["kind"] == "barh":
                ax.set_yticks(range(len(categories)), labels)
                if not spec.get("group"):
                    ax.bar_label(bars, fmt="%.2g", padding=3, fontsize=8)
            else:
                ax.set_xticks(range(len(categories)), labels, rotation=0)
        elif spec["kind"] in {"scatter", "line"}:
            if spec["kind"] == "scatter":
                ax.scatter(
                    [r[x] for r in subset],
                    [r[y] for r in subset],
                    label=group or None,
                    color=color,
                    marker=("o", "s", "^", "D")[index % 4],
                    alpha=0.65,
                    s=12,
                )
                if spec.get("ellipse") and len(subset) >= 10:
                    import numpy as np
                    from matplotlib.patches import Ellipse

                    points = np.array([[r[x], r[y]] for r in subset], dtype=float)
                    covariance = np.cov(points, rowvar=False)
                    if np.isfinite(covariance).all():
                        values, vectors = np.linalg.eigh(covariance)
                        order = values.argsort()[::-1]
                        values, vectors = values[order], vectors[:, order]
                        angle = np.degrees(np.arctan2(vectors[1, 0], vectors[0, 0]))
                        center = points.mean(axis=0)
                        axes = 4 * np.sqrt(np.maximum(values, 0))
                        ax.add_patch(
                            Ellipse(
                                center,
                                axes[0],
                                axes[1],
                                angle=angle,
                                facecolor=color,
                                edgecolor=color,
                                alpha=0.15,
                            )
                        )
                        ax.autoscale_view()

            else:
                if spec.get("lower") and spec.get("upper"):
                    ax.fill_between(
                        [r[x] for r in subset],
                        [r[spec["lower"]] for r in subset],
                        [r[spec["upper"]] for r in subset],
                        color=color,
                        alpha=0.18,
                        linewidth=0,
                    )
                ax.plot(
                    [r[x] for r in subset],
                    [r[y] for r in subset],
                    label=group or None,
                    color=color,
                    marker=("o", "s", "^", "D")[index % 4],
                    markersize=2.5,
                    linewidth=1.0,
                    linestyle=("-", "--", "-.", ":")[index % 4],
                    # Bound marker density for long series, including log axes.
                    # Every measured point remains in the line and data export.
                    markevery=max(1, (len(subset) + 23) // 24),
                )
        else:
            plt.close(fig)
            raise ValueError(f"Unsupported plot kind: {spec['kind']}")
    if spec["kind"] in {"barh", "stacked_barh"}:
        ax.margins(x=0.15)
        ax.set_xlim(left=min(0, min(r[y] for r in rows) * 1.15))
        ax.invert_yaxis()
        ax.set(xlabel=spec["ylabel"], ylabel=spec["xlabel"])
    else:
        if spec["kind"] == "bar":
            # Keep autoscaling active until every group's bars have been added.
            ax.set_ylim(bottom=min(0, min(r[y] for r in rows) * 1.05))
        else:
            ax.set_xscale(spec.get("xscale", "linear"))
        ax.set(xlabel=spec["xlabel"], ylabel=spec["ylabel"])
    ax.set_title(spec["title"], loc="left", pad=8)
    ax.grid(
        axis="x" if spec["kind"] in {"barh", "stacked_barh"} else "y",
        color="#E9EDF1",
        linewidth=0.5,
    )
    ax.tick_params(length=2, width=0.5)
    ax.set_axisbelow(True)
    if spec.get("group"):
        handles, labels = ax.get_legend_handles_labels()
        fig.legend(
            handles,
            [textwrap.fill(label, 30) for label in labels],
            loc="outside upper center",
            frameon=False,
            fontsize=8,
            ncol=min(len(groups), 4 if max(map(len, labels)) <= 16 else 3),
            handlelength=1.5,
            columnspacing=1.2,
        )
    return fig
