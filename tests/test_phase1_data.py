"""Phase 1: data loading and scoring validation

Checks:
- Load 30 questions from HuggingFaceH4/aime_2024 (split="train")
- Each question has a problem field and an answer field (a 3-digit numeric string)
- The dataset path comes from config rather than being hardcoded
- NumericScorer compares integer answers correctly
- NumericScorer can extract answers from the \boxed{210} format
- ResultStore writes JSONL and can read it back
- ReportGenerator computes accuracy correctly

No external services are required, other than the initial HuggingFace dataset download.
"""
import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.aime import AIMEBenchmark
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.results.report import ReportGenerator
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult
from alphadiana.scorer.numeric import NumericScorer


# ---------------------------------------------------------------------------
# 1.1 AIME 2024 dataset loading
# ---------------------------------------------------------------------------

class TestAIMEDatasetLoading:
    """Verify that the data is loaded correctly from HuggingFaceH4/aime_2024."""

    @pytest.fixture(scope="class")
    def aime_tasks(self):
        benchmark = AIMEBenchmark()
        config = {
            "dataset": "HuggingFaceH4/aime_2024",
            "split": "train",
        }
        return benchmark.load_tasks(config)

    def test_loads_30_tasks(self, aime_tasks):
        """AIME 2024 consists of 30 questions (15 for AIME I + 15 for AIME II)."""
        assert len(aime_tasks) == 30

    def test_task_has_problem(self, aime_tasks):
        """Each question has a non-empty problem text."""
        for task in aime_tasks:
            assert task.problem, f"Task {task.task_id} has empty problem"
            assert len(task.problem) > 20, f"Task {task.task_id} problem too short"

    def test_task_has_ground_truth(self, aime_tasks):
        """Each question has a ground_truth answer."""
        for task in aime_tasks:
            assert task.ground_truth, f"Task {task.task_id} has empty ground_truth"

    def test_ground_truth_is_integer_string(self, aime_tasks):
        """AIME answers are integers from 0 to 999."""
        for task in aime_tasks:
            val = int(task.ground_truth)
            assert 0 <= val <= 999, f"Task {task.task_id}: answer {val} out of range"

    def test_task_id_unique(self, aime_tasks):
        """task_id is unique."""
        ids = [t.task_id for t in aime_tasks]
        assert len(ids) == len(set(ids))

    def test_task_has_metadata(self, aime_tasks):
        """Each question has metadata (source, index, etc.)."""
        for task in aime_tasks:
            assert "source" in task.metadata
            assert task.metadata["source"] == "HuggingFaceH4/aime_2024"
            assert "index" in task.metadata

    def test_dataset_path_from_config(self):
        """The dataset path comes from config rather than being hardcoded."""
        benchmark = AIMEBenchmark()
        # Error should be reported when dataset is not specified
        with pytest.raises(ValueError, match="dataset"):
            benchmark.load_tasks({})

    def test_default_scorer_is_numeric(self):
        """AIME benchmark default scorer is numeric."""
        benchmark = AIMEBenchmark()
        assert benchmark.default_scorer() == "numeric"

    def test_benchmark_name(self):
        """The benchmark name is 'aime'."""
        benchmark = AIMEBenchmark()
        assert benchmark.name == "aime"


# ---------------------------------------------------------------------------
# 1.2 NumericScorer scoring for AIME answer formats
# ---------------------------------------------------------------------------

