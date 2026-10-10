"""Offline reproductions from Tian's review; no provider or container is started."""

import json

import pytest

import alphadiana as ad
from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.artifacts import Artifacts
from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.registry import AgentRegistry
from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import ScorerRegistry


class ReviewTasks(Benchmark):
    def load_tasks(self, config):
        return [BenchmarkTask(name, "Return 2", "2") for name in ("ok", "failed")]


class ReviewAgent(Agent):
    closed = 0

    def setup(self, config):
        pass

    def solve(self, task, sandbox=None):
        if task.task_id == "failed":
            raise RuntimeError("constructed execution failure")
        return AgentResponse("2")

    def teardown(self):
        type(self).closed += 1


class ReviewScorer(Scorer):
    name = "review_score"

    def score(self, task, response):
        return ScoreResult(True, 1.0, "2", response.answer)


def test_strict_python_run_rejects_partial_failure(tmp_path, monkeypatch):
    monkeypatch.setitem(BenchmarkRegistry._registry, "review_tasks", ReviewTasks)
    monkeypatch.setitem(AgentRegistry._registry, "review_agent", ReviewAgent)
    monkeypatch.setitem(ScorerRegistry._registry, "review_score", ReviewScorer)
    before = ReviewAgent.closed
    with pytest.raises(RuntimeError, match="Strict report failed"):
        ad.runner(output_dir=str(tmp_path), run_id="mixed", strict_report=True).compute(
            agent=ad.agent("review_agent", version="fixture-1"),
            data=ad.dataset("review_tasks"),
            scorer=ad.scorer.create("review_score"),
        )
    assert ReviewAgent.closed > before
    rows = Artifacts(tmp_path / "mixed.jsonl").records()
    assert len(rows) == 2
    assert sum(row["score_status"] == "valid_scored" for row in rows) == 1


def test_saved_attempt_keeps_its_trace_after_a_rerun(tmp_path):
    store = ResultStore(str(tmp_path), "attempts")
    task = BenchmarkTask("task", "Return 2", "2")
    store.append(
        task,
        AgentResponse("old", response_json={"answer": "old"}),
        ScoreResult(False, 0.0, "2", "old"),
    )
    artifacts = Artifacts(store.path)
    first = artifacts.records()[0]
    trace_path = artifacts.file(first, "normalized_trace")
    original_trace = trace_path.read_bytes()
    store.append(
        task,
        AgentResponse("new", response_json={"answer": "new"}),
        ScoreResult(False, 0.0, "2", "new"),
    )
    assert len(artifacts.records(latest=False)) == 2
    assert trace_path.read_bytes() == original_trace
    assert (
        json.loads(artifacts.file(first, "response_json").read_text())["answer"]
        == "old"
    )


