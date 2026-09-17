import json
from types import SimpleNamespace

import pytest

from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.analysis.io.status import (
    infer_score_status,
    is_legacy_timeout_zero_record,
    is_valid_completed_record,
)
from alphadiana.analysis.report import ReportGenerator
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.registry import AgentRegistry
from alphadiana.scorer.base import ScoreResult, Scorer
from alphadiana.scorer.registry import ScorerRegistry
from alphadiana.single_rollout import _record_payload, _select_task, main, run_single


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


class _EvaluatorFailureScorer(Scorer):
    @property
    def name(self) -> str:
        return "single_rollout_evaluator_failure"

    def score(self, task, response) -> ScoreResult:
        return ScoreResult(
            correct=False,
            score=-1000.0,
            expected=task.ground_truth,
            predicted=response.answer,
            metadata={"error": "official evaluator unavailable"},
        )


ScorerRegistry.register("single_rollout_evaluator_failure", _EvaluatorFailureScorer)


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


@pytest.mark.parametrize(
    ("benchmark_name", "expected_status"),
    [
        ("ale_bench", "verifier_error"),
        ("evox_problems", "verifier_error"),
        ("custom", "scorer_error"),
    ],
)
def test_evaluator_error_is_not_a_candidate_score(benchmark_name, expected_status):
    record = {
        "benchmark_name": benchmark_name,
        "score_status": "valid_scored",
        "score": -1000.0,
        "correct": False,
        "score_metadata": {"error": "official evaluator unavailable"},
    }
    assert infer_score_status(record) == expected_status


def test_candidate_compilation_error_remains_a_valid_score():
    record = {
        "benchmark_name": "ale_bench",
        "score": -1000.0,
        "correct": False,
        "score_metadata": {"judge_result": "COMPILATION_ERROR"},
    }
    assert infer_score_status(record) == "valid_scored"


def test_ale_internal_error_is_invalid_evaluation():
    record = {
        "benchmark_name": "ale_bench",
        "score_status": "valid_scored",
        "score": -1000.0,
        "correct": False,
        "score_metadata": {"judge_result": "INTERNAL_ERROR"},
    }
    assert infer_score_status(record) == "verifier_error"
    assert is_valid_completed_record(record) is False


def test_evaluator_error_is_not_normalized_to_legacy_timeout_score():
    record = {
        "benchmark_name": "evox_problems",
        "error": {"error_type": "AgentTimeoutError", "message": "timed out after 60s"},
        "score_metadata": {"error": "official evaluator unavailable"},
    }
    assert is_legacy_timeout_zero_record(record) is False
    assert infer_score_status(record) == "verifier_error"


@pytest.mark.parametrize("status", ["verifier_error", "scorer_error"])
def test_typed_evaluator_timeout_is_not_a_candidate_timeout(status):
    record = {
        "error": {"error_type": status, "message": "timed out after 60s"},
        "score": None,
        "correct": None,
    }
    assert not is_legacy_timeout_zero_record(record)
    assert infer_score_status(record) == status


def test_empty_exception_message_still_invalidates_score():
    record = {
        "benchmark_name": "ale_bench",
        "score": -1000.0,
        "correct": False,
        "score_metadata": {"error": ""},
    }
    assert infer_score_status(record) == "verifier_error"


@pytest.mark.parametrize(
    "score_metadata",
    [
        {"metrics": {"combined_score": 0.0, "error": 0.0}},
        {"metrics": {"combined_score": 0.0}, "artifact_keys": ["error", "status"]},
        {"metrics": {"combined_score": 0.0}, "artifact_keys": ["error"]},
    ],
)
def test_evox_upstream_failure_signals_never_become_rewards(score_metadata):
    record = {
        "benchmark_name": "evox_problems",
        "score_status": "valid_scored",
        "score": -1000.0,
        "correct": False,
        "score_metadata": score_metadata,
    }
    assert infer_score_status(record) == "verifier_error"
    assert not is_valid_completed_record(record)
    record.update(score=None, correct=None, score_status="runtime_error")
    record["error"] = {
        "error_type": "AgentTimeoutError",
        "message": "timed out after 60s",
    }
    assert not is_legacy_timeout_zero_record(record)
    assert infer_score_status(record) == "verifier_error"


def test_evox_measured_zero_without_error_remains_valid():
    assert (
        infer_score_status(
            {
                "benchmark_name": "evox_problems",
                "score": 0.0,
                "correct": True,
                "score_metadata": {
                    "metrics": {"combined_score": 0.0},
                    "artifact_keys": [],
                },
            }
        )
        == "valid_scored"
    )


@pytest.mark.parametrize(
    ("score_status", "error_type", "expected_category"),
    [
        ("provider_error", "ProviderError", "serving_failure"),
        ("runtime_error", "native_harness_container", "infrastructure_failure"),
        ("runtime_error", "native_harness_execution", "execution_failure"),
        ("verifier_error", "", "evaluation_failure"),
        ("no_answer", "", "candidate_failure"),
        ("agent_error", "AgentError", "execution_failure"),
        ("unscored", "", "unscored_failure"),
        ("preserved_failure", "", "unscored_failure"),
    ],
)
def test_single_rollout_failure_categories(
    tmp_path, score_status, error_type, expected_category
):
    config = SimpleNamespace(
        output_dir=str(tmp_path),
        run_id="run",
        agent_name="pi",
        agent_version="v1",
        benchmark_name="ale_bench",
        scorer_name="ale_bench",
    )
    store = SimpleNamespace(
        path=str(tmp_path / "run.jsonl"),
        manifest_path=tmp_path / "run" / "run_manifest.json",
    )
    runner = SimpleNamespace(result_store=store)
    record = {
        "score_status": score_status,
        "score": -1000.0,
        "correct": False,
        "error": {"error_type": error_type} if error_type else None,
    }
    payload = _record_payload(config, runner, "task", record)
    assert payload["outcome_category"] == expected_category
    assert payload["score"] is None
    assert payload["correct"] is None


def test_single_rollout_masks_evaluator_failure_score(tmp_path):
    config = _config(tmp_path)
    result = run_single(
        str(config), overrides=["scorer.name=single_rollout_evaluator_failure"]
    )
    assert result["status"] == "scorer_error"
    assert result["score_status"] == "scorer_error"
    assert result["outcome_category"] == "evaluation_failure"
    assert result["score"] is None
    assert result["correct"] is None

    with open(result["evidence"]["result_jsonl"], encoding="utf-8") as source:
        records = [json.loads(line) for line in source]
    assert len(records) == 1
    assert records[0]["score"] == -1000.0  # retained only as forensic evidence
    assert records[0]["score_status"] == "scorer_error"
    assert is_valid_completed_record(records[0]) is False

    summary = ReportGenerator().generate(
        ResultStore(str(tmp_path / "results"), result["run_id"])
    )
    assert summary.valid_scored == 0
    assert summary.failed == 1


def test_main_writes_error_result_file(tmp_path):
    output = tmp_path / "result.json"

    code = main(
        [str(_config(tmp_path)), "--task-id", "missing", "--output", str(output)]
    )

    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["error_type"] == "ValueError"
