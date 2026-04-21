from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.benchmark.swebench_pro import SWEBenchProBenchmark
from alphadiana.results.report import ReportGenerator
from alphadiana.scorer.swebench_pro import SWEBenchProScorer


def _row(
    *,
    instance_id: str,
    repo: str,
    base_commit: str,
    problem_statement: str,
    dockerhub_tag: str,
    fail_to_pass: list[str] | None = None,
    pass_to_pass: list[str] | None = None,
) -> dict:
    return {
        "repo": repo,
        "instance_id": instance_id,
        "base_commit": base_commit,
        "problem_statement": problem_statement,
        "requirements": f"requirements for {instance_id}",
        "interface": "cli",
        "repo_language": "Python",
        "fail_to_pass": fail_to_pass or ["tests/test_issue.py::test_regression"],
        "pass_to_pass": pass_to_pass or ["tests/test_smoke.py::test_existing_behavior"],
        "before_repo_set_cmd": "python -m pip install -e .",
        "selected_test_files_to_run": ["tests/test_issue.py"],
        "dockerhub_tag": dockerhub_tag,
    }


def _make_task(
    *,
    task_id: str = "instance_demo",
    fail_to_pass: list[str] | None = None,
    pass_to_pass: list[str] | None = None,
) -> BenchmarkTask:
    row = _row(
        instance_id=task_id,
        repo="demo/repo",
        base_commit="abc123",
        problem_statement="Fix the bug.",
        dockerhub_tag="demo-tag",
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
    )
    return BenchmarkTask(
        task_id=task_id,
        problem=row["problem_statement"],
        ground_truth={"base_commit": row["base_commit"]},
        metadata={
            "repo": row["repo"],
            "before_repo_set_cmd": row["before_repo_set_cmd"],
            "selected_test_files_to_run": row["selected_test_files_to_run"],
            "fail_to_pass": row["fail_to_pass"],
            "pass_to_pass": row["pass_to_pass"],
        },
    )


def _fake_paths(tmp_path: Path, task_id: str) -> tuple[Path, Path]:
    eval_script_path = tmp_path / "swe_bench_pro_eval.py"
    eval_script_path.write_text("print('fake evaluator')\n", encoding="utf-8")
    (tmp_path / "dockerfiles" / "base_dockerfile").mkdir(parents=True)

    scripts_dir = tmp_path / "run_scripts" / task_id
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts_dir / "parser.py").write_text("print('parser')\n", encoding="utf-8")
    return eval_script_path, scripts_dir.parent


def _make_scorer(tmp_path: Path, task_id: str = "instance_demo") -> SWEBenchProScorer:
    eval_script_path, scripts_dir = _fake_paths(tmp_path, task_id)
    scorer = SWEBenchProScorer()
    scorer.setup(
        {
            "eval_script_path": str(eval_script_path),
            "scripts_dir": str(scripts_dir),
            "dockerhub_username": "demo-user",
            "python_bin": sys.executable,
            "use_local_docker": True,
            "num_workers": 1,
            "redo": True,
        }
    )
    return scorer


