"""Publication renderers for explicit measurement tables (no metric computation)."""

from __future__ import annotations

import json
import re
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
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
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
    colors = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"]
    if spec["kind"] == "heatmap":
        import math
        import textwrap

        columns = min(2, len(groups))
        rows_count = math.ceil(len(groups) / columns)
        fig, axes = plt.subplots(
            rows_count,
            columns,
            figsize=(5.8 * columns, 5.0 * rows_count),
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
            im = ax.imshow(matrix, cmap="viridis", vmin=0, vmax=vmax)
            ax.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
            ax.set_yticks(range(len(labels)), labels)
            ax.set(
                title="\n".join(textwrap.wrap(group or spec["title"], width=32)),
                xlabel=spec["xlabel"],
                ylabel=spec["ylabel"],
            )
            fig.colorbar(im, ax=ax, label="Transitions")
        for ax in list(axes.flat)[len(groups) :]:
            ax.set_visible(False)
        return fig
    fig, ax = plt.subplots(figsize=(7.2, 4.2), layout="constrained")
    categories = list(dict.fromkeys(str(r[x]) for r in rows))
    for index, group in enumerate(groups):
        subset = [
            r
            for r in rows
            if not spec.get("group") or str(r.get(spec["group"], "")) == group
        ]
        color = colors[index % len(colors)]
        if spec["kind"] == "bar":
            width = 0.8 / len(groups)
            positions = [
                categories.index(str(r[x])) - 0.4 + width * (index + 0.5)
                for r in subset
            ]
            ax.bar(
                positions,
                [r[y] for r in subset],
                width,
                color=color,
                label=group or None,
                hatch=("", "//", "..", "xx")[index % 4],
            )
            ax.set_xticks(range(len(categories)), categories, rotation=35, ha="right")
            ax.set_ylim(bottom=0)
        elif spec["kind"] in {"scatter", "line"}:
            if spec["kind"] == "scatter":
                ax.scatter(
                    [r[x] for r in subset],
                    [r[y] for r in subset],
                    label=group or None,
                    color=color,
                    marker=("o", "s", "^", "D")[index % 4],
                    alpha=0.8,
                )
            else:
                ax.plot(
                    [r[x] for r in subset],
                    [r[y] for r in subset],
                    label=group or None,
                    color=color,
                    marker=("o", "s", "^", "D")[index % 4],
                )
        else:
            plt.close(fig)
            raise ValueError(f"Unsupported plot kind: {spec['kind']}")
    ax.set(title=spec["title"], xlabel=spec["xlabel"], ylabel=spec["ylabel"])
    ax.set_xscale(spec.get("xscale", "linear"))
    ax.grid(axis="y", alpha=0.2)
    ax.set_axisbelow(True)
    if spec.get("group"):
        ax.legend(frameon=False, fontsize=8, ncol=2 if len(groups) > 4 else 1)
    return fig
