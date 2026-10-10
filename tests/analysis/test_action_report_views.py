"""Constructed retry histories exercise the real offline report CLI."""

import csv
import json

import pytest

from alphadiana.analysis.scripts.analyze_action_space import main


def run_report(tmp_path, rows, manifest):
    source = tmp_path / "run.jsonl"
    original = "".join(json.dumps(row) + "\n" for row in rows)
    source.write_text(original, encoding="utf-8")
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["--results-dir", str(tmp_path), "--run-id", "run"]) == 0
    assert source.read_text(encoding="utf-8") == original
    reliability = json.loads((folder / "action_space_reliability.json").read_text())
    clusters = json.loads((folder / "action_space_clusters.json").read_text())
    markdown = (folder / "action_space_report.md").read_text(encoding="utf-8")
    with (folder / "action_space_features.csv").open(newline="", encoding="utf-8") as handle:
        features = list(csv.DictReader(handle))
    return reliability["metrics"], clusters, markdown, features


@pytest.mark.parametrize("manifest", [{}, {"expected_sample_count": 2}])
@pytest.mark.parametrize("provider_error", [False, True])
def test_retry_history_and_latest_sample_are_explicit(tmp_path, manifest, provider_error):
    first = dict(task_id="a", sample_index=0, correct=True, score=1, predicted="1")
    retry = dict(task_id="a", sample_index=0, correct=False, score=0, predicted="2")
    if provider_error:
        retry.update(score=None, correct=None, error={"error_type": "provider_error"})
    metrics, clusters, markdown, features = run_report(tmp_path, [first, retry], manifest)

    valid = 0 if provider_error else 1
    expected = manifest.get("expected_sample_count", 1)
    assert metrics["data_view"] == "latest_samples"
    assert metrics["written_records"] == 1
    assert metrics["valid_scored"] == valid
    assert metrics["observed_valid_accuracy"] == 0
    assert metrics["pass_at_k"] == 0
    assert metrics["denominators"]["observed_valid_accuracy"] == valid
    assert metrics["denominators"]["coverage"] == expected
    assert metrics["denominators"]["expected_sample_accuracy"] == expected
    assert metrics["missing_samples"] == expected - 1
    assert f"| Expected Samples | {expected} |" in markdown
    assert "| Written Records | 2 | 1 |" in markdown
    assert f"| Valid Scored | {1 + valid} | {valid} |" in markdown
    assert "| Count | All attempts | Latest samples |" in markdown
    assert "Reliability Metrics (latest samples)" in markdown
    assert "last persisted attempt" in markdown
    assert "Cluster Views (all attempts)" in markdown
    assert f"| Observed Valid Accuracy | 0.0000 | {valid} valid scored samples |" in markdown

    # Historical clustering keeps the superseded successful attempt.
    assert clusters["data_view"] == "all_attempts"
    for name, count in [("valid_scored_only", 1 + valid), ("all_records_status", 2)]:
        view = clusters["views"][name]
        assert view["data_view"] == "all_attempts"
        assert view["n_rows"] == view["denominator"] == count
        clustered = [row for cluster in view["clusters"] for row in cluster["rows"]]
        assert len(clustered) == count
        assert sum(row["correct"] is True for row in clustered) == 1
    assert len(features) == 2
    assert {row["data_view"] for row in features} == {"all_attempts"}


@pytest.mark.parametrize("rows", [[], [dict(task_id="a", correct=True, score=1)]])
def test_views_without_retries_and_with_missing_samples(tmp_path, rows):
    metrics, clusters, markdown, features = run_report(
        tmp_path, rows, {"expected_task_ids": ["a"], "num_samples": 2, "expected_sample_count": 2}
    )
    assert metrics["missing_samples"] == 2 - len(rows)
    assert metrics["denominators"]["pass_at_k"] == 1
    assert metrics["denominators"]["avg_at_k"] == 1
    assert metrics["denominators"]["pass_power_k"] == 1
    assert metrics["num_samples"] == 2
    assert f"| Written Records | {len(rows)} | {len(rows)} |" in markdown
    assert clusters["views"]["all_records_status"]["denominator"] == len(rows)
    assert len(features) == len(rows)
