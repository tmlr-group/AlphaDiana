"""Regression coverage for the SWE-bench evaluation harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.results.report import ReportGenerator
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult
from alphadiana.scorer.swebench_pro import SWEBenchProScorer


def _fake_paths(tmp_path: Path, task_id: str = "instance_demo") -> tuple[Path, Path]:
    eval_script_path = tmp_path / "swe_bench_pro_eval.py"
    eval_script_path.write_text("print('fake evaluator')\n", encoding="utf-8")

    scripts_dir = tmp_path / "run_scripts" / task_id
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts_dir / "parser.py").write_text("print('parser')\n", encoding="utf-8")
    return eval_script_path, scripts_dir.parent


def _make_task(task_id: str = "instance_demo") -> BenchmarkTask:
    return BenchmarkTask(
        task_id=task_id,
        problem="Fix the bug.",
        ground_truth={"base_commit": "abc123"},
        metadata={
            "repo": "demo/repo",
            "before_repo_set_cmd": "source /venv/bin/activate",
            "selected_test_files_to_run": "['tests/test_demo.py::test_fix']",
            "fail_to_pass": ["tests/test_demo.py::test_fix"],
            "pass_to_pass": "['tests/test_demo.py::test_regression']",
        },
    )


def _make_scorer(tmp_path: Path, task_id: str = "instance_demo") -> SWEBenchProScorer:
    eval_script_path, scripts_dir = _fake_paths(tmp_path, task_id=task_id)
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


def _write_fake_evaluator_outputs(
    output_dir: Path,
    task_id: str,
    *,
    passed_tests: list[str],
    eval_result: bool,
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
    (output_dir / "eval_results.json").write_text(
        json.dumps({task_id: eval_result}, indent=2),
        encoding="utf-8",
    )


def _arg_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_swebench_subprocess_builds_expected_argv_and_raw_sample_strings(tmp_path, monkeypatch):
    scorer = _make_scorer(tmp_path)
    task = _make_task()
    response = AgentResponse(answer="diff --git a/demo.py b/demo.py")
    seen: dict[str, object] = {}

    def fake_run(argv, capture_output, text, check):
        assert capture_output is True
        assert text is True
        assert check is False
        seen["argv"] = list(argv)

        raw_sample_path = Path(_arg_value(argv, "--raw_sample_path"))
        patch_path = Path(_arg_value(argv, "--patch_path"))
        output_dir = Path(_arg_value(argv, "--output_dir"))
        raw_sample = json.loads(raw_sample_path.read_text(encoding="utf-8"))
        patches = json.loads(patch_path.read_text(encoding="utf-8"))

        seen["raw_sample"] = raw_sample
        seen["patches"] = patches

        _write_fake_evaluator_outputs(
            output_dir,
            task.task_id,
            passed_tests=[
                "tests/test_demo.py::test_fix",
                "tests/test_demo.py::test_regression",
            ],
            eval_result=True,
        )
        return subprocess.CompletedProcess(argv, 0, stdout="subprocess ok\n", stderr="")

    monkeypatch.setattr("alphadiana.scorer.swebench_pro.subprocess.run", fake_run)

    result = scorer.score(task, response)

    argv = seen["argv"]
    assert "--raw_sample_path" in argv
    assert "--patch_path" in argv
    assert "--output_dir" in argv
    assert "--scripts_dir" in argv
    assert "--dockerhub_username" in argv
    assert "--use_local_docker" in argv

    raw_sample = seen["raw_sample"]
    assert raw_sample["fail_to_pass"] == "['tests/test_demo.py::test_fix']"
    assert raw_sample["pass_to_pass"] == "['tests/test_demo.py::test_regression']"
    assert raw_sample["selected_test_files_to_run"] == "['tests/test_demo.py::test_fix']"
    assert isinstance(raw_sample["fail_to_pass"], str)
    assert isinstance(raw_sample["pass_to_pass"], str)
    assert isinstance(raw_sample["selected_test_files_to_run"], str)

    patches = seen["patches"]
    assert patches == [
        {
            "instance_id": task.task_id,
            "patch": "diff --git a/demo.py b/demo.py",
            "prefix": "alphadiana",
        }
    ]
    assert result.correct is True
    assert result.score == 1.0


def test_swebench_empty_patch_skips_subprocess(tmp_path, monkeypatch):
    scorer = _make_scorer(tmp_path)
    task = _make_task()
    response = AgentResponse(answer="   ")
    called = {"value": False}

    def fake_run(*args, **kwargs):
        called["value"] = True
        raise AssertionError("subprocess should not be called for empty patch")

    monkeypatch.setattr("alphadiana.scorer.swebench_pro.subprocess.run", fake_run)

    result = scorer.score(task, response)

    assert called["value"] is False
    assert result.correct is False
    assert result.score == 0.0
    assert "Empty patch" in result.rationale


def test_swebench_parse_outputs_populates_workspace_file_contents_and_score_metadata(
    tmp_path,
    monkeypatch,
):
    scorer = _make_scorer(tmp_path)
    task = _make_task()
    response = AgentResponse(answer="diff --git a/demo.py b/demo.py")

    def fake_run(argv, capture_output, text, check):
        output_dir = Path(_arg_value(argv, "--output_dir"))
        _write_fake_evaluator_outputs(
            output_dir,
            task.task_id,
            passed_tests=["tests/test_demo.py::test_fix"],
            eval_result=False,
        )
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("alphadiana.scorer.swebench_pro.subprocess.run", fake_run)

    result = scorer.score(task, response)

    assert result.correct is False
    assert result.predicted == "unresolved"
    assert result.metadata["fail_to_pass_expected"] == ["tests/test_demo.py::test_fix"]
    assert result.metadata["fail_to_pass_passed"] == ["tests/test_demo.py::test_fix"]
    assert result.metadata["fail_to_pass_missing"] == []
    assert result.metadata["pass_to_pass_expected"] == ["tests/test_demo.py::test_regression"]
    assert result.metadata["pass_to_pass_passed"] == []
    assert result.metadata["pass_to_pass_missing"] == ["tests/test_demo.py::test_regression"]
    assert result.metadata["resolved"] is False

    assert "/swebench_eval/eval_results.json" in response.workspace_file_contents
    assert (
        f"/swebench_eval/{task.task_id}/alphadiana_output.json"
        in response.workspace_file_contents
    )
    assert (
        f"/swebench_eval/{task.task_id}/alphadiana_stdout.log"
        in response.workspace_file_contents
    )
    assert (
        f"/swebench_eval/{task.task_id}/alphadiana_stderr.log"
        in response.workspace_file_contents
    )
    assert (
        f"/swebench_eval/{task.task_id}/alphadiana_entryscript.sh"
        in response.workspace_file_contents
    )


def test_result_store_persists_score_metadata(tmp_path):
    store = ResultStore(
        output_dir=str(tmp_path),
        run_id="swebench-run",
        run_metadata={
            "run_id": "swebench-run",
            "agent_name": "direct_llm",
            "agent_version": "1.0",
            "benchmark_name": "swebench_pro_os",
            "scorer_name": "swebench_pro",
            "num_samples": 1,
        },
    )
    task = _make_task("instance_store")
    response = AgentResponse(
        answer="patch",
        metadata={"transport": "response metadata"},
        workspace_file_contents={"/swebench_eval/eval_results.json": "{\"instance_store\": true}"},
    )
    score = ScoreResult(
        correct=True,
        score=1.0,
        expected=str(task.ground_truth),
        predicted="resolved",
        rationale="done",
        metadata={"score_metadata": True, "resolved": True},
    )

    store.append(task, response, score)

    jsonl_record = json.loads(Path(store.path).read_text(encoding="utf-8").strip())
    per_task_records = json.loads(
        (Path(tmp_path) / "swebench-run" / "tasks" / f"{task.task_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert jsonl_record["score_metadata"] == {"score_metadata": True, "resolved": True}
    assert jsonl_record["metadata"] == {"transport": "response metadata"}
    assert per_task_records[0]["score_metadata"] == {"score_metadata": True, "resolved": True}


def test_swebench_report_includes_resolve_rate_alias(tmp_path):
    store = ResultStore(
        output_dir=str(tmp_path),
        run_id="report-run",
        run_metadata={
            "run_id": "report-run",
            "agent_name": "direct_llm",
            "agent_version": "1.0",
            "benchmark_name": "swebench_pro_os",
            "scorer_name": "swebench_pro",
            "num_samples": 1,
        },
    )
    task_ok = _make_task("instance_ok")
    task_bad = _make_task("instance_bad")
    store.append(
        task_ok,
        AgentResponse(answer="patch-ok"),
        ScoreResult(
            correct=True,
            score=1.0,
            expected=str(task_ok.ground_truth),
            predicted="resolved",
            rationale="ok",
            metadata={"resolved": True},
        ),
    )
    store.append(
        task_bad,
        AgentResponse(answer="patch-bad"),
        ScoreResult(
            correct=False,
            score=0.0,
            expected=str(task_bad.ground_truth),
            predicted="unresolved",
            rationale="bad",
            metadata={"resolved": False},
        ),
    )

    summary = ReportGenerator().generate(store)
    markdown = ReportGenerator().to_markdown(summary)

    assert "Accuracy (total)" in markdown
    assert "Resolve Rate" in markdown
    assert "swebench_pro_os" in markdown
