"""Report writers and CLI output use the same English artifact contract."""

import json

import pytest

from alphadiana.analysis import render_trajectory_discussion
from alphadiana.analysis.actions.trajectory_report import write_trajectory_outputs
from alphadiana.analysis.insights.reports import write_behavior_insight_outputs


def test_behavior_writer_preserves_evidence_and_writes_one_report(tmp_path):
    denominators = [{"corpus_label": "fixture", "selected_records": 2, "valid_scored": 1}]
    events = [{"task_id": "task", "text_span": "Reasoning evidence"}]
    paths = write_behavior_insight_outputs(
        tmp_path,
        denominator_rows=denominators,
        event_rows=events,
        insight_claims=[],
        case_anchors=[],
    )
    assert {p.name for p in tmp_path.iterdir()} == {
        "corpus_inventory.json", "corpus_inventory.csv", "action_events.csv",
        "insight_claims.json", "case_anchors.json", "behavior_insights.md",
    }
    assert json.loads(paths["corpus_inventory_json"].read_text()) == denominators
    assert "Reasoning evidence" in paths["action_events_csv"].read_text()
    report = paths["markdown"].read_text()
    assert report.startswith("# Model-Harness Behavior Insights")
    assert "fixture" in report
    assert "behavior_insights_zh" not in report


def test_trajectory_writer_keeps_metric_values_in_english_discussion(tmp_path):
    metrics = {
        "action_allocation": [
            dict(harness="low", canonical_action="verify", delta_correct_minus_incorrect=-0.1),
            dict(harness="high", canonical_action="verify", delta_correct_minus_incorrect=0.2),
        ],
        "diagnostics": {"sequence_count": 3, "valid_scored_sequence_count": 2},
    }
    paths = write_trajectory_outputs(
        tmp_path, inventory={}, event_rows=[], metrics=metrics, case_studies=[],
    )
    assert {p.name for p in tmp_path.iterdir()} == {
        "corpus_inventory.json", "action_events.csv", "trajectory_metrics.json",
        "case_studies.json", "gpqa_trajectory_appendix.tex", "gpqa_trajectory_discussion.md",
    }
    assert json.loads(paths["trajectory_metrics"].read_text()) == metrics
    report = paths["discussion"].read_text()
    assert report == render_trajectory_discussion({}, metrics, [], output_files=paths)
    assert "Sequences: 3; valid-scored sequences: 2" in report
    assert "ranges from `low` (-10.0 pp) to `high` (+20.0 pp)" in report
    assert "Metric Definitions" in report


@pytest.mark.parametrize("report_kind", ["behavior", "trajectory"])
@pytest.mark.parametrize("summary_input", ["omitted", "missing", "provided"])
def test_offline_cli_stdout_reads_english_report(tmp_path, capsys, monkeypatch, report_kind, summary_input):
    from alphadiana.analysis.scripts import analyze_behavior_insights, analyze_gpqa_trajectories
    from alphadiana.analysis.insights.corpus import ENV_CORPUS_VARS

    for name in ENV_CORPUS_VARS:
        monkeypatch.delenv(name, raising=False)
    results = tmp_path / "results"
    results.mkdir()
    output = tmp_path / "output"
    args = ["--results-dir", str(results), "--output-dir", str(output), "--stdout"]
    if summary_input != "omitted":
        summary_path = tmp_path / "measurement_summary.json"
        if summary_input == "provided":
            summary_path.write_text(json.dumps({
                "paired_net_gain": [{"harness": "fixture", "paired_net_gain": 2}]
            }))
        args.extend(["--measurement-summary", str(summary_path)])
    if report_kind == "behavior":
        cli = analyze_behavior_insights
        args.append("--no-include-hf-synced")
        filename = "behavior_insights.md"
    else:
        cli = analyze_gpqa_trajectories
        filename = "gpqa_trajectory_discussion.md"
    assert cli.main(args) == 0
    stdout = capsys.readouterr().out
    assert (output / filename).read_text() in stdout
    assert not list(output.glob("*_zh.md"))
    if report_kind == "trajectory":
        metrics = json.loads((output / "trajectory_metrics.json").read_text())
        measurements = metrics["measurement_insights"]
        assert measurements["available"] is (summary_input == "provided")
        if summary_input == "provided":
            assert measurements["paired_net_gain"][0]["paired_net_gain"] == 2
