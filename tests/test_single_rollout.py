import json

import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.registry import AgentRegistry
from alphadiana.single_rollout import _select_task, main, run_single


def _task(task_id: str) -> BenchmarkTask:
    return BenchmarkTask(task_id=task_id, problem="p", ground_truth="g")


def test_select_task_requires_one_task_without_id():
    with pytest.raises(ValueError, match="exactly one task"):
        _select_task([_task("a"), _task("b")], None)


def test_select_task_uses_explicit_id():
    selected = _select_task([_task("a"), _task("b")], "b")
    assert selected.task_id == "b"


def test_select_task_reports_unknown_id():
    with pytest.raises(ValueError, match="was not found"):
        _select_task([_task("a")], "missing")


class _FixedAnswerAgent(Agent):
    solved: list[str] = []

    @property
    def name(self) -> str:
        return "single_rollout_fixed_answer"

    def setup(self, config: dict) -> None:
        self.answer = config["answer"]

    def solve(self, task, sandbox=None) -> AgentResponse:
        _FixedAnswerAgent.solved.append(task.task_id)
        return AgentResponse(
            answer=self.answer,
            trajectory=[{"role": "assistant", "content": self.answer}],
        )


AgentRegistry.register("single_rollout_fixed_answer", _FixedAnswerAgent)


def _config(tmp_path, *, num_samples: int = 1):
    path = tmp_path / "task.yaml"
    path.write_text(
        f"""run_id: fixed
output_dir: {tmp_path / "results"}
num_samples: {num_samples}
agent:
  name: single_rollout_fixed_answer
  version: v1
  config:
    answer: "5"
benchmark:
  name: custom
  config:
    problems:
      - id: addition
        problem: What is 2 + 3?
        answer: "5"
scorer:
  name: exact_match
""",
        encoding="utf-8",
    )
    return path


def test_run_single_executes_every_call_in_a_fresh_run(tmp_path):
    config = _config(tmp_path)
    _FixedAnswerAgent.solved.clear()

    first = run_single(str(config))
    second = run_single(str(config), overrides=["agent.config.answer=7"])

    assert _FixedAnswerAgent.solved == ["addition", "addition"]
    assert first["run_id"] != second["run_id"]
    assert (first["status"], first["correct"]) == ("complete", True)
    assert (second["status"], second["correct"]) == ("complete", False)
    assert second["evidence"]["trajectory_available"] is True


def test_run_single_rejects_existing_run_id(tmp_path):
    config = _config(tmp_path)
    run_single(str(config), run_id="reused")

    with pytest.raises(ValueError, match="already has results"):
        run_single(str(config), run_id="reused")


def test_run_single_rejects_multiple_samples(tmp_path):
    with pytest.raises(ValueError, match="num_samples"):
        run_single(str(_config(tmp_path, num_samples=2)))


def test_main_writes_error_result_file(tmp_path):
    output = tmp_path / "result.json"

    code = main([str(_config(tmp_path)), "--task-id", "missing", "--output", str(output)])

    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["error_type"] == "ValueError"
