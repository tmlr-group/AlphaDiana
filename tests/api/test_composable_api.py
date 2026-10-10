"""Real Runner contracts with custom components; no model service required."""

import json

import pytest

import alphadiana as ad
from alphadiana.artifacts import Artifacts
from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.registry import AgentRegistry
from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import ScorerRegistry


class TinyBenchmark(Benchmark):
    def load_tasks(self, config):
        return [BenchmarkTask(f"task-{i}", "Return two", "2") for i in range(3)]


class TinyAgent(Agent):
    closed = 0

    def setup(self, config):
        self.answer = config.get("answer", "2")

    def solve(self, task, sandbox=None):
        return AgentResponse(
            self.answer,
            trajectory=[{"type": "answer", "content": self.answer}],
            token_usage={"completion_tokens": 4},
            wall_time_sec=0.1,
            token_entropy_stats={"mean": 0.5, "n_tokens": 4},
        )

    def teardown(self):
        type(self).closed += 1


class TinyScorer(Scorer):
    name = "test_score"

    def score(self, task, response):
        correct = response.answer == task.ground_truth
        return ScoreResult(correct, float(correct), task.ground_truth, response.answer)


@pytest.fixture
def custom(monkeypatch):
    monkeypatch.setitem(BenchmarkRegistry._registry, "test_tasks", TinyBenchmark)
    monkeypatch.setitem(AgentRegistry._registry, "test_agent", TinyAgent)
    monkeypatch.setitem(ScorerRegistry._registry, "test_score", TinyScorer)


def execute(tmp_path, **options):
    return ad.runner(output_dir=str(tmp_path), run_id="custom", **options).compute(
        agent=ad.agent("test_agent", version="fixture-1"),
        data=ad.dataset("test_tasks"),
        scorer=ad.scorer.create("test_score"),
    )


def test_custom_components_use_runner_and_rescore(custom, tmp_path):
    artifacts = execute(tmp_path)
    original = artifacts.path.read_bytes()
    assert len(artifacts.records()) == 3
    assert all(r["score_status"] == "valid_scored" for r in artifacts.records())
    assert TinyAgent.closed > 0
    rescored = artifacts.rescore(ad.scorer.create("test_score"))
    assert all(r["correct"] for r in rescored.records())
    assert artifacts.path.read_bytes() == original
    assert rescored.records()[0]["rescoring"]["source_sha256"]
    with pytest.raises(FileExistsError):
        artifacts.rescore(ad.scorer.create("test_score"), output=artifacts.path)


def test_analyzer_exports_real_tables_and_vectors(custom, tmp_path):
    artifacts = execute(tmp_path)
    analyzer = ad.analyzer(artifacts)
    result = analyzer.compute()
    performance = result["analyses"]["execution"]["tables"]["performance"][0]
    assert performance["accuracy"] == 1
    assert performance["tokens_per_correct"] == 4
    receipt = analyzer.export(tmp_path / "figures")
    assert "execution-accuracy.pdf" in receipt["files"]
    assert (
        (tmp_path / "figures/execution-accuracy.pdf").read_bytes().startswith(b"%PDF")
    )
    assert (tmp_path / "figures/execution-accuracy.data.json").exists()


def test_latest_failure_not_hidden_and_all_attempts_available(custom, tmp_path):
    artifacts = execute(tmp_path)
    latest = artifacts.records()[0]
    latest.update(
        score=None,
        correct=None,
        error={"error_type": "RuntimeError", "message": "failed"},
    )
    with artifacts.path.open("a") as stream:
        stream.write(json.dumps(latest) + "\n")
    assert len(artifacts.records(latest=False)) == 4
    assert len(artifacts.records()) == 3
    assert artifacts.records()[0]["score_status"] != "valid_scored"


def test_malformed_artifacts_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"task_id":')
    with pytest.raises(ValueError, match="Invalid artifact record"):
        Artifacts(path).records()


