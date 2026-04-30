from __future__ import annotations

import json
from pathlib import Path


FORBIDDEN_OUTPUT_MARKERS = ("/data0", "/data2", "/home", "sk-", "api_key=")


def test_analyze_behavior_insights_cli_writes_required_outputs(tmp_path, monkeypatch):
    from scripts import analyze_behavior_insights as cli

    output_dir = tmp_path / "out"
    private_root = "/data2/private/hle-opencode/run-a"
    monkeypatch.setenv("PHASE15_HLE_OPENCODE_LOGPROBS", private_root)
    monkeypatch.delenv("PHASE15_HLE_ZEROCLAW_LOGPROBS", raising=False)
    monkeypatch.delenv("PHASE15_COLLAB_RESULTS_ROOT", raising=False)

    exit_code = cli.main(
        [
            "--results-dir",
            str(tmp_path / "missing-results"),
            "--output-dir",
            str(output_dir),
            "--measurement-summary",
            str(tmp_path / "missing-measurements.json"),
        ]
    )

    assert exit_code == 0
    for filename in (
        "corpus_inventory.json",
        "corpus_inventory.csv",
        "action_events.csv",
        "insight_claims.json",
        "case_anchors.json",
        "behavior_insights.md",
        "behavior_insights_zh.md",
    ):
        assert (output_dir / filename).exists()

    assert "Model-Harness Composite" in (output_dir / "insight_claims.json").read_text(encoding="utf-8")
    assert "Model-Harness Behavior Insights" in (output_dir / "behavior_insights.md").read_text(encoding="utf-8")
    assert "模型-框架行为洞察" in (output_dir / "behavior_insights_zh.md").read_text(encoding="utf-8")

    combined = "\n".join(path.read_text(encoding="utf-8") for path in sorted(output_dir.iterdir()))
    assert "PHASE15_HLE_OPENCODE_LOGPROBS" in combined
    assert private_root not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_MARKERS)


def test_cli_wires_selected_records_metrics_and_measurement_summary(tmp_path, monkeypatch):
    from alphadiana.analysis.insight_corpus import CorpusSpec
    from scripts import analyze_behavior_insights as cli

    output_dir = tmp_path / "out"
    measurement_path = tmp_path / "measurement_summary.json"
    measurement_summary = {"low_entropy_long_collapse": {"case_ids": ["opencode:t2:0"]}}
    measurement_path.write_text(json.dumps(measurement_summary), encoding="utf-8")

    spec_a = CorpusSpec(
        label="a",
        benchmark="gpqa",
        harness="opencode",
        model_label="Qwen/Qwen3.5-27B",
        results_dir=tmp_path,
        run_id="run-a",
        source_kind="repo",
        public_ref="results/run-a",
        env_var=None,
    )
    spec_b = CorpusSpec(
        label="b",
        benchmark="gpqa",
        harness="openclaw",
        model_label="Qwen/Qwen3.5-27B",
        results_dir=tmp_path,
        run_id="run-b",
        source_kind="repo",
        public_ref="results/run-b",
        env_var=None,
    )
    selected_records = [
        {"run_id": "run-a", "harness": "opencode", "task_id": "t1", "sample_index": 0, "score_status": "valid_scored", "correct": True},
        {"run_id": "run-b", "harness": "openclaw", "task_id": "t2", "sample_index": 0, "score_status": "valid_scored", "correct": False},
    ]
    denominator_rows = [
        {"corpus_label": "a", "benchmark": "gpqa", "harness": "opencode", "status": "validated_records", "selected_records": 1, "valid_scored": 1},
        {"corpus_label": "b", "benchmark": "gpqa", "harness": "openclaw", "status": "validated_records", "selected_records": 1, "valid_scored": 1},
    ]
    calls: list[str] = []

    monkeypatch.setattr(cli, "load_phase15_corpus_specs", lambda *args, **kwargs: [spec_a, spec_b])
    monkeypatch.setattr(cli, "build_denominator_ledger", lambda specs: denominator_rows)
    monkeypatch.setattr(cli, "load_run_bundle", lambda results_dir, run_id: {"run_id": run_id})
    monkeypatch.setattr(cli, "load_selected_task_records", lambda bundle: [record for record in selected_records if record["run_id"] == bundle["run_id"]])

    def fake_extract(bundle, *, harness):
        return [
            {
                "run_id": bundle["run_id"],
                "harness": harness,
                "task_id": "t1",
                "sample_index": 0,
                "step_id": "0",
                "canonical_action": "reason",
                "score_status": "valid_scored",
                "correct": True,
            }
        ]

    def fake_metrics(event_rows, *, inventory_rows, measurement_summary):
        calls.append("metrics")
        assert inventory_rows == denominator_rows
        assert measurement_summary == {"low_entropy_long_collapse": {"case_ids": ["opencode:t2:0"]}}
        assert len(event_rows) == 2
        return {"measurement_insights": {"available": False}}

    def fake_cases(*, event_rows, selected_records, measurement_summary):
        calls.append("cases")
        assert selected_records == [
            {"run_id": "run-a", "harness": "opencode", "task_id": "t1", "sample_index": 0, "score_status": "valid_scored", "correct": True},
            {"run_id": "run-b", "harness": "openclaw", "task_id": "t2", "sample_index": 0, "score_status": "valid_scored", "correct": False},
        ]
        assert measurement_summary == {"low_entropy_long_collapse": {"case_ids": ["opencode:t2:0"]}}
        return [{"case_anchor_id": "case-1", "anchor_id": "case-1", "anchor_type": "low_entropy_long_wrong"}]

    def fake_claims(*, denominator_rows, metrics, measurement_summary, case_anchors):
        calls.append("claims")
        assert calls == ["metrics", "cases", "claims"]
        assert metrics == {"measurement_insights": {"available": False}}
        assert measurement_summary == {"low_entropy_long_collapse": {"case_ids": ["opencode:t2:0"]}}
        assert case_anchors == [{"case_anchor_id": "case-1", "anchor_id": "case-1", "anchor_type": "low_entropy_long_wrong"}]
        return [{"claim_id": "model_harness_composite", "mechanism_name": "Model-Harness Composite"}]

    monkeypatch.setattr(cli, "extract_action_event_rows", fake_extract)
    monkeypatch.setattr(cli, "compute_outcome_conditioned_metrics", fake_metrics)
    monkeypatch.setattr(cli, "select_insight_case_anchors", fake_cases)
    monkeypatch.setattr(cli, "build_insight_claims", fake_claims)

    assert cli.main(["--results-dir", str(tmp_path), "--output-dir", str(output_dir), "--measurement-summary", str(measurement_path)]) == 0
    assert calls == ["metrics", "cases", "claims"]


