"""Tests for pass@k multi-sampling support.

Covers:
- ExperimentConfig.num_samples field
- ResultStore sample_index in records, dedup, and checkpoint
- ReportGenerator pass@k and per-category pass@k computation
- Runner work item expansion and checkpoint with sample_index
- Backward compatibility: num_samples=1 behaves identically to before
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.results.report import ReportGenerator, RunSummary
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


# ===================================================================
# ExperimentConfig
# ===================================================================


class TestNumSamplesConfig:
    """num_samples field in ExperimentConfig."""

    def test_default_is_1(self):
        cfg = ExperimentConfig(
            agent_name="a", agent_version="1", benchmark_name="b", scorer_name="s",
        )
        assert cfg.num_samples == 1

    def test_explicit_value(self):
        cfg = ExperimentConfig(
            agent_name="a", agent_version="1", benchmark_name="b", scorer_name="s",
            num_samples=5,
        )
        assert cfg.num_samples == 5

    def test_from_yaml(self, tmp_path):
        yaml_content = """
agent:
  name: direct_llm
  version: "1.0"
  config: {}
benchmark:
  name: aime
  config: {}
scorer:
  name: numeric
  config: {}
num_samples: 8
"""
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text(yaml_content)
        cfg = ExperimentConfig.from_yaml(str(cfg_file))
        assert cfg.num_samples == 8

    def test_from_yaml_default(self, tmp_path):
        yaml_content = """
agent:
  name: direct_llm
  version: "1.0"
  config: {}
benchmark:
  name: aime
  config: {}
scorer:
  name: numeric
  config: {}
