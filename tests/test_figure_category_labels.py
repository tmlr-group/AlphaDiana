from pathlib import Path

import pytest

from alphadiana.analysis.analyzer import Analyzer, Figure, TableAnalysis


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
