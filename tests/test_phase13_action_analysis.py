from __future__ import annotations

import json

import pytest

from alphadiana.analysis import (
    build_record_feature_rows,
    compute_reliability_summary,
    load_jsonl_records,
    load_run_bundle,
)
from alphadiana.analysis.result_reader import resolve_run_relative_path


def test_load_run_bundle_reads_jsonl_manifest_and_task_lists(tmp_path):
    results_dir = tmp_path / "results"
    run_dir = results_dir / "demo_run"
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True)

    (results_dir / "demo_run.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"task_id": "t1", "sample_index": 0}),
                "",
                "{not json",
                json.dumps({"task_id": "t2", "sample_index": 0}),
            ]
        ),
        encoding="utf-8",
    )
    (run_dir / "run_manifest.json").write_text(
        json.dumps({"run_id": "demo_run", "num_samples": 1}),
        encoding="utf-8",
    )
    (tasks_dir / "t1.json").write_text(
        json.dumps([{"task_id": "t1", "sample_index": 0}]),
        encoding="utf-8",
    )
    (tasks_dir / "t2.json").write_text(
        json.dumps({"task_id": "t2", "sample_index": 0}),
        encoding="utf-8",
    )

    bundle = load_run_bundle(results_dir, "demo_run")

    assert bundle.results_dir == results_dir
    assert bundle.run_id == "demo_run"
    assert bundle.jsonl_path == results_dir / "demo_run.jsonl"
    assert bundle.run_dir == run_dir
    assert bundle.manifest == {"run_id": "demo_run", "num_samples": 1}
    assert [record["task_id"] for record in bundle.records] == ["t1", "t2"]
    assert list(bundle.task_records) == ["t1", "t2"]
    assert bundle.task_records["t1"] == [{"task_id": "t1", "sample_index": 0}]
    assert bundle.task_records["t2"] == [{"task_id": "t2", "sample_index": 0}]


def test_load_jsonl_records_skips_blank_and_malformed_lines(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"task_id": "ok1"}),
                "   ",
                "{malformed",
                json.dumps(["not", "a", "dict"]),
                json.dumps({"task_id": "ok2"}),
            ]
        ),
        encoding="utf-8",
    )

    assert load_jsonl_records(path) == [{"task_id": "ok1"}, {"task_id": "ok2"}]


def test_resolve_run_relative_path_rejects_absolute_paths(tmp_path):
    with pytest.raises(ValueError, match="absolute paths are not allowed in ResultStore artifact refs"):
        resolve_run_relative_path(tmp_path / "results", "/tmp/secret.jsonl")


def test_record_feature_rows_prefer_int16_entropy(tmp_path):
    results_dir = tmp_path / "results"
    run_dir = results_dir / "demo_run"
    (run_dir / "logprobs_int16").mkdir(parents=True)
    records = [
        {
            "run_id": "demo_run",
            "task_id": "t1",
            "sample_index": 0,
            "score_status": "valid_scored",
            "correct": True,
            "predicted": "A",
            "ground_truth": "A",
            "metadata": {},
            "logprobs_int16_path": "demo_run/logprobs_int16/t1.jsonl",
            "int16_probability_scale": 1000,
            "token_entropy_stats": {"mean": 99.0, "p90": 99.0, "max": 99.0, "n_tokens": 99},
            "wall_time_sec": 1.5,
            "token_usage": {"completion_tokens": 7},
        },
        {
            "run_id": "demo_run",
            "task_id": "t2",
            "sample_index": 0,
            "score_status": "runtime_error",
            "correct": None,
            "predicted": None,
            "ground_truth": "B",
            "metadata": {"logprobs_capture_status": "unsupported"},
            "error": {"error_type": "SandboxTimeout"},
        },
    ]
    (results_dir / "demo_run.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )
    (run_dir / "run_manifest.json").write_text(json.dumps({"run_id": "demo_run"}), encoding="utf-8")
    (run_dir / "logprobs_int16" / "t1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "token_index": 0,
                        "token": "A",
                        "entropy_nats": 0.2,
                        "top20": [{"token": "A", "prob_i16": 200}, {"token": "B", "prob_i16": 50}],
                    }
                ),
                json.dumps(
                    {
                        "token_index": 1,
                        "token": ".",
                        "entropy_nats": 0.6,
                        "top20": [{"token": ".", "prob_i16": 300}],
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    rows = build_record_feature_rows(load_run_bundle(results_dir, "demo_run"))

    assert set(rows[0]) == {
        "run_id",
        "task_id",
        "sample_index",
        "score_status",
        "correct",
        "predicted",
        "ground_truth",
        "answer_length",
        "logprobs_capture_status",
        "has_logprobs",
        "n_tokens",
        "entropy_mean",
        "entropy_p90",
        "entropy_max",
        "top1_mean_prob",
        "wall_time_sec",
        "completion_tokens",
        "error_type",
    }
    assert rows[0]["logprobs_capture_status"] == "captured"
    assert rows[0]["has_logprobs"] is True
    assert rows[0]["n_tokens"] == 2
    assert rows[0]["entropy_mean"] == pytest.approx(0.4)
    assert rows[0]["entropy_p90"] == pytest.approx(0.6)
    assert rows[0]["entropy_max"] == pytest.approx(0.6)
    assert rows[0]["top1_mean_prob"] == pytest.approx(0.25)
    assert rows[0]["completion_tokens"] == 7
    assert rows[1]["score_status"] == "runtime_error"
    assert rows[1]["logprobs_capture_status"] == "unsupported"
    assert rows[1]["error_type"] == "SandboxTimeout"


def test_single_sample_reliability_does_not_claim_pass_power_k():
    records = [
        {"task_id": "t1", "sample_index": 0, "score_status": "valid_scored", "correct": True},
        {"task_id": "t2", "sample_index": 0, "score_status": "runtime_error", "correct": None},
    ]
    manifest = {
        "num_samples": 1,
        "expected_task_ids": ["t1", "t2", "t3"],
        "expected_sample_count": 3,
    }

    summary = compute_reliability_summary(records, manifest)

    assert summary["observed_valid_accuracy"] == 1.0
    assert summary["expected_sample_accuracy"] == pytest.approx(1 / 3)
    assert summary["coverage"] == pytest.approx(2 / 3)
    assert summary["missing_samples"] == 1
    assert summary["error_records"] == 1
    assert summary["pass_at_k"] == pytest.approx(1 / 3)
    assert summary["avg_at_k"] == pytest.approx(1 / 3)
    assert summary["pass_power_k_available"] is False
    assert summary["pass_power_k"] is None


def test_reliability_pass_power_k_requires_full_repeated_samples():
    records = [
        {"task_id": "t1", "sample_index": 0, "score_status": "valid_scored", "correct": True},
        {"task_id": "t1", "sample_index": 1, "score_status": "valid_scored", "correct": False},
        {"task_id": "t2", "sample_index": 0, "score_status": "valid_scored", "correct": True},
        {"task_id": "t2", "sample_index": 1, "score_status": "valid_scored", "correct": True},
    ]
    manifest = {"num_samples": 2, "expected_task_ids": ["t1", "t2"]}

    summary = compute_reliability_summary(records, manifest)

    assert summary["pass_power_k_available"] is True
    assert summary["pass_at_k"] == 1.0
    assert summary["avg_at_k"] == pytest.approx(0.75)
    assert summary["pass_power_k"] == pytest.approx(((1 / 2) ** 2 + 1.0**2) / 2)