def test_phase15_reports_are_deterministic(tmp_path):
    from alphadiana.analysis.insight_reports import write_phase15_outputs

    denominator_rows = [
        {"corpus_label": "z", "benchmark": "gpqa", "harness": "zeroclaw", "status": "validated_records", "selected_records": 1, "valid_scored": 1},
        {"corpus_label": "a", "benchmark": "gpqa", "harness": "opencode", "status": "validated_records", "selected_records": 1, "valid_scored": 1},
    ]
    insight_claims = [
        {"claim_id": "z_claim", "mechanism_name": "Z Claim", "old_story": "old", "falsifier_question": "q", "denominator_filter": "d", "result": {"b": 2}},
        {"claim_id": "a_claim", "mechanism_name": "A Claim", "old_story": "old", "falsifier_question": "q", "denominator_filter": "d", "result": {"a": 1}},
    ]
    case_anchors = [
        {"case_anchor_id": "z-case", "anchor_id": "z-case", "anchor_type": "operational_error", "evidence_snippets": ["z"]},
        {"case_anchor_id": "a-case", "anchor_id": "a-case", "anchor_type": "paired_rescue", "evidence_snippets": ["a"]},
    ]

    first = write_phase15_outputs(
        tmp_path / "first",
        denominator_rows=denominator_rows,
        event_rows=[],
        insight_claims=insight_claims,
        case_anchors=case_anchors,
    )
    second = write_phase15_outputs(
        tmp_path / "second",
        denominator_rows=list(reversed(denominator_rows)),
        event_rows=[],
        insight_claims=list(reversed(insight_claims)),
        case_anchors=list(reversed(case_anchors)),
    )

    for key in ("markdown", "markdown_zh", "insight_claims", "case_anchors", "corpus_inventory_json"):
        assert first[key].read_text(encoding="utf-8") == second[key].read_text(encoding="utf-8")


def test_unavailable_env_root_is_reported_without_path_leak(tmp_path, monkeypatch):
    from scripts import analyze_behavior_insights as cli

    output_dir = tmp_path / "out"
    monkeypatch.setenv("PHASE15_COLLAB_RESULTS_ROOT", "/home/private/collab/sk-secret/api_key=value")

    assert cli.main(["--results-dir", str(tmp_path / "results"), "--output-dir", str(output_dir)]) == 0

    inventory = json.loads((output_dir / "corpus_inventory.json").read_text(encoding="utf-8"))
    assert any(row["corpus_label"] == "collab-results-env" and row["status"] == "unavailable" for row in inventory)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sorted(output_dir.iterdir()))
    assert "PHASE15_COLLAB_RESULTS_ROOT" in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_MARKERS)