"""
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text(yaml_content)
        cfg = ExperimentConfig.from_yaml(str(cfg_file))
        assert cfg.num_samples == 1


# ===================================================================
# ResultStore — sample_index in records
# ===================================================================


class TestResultStoreSampleIndex:
    """ResultStore records include sample_index."""

    def _task(self, tid="t1"):
        return BenchmarkTask(task_id=tid, problem="P", ground_truth="42")

    def _resp(self, answer="42"):
        return AgentResponse(answer=answer)

    def _score(self, correct=True):
        return ScoreResult(correct=correct, score=1.0 if correct else 0.0,
                           expected="42", predicted="42")

    def test_sample_index_in_record(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="si")
        store.append(self._task(), self._resp(), self._score(), sample_index=2)
        records = store.load()
        assert records[0]["sample_index"] == 2

    def test_default_sample_index_is_0(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="si0")
        store.append(self._task(), self._resp(), self._score())
        records = store.load()
        assert records[0]["sample_index"] == 0

    def test_dedup_by_task_and_sample(self, tmp_path):
        """Two records for same task_id but different sample_index are both kept."""
        store = ResultStore(output_dir=str(tmp_path), run_id="dup")
        store.append(self._task(), self._resp("a1"), self._score(True), sample_index=0)
        store.append(self._task(), self._resp("a2"), self._score(False), sample_index=1)
        records = store.load()
        assert len(records) == 2

    def test_dedup_same_sample_keeps_latest(self, tmp_path):
        """Two records for same (task_id, sample_index) keeps the latest."""
        store = ResultStore(output_dir=str(tmp_path), run_id="dup2")
        store.append(self._task(), self._resp("old"), self._score(False), sample_index=0)
        store.append(self._task(), self._resp("new"), self._score(True), sample_index=0)
        records = store.load()
        assert len(records) == 1
        assert records[0]["predicted"] == "new"

    def test_error_record_has_sample_index(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="err")
        store.append_error(
            self._task(), error={"error": "boom", "error_type": "test"},
            sample_index=3,
        )
        records = store.load()
        assert records[0]["sample_index"] == 3

    def test_completed_sample_ids(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="cs")
        store.append(self._task("t1"), self._resp(), self._score(), sample_index=0)
        store.append(self._task("t1"), self._resp(), self._score(), sample_index=1)
        store.append_error(
            self._task("t1"), error={"error": "x", "error_type": "y"},
            sample_index=2,
        )
        completed = store.completed_sample_ids()
        assert ("t1", 0) in completed
        assert ("t1", 1) in completed
        assert ("t1", 2) not in completed  # error, correct=None

    def test_completed_task_ids_backward_compat(self, tmp_path):
        """completed_task_ids() still works — returns task_ids with any completed sample."""
        store = ResultStore(output_dir=str(tmp_path), run_id="bt")
        store.append(self._task("t1"), self._resp(), self._score(), sample_index=0)
        store.append_error(
            self._task("t2"), error={"error": "x", "error_type": "y"},
            sample_index=0,
        )
        completed = store.completed_task_ids()
        assert "t1" in completed
        assert "t2" not in completed


# ===================================================================
# ResultStore — artifact paths with sample_index
# ===================================================================


class TestArtifactPathsMultiSample:
    """Artifacts for sample_index > 0 use sample_N subdirectory."""

    def test_sample_0_uses_task_id_directly(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="art0")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")
        resp = AgentResponse(answer="42", system_prompt="Be helpful.")
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
        store.append(task, resp, score, sample_index=0)

        path = tmp_path / "art0" / "artifacts" / "t1" / "agent" / "system_prompt.txt"
        assert path.exists()

    def test_sample_1_uses_sample_subdir(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="art1")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")
        resp = AgentResponse(answer="42", system_prompt="Be helpful.")
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
        store.append(task, resp, score, sample_index=1)

        path = tmp_path / "art1" / "artifacts" / "t1" / "sample_1" / "agent" / "system_prompt.txt"
        assert path.exists()

    def test_per_task_json_filename(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="ptj")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")
        resp = AgentResponse(answer="42")
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")

        store.append(task, resp, score, sample_index=0)
        store.append(task, resp, score, sample_index=2)

        assert (tmp_path / "ptj" / "tasks" / "t1.json").exists()
        assert (tmp_path / "ptj" / "tasks" / "t1_s2.json").exists()


# ===================================================================
# ReportGenerator — pass@k
# ===================================================================


class TestPassAtK:
    """Pass@k computation in ReportGenerator."""

    def _cfg(self, run_id, num_samples=1):
        return ExperimentConfig(
            run_id=run_id, agent_name="test", agent_version="1",
            benchmark_name="test", scorer_name="numeric",
            num_samples=num_samples,
        )

    def _store_samples(self, tmp_path, run_id, task_results):
        """Helper: task_results is a list of (task_id, [(sample_index, correct), ...])."""
        store = ResultStore(output_dir=str(tmp_path), run_id=run_id)
        for task_id, samples in task_results:
            task = BenchmarkTask(task_id=task_id, problem="P", ground_truth="42")
            for si, correct in samples:
                resp = AgentResponse(answer="42" if correct else "0")
                score = ScoreResult(
                    correct=correct, score=1.0 if correct else 0.0,
                    expected="42", predicted="42" if correct else "0",
                )
                store.append(task, resp, score, sample_index=si)
        return store

    def test_single_sample_pass_at_1_equals_accuracy(self, tmp_path):
        """With k=1, pass@1 == accuracy."""
        store = self._store_samples(tmp_path, "pk1", [
            ("t1", [(0, True)]),
            ("t2", [(0, False)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("pk1", num_samples=1))
        assert summary.pass_at_k == summary.accuracy == 0.5

    def test_pass_at_3_all_correct(self, tmp_path):
        """All tasks have at least 1 correct → pass@3 = 1.0."""
        store = self._store_samples(tmp_path, "pk3a", [
            ("t1", [(0, True), (1, False), (2, False)]),
            ("t2", [(0, False), (1, True), (2, False)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("pk3a", num_samples=3))
        assert summary.pass_at_k == 1.0
        assert summary.num_samples == 3

    def test_pass_at_3_none_correct(self, tmp_path):
        """No task has any correct sample → pass@3 = 0.0."""
        store = self._store_samples(tmp_path, "pk3b", [
            ("t1", [(0, False), (1, False), (2, False)]),
            ("t2", [(0, False), (1, False), (2, False)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("pk3b", num_samples=3))
        assert summary.pass_at_k == 0.0

    def test_pass_at_3_partial(self, tmp_path):
        """1 of 2 tasks has a correct sample → pass@3 = 0.5."""
        store = self._store_samples(tmp_path, "pk3c", [
            ("t1", [(0, False), (1, True), (2, False)]),
            ("t2", [(0, False), (1, False), (2, False)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("pk3c", num_samples=3))
        assert summary.pass_at_k == 0.5

    def test_accuracy_is_per_sample(self, tmp_path):
        """accuracy counts per-sample, pass@k counts per-task."""
        store = self._store_samples(tmp_path, "acc", [
            ("t1", [(0, True), (1, True), (2, True)]),
            ("t2", [(0, False), (1, False), (2, False)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("acc", num_samples=3))
        # accuracy = 3 correct / 6 total completed = 0.5
        assert summary.accuracy == 0.5
        # pass@k = 1 of 2 tasks has ≥1 correct = 0.5
        assert summary.pass_at_k == 0.5

    def test_pass_at_k_differs_from_accuracy(self, tmp_path):
        """pass@k can be higher than per-sample accuracy."""
        store = self._store_samples(tmp_path, "diff", [
            ("t1", [(0, True), (1, False), (2, False)]),  # 1/3 correct, but task passes
            ("t2", [(0, False), (1, True), (2, False)]),   # 1/3 correct, but task passes
        ])
        summary = ReportGenerator().generate(store, self._cfg("diff", num_samples=3))
        # accuracy = 2/6 ≈ 0.333
        assert abs(summary.accuracy - 1 / 3) < 1e-6
        # pass@3 = 2/2 = 1.0 (both tasks have at least 1 correct)
        assert summary.pass_at_k == 1.0


class TestAvgAtK:
    """Avg@k computation: per-task average correctness rate, averaged across tasks."""

    def _cfg(self, run_id, num_samples=1):
        return ExperimentConfig(
            run_id=run_id, agent_name="test", agent_version="1",
            benchmark_name="test", scorer_name="numeric",
            num_samples=num_samples,
        )

    def _store_samples(self, tmp_path, run_id, task_results):
        store = ResultStore(output_dir=str(tmp_path), run_id=run_id)
        for task_id, samples in task_results:
            task = BenchmarkTask(task_id=task_id, problem="P", ground_truth="42")
            for si, correct in samples:
                resp = AgentResponse(answer="42" if correct else "0")
                score = ScoreResult(
                    correct=correct, score=1.0 if correct else 0.0,
                    expected="42", predicted="42" if correct else "0",
                )
                store.append(task, resp, score, sample_index=si)
        return store

    def test_avg_at_1_equals_accuracy(self, tmp_path):
        """With k=1, avg@1 == accuracy."""
        store = self._store_samples(tmp_path, "ak1", [
            ("t1", [(0, True)]),
            ("t2", [(0, False)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("ak1", num_samples=1))
        assert summary.avg_at_k == 0.5
        assert summary.avg_at_k == summary.accuracy

    def test_avg_at_3_uniform(self, tmp_path):
        """Each task has 1/3 correct → avg@3 = 1/3."""
        store = self._store_samples(tmp_path, "ak3u", [
            ("t1", [(0, True), (1, False), (2, False)]),
            ("t2", [(0, False), (1, True), (2, False)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("ak3u", num_samples=3))
        # t1: 1/3, t2: 1/3 → avg = 1/3
        assert abs(summary.avg_at_k - 1 / 3) < 1e-6

    def test_avg_at_k_differs_from_pass_at_k(self, tmp_path):
        """avg@k < pass@k when tasks barely pass."""
        store = self._store_samples(tmp_path, "akdiff", [
            ("t1", [(0, True), (1, False), (2, False)]),   # avg=1/3, passes
            ("t2", [(0, False), (1, False), (2, False)]),   # avg=0, fails
        ])
        summary = ReportGenerator().generate(store, self._cfg("akdiff", num_samples=3))
        # pass@3 = 1/2 = 0.5 (t1 passes)
        assert summary.pass_at_k == 0.5
        # avg@3 = (1/3 + 0) / 2 = 1/6
        assert abs(summary.avg_at_k - 1 / 6) < 1e-6

    def test_avg_at_k_all_correct(self, tmp_path):
        store = self._store_samples(tmp_path, "akall", [
            ("t1", [(0, True), (1, True)]),
            ("t2", [(0, True), (1, True)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("akall", num_samples=2))
        assert summary.avg_at_k == 1.0

    def test_avg_at_k_mixed(self, tmp_path):
        """t1: 2/3 correct, t2: 1/3 correct → avg@3 = (2/3 + 1/3)/2 = 0.5."""
        store = self._store_samples(tmp_path, "akmix", [
            ("t1", [(0, True), (1, True), (2, False)]),
            ("t2", [(0, False), (1, True), (2, False)]),
        ])
        summary = ReportGenerator().generate(store, self._cfg("akmix", num_samples=3))
        assert abs(summary.avg_at_k - 0.5) < 1e-6


class TestPassAtKPerCategory:
    """Per-category pass@k computation."""

    def _cfg(self, run_id, num_samples=3):
        return ExperimentConfig(
            run_id=run_id, agent_name="test", agent_version="1",
            benchmark_name="test", scorer_name="numeric",
            num_samples=num_samples,
        )

    def test_per_category_pass_at_k(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="cpk")

        # Task t1 in category "algebra": 1 correct out of 2
        for si, correct in [(0, True), (1, False)]:
            task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42",
                                 metadata={"category": "algebra"})
            resp = AgentResponse(answer="42" if correct else "0")
            score = ScoreResult(correct=correct, score=1.0 if correct else 0.0,
                                expected="42", predicted="42" if correct else "0")
            store.append(task, resp, score, sample_index=si)

        # Task t2 in category "geometry": 0 correct out of 2
        for si, correct in [(0, False), (1, False)]:
            task = BenchmarkTask(task_id="t2", problem="P", ground_truth="42",
                                 metadata={"category": "geometry"})
            resp = AgentResponse(answer="0")
            score = ScoreResult(correct=False, score=0.0, expected="42", predicted="0")
            store.append(task, resp, score, sample_index=si)

        summary = ReportGenerator().generate(store, self._cfg("cpk", num_samples=2))
        assert summary.per_category_pass_at_k["algebra"] == 1.0
        assert summary.per_category_pass_at_k["geometry"] == 0.0
        # avg@k: algebra = 1/2 = 0.5, geometry = 0/2 = 0.0
        assert summary.per_category_avg_at_k["algebra"] == 0.5
        assert summary.per_category_avg_at_k["geometry"] == 0.0


# ===================================================================
# ReportGenerator — markdown output
# ===================================================================


class TestPassAtKMarkdown:
    """Markdown output includes pass@k information."""

    def _cfg(self, run_id, num_samples=1):
        return ExperimentConfig(
            run_id=run_id, agent_name="test", agent_version="1",
            benchmark_name="test", scorer_name="numeric",
            num_samples=num_samples,
        )

    def test_markdown_includes_pass_at_k(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="md")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")
        store.append(task, AgentResponse(answer="42"),
                     ScoreResult(correct=True, score=1.0, expected="42", predicted="42"))

        gen = ReportGenerator()
        summary = gen.generate(store, self._cfg("md", num_samples=1))
        md = gen.to_markdown(summary)
        assert "Pass@1" in md
        assert "Avg@1" in md
        assert "Num Samples (k)" in md

    def test_markdown_per_category_pass_at_k_hidden_for_k1(self, tmp_path):
        """Per-category pass@k table only shown when num_samples > 1."""
        store = ResultStore(output_dir=str(tmp_path), run_id="md1")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42",
                             metadata={"category": "algebra"})
        store.append(task, AgentResponse(answer="42"),
                     ScoreResult(correct=True, score=1.0, expected="42", predicted="42"))

        gen = ReportGenerator()
        summary = gen.generate(store, self._cfg("md1", num_samples=1))
        md = gen.to_markdown(summary)
        assert "Per-Category Pass@" not in md

    def test_markdown_per_category_pass_at_k_shown_for_k3(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="md3")
        for si in range(3):
            task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42",
                                 metadata={"category": "algebra"})
            store.append(task, AgentResponse(answer="42"),
                         ScoreResult(correct=si == 0, score=1.0 if si == 0 else 0.0,
                                     expected="42", predicted="42"),
                         sample_index=si)

        gen = ReportGenerator()
        summary = gen.generate(store, self._cfg("md3", num_samples=3))
        md = gen.to_markdown(summary)
        assert "Per-Category Pass@3" in md
        assert "algebra" in md


# ===================================================================
# RunSummary fields
# ===================================================================


class TestRunSummaryFields:
    """RunSummary has pass@k related fields."""

    def test_default_values(self):
        s = RunSummary(
            run_id="r", agent="a", agent_version="1", benchmark="b",
            total_tasks=0, completed=0, failed=0,
            accuracy=0.0, accuracy_total=0.0, mean_score=0.0,
            mean_wall_time_sec=0.0, total_tokens={}, per_category={},
        )
        assert s.num_samples == 1
        assert s.pass_at_k == 0.0
        assert s.avg_at_k == 0.0
        assert s.per_category_pass_at_k == {}
        assert s.per_category_avg_at_k == {}


# ===================================================================
# Backward compatibility
# ===================================================================


class TestBackwardCompatibility:
    """num_samples=1 is fully backward compatible."""

    def test_load_legacy_records_without_sample_index(self, tmp_path):
        """Records without sample_index field are treated as sample_index=0."""
        store = ResultStore(output_dir=str(tmp_path), run_id="legacy")
        # Manually write a legacy record without sample_index
        record = {
            "task_id": "t1",
            "problem": "P",
            "ground_truth": "42",
            "predicted": "42",
            "correct": True,
            "score": 1.0,
        }
        with open(store.path, "w") as f:
            f.write(json.dumps(record) + "\n")

        records = store.load()
        assert len(records) == 1
        assert records[0].get("sample_index", 0) == 0

    def test_completed_task_ids_works_with_legacy(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="leg2")
        record = {"task_id": "t1", "correct": True, "score": 1.0}
        with open(store.path, "w") as f:
            f.write(json.dumps(record) + "\n")

        assert "t1" in store.completed_task_ids()

    def test_completed_sample_ids_works_with_legacy(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="leg3")
        record = {"task_id": "t1", "correct": True, "score": 1.0}
        with open(store.path, "w") as f:
            f.write(json.dumps(record) + "\n")

        assert ("t1", 0) in store.completed_sample_ids()
