from pathlib import Path

import pytest

from alphadiana.analysis.analyzer import Analyzer, Figure, TableAnalysis


def test_grouped_bars_keep_larger_later_groups_visible():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    from alphadiana.analysis.figures import _render

    # Observed SWE Mini counts: the later OpenCode group exceeds OpenClaw.
    rows = [
        {"run": "OpenClaw", "action": "tool_use", "count": 49},
        {"run": "OpenCode", "action": "recover", "count": 80},
    ]
    spec = dict(
        kind="bar", x="action", y="count", group="run",
        title="Actions", xlabel="Action", ylabel="Events",
    )
    fig = _render(plt, spec, rows)
    try:
        fig.canvas.draw()
        ax = fig.axes[0]
        assert ax.get_ylim()[0] == 0
        assert ax.get_ylim()[1] >= 80
        assert [bar.get_height() for bar in ax.patches] == [49, 80]
    finally:
        plt.close(fig)


@pytest.mark.parametrize("grouped", [False, True])
def test_exported_bar_preserves_measured_category_labels(tmp_path: Path, grouped):
    pytest.importorskip("matplotlib")
    rows = [
        {"category": "Direct", "count": 2, "group": "Observed"},
        {"category": "OpenClaw", "count": 3, "group": "Observed"},
    ]
    plugin = TableAnalysis(
        "labels",
        {"counts": rows},
        [
            Figure(
                "counts",
                "counts",
                "bar",
                "category",
                "count",
                "Counts",
                "Category",
                "Count",
                "group" if grouped else None,
            )
        ],
        provenance={"source": "constructed label regression"},
    )
    Analyzer([], plugins=[plugin]).export(tmp_path, formats=["svg"])
    svg = (tmp_path / "labels-counts.svg").read_text()
    assert ">Direct</text>" in svg
    assert ">OpenClaw</text>" in svg


def test_dense_log_line_keeps_data_with_readable_marker_density(tmp_path: Path):
    import json
    import xml.etree.ElementTree as ET

    pytest.importorskip("matplotlib")
    rows = [
        {"position": i * 32 + 1, "entropy": 0.2 + (i % 3) / 100} for i in range(300)
    ]
    plugin = TableAnalysis(
        "dense",
        {"entropy": rows},
        [
            Figure(
                "entropy",
                "entropy",
                "line",
                "position",
                "entropy",
                "Entropy",
                "Position",
                "Nats",
                xscale="log",
            )
        ],
        provenance={"source": "constructed long-request regression"},
    )
    Analyzer([], plugins=[plugin]).export(tmp_path, formats=["svg"])
    assert json.loads((tmp_path / "dense-entropy.data.json").read_text()) == rows
    root = ET.parse(tmp_path / "dense-entropy.svg")
    markers = [
        node
        for node in root.iter()
        if node.tag.endswith("}use") and "fill: #0072b2" in node.get("style", "")
    ]
    assert 0 < len(markers) < 50
