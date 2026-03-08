"""Phase 1: 数据加载与评分验证

验证点:
- 从 HuggingFaceH4/aime_2024 加载 30 道题 (split="train")
- 每道题有 problem（题目文本）和 answer（3 位数字字符串）
- dataset 路径从 config 传入，不硬编码
- NumericScorer 正确比对整数答案
- NumericScorer 处理 \boxed{210} 格式提取
- ResultStore 写入 JSONL 并可读回
- ReportGenerator 计算 accuracy 正确

无需外部服务（除首次下载 HuggingFace 数据集）。
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

    def test_task_has_metadata(self, aime_tasks):
        """每道题有 metadata（source, index 等）。"""
        for task in aime_tasks:
            assert "source" in task.metadata
            assert task.metadata["source"] == "HuggingFaceH4/aime_2024"
            assert "index" in task.metadata

    def test_dataset_path_from_config(self):
        """dataset 路径从 config 传入，不硬编码。"""
        benchmark = AIMEBenchmark()
        # 未指定 dataset 时应报错
        with pytest.raises(ValueError, match="dataset"):
            benchmark.load_tasks({})

    def test_default_scorer_is_numeric(self):
        """AIME benchmark 默认 scorer 是 numeric。"""
        benchmark = AIMEBenchmark()
        assert benchmark.default_scorer() == "numeric"

    def test_benchmark_name(self):
        """Benchmark name 为 'aime'。"""
        benchmark = AIMEBenchmark()
        assert benchmark.name == "aime"


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
        """整数完全匹配: '210' vs '210' → correct。"""
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
        """答案不同: '210' vs '211' → incorrect。"""
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

    def test_boxed_nested_braces(self, scorer):
        r"""嵌套大括号的 \boxed{}。"""
        output = "\\boxed{2^{10}}"
        task, resp = self._make("1024", output)
        # 嵌套内容 "2^{10}" 无法解析为数字，应返回 incorrect
        result = scorer.score(task, resp)
        assert result.correct is False

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

    def test_unparseable_prediction(self, scorer):
        """无法解析的预测值应返回 incorrect。"""
        task, resp = self._make("42", "I don't know")
        result = scorer.score(task, resp)
        assert result.correct is False
        assert result.score == 0.0

    def test_score_result_has_expected_and_predicted(self, scorer):
        """ScoreResult 应包含 expected 和 predicted 字段。"""
        task, resp = self._make("42", "42")
        result = scorer.score(task, resp)
        assert result.expected == "42"
        assert result.predicted == "42"

    def test_thousands_separator(self, scorer):
        """包含千位分隔符的数字: '1,000' vs '1000'，scorer 能正确处理。"""
        task, resp = self._make("1000", "1,000")
        result = scorer.score(task, resp)
        assert result.correct is True


# ---------------------------------------------------------------------------
# 1.3 ResultStore 写入 JSONL 并可读回
# ---------------------------------------------------------------------------

class TestResultStore:
    """验证 ResultStore 的 JSONL 读写。"""

    def test_append_and_load_roundtrip(self, tmp_path):
        """写入后读回，数据一致。"""
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
        """多次 append 后能全部读回。"""
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
        """未写入时 load 返回空列表。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="empty-test")
        results = store.load()
        assert results == []

    def test_creates_output_dir(self, tmp_path):
        """自动创建 output_dir。"""
        nested = str(tmp_path / "nested" / "dir")
        store = ResultStore(output_dir=nested, run_id="dir-test")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="1")
        resp = AgentResponse(answer="1")
        score = ScoreResult(correct=True, score=1.0, expected="1", predicted="1")
        store.append(task, resp, score)
        assert len(store.load()) == 1


# ---------------------------------------------------------------------------
# 1.4 ReportGenerator 计算 accuracy 正确
# ---------------------------------------------------------------------------

class TestReportGenerator:
    """验证 ReportGenerator 的准确率计算和报告生成。"""

    def _make_config(self, run_id="test-run"):
        return ExperimentConfig(
            run_id=run_id,
            agent_name="openclaw",
            agent_version="latest",
            benchmark_name="aime",
            scorer_name="numeric",
        )

    def test_accuracy_calculation(self, tmp_path):
        """3 correct / 5 total → accuracy = 0.6。"""
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
        """全部正确 → accuracy = 1.0。"""
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
        """全部错误 → accuracy = 0.0。"""
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
        """Token 统计聚合正确。"""
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
        """生成的 markdown 报告包含关键信息。"""
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
        """RunSummary 包含所有必要字段。"""
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