def test_successful_verifier_replay_replaces_failed_verifier_status(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from alphadiana.benchmarks.terminal_bench2.replay import VerifierReplay

    monkeypatch.setattr(
        VerifierReplay,
        "run",
        lambda *a, **kw: (
            SimpleNamespace(reward="1", status="ok", reward_path="new/reward.txt"),
            {},
        ),
    )
    store = ResultStore(
        str(tmp_path), "tb2", run_metadata={"benchmark_name": "terminal_bench2"}
    )
    store.append(
        BenchmarkTask("task", "Solve", None),
        AgentResponse(None, metadata={"verifier_status": "missing_reward"}),
        ScoreResult(False, 0.0, "1", None),
    )
    source = Artifacts(store.path)
    original = source.path.read_bytes()
    replay = source.rescore(ad.scorer.create("terminal_bench2"), allow_environment=True)
    row = replay.records()[0]
    assert row["score"] == 1.0
    assert row["score_metadata"]["verifier_status"] == "ok"
    assert row["score_status"] == "valid_scored"
    assert source.path.read_bytes() == original


def test_explicit_opencode_model_wins_over_environment(monkeypatch):
    from alphadiana.harness.opencode.agent import OpenCodeAgent

    monkeypatch.setenv("OPENAI_MODEL_NAME", "environment-model")
    config = ad.runner().configuration(
        agent=ad.agent("opencode", model="custom/explicit-model"),
        data=ad.dataset("aime"),
        scorer=ad.scorer.create("numeric"),
    )
    native = OpenCodeAgent()
    native.setup({**config.agent_config, "execution_mode": "local"})
    assert native._api_model == "explicit-model"


@pytest.mark.parametrize("first_failed", [False, True])
def test_attempt_archives_keep_native_files_and_logprobs(tmp_path, first_failed):
    task = BenchmarkTask("task", "Return 2", "2")
    store = ResultStore(str(tmp_path), "history")
    first = AgentResponse(
        "old",
        workspace_file_contents={"/native/session.jsonl": "old session"},
        artifact_manifest={"files": {"native_session": "/native/session.jsonl"}},
        metadata={
            "logprob_records": [{"token_index": 0, "token": "old", "top_logprobs": []}]
        },
    )
    if first_failed:
        store.append_error(task, response=first, error={"error_type": "AgentError"})
    else:
        store.append(task, first, ScoreResult(False, 0.0, "2", "old"))
    artifacts = Artifacts(store.path)
    row = artifacts.records()[0]
    originals = {
        key: artifacts.file(row, key).read_bytes()
        for key in (
            "normalized_trace",
            "native_session",
            "logprobs_float",
            "logprobs_int16",
        )
    }
    derived = artifacts.rescore(ReviewScorer())
    # A reopened store must not reuse attempt names from the previous process.
    reopened = ResultStore(str(tmp_path), "history")
    reopened.append(
        task,
        AgentResponse(
            "new",
            workspace_file_contents={"/native/session.jsonl": "new session"},
            metadata={
                "logprob_records": [
                    {"token_index": 0, "token": "new", "top_logprobs": []}
                ]
            },
        ),
        ScoreResult(False, 0.0, "2", "new"),
    )
    for key, original in originals.items():
        assert artifacts.file(row, key).read_bytes() == original
        assert derived.file(derived.records()[0], key).read_bytes() == original
    assert (tmp_path / row["logprobs_path"]).read_bytes() == originals["logprobs_float"]
    trace = json.loads(originals["normalized_trace"])
    assert (
        trace["refs"]["native_session"]
        == row["artifact_manifest"]["files"]["native_session"]
    )
    assert (
        trace["refs"]["logprobs_float"]
        == row["artifact_manifest"]["files"]["logprobs_float"]
    )
    assert artifacts.records()[0]["predicted"] == "new"
    assert len(artifacts.records(latest=False)) == 2


@pytest.mark.parametrize(
    "failure_reason", ["provider_error", "control_plane_unavailable"]
)
def test_verifier_replay_does_not_hide_solver_failures(
    tmp_path, monkeypatch, failure_reason
):
    from types import SimpleNamespace

    from alphadiana.benchmarks.terminal_bench2.replay import VerifierReplay

    monkeypatch.setattr(
        VerifierReplay,
        "run",
        lambda *a, **kw: (
            SimpleNamespace(reward="1", status="ok", reward_path="new/reward.txt"),
            {},
        ),
    )
    store = ResultStore(
        str(tmp_path), "tb2", run_metadata={"benchmark_name": "terminal_bench2"}
    )
    store.append(
        BenchmarkTask("task", "Solve", None),
        AgentResponse(
            None,
            metadata={
                "verifier_status": "missing_reward",
                "failure_reason": failure_reason,
            },
        ),
        ScoreResult(False, 0.0, "1", None),
    )
    source = Artifacts(store.path)
    replay = source.rescore(ad.scorer.create("terminal_bench2"), allow_environment=True)
    assert replay.records()[0]["score_status"] != "valid_scored"
    assert replay.records()[0]["metadata"]["failure_reason"] == failure_reason


@pytest.mark.parametrize(
    "name",
    [
        "opencode",
        "terminal_bench2_opencode",
        "terminal_bench2_openclaw",
        "terminal_bench2_zeroclaw",
    ],
)
def test_explicit_provider_model_has_same_precedence_in_yaml(
    tmp_path, monkeypatch, name
):
    from alphadiana.engine.config.experiment_config import ExperimentConfig

    monkeypatch.setenv("OPENAI_MODEL_NAME", "environment-model")
    source = tmp_path / "run.yaml"
    source.write_text(
        f"agent:\n  name: {name}\n  version: fixture\n  config:\n    model: custom/explicit-model\n"
        "benchmark:\n  name: aime\nscorer:\n  name: numeric\n"
    )
    yaml_config = ExperimentConfig.from_yaml(str(source))
    python_config = ad.runner().configuration(
        agent=ad.agent(name, model="custom/explicit-model"),
        data=ad.dataset("aime"),
        scorer=ad.scorer.create("numeric"),
    )
    assert yaml_config.agent_config == python_config.agent_config
    assert python_config.agent_config["model_name"] == "explicit-model"


def test_rescoring_does_not_mutate_saved_solver_metadata(tmp_path):
    class MutatingScorer(ReviewScorer):
        def score(self, task, response):
            response.metadata["nested"]["value"] = "new verifier metadata"
            return super().score(task, response)

    store = ResultStore(str(tmp_path), "solver")
    store.append(
        BenchmarkTask("task", "Return 2", "2"),
        AgentResponse("2", metadata={"nested": {"value": "original"}}),
        ScoreResult(True, 1, "2", "2"),
    )
    source = Artifacts(store.path)
    rescored = source.rescore(MutatingScorer())
    assert source.records()[0]["metadata"]["nested"]["value"] == "original"
    assert rescored.records()[0]["metadata"]["nested"]["value"] == "original"


def test_rescoring_keeps_original_task_inputs_when_scorer_mutates_them(tmp_path):
    class MutatingTaskScorer(ReviewScorer):
        def score(self, task, response):
            task.metadata["nested"]["value"] = "scorer working state"
            task.ground_truth["expected"] = "scorer working answer"
            response.answer["value"] = "scorer working prediction"
            return super().score(task, response)

    store = ResultStore(str(tmp_path), "task-inputs")
    store.append(
        BenchmarkTask(
            "task", "Return 2", {"expected": "2"}, {"nested": {"value": "original"}}
        ),
        AgentResponse({"value": "2"}),
        ScoreResult(True, 1, "2", "2"),
    )
    source = Artifacts(store.path)
    original = source.records()[0]
    rescored = source.rescore(MutatingTaskScorer())
    derived = rescored.records()[0]
    assert derived["task_metadata"] == original["task_metadata"]
    assert derived["ground_truth"] == original["ground_truth"]
    assert derived["predicted"] == original["predicted"]
    assert source.records()[0] == original


def test_shard_merge_retains_attempt_logprob_namespace(tmp_path):
    task = BenchmarkTask("task", "Return 2", "2")
    shard = ResultStore(str(tmp_path), "shard")
    shard.append(
        task,
        AgentResponse(
            "2",
            metadata={
                "logprob_records": [
                    {"token_index": 0, "token": "2", "top_logprobs": []}
                ]
            },
        ),
        ScoreResult(True, 1, "2", "2"),
    )
    target = ResultStore(str(tmp_path), "merged")
    target.merge_result_stores(
        target_run_id="merged",
        shard_run_ids=["shard"],
        expected_tasks=[task],
        num_samples=1,
    )
    original = Artifacts(shard.path)
    merged = Artifacts(target.path)
    row = merged.records()[0]
    for key in ("normalized_trace", "logprobs_float", "logprobs_int16"):
        assert (
            merged.file(row, key).read_bytes()
            == original.file(original.records()[0], key).read_bytes()
        )
    assert (tmp_path / row["logprobs_path"]).read_bytes() == merged.file(
        row, "logprobs_float"
    ).read_bytes()


def test_explicit_provider_name_is_preserved(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_NAME", "environment-model")
    config = ad.runner().configuration(
        agent=ad.agent(
            "opencode", model="custom/cli-alias", model_name="provider-model"
        ),
        data=ad.dataset("aime"),
        scorer=ad.scorer.create("numeric"),
    )
    assert config.agent_config["model_name"] == "provider-model"