def _arg_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _write_fake_evaluator_outputs(
    output_dir: Path,
    task_id: str,
    *,
    passed_tests: list[str],
    resolved_ids: list[str] | None = None,
    write_eval_results: bool = True,
) -> None:
    task_dir = output_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    tests = [{"name": name, "status": "PASSED"} for name in passed_tests]
    (task_dir / "alphadiana_output.json").write_text(
        json.dumps({"tests": tests}, indent=2),
        encoding="utf-8",
    )
    (task_dir / "alphadiana_stdout.log").write_text("stdout from evaluator\n", encoding="utf-8")
    (task_dir / "alphadiana_stderr.log").write_text("stderr from evaluator\n", encoding="utf-8")
    (task_dir / "alphadiana_entryscript.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    if write_eval_results:
        (output_dir / "eval_results.json").write_text(
            json.dumps({"resolved_ids": resolved_ids or []}, indent=2),
            encoding="utf-8",
        )


class _Store:
    def __init__(self, results: list[dict]) -> None:
        self._results = results
        self.run_id = "phase8-report"

    def load(self) -> list[dict]:
        return self._results


def _result_record(
    *,
    task_id: str,
    sample_index: int,
    category: str,
    correct: bool,
    score: float | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "sample_index": sample_index,
        "correct": correct,
        "score": float(correct) if score is None else score,
        "task_metadata": {"category": category},
        "metadata": {},
        "token_usage": {},
        "wall_time_sec": 1.0,
    }


def test_swebench_pro_default_scorer_is_swebench_pro():
    bench = SWEBenchProBenchmark()
    assert bench.default_scorer() == "swebench_pro", (
        "SWEBenchProBenchmark must return 'swebench_pro' from default_scorer(); "
        "silent fallback to exact_match corrupts all benchmark results"
    )


def test_scorer_honors_eval_results_json_not_resolved(tmp_path, monkeypatch):
    scorer = _make_scorer(tmp_path)
    task = _make_task(task_id="instance_not_resolved")
    response = AgentResponse(answer="diff --git a/demo.py b/demo.py")

    def fake_run(argv, capture_output, text, check, cwd):
        output_dir = Path(_arg_value(argv, "--output_dir"))
        _write_fake_evaluator_outputs(
            output_dir,
            task.task_id,
            passed_tests=[
                "tests/test_issue.py::test_regression",
                "tests/test_smoke.py::test_existing_behavior",
            ],
            resolved_ids=[],
        )
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("alphadiana.scorer.swebench_pro.subprocess.run", fake_run)

    result = scorer.score(task, response)

    assert result.correct is False
    assert result.metadata["resolved"] is False


def test_scorer_honors_eval_results_json_resolved(tmp_path, monkeypatch):
    scorer = _make_scorer(tmp_path)
    task = _make_task(task_id="instance_resolved")
    response = AgentResponse(answer="diff --git a/demo.py b/demo.py")

    def fake_run(argv, capture_output, text, check, cwd):
        output_dir = Path(_arg_value(argv, "--output_dir"))
        _write_fake_evaluator_outputs(
            output_dir,
            task.task_id,
            passed_tests=[],
            resolved_ids=[task.task_id],
        )
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("alphadiana.scorer.swebench_pro.subprocess.run", fake_run)

    result = scorer.score(task, response)

    assert result.correct is True
    assert result.metadata["resolved"] is True


def test_scorer_missing_eval_results_json_falls_back_gracefully(tmp_path, monkeypatch, caplog):
    scorer = _make_scorer(tmp_path)
    task = _make_task(task_id="instance_missing_eval_results")
    response = AgentResponse(answer="diff --git a/demo.py b/demo.py")

    def fake_run(argv, capture_output, text, check, cwd):
        output_dir = Path(_arg_value(argv, "--output_dir"))
        _write_fake_evaluator_outputs(
            output_dir,
            task.task_id,
            passed_tests=[
                "tests/test_issue.py::test_regression",
                "tests/test_smoke.py::test_existing_behavior",
            ],
            write_eval_results=False,
        )
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("alphadiana.scorer.swebench_pro.subprocess.run", fake_run)

    result = scorer.score(task, response)

    assert result.correct is True
    assert "eval_results.json absent" in caplog.text


def test_scorer_rejects_empty_fail_to_pass(tmp_path, monkeypatch):
    scorer = _make_scorer(tmp_path)
    task = _make_task(
        task_id="instance_empty_expected_tests",
        fail_to_pass=[],
        pass_to_pass=[],
    )
    response = AgentResponse(answer="diff --git a/demo.py b/demo.py")

    def fake_run(argv, capture_output, text, check, cwd):
        output_dir = Path(_arg_value(argv, "--output_dir"))
        _write_fake_evaluator_outputs(
            output_dir,
            task.task_id,
            passed_tests=[],
            resolved_ids=[task.task_id],
        )
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("alphadiana.scorer.swebench_pro.subprocess.run", fake_run)

    with pytest.raises(ValueError, match="fail_to_pass and pass_to_pass are both empty; cannot score"):
        scorer.score(task, response)


def test_report_total_tasks_counts_unique_task_ids():
    results = [
        _result_record(task_id="t1", sample_index=0, category="math", correct=False),
        _result_record(task_id="t1", sample_index=1, category="math", correct=True),
        _result_record(task_id="t1", sample_index=2, category="math", correct=False),
        _result_record(task_id="t2", sample_index=0, category="code", correct=False),
        _result_record(task_id="t2", sample_index=1, category="code", correct=False),
        _result_record(task_id="t2", sample_index=2, category="code", correct=True),
    ]

    summary = ReportGenerator().generate(_Store(results))

    assert summary.total_tasks == 2


def test_report_per_category_uses_task_level_denominators():
    results = [
        _result_record(task_id="math-1", sample_index=0, category="math", correct=False),
        _result_record(task_id="math-1", sample_index=1, category="math", correct=True),
        _result_record(task_id="math-2", sample_index=0, category="math", correct=False),
        _result_record(task_id="math-2", sample_index=1, category="math", correct=False),
        _result_record(task_id="code-1", sample_index=0, category="code", correct=False),
        _result_record(task_id="code-1", sample_index=1, category="code", correct=False),
        _result_record(task_id="code-1", sample_index=2, category="code", correct=True),
    ]

    summary = ReportGenerator().generate(_Store(results))

    assert summary.per_category["math"]["denominator"] == 2
    assert summary.per_category["code"]["denominator"] == 1
