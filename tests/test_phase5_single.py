"""Phase 5: OpenClawAgent 单题验证

验证 AlphaDiana OpenClawAgent 全链路:
  agent.setup() → agent.solve(task) → scorer.score() → result_store.append()

需要:
  - OpenClaw gateway 可用 (Phase 4)
  - OPENCLAW_SANDBOX_ID 环境变量

用 pytest -m integration 运行这些测试。
"""
import os

import pytest

pytestmark = pytest.mark.integration


def _get_sandbox_id() -> str | None:
    return os.environ.get("OPENCLAW_SANDBOX_ID")


def _get_openclaw_api_base() -> str:
    sandbox_id = _get_sandbox_id()
    return f"http://localhost:9001/apis/envs/sandbox/v1/sandboxes/{sandbox_id}/proxy/v1"


@pytest.mark.skipif(
    _get_sandbox_id() is None,
    reason="OPENCLAW_SANDBOX_ID not set."
)
class TestOpenClawAgentSingleTask:
    """用 OpenClawAgent 跑一道 AIME 题。"""

    @pytest.fixture
    def agent(self):
        from alphadiana.agent.openclaw import OpenClawAgent
        a = OpenClawAgent()
        a.setup({
            "version": "latest",
            "api_base": _get_openclaw_api_base(),
            "model": "openclaw",
            "gateway_token": "OPENCLAW",
            "temperature": 0.0,
            "max_tokens": 8192,
        })
        return a

    @pytest.fixture
    def sample_task(self):
        """一道简单的 AIME 风格题目。"""
        from alphadiana.benchmark.base import BenchmarkTask
        return BenchmarkTask(
            task_id="aime_test_1",
            problem=(
                "Find the number of positive integers n less than or equal to "
                "100 that are divisible by 3 but not by 5."
            ),
            ground_truth="27",
        )

    def test_agent_setup(self, agent):
        """Agent 初始化成功。"""
        assert agent.name == "openclaw"
        assert agent.version == "latest"

    def test_solve_returns_response(self, agent, sample_task):
        """solve() 返回 AgentResponse。"""
        from alphadiana.agent.base import AgentResponse
        resp = agent.solve(sample_task)
        assert isinstance(resp, AgentResponse)
        assert resp.answer is not None
        assert len(str(resp.answer)) > 0

    def test_raw_output_has_reasoning(self, agent, sample_task):
        """raw_output 包含推理过程。"""
        resp = agent.solve(sample_task)
        assert len(resp.raw_output) > 50, "Expected reasoning text in raw_output"

    def test_wall_time_recorded(self, agent, sample_task):
        """wall_time_sec 被记录。"""
        resp = agent.solve(sample_task)
        assert resp.wall_time_sec > 0

    def test_scorer_works_on_response(self, agent, sample_task):
        """NumericScorer 能对 agent 输出评分。"""
        from alphadiana.scorer.numeric import NumericScorer
        resp = agent.solve(sample_task)
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(sample_task, resp)
        # 不检查正确性（模型可能答错），只检查不崩溃
        assert result.score in (0.0, 1.0)
        assert isinstance(result.correct, bool)
        assert result.expected == "27"

    def test_result_store_saves(self, agent, sample_task, tmp_path):
        """ResultStore 能保存 agent 结果。"""
        from alphadiana.results.result_store import ResultStore
        from alphadiana.scorer.numeric import NumericScorer

        resp = agent.solve(sample_task)
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        score = scorer.score(sample_task, resp)

        store = ResultStore(output_dir=str(tmp_path), run_id="single-test")
        store.append(sample_task, resp, score)

        results = store.load()
        assert len(results) == 1
        assert results[0]["task_id"] == "aime_test_1"
        assert results[0]["predicted"] is not None


@pytest.mark.skipif(
    _get_sandbox_id() is None,
    reason="OPENCLAW_SANDBOX_ID not set."
)
class TestOpenClawOnRealAIMETask:
    """用真实 AIME 2024 题目测试。"""

    @pytest.fixture
    def agent(self):
        from alphadiana.agent.openclaw import OpenClawAgent
        a = OpenClawAgent()
        a.setup({
            "version": "latest",
            "api_base": _get_openclaw_api_base(),
            "model": "openclaw",
            "gateway_token": "OPENCLAW",
            "temperature": 0.0,
            "max_tokens": 8192,
        })
        return a

    def test_solve_first_aime_task(self, agent):
        """加载 AIME 2024 第一题并求解。"""
        from alphadiana.benchmark.aime import AIMEBenchmark
        from alphadiana.scorer.numeric import NumericScorer

        benchmark = AIMEBenchmark()
        tasks = benchmark.load_tasks({
            "dataset": "HuggingFaceH4/aime_2024",
            "split": "train",
        })
        assert len(tasks) > 0

        task = tasks[0]
        resp = agent.solve(task)

        assert resp.answer is not None
        assert resp.raw_output, "Expected non-empty raw_output"

        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(task, resp)

        print(f"\nTask: {task.task_id}")
        print(f"Ground truth: {task.ground_truth}")
        print(f"Predicted: {resp.answer}")
        print(f"Correct: {result.correct}")
        print(f"Wall time: {resp.wall_time_sec:.1f}s")
