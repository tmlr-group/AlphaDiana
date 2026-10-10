"""Optional integration against the user's preserved, read-only paper inputs."""
import os
from pathlib import Path

import pytest

from alphadiana.analysis.pipeline import Analyzer
from scripts.paper_analysis.load_figure_tables import PaperFigureTables


@pytest.mark.skipif(not os.environ.get("ALPHADIANA_PAPER_ARCHIVE"), reason="external paper archive not configured")
def test_archive_concepts_and_export(tmp_path):
    archive=PaperFigureTables(os.environ["ALPHADIANA_PAPER_ARCHIVE"])
    analysis=Analyzer([],plugins=[archive])
    report=analysis.compute()
    measured=report["analyses"]["paper_archive"]
    names={figure["name"] for figure in measured["figures"]}
    for prefix in ("macro_", "interactive_", "tool_", "skill_", "memory", "actions_", "transitions_", "post_tool_entropy_", "entropy_scatter_", "error_incidence_", "efficiency_micro_"):
        assert any(name.startswith(prefix) for name in names)
    assert measured["coverage"]["historical"] is True
    assert all(len(source["sha256"]) == 64 for source in measured["coverage"]["sources"])
    assert all(figure["xscale"] == "log" for figure in measured["figures"] if figure["name"].startswith("post_tool_entropy_"))
    receipt=analysis.export(tmp_path/"figures",formats=("pdf",))
    assert len(receipt["files"]) == len(names)
    assert receipt["skipped"] == []
    assert all((tmp_path/"figures"/file).read_bytes().startswith(b"%PDF") for file in receipt["files"])
