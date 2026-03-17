"""Phase 5: validate OpenClawAgent on a single task

Checks:
- OpenClawAgent.setup() initializes correctly (api_base, gateway_token, model)
- OpenClawAgent.solve(task) calls the OpenClaw gateway and returns AgentResponse
- raw_output contains reasoning text
- _extract_answer() extracts the answer from the output (\\boxed{} or the last line)
- NumericScorer.score() can score the output
- ResultStore.append() saves the result to JSONL

Requires:
  - OpenClaw gateway available (Phase 4)
  - OPENCLAW_SANDBOX_ID environment variable

Run these tests with pytest -m integration.
"""
import os

import pytest

from alphadiana.utils.rock_ports import resolve_rock_ports_from_env

pytestmark = pytest.mark.integration
PORTS = resolve_rock_ports_from_env()


def _get_sandbox_id() -> str | None:
    return os.environ.get("OPENCLAW_SANDBOX_ID")


def _get_openclaw_api_base() -> str:
    sandbox_id = _get_sandbox_id()
    return f"{PORTS.proxy_api_url}/sandboxes/{sandbox_id}/proxy/v1"


# ---------------------------------------------------------------------------
# 5.1 _extract_answer unit tests (no external service required)
# ---------------------------------------------------------------------------

class TestExtractAnswer:
    """Validate the answer extraction logic of _extract_answer."""

    def test_boxed_simple(self):
        from alphadiana.agent.openclaw import _extract_answer
        assert _extract_answer("The answer is \\boxed{42}") == "42"

    def test_boxed_with_reasoning(self):
        from alphadiana.agent.openclaw import _extract_answer
        text = "First we compute 6*7=42. So the answer is \\boxed{42}."
        assert _extract_answer(text) == "42"

    def test_boxed_nested(self):
        from alphadiana.agent.openclaw import _extract_answer
        text = "\\boxed{2^{10}}"
        assert _extract_answer(text) == "2^{10}"

    def test_last_boxed_wins(self):
        from alphadiana.agent.openclaw import _extract_answer
        text = "\\boxed{10} ... wait, \\boxed{42}"
        assert _extract_answer(text) == "42"

    def test_no_boxed_falls_back_to_last_line(self):
        from alphadiana.agent.openclaw import _extract_answer
        text = "Step 1: compute\nStep 2: simplify\n42"
        assert _extract_answer(text) == "42"

    def test_empty_string(self):
        from alphadiana.agent.openclaw import _extract_answer
        assert _extract_answer("") == ""

    def test_single_line(self):
        from alphadiana.agent.openclaw import _extract_answer
        assert _extract_answer("210") == "210"


# ---------------------------------------------------------------------------
# 5.2 OpenClawAgent integration tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _get_sandbox_id() is None,
    reason="OPENCLAW_SANDBOX_ID not set."
)
class TestOpenClawAgentSingleTask:
    """Run a single AIME problem with OpenClawAgent."""

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
        """A simple AIME-style problem."""
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
        """The agent initializes successfully with the correct name and version."""
        assert agent.name == "openclaw"
        assert agent.version == "latest"

    def test_solve_returns_response(self, agent, sample_task):
        """solve() returns AgentResponse."""
        from alphadiana.agent.base import AgentResponse
        resp = agent.solve(sample_task)
        assert isinstance(resp, AgentResponse)
        assert resp.answer is not None
        assert len(str(resp.answer)) > 0

    def test_raw_output_has_reasoning(self, agent, sample_task):
        """raw_output contains reasoning text."""
        resp = agent.solve(sample_task)
        assert len(resp.raw_output) > 50, "Expected reasoning text in raw_output"

    def test_wall_time_recorded(self, agent, sample_task):
        """wall_time_sec is recorded and greater than 0."""
        resp = agent.solve(sample_task)
        assert resp.wall_time_sec > 0

    def test_token_usage_recorded(self, agent, sample_task):
        """token_usage is recorded."""
        resp = agent.solve(sample_task)
        if resp.token_usage:
            assert "prompt_tokens" in resp.token_usage
            assert "completion_tokens" in resp.token_usage

    def test_trajectory_recorded(self, agent, sample_task):
        """trajectory contains the conversation history."""
        resp = agent.solve(sample_task)
        assert len(resp.trajectory) > 0
        roles = [m["role"] for m in resp.trajectory]
        # OpenClaw manages its own system prompt internally.
        # The trajectory should at least contain user and assistant messages.
        assert "user" in roles
        assert "assistant" in roles

    def test_scorer_works_on_response(self, agent, sample_task):
        """NumericScorer can score the agent output."""
        from alphadiana.scorer.numeric import NumericScorer
        resp = agent.solve(sample_task)
        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(sample_task, resp)
        # Do not assert correctness here; the model may answer incorrectly.
        # We only verify that scoring does not crash.
        assert result.score in (0.0, 1.0)
        assert isinstance(result.correct, bool)
        assert result.expected == "27"

    def test_result_store_saves(self, agent, sample_task, tmp_path):
        """ResultStore can save the agent result to JSONL."""
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


# ---------------------------------------------------------------------------
# 5.3 Test with the real first AIME 2024 problem
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _get_sandbox_id() is None,
    reason="OPENCLAW_SANDBOX_ID not set."
)
class TestOpenClawOnRealAIMETask:
    """Test the full pipeline with a real AIME 2024 problem."""

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
        """Load and solve the first AIME 2024 problem to validate the full pipeline."""
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
        # OpenClaw gateway may timeout on complex AIME problems,
        # so raw_output might be empty. We just verify the pipeline doesn't crash.

        scorer = NumericScorer()
        scorer.setup({"tolerance": 1e-6})
        result = scorer.score(task, resp)

        print(f"\nTask: {task.task_id}")
        print(f"Ground truth: {task.ground_truth}")
        print(f"Predicted: {resp.answer}")
        print(f"Correct: {result.correct}")
        print(f"Wall time: {resp.wall_time_sec:.1f}s")