def test_ambiguous_environment_and_source_rejected():
    with pytest.raises(TypeError, match="concrete backend"):
        ad.runner(env="sandbox")
    with pytest.raises(ValueError):
        ad.dataset("a/b", benchmark="custom", dataset="other")


def test_configuration_is_detached_from_factory_inputs():
    config = {"nested": [1]}
    spec = ad.agent("plugin", parameters=config)
    config["nested"].append(2)
    recipe = ad.runner()
    built = recipe.configuration(
        agent=spec, data=ad.dataset("plugin"), scorer=ad.scorer.create("plugin")
    )
    assert built.agent_config["parameters"]["nested"] == [1]
    built.agent_config["parameters"]["nested"].append(3)
    assert spec.config["parameters"]["nested"] == [1]


def test_legacy_scorer_imports_remain_modules():
    from types import ModuleType

    from alphadiana.scorer import numeric

    assert isinstance(numeric, ModuleType)
    assert callable(ad.scorer.create)


def test_python_and_yaml_configuration_parity(tmp_path):
    from dataclasses import asdict

    import yaml

    from alphadiana.engine.config.experiment_config import ExperimentConfig

    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "run_id": "parity",
                "output_dir": str(tmp_path),
                "agent": {"name": "plugin", "version": "v1", "config": {"custom": 2}},
                "benchmark": {"name": "plugin", "config": {}},
                "scorer": {"name": "plugin"},
            }
        )
    )
    cli = ExperimentConfig.from_yaml(str(path))
    api = ad.runner(run_id="parity", output_dir=str(tmp_path)).configuration(
        agent=ad.agent("plugin", version="v1", custom=2),
        data=ad.dataset("plugin"),
        scorer=ad.scorer.create("plugin"),
    )
    assert asdict(cli) == asdict(api)


def test_custom_analysis_plugin_has_no_core_routing(custom, tmp_path):
    from alphadiana.analysis.measurements import Figure, Measurement

    class Plugin:
        name = "custom_measurement"

        def measure(self, artifacts):
            return Measurement(
                {
                    "counts": [
                        {"source": "custom", "count": len(artifacts[0].records())}
                    ]
                },
                [
                    Figure(
                        "counts",
                        "counts",
                        "bar",
                        "source",
                        "count",
                        "Custom count",
                        "Source",
                        "Records",
                    )
                ],
            )

    artifacts = execute(tmp_path)
    report = ad.analyzer(artifacts, plugins=[Plugin()])
    assert (
        report.compute()["analyses"]["custom_measurement"]["tables"]["counts"][0][
            "count"
        ]
        == 3
    )
    assert report.export(tmp_path / "custom-figures", formats=("svg",))["files"] == [
        "custom_measurement-counts.svg"
    ]


def test_empty_execution_does_not_claim_success(custom, tmp_path, monkeypatch):
    monkeypatch.setattr(TinyBenchmark, "load_tasks", lambda self, config: [])
    with pytest.raises((RuntimeError, FileNotFoundError)):
        execute(tmp_path)


def test_environment_rescore_requires_opt_in(custom, tmp_path):
    class EnvScorer(TinyScorer):
        requires_environment = True

    artifacts = execute(tmp_path)
    with pytest.raises(ValueError, match="allow_environment"):
        artifacts.rescore(EnvScorer())


def test_tb2_missing_snapshot_fails_before_container_start(custom, tmp_path):
    from alphadiana.benchmarks.terminal_bench2.scorer import TerminalBench2Scorer

    artifacts = execute(tmp_path)
    with pytest.raises(ValueError, match="snapshot"):
        artifacts.rescore(TerminalBench2Scorer(), allow_environment=True)


