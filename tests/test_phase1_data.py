"""Phase 1: 数据加载与评分验证

验证 AIME 2024 数据集加载、NumericScorer 评分、ResultStore 读写。
无需外部服务（除首次下载 HuggingFace 数据集）。
"""
import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.aime import AIMEBenchmark
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.results.report import ReportGenerator
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.numeric import NumericScorer


# ---------------------------------------------------------------------------
# 1.1 AIME 2024 数据集加载
# ---------------------------------------------------------------------------

class TestAIMEDatasetLoading:
    """验证从 HuggingFaceH4/aime_2024 正确加载数据。"""

    @pytest.fixture(scope="class")
    def aime_tasks(self):
        benchmark = AIMEBenchmark()
        config = {
            "dataset": "HuggingFaceH4/aime_2024",
            "split": "train",
        }
        return benchmark.load_tasks(config)

    def test_loads_30_tasks(self, aime_tasks):
        """AIME 2024 包含 30 道题（AIME I + AIME II 各 15 题）。"""
        assert len(aime_tasks) == 30

    def test_task_has_problem(self, aime_tasks):
        """每道题有非空的 problem 文本。"""
        for task in aime_tasks:
            assert task.problem, f"Task {task.task_id} has empty problem"
            assert len(task.problem) > 20, f"Task {task.task_id} problem too short"

    def test_task_has_ground_truth(self, aime_tasks):
        """每道题有 ground_truth 答案。"""
        for task in aime_tasks:
            assert task.ground_truth, f"Task {task.task_id} has empty ground_truth"

    def test_ground_truth_is_integer_string(self, aime_tasks):
        """AIME 答案是 0-999 的整数。"""
        for task in aime_tasks:
            val = int(task.ground_truth)
            assert 0 <= val <= 999, f"Task {task.task_id}: answer {val} out of range"

    def test_task_id_unique(self, aime_tasks):
        """task_id 唯一。"""
        ids = [t.task_id for t in aime_tasks]
        assert len(ids) == len(set(ids))

    def test_dataset_path_required(self):
        """未指定 dataset 时应报错。"""
        benchmark = AIMEBenchmark()
        with pytest.raises(ValueError, match="dataset"):
            benchmark.load_tasks({})

    def test_default_scorer_is_numeric(self):
        benchmark = AIMEBenchmark()
        assert benchmark.default_scorer() == "numeric"


# ---------------------------------------------------------------------------
# 1.2 NumericScorer 对 AIME 答案格式的评分
# ---------------------------------------------------------------------------

class TestNumericScorerAIME:
    """验证 NumericScorer 对 AIME 整数答案的评分逻辑。"""

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
        """整数完全匹配。"""
        task, resp = self._make("210", "210")
        result = scorer.score(task, resp)
        assert result.correct is True
        assert result.score == 1.0

    def test_correct_integer_as_float(self, scorer):
        """'210' vs '210.0' 应匹配。"""
        task, resp = self._make("210", "210.0")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_incorrect_integer(self, scorer):
        """答案不同。"""
        task, resp = self._make("210", "211")
        result = scorer.score(task, resp)
        assert result.correct is False
        assert result.score == 0.0

    def test_boxed_extraction(self, scorer):
        r"""从 \boxed{210} 中提取答案。"""
        task, resp = self._make("210", "The answer is \\boxed{210}.")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_boxed_with_reasoning(self, scorer):
        r"""推理过程后跟 \boxed{}。"""
        output = "First we compute... so the answer is \\boxed{42}"
        task, resp = self._make("42", output)
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_leading_zeros(self, scorer):
        """AIME 答案可能有前导零如 '042'，NumericScorer 应当处理。"""
        task, resp = self._make("42", "042")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_answer_zero(self, scorer):
        """答案为 0。"""
        task, resp = self._make("0", "0")
        result = scorer.score(task, resp)
        assert result.correct is True

    def test_answer_999(self, scorer):
        """答案为 999（AIME 最大值）。"""
        task, resp = self._make("999", "999")
        result = scorer.score(task, resp)
        assert result.correct is True


# ---------------------------------------------------------------------------
# 1.3 ResultStore 与 ReportGenerator
# ---------------------------------------------------------------------------

class TestResultStoreAndReport:
    """验证结果存储和报告生成。"""

    def test_append_and_load_roundtrip(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="phase1-test")

        task = BenchmarkTask(task_id="aime_1", problem="Compute X.", ground_truth="42")
        resp = AgentResponse(
            answer="42",
            token_usage={"prompt_tokens": 100, "completion_tokens": 200},
            wall_time_sec=5.0,
        )
        from alphadiana.scorer.base import ScoreResult
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")

        store.append(task, resp, score)
        results = store.load()

        assert len(results) == 1
        r = results[0]
        assert r["task_id"] == "aime_1"
        assert r["ground_truth"] == "42"
        assert r["predicted"] == "42"
        assert r["correct"] is True
        assert r["score"] == 1.0
        assert "timestamp" in r

    def test_report_accuracy(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="acc-test")
        from alphadiana.scorer.base import ScoreResult

        # 3 correct, 2 incorrect
        for i in range(5):
            correct = i < 3
            task = BenchmarkTask(task_id=f"t{i}", problem=f"P{i}", ground_truth="42")
            resp = AgentResponse(answer="42" if correct else "0")
            score = ScoreResult(
                correct=correct, score=1.0 if correct else 0.0,
                expected="42", predicted="42" if correct else "0",
            )
            store.append(task, resp, score)

        config = ExperimentConfig(
            run_id="acc-test",
            agent_name="openclaw",
            agent_version="latest",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        gen = ReportGenerator()
        summary = gen.generate(store, config)

        assert summary.total_tasks == 5
        assert summary.accuracy == pytest.approx(0.6)
        assert summary.mean_score == pytest.approx(0.6)

    def test_report_markdown(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="md-test")
        from alphadiana.scorer.base import ScoreResult

        task = BenchmarkTask(task_id="t1", problem="P1", ground_truth="42")
        resp = AgentResponse(answer="42")
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")
        store.append(task, resp, score)

        config = ExperimentConfig(
            run_id="md-test",
            agent_name="openclaw",
            agent_version="latest",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        gen = ReportGenerator()
        summary = gen.generate(store, config)
        md = gen.to_markdown(summary)

        assert "md-test" in md
        assert "openclaw" in md
        assert "1.0000" in md
