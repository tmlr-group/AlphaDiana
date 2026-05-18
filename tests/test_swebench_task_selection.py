from __future__ import annotations

import json

from alphadiana.benchmark.swe_bench import SWEBenchBenchmark


def _row(instance_id: str, repo: str = "demo/repo") -> dict:
    return {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": "abc123",
        "problem_statement": f"Fix {instance_id}",
        "patch": "diff --git a/x b/x",
        "version": "1.0",
        "FAIL_TO_PASS": [],
        "PASS_TO_PASS": [],
        "test_patch": "",
        "environment_setup_commit": "",
    }


def test_swe_bench_loads_taskset_path_in_order(tmp_path, monkeypatch) -> None:
    dataset = [
        _row("astropy__astropy-12907", "astropy/astropy"),
        _row("django__django-10554", "django/django"),
        _row("astropy__astropy-13033", "astropy/astropy"),
    ]
    monkeypatch.setattr("alphadiana.benchmark.swe_bench.load_dataset_with_retry", lambda *_args, **_kwargs: dataset)
    taskset = tmp_path / "taskset.json"
    taskset.write_text(
        json.dumps({"task_ids": ["astropy__astropy-13033", "astropy__astropy-12907"]}),
        encoding="utf-8",
    )

    tasks = SWEBenchBenchmark().load_tasks({"taskset_path": str(taskset)})

    assert [task.task_id for task in tasks] == [
        "swe_astropy__astropy-13033",
        "swe_astropy__astropy-12907",
    ]
    assert [task.metadata["dataset_index"] for task in tasks] == [2, 0]


def test_swe_bench_loads_dataset_indices_in_order(monkeypatch) -> None:
    dataset = [_row("one"), _row("two"), _row("three")]
    monkeypatch.setattr("alphadiana.benchmark.swe_bench.load_dataset_with_retry", lambda *_args, **_kwargs: dataset)

    tasks = SWEBenchBenchmark().load_tasks({"dataset_indices": [2, 0]})

    assert [task.task_id for task in tasks] == ["swe_three", "swe_one"]


def test_swe_bench_rejects_missing_task_id(monkeypatch) -> None:
    dataset = [_row("one")]
    monkeypatch.setattr("alphadiana.benchmark.swe_bench.load_dataset_with_retry", lambda *_args, **_kwargs: dataset)

    try:
        SWEBenchBenchmark().load_tasks({"task_ids": ["missing"]})
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected missing task id to fail")