class TestNumericScorerAIME:
    """Validate NumericScorer's scoring logic for AIME integer answers."""

    @pytest.fixture
    def scorer(self):
        s = NumericScorer()
        s.setup({"tolerance": 1e-6})
        return s

    def _make(self, ground_truth, predicted):
        task = BenchmarkTask(task_id="t1", problem="test", ground_truth=ground_truth)
        resp = AgentResponse(answer=predicted)
        return task, resp

    def test_correct_integer(self, scorer):
        """Exact integer match: '210' vs '210' -> correct."""
        task, resp = self._make("210", "210")
        result = scorer.score(task, resp)
        assert result.correct is True
        assert result.score == 1.0

    def test_correct_integer_as_float(self, scorer):
        """'210' vs '210.0' should match."""
        task, resp = self._make("210", "210.0")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_incorrect_integer(self, scorer):
        """Different answers: '210' vs '211' -> incorrect."""
        task, resp = self._make("210", "211")
        result = scorer.score(task, resp)
        assert result.correct is False
        assert result.score == 0.0

    def test_boxed_extraction(self, scorer):
        r"""Extract the answer from \boxed{210}."""
        task, resp = self._make("210", "The answer is \\boxed{210}.")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_boxed_with_reasoning(self, scorer):
        r"""Reasoning text followed by \boxed{}."""
        output = "First we compute... so the answer is \\boxed{42}"
        task, resp = self._make("42", output)
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_boxed_nested_braces(self, scorer):
        r"""\boxed{} with nested braces."""
        output = "\\boxed{2^{10}}"
        task, resp = self._make("1024", output)
        # The nested content "2^{10}" cannot be parsed as a number, so it should be incorrect.
        result = scorer.score(task, resp)
        assert result.correct is False

    def test_leading_zeros(self, scorer):
        """AIME answers may have leading zeros such as '042'; NumericScorer should handle them."""
        task, resp = self._make("42", "042")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_answer_zero(self, scorer):
        """The answer is 0."""
        task, resp = self._make("0", "0")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_answer_999(self, scorer):
        """The answer is 999 (AIME maximum)."""
        task, resp = self._make("999", "999")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_unparseable_prediction(self, scorer):
        """An unparseable prediction should return incorrect."""
        task, resp = self._make("42", "I don't know")
        result = scorer.score(task, resp)
        assert result.correct is False
        assert result.score == 0.0

    def test_score_result_has_expected_and_predicted(self, scorer):
        """ScoreResult should contain expected and predicted fields."""
        task, resp = self._make("42", "42")
        result = scorer.score(task, resp)
        assert result.expected == "42"
        assert result.predicted == "42"

    def test_thousands_separator(self, scorer):
        """Numbers with thousands separators, like '1,000' vs '1000', are handled correctly."""
        task, resp = self._make("1000", "1,000")
        result = scorer.score(task, resp)
        assert result.correct is True


# ---------------------------------------------------------------------------
# 1.3 ResultStore writes JSONL and can read it back
# ---------------------------------------------------------------------------

class TestResultStore:
    """Validate ResultStore's JSONL reads and writes."""

    def test_append_and_load_roundtrip(self, tmp_path):
        """Data remains consistent after writing and reading it back."""
        store = ResultStore(output_dir=str(tmp_path), run_id="phase1-test")

        task = BenchmarkTask(task_id="aime_1", problem="Compute X.", ground_truth="42")
        resp = AgentResponse(
            answer="42",
            token_usage={"prompt_tokens": 100, "completion_tokens": 200},
            wall_time_sec=5.0,
        )
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")

        store.append(task, resp, score)
        results = store.load()

        assert len(results) == 1
        r = results[0]
        assert r["task_id"] == "aime_1"
        assert r["problem"] == "Compute X."
        assert r["ground_truth"] == "42"
        assert r["predicted"] == "42"
        assert r["correct"] is True
        assert r["score"] == 1.0
        assert r["wall_time_sec"] == 5.0
        assert r["token_usage"]["prompt_tokens"] == 100
        assert r["token_usage"]["completion_tokens"] == 200
        assert "timestamp" in r

    def test_multiple_appends(self, tmp_path):
        """Read it all back after multiple appends."""
        store = ResultStore(output_dir=str(tmp_path), run_id="multi-test")

        for i in range(5):
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="42")
            resp = AgentResponse(answer="42" if i < 3 else "0")
            score = ScoreResult(
                correct=i < 3, score=1.0 if i < 3 else 0.0,
                expected="42", predicted="42" if i < 3 else "0",
            )
            store.append(task, resp, score)

        results = store.load()
        assert len(results) == 5

    def test_empty_store_returns_empty_list(self, tmp_path):
        """Load returns an empty list when not written."""
        store = ResultStore(output_dir=str(tmp_path), run_id="empty-test")
        results = store.load()
        assert results == []

    def test_creates_output_dir(self, tmp_path):
        """Create output_dir automatically."""
        nested = str(tmp_path / "nested" / "dir")
        store = ResultStore(output_dir=nested, run_id="dir-test")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="1")
        resp = AgentResponse(answer="1")
        score = ScoreResult(correct=True, score=1.0, expected="1", predicted="1")
        store.append(task, resp, score)
        assert len(store.load()) == 1