def test_rescore_retains_binary_inputs_outside_source_directory(
    custom, tmp_path, monkeypatch
):
    from alphadiana.benchmarks.base import BenchmarkTask

    monkeypatch.setattr(
        TinyBenchmark,
        "load_tasks",
        lambda self, config: [
            BenchmarkTask(
                "binary",
                "image task",
                "2",
                attachments={"image.png": b"binary-image-input"},
            )
        ],
    )
    artifacts = execute(tmp_path / "source")

    class AttachmentScorer(TinyScorer):
        def score(self, task, response):
            assert task.attachments["image.png"] == b"binary-image-input"
            return super().score(task, response)

    derived = artifacts.rescore(
        AttachmentScorer(), output=tmp_path / "other/rescored.jsonl"
    )
    assert derived.task(derived.records()[0]).attachments == {
        "image.png": b"binary-image-input"
    }
    assert derived.manifest()["expected_sample_count"] == 1
    original = artifacts.records()[0]
    reference = original["task_attachments"]["image.png"]
    (artifacts.artifact_root(original) / reference["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        artifacts.rescore(AttachmentScorer())


def test_repeated_rescoring_preserves_solver_output(custom, tmp_path):
    class LabelScorer(TinyScorer):
        def score(self, task, response):
            assert response.answer == "2"
            return ScoreResult(True, 1.0, "2", "resolved")

    artifacts = execute(tmp_path)
    first = artifacts.rescore(LabelScorer())
    second = first.rescore(LabelScorer())
    assert all(row["predicted"] == "2" for row in second.records())
    assert second.records()[0]["rescoring"]["scorer_predicted"] == "resolved"


def test_failed_attempt_retains_binary_inputs_for_reopening(tmp_path):
    from alphadiana.results.store import ResultStore

    store = ResultStore(str(tmp_path), "failed-binary")
    task = BenchmarkTask(
        "image-task", "Read image", "2", attachments={"image.png": b"input"}
    )
    store.append_error(task, error={"error_type": "RuntimeError", "error": "failed"})
    artifact = Artifacts(store.path)
    record = artifact.records()[0]
    assert record["score_status"] != "valid_scored"
    assert artifact.task(record).attachments == task.attachments
    reference = record["task_attachments"]["image.png"]
    (artifact.artifact_root(record) / reference["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="attachment changed"):
        store.append_error(task, error={"error": "still failed"})


def test_python_agent_env_defaults_match_yaml_and_preserve_gateways(
    tmp_path, monkeypatch
):
    from alphadiana.engine.config.experiment_config import ExperimentConfig
    from alphadiana.engine.config.validator import ConfigValidator

    monkeypatch.setenv("OPENAI_MODEL_NAME", "execution-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://execution.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "execution-key")
    agent = ad.agent("direct_llm", version="1.0")
    runner = ad.runner()
    config = runner.configuration(
        agent=agent, data=ad.dataset("aime"), scorer=ad.scorer.create("numeric")
    )
    assert not ConfigValidator().validate(config)
    path = tmp_path / "run.yaml"
    path.write_text(
        "agent:\n  name: direct_llm\n  version: '1.0'\nbenchmark:\n  name: aime\nscorer:\n  name: numeric\n"
    )
    assert config.agent_config == ExperimentConfig.from_yaml(str(path)).agent_config
    assert agent.config == {}  # Resolve a copy, never mutate a reusable specification.
    explicit = ad.agent(
        "direct_llm",
        version="1.0",
        model="override",
        api_base="https://explicit.example/v1",
    )
    result = runner.configuration(
        agent=explicit, data=ad.dataset("aime"), scorer=ad.scorer.create("numeric")
    )
    assert result.agent_config["model"] == "override"
    assert result.agent_config["api_base"] == "https://explicit.example/v1"
    native = ad.agent("openclaw", version="2026.3.2", runtime_backend="podman")
    result = runner.configuration(
        agent=native, data=ad.dataset("aime"), scorer=ad.scorer.create("numeric")
    )
    assert not result.agent_config.get(
        "api_base"
    )  # Provider URL is not a deployed gateway.
