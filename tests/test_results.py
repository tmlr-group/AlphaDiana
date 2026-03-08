"""Tests for result storage and report generation."""

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.results.report import ReportGenerator
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


class TestResultStoreAppendAndLoad:
    """验证 ResultStore 的 append/load 操作。"""

    def test_roundtrip(self, tmp_path):
        """Append 一条记录后 load 回来，字段一致。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="test-run")

        task = BenchmarkTask(
            task_id="task-1",
            problem="What is 6 * 7?",
            ground_truth="42",
        )
        response = AgentResponse(
            answer="42",
            trajectory=[{"step": 1, "action": "compute"}],
            token_usage={"prompt_tokens": 10, "completion_tokens": 5},
            wall_time_sec=1.5,
        )
        score = ScoreResult(
            correct=True,
            score=1.0,
            expected="42",
            predicted="42",
            rationale="Exact match.",
        )

        store.append(task, response, score)

        results = store.load()
        assert len(results) == 1

        record = results[0]
        assert record["task_id"] == "task-1"
        assert record["problem"] == "What is 6 * 7?"
        assert record["ground_truth"] == "42"
        assert record["predicted"] == "42"
        assert record["correct"] is True
        assert record["score"] == 1.0
        assert record["rationale"] == "Exact match."
        assert record["wall_time_sec"] == 1.5
        assert record["token_usage"]["prompt_tokens"] == 10
        assert record["token_usage"]["completion_tokens"] == 5
        assert record["trajectory"] == [{"step": 1, "action": "compute"}]
        assert "timestamp" in record

    def test_multiple_records(self, tmp_path):
        """多次 append 后 load 全部记录。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="multi-run")

        for i in range(10):
            task = BenchmarkTask(task_id=f"task-{i}", problem=f"P{i}", ground_truth="42")
            response = AgentResponse(answer="42", wall_time_sec=float(i))
            score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
            store.append(task, response, score)

        results = store.load()
        assert len(results) == 10
        assert [r["task_id"] for r in results] == [f"task-{i}" for i in range(10)]

    def test_empty_store(self, tmp_path):
        """未写入时 load 返回空列表。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="empty")
        assert store.load() == []

    def test_jsonl_path(self, tmp_path):
        """ResultStore.path 指向正确的 JSONL 文件。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="my-run")
        assert store.path.endswith("my-run.jsonl")

    def test_preserves_token_usage(self, tmp_path):
        """token_usage 被完整保留。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="token-run")

        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="1")
        response = AgentResponse(
            answer="1",
            token_usage={
                "prompt_tokens": 100,
                "completion_tokens": 200,
                "total_tokens": 300,
            },
        )
        score = ScoreResult(correct=True, score=1.0, expected="1", predicted="1")
        store.append(task, response, score)

        r = store.load()[0]
        assert r["token_usage"]["prompt_tokens"] == 100
        assert r["token_usage"]["completion_tokens"] == 200
        assert r["token_usage"]["total_tokens"] == 300


class TestReportGenerator:
    """验证 ReportGenerator 的报告生成。"""

    def _make_config(self, run_id="report-test"):
        return ExperimentConfig(
            run_id=run_id,
            agent_name="test-agent",
            agent_version="v1.0",
            benchmark_name="test-bench",
            scorer_name="exact_match",
        )

    def test_summary_stats(self, tmp_path):
        """Generate a summary from stored results (1 correct, 1 incorrect)。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="report-test")

        for i, (answer, correct) in enumerate([("42", True), ("99", False)]):
            task = BenchmarkTask(
                task_id=f"task-{i}",
                problem=f"Problem {i}",
                ground_truth="42",
            )
            response = AgentResponse(
                answer=answer,
                token_usage={"prompt_tokens": 10, "completion_tokens": 5},
                wall_time_sec=2.0,
            )
            score = ScoreResult(
                correct=correct,
                score=1.0 if correct else 0.0,
                expected="42",
                predicted=answer,
            )
            store.append(task, response, score)

        report_gen = ReportGenerator()
        summary = report_gen.generate(store, self._make_config())

        assert summary.run_id == "report-test"
        assert summary.agent == "test-agent"
        assert summary.agent_version == "v1.0"
        assert summary.benchmark == "test-bench"
        assert summary.total_tasks == 2
        assert summary.completed == 2
        assert summary.accuracy == 0.5
        assert summary.mean_score == 0.5
        assert summary.mean_wall_time_sec == 2.0
        assert summary.total_tokens["prompt_tokens"] == 20
        assert summary.total_tokens["completion_tokens"] == 10

    def test_markdown_generation(self, tmp_path):
        """Markdown 报告包含关键信息。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="md-test")

        task = BenchmarkTask(task_id="t1", problem="P1", ground_truth="42")
        response = AgentResponse(answer="42")
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
        store.append(task, response, score)

        report_gen = ReportGenerator()
        summary = report_gen.generate(store, self._make_config("md-test"))
        markdown = report_gen.to_markdown(summary)

        assert "md-test" in markdown
        assert "test-agent" in markdown
        assert "1.0000" in markdown
        assert "Accuracy" in markdown
        assert "Mean Score" in markdown

    def test_all_correct(self, tmp_path):
        """全部正确时 accuracy = 1.0。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="all-correct")

        for i in range(5):
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="42")
            response = AgentResponse(answer="42")
            score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
            store.append(task, response, score)

        report_gen = ReportGenerator()
        summary = report_gen.generate(store, self._make_config("all-correct"))
        assert summary.accuracy == pytest.approx(1.0)
        assert summary.mean_score == pytest.approx(1.0)

    def test_all_incorrect(self, tmp_path):
        """全部错误时 accuracy = 0.0。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="all-wrong")

        for i in range(5):
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="42")
            response = AgentResponse(answer="0")
            score = ScoreResult(correct=False, score=0.0, expected="42", predicted="0")
            store.append(task, response, score)

        report_gen = ReportGenerator()
        summary = report_gen.generate(store, self._make_config("all-wrong"))
        assert summary.accuracy == pytest.approx(0.0)
        assert summary.mean_score == pytest.approx(0.0)

    def test_wall_time_aggregation(self, tmp_path):
        """mean_wall_time_sec 计算正确。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="wall-time")

        for i in range(4):
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="1")
            response = AgentResponse(answer="1", wall_time_sec=float(i + 1))
            score = ScoreResult(correct=True, score=1.0, expected="1", predicted="1")
            store.append(task, response, score)

        report_gen = ReportGenerator()
        summary = report_gen.generate(store, self._make_config("wall-time"))
        # mean of [1, 2, 3, 4] = 2.5
        assert summary.mean_wall_time_sec == pytest.approx(2.5)
