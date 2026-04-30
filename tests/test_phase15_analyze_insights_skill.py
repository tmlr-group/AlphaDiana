from __future__ import annotations

import re
from pathlib import Path


SKILL_PATH = Path(".agents/skills/analyze-insights/SKILL.md")
CONTEXT_NOTE_PATH = Path("context/phase15-insight-analysis/skills.md")
CONTEXT_INDEX_PATH = Path("context/README.md")
STANDARD_COMMAND = (
    "python scripts/analyze_behavior_insights.py --results-dir results "
    "--output-dir results/phase15_behavior_insights"
)


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(?P<body>.*?)\n---\n", text, flags=re.DOTALL)
    assert match, "skill must start with YAML frontmatter"
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, value = line.split(": ", 1)
        fields[key] = value
    return fields


def test_analyze_insights_skill_frontmatter_and_sop():
    text = _read_skill()
    fields = _frontmatter(text)

    assert fields == {
        "name": "analyze-insights",
        "description": (
            "Mine mechanism-level insights from persisted AlphaDiana harness results. "
            "Use when analyzing benchmark artifacts for hidden variables, decisive falsifier measurements, "
            "model-harness behavior, trajectory/cost/error mechanisms, denominator-scoped support, "
            "deterministic case anchors, or paper-ready narrative replacements."
        ),
    }
    assert "# Analyze Insights" in text
    assert "## Workflow" in text
    for phrase in (
        "Define the research object as a model-harness composite.",
        "Build the denominator ledger before metrics.",
        "Formulate the falsifier before computing measurements.",
        "Use deterministic parsers and reviewable snippets.",
        "Name the mechanism only after denominator checks.",
        "Write the narrative replacement with caveats.",
    ):
        assert phrase in text

    assert "## Standard Command" in text
    assert STANDARD_COMMAND in text
    assert "## Required Checks" in text
    for artifact in (
        "results/phase15_behavior_insights/corpus_inventory.json",
        "results/phase15_behavior_insights/insight_claims.json",
        "results/phase15_behavior_insights/case_anchors.json",
    ):
        assert artifact in text
    assert (
        'rg -n "[/]data0|[/]data2|[/]home[/]|sk-[A-Za-z0-9_-]{8,}|api[_-]?key" '
        "results/phase15_behavior_insights context/phase15-insight-analysis"
    ) in text

    assert "## Do Not" in text
    for forbidden in (
        "Do not launch live provider calls.",
        "Do not use LLM-assisted primary labeling.",
        "Do not make raw accuracy-only claims.",
        "Do not commit absolute paths.",
    ):
        assert forbidden in text


def test_skill_context_note_is_indexed_and_path_safe():
    note = CONTEXT_NOTE_PATH.read_text(encoding="utf-8")
    index = CONTEXT_INDEX_PATH.read_text(encoding="utf-8")

    assert "# Project-Local Analyze Insights Skill" in note
    assert ".agents/skills/analyze-insights/SKILL.md" in note
    assert STANDARD_COMMAND in note
    assert "does not mutate" in note
    assert "results/phase15_behavior_insights/behavior_insights.md" in note
    assert "results/phase15_behavior_insights/insight_claims.json" in note
    assert "context/phase15-insight-analysis/run_evidence.md" in note
    assert "denominator ledger" in note
    assert "phase15-insight-analysis/skills.md" in index
    assert not re.search(r"/home/|/data0|/data2", note)
