from __future__ import annotations

import json

import pytest

from alphadiana.analysis import load_jsonl_records, load_run_bundle
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