# ---------------------------------------------------------------------------
# 1.4 ReportGenerator computes accuracy correctly
# ---------------------------------------------------------------------------

class TestReportGenerator:
    """Verify ReportGenerator accuracy calculation and report generation."""

    def _make_config(self, run_id="test-run"):
        return ExperimentConfig(
            run_id=run_id,
            agent_name="openclaw",
            agent_version="latest",
            benchmark_name="aime",
            scorer_name="numeric",
        )

    def test_accuracy_calculation(self, tmp_path):
        """3 correct / 5 total -> accuracy = 0.6."""
        store = ResultStore(output_dir=str(tmp_path), run_id="acc-test")

        for i in range(5):
            correct = i < 3
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="42")
            resp = AgentResponse(answer="42" if correct else "0")
            score = ScoreResult(
                correct=correct, score=1.0 if correct else 0.0,
                expected="42", predicted="42" if correct else "0",
            )
            store.append(task, resp, score)

        gen = ReportGenerator()
        summary = gen.generate(store, self._make_config("acc-test"))

        assert summary.total_tasks == 5
        assert summary.accuracy == pytest.approx(0.6)
        assert summary.mean_score == pytest.approx(0.6)

    def test_perfect_accuracy(self, tmp_path):
        """All correct -> accuracy = 1.0."""
        store = ResultStore(output_dir=str(tmp_path), run_id="perfect-test")

        for i in range(3):
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="42")
            resp = AgentResponse(answer="42")
            score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
            store.append(task, resp, score)

        gen = ReportGenerator()
        summary = gen.generate(store, self._make_config("perfect-test"))
        assert summary.accuracy == pytest.approx(1.0)

    def test_zero_accuracy(self, tmp_path):
        """All incorrect -> accuracy = 0.0."""
        store = ResultStore(output_dir=str(tmp_path), run_id="zero-test")

        for i in range(3):
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="42")
            resp = AgentResponse(answer="0")
            score = ScoreResult(correct=False, score=0.0, expected="42", predicted="0")
            store.append(task, resp, score)

        gen = ReportGenerator()
        summary = gen.generate(store, self._make_config("zero-test"))
        assert summary.accuracy == pytest.approx(0.0)

    def test_token_aggregation(self, tmp_path):
        """Token statistics are aggregated correctly."""
        store = ResultStore(output_dir=str(tmp_path), run_id="token-test")

        for i in range(3):
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="42")
            resp = AgentResponse(
                answer="42",
                token_usage={"prompt_tokens": 100, "completion_tokens": 50},
            )
            score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
            store.append(task, resp, score)

        gen = ReportGenerator()
        summary = gen.generate(store, self._make_config("token-test"))
        assert summary.total_tokens["prompt_tokens"] == 300
        assert summary.total_tokens["completion_tokens"] == 150

    def test_markdown_report(self, tmp_path):
        """The resulting markdown report contains key information."""
        store = ResultStore(output_dir=str(tmp_path), run_id="md-test")

        task = BenchmarkTask(task_id="t1", problem="P1", ground_truth="42")
        resp = AgentResponse(answer="42")
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
        store.append(task, resp, score)

        gen = ReportGenerator()
        summary = gen.generate(store, self._make_config("md-test"))
        md = gen.to_markdown(summary)

        assert "md-test" in md
        assert "openclaw" in md
        assert "aime" in md
        assert "1.0000" in md
        assert "Accuracy" in md

    def test_summary_fields(self, tmp_path):
        """RunSummary contains all required fields."""
        store = ResultStore(output_dir=str(tmp_path), run_id="fields-test")

        task = BenchmarkTask(task_id="t1", problem="P1", ground_truth="42")
        resp = AgentResponse(answer="42", wall_time_sec=2.5)
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
        store.append(task, resp, score)

        gen = ReportGenerator()
        summary = gen.generate(store, self._make_config("fields-test"))

        assert summary.run_id == "fields-test"
        assert summary.agent == "openclaw"
        assert summary.agent_version == "latest"
        assert summary.benchmark == "aime"
        assert summary.total_tasks == 1
        assert summary.completed == 1
        assert summary.failed == 0
        assert summary.mean_wall_time_sec == pytest.approx(2.5)
        assert summary.timestamp  # non-empty
