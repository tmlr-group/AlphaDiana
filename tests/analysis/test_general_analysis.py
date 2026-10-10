"""Fresh-run statistics and optional judge contracts, with no external service."""

import json
import math
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from tests.analysis.action_target_fixtures import attach_targets, fixed_item
from alphadiana.analysis.actions.llm_annotations import ActionAnnotations
from alphadiana.analysis.measurements import ExecutionMetrics, Figure
from alphadiana.analysis.measurements import EntropyAnalysis
from alphadiana.analysis.failure_annotations import FailureAnnotations
from alphadiana.analysis.export import _render
from alphadiana.artifacts import Artifacts
from alphadiana.scorer.judge_config import JudgeConfig
from alphadiana.scorer.llm_judge import LLMJudgeScorer


def source(tmp_path, rows, files=None):
    path = tmp_path / "run.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    for name, data in (files or {}).items():
        target = tmp_path / "run" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)
    return Artifacts(path)


def record(task, correct=False, **kwargs):
    return dict(task_id=task, correct=correct, score=int(correct), **kwargs)


def judge_options(tmp_path):
    return dict(
        cache_dir=tmp_path / "cache",
        model="fixture",
        model_version="revision-1",
        base_url="http://127.0.0.1:1/v1",
        api_key="fixture-secret",
    )


def response(answer):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=json.dumps(answer)),
            )
        ],
        model="fixture",
        id="fixture",
        usage=None,
    )


def test_judge_requires_user_configuration_and_validates_url(monkeypatch):
    for name in [
        "OPENAI_MODEL",
        "OPENAI_MODEL_NAME",
        "JUDGE_MODEL",
        "OPENAI_BASE_URL",
        "JUDGE_API_BASE",
        "OPENAI_API_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(
        ValueError, match="OPENAI_MODEL.*OPENAI_BASE_URL.*OPENAI_API_KEY"
    ):
        JudgeConfig.resolve()
    monkeypatch.setenv("OPENAI_MODEL_NAME", "alias")
    monkeypatch.setenv("OPENAI_MODEL", "selected")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider.example/v1/")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    config = JudgeConfig.resolve()
    assert (
        config.model == "selected" and config.base_url == "https://provider.example/v1"
    )
    assert "secret-value" not in repr(config)
    assert JudgeConfig.resolve(model="explicit").model == "explicit"
    for endpoint in [
        "file:///tmp/provider",
        "https://user:secret@host/v1",
        "https://host/v1?key=secret",
    ]:
        with pytest.raises(ValueError):
            JudgeConfig.resolve(base_url=endpoint)
    scorer = LLMJudgeScorer()
    scorer.setup({})
    assert scorer._judge_model == "selected" and scorer._api_base == config.base_url


@pytest.mark.parametrize(
    "answer",
    [
        '{"correct":"false","rationale":"no"}',
        '{"correct":1,"rationale":"yes"}',
        "correct: true",
        "[]",
    ],
)
def test_scorer_rejects_non_boolean_judgments(answer):
    with pytest.raises((ValueError, TypeError)):
        LLMJudgeScorer._parse_judge_response(answer)


def test_entropy_event_reset_weighting_missingness_and_length(tmp_path):
    top = [{"token": "a", "logprob": -1}, {"token": "b", "logprob": -1}]

    def token(call, index, distribution=top, follows=True):
        return dict(
            call_id=call,
            call_token_index=index,
            follows_tool_result=follows,
            top_logprobs=distribution,
        )

    tokens = [
        token("one", 0),
        token("one", 1),
        token("one", 2),
        token("two", 0, [{"token": "a", "logprob": 0}]),
        token("other", 0, follows=False),
        {"top_logprobs": top},
        {},
    ]
    rows = [
        record(
            "correct",
            True,
            token_usage={"completion_tokens": 100},
            artifact_manifest={"files": {"logprobs_float": "tokens.jsonl"}},
        ),
        record(
            "incorrect", artifact_manifest={"files": {"logprobs_float": "tokens.jsonl"}}
        ),
        record("missing"),
        {"task_id": "unscored"},
    ]
    artifacts = source(
        tmp_path, rows, {"tokens.jsonl": "".join(json.dumps(t) + "\n" for t in tokens)}
    )
    measured = EntropyAnalysis(min_observations=2).measure([artifacts])
    bins = measured.tables["post_tool_bins"]
    first = next(r for r in bins if r["outcome"] == "Correct" and r["bin_left"] == 1)
    assert first["n_calls"] == 2 and first["n_tokens"] == 2
    assert first["token_position"] == pytest.approx(math.sqrt(2))
    assert first["entropy_nats"] == pytest.approx(math.log(2) / 2)
    assert first["standard_error"] == pytest.approx(math.log(2) / math.sqrt(8))
    second = next(r for r in bins if r["outcome"] == "Correct" and r["bin_left"] == 2)
    assert second["n_tokens"] == 2 and second["n_calls"] == 1
    assert second["entropy_nats"] == pytest.approx(math.log(2))
    assert {r["outcome"] for r in bins} == {"Correct", "Incorrect"}
    coverage = measured.coverage["run"]
    assert coverage["missing_sidecars"] == coverage["excluded_unscored"] == 1
    assert coverage["missing_attribution"] == coverage["missing_top_k"] == 2
    assert coverage["post_tool_tokens"] == 8 and coverage["other_request_tokens"] == 2
    (scatter,) = measured.tables["entropy_length"]
    assert (
        scatter["log10_output_tokens"] == 2 and scatter["observed_entropy_tokens"] == 6
    )
    assert (
        EntropyAnalysis(min_observations=5)
        .measure([artifacts])
        .tables["post_tool_entropy"]
        == []
    )


def test_reliability_missing_attempts_and_mean_resource_denominators(tmp_path):
    artifacts = source(
        tmp_path,
        [
            record(
                "a",
                True,
                sample_index=0,
                wall_time_sec=2,
                token_usage={"completion_tokens": 8},
            ),
            record("a", sample_index=1, wall_time_sec=4),
        ],
    )
    artifacts.store.save_manifest(
        {"num_samples": 4, "expected_task_ids": ["a", "b"], "expected_sample_count": 8}
    )
    result = ExecutionMetrics().measure([artifacts])
    (row,) = result.tables["performance"]
    assert row["pass_at_k"] == 0.5 and row["avg_at_k"] == 0.125
    assert row["mean_output_tokens"] == 8 and row["mean_seconds"] == 3
    assert {r["metric"] for r in result.tables["reliability"]} == {"Pass@4", "Avg@4"}


def test_failure_incidence_overlapping_labels_and_missing_evidence(tmp_path):
    trace = json.dumps(
        {"steps": [{"role": "assistant", "content": "tool failed; retry failed"}]}
    )
    artifacts = source(
        tmp_path,
        [
            record(
                "bad", artifact_manifest={"files": {"normalized_trace": "trace.json"}}
            ),
            record("missing"),
            record("good", True),
        ],
        {"artifacts/trace.json": trace},
    )
    with FailureAnnotations(**judge_options(tmp_path)) as labels:
        labels.client.chat.completions.create = lambda **kw: response(
            {
                "labels": [
                    {"code": "TE-2", "quote": "tool failed"},
                    {"code": "TE-5", "quote": "retry failed"},
                ]
            }
        )
        result = labels.measure([artifacts])
        rates = {r["code"]: r for r in result.tables["incidence"]}
        assert (
            rates["TE-2"]["incidence_percent"]
            == rates["TE-5"]["incidence_percent"]
            == 100
        )
        assert rates["RC-1"]["incidence_percent"] == 0
        assert (
            result.coverage["run"]["annotated"]
            == result.coverage["run"]["missing_traces"]
            == 1
        )
        assert labels.measure([artifacts]).tables == result.tables
        for answer in [
            {"labels": [{"code": "TE-2", "quote": "invented"}]},
            {"labels": [{"code": "invented", "quote": "tool failed"}]},
            {"labels": [{"code": "TE-2", "quote": "tool failed"}] * 2},
        ]:
            with pytest.raises(ValueError):
                labels._validate(answer, "tool failed")
        cache = next((tmp_path / "cache").glob("*.json")).read_text()
        assert "fixture-secret" not in cache
        labels.max_trace_chars = 1
        with pytest.raises(ValueError, match="max_trace_chars"):
            labels.measure([artifacts])


def test_failure_annotations_include_invalid_runs_with_correct_answers(tmp_path):
    artifacts = source(
        tmp_path,
        [
            record(
                "interrupted",
                True,
                metadata={"failure_reason": "provider_error"},
                artifact_manifest={"files": {"normalized_trace": "trace.json"}},
            ),
            record("completed", True),
        ],
        {
            "artifacts/trace.json": json.dumps(
                {
                    "steps": [
                        {"role": "assistant", "content": "Provider connection failed"}
                    ]
                }
            )
        },
    )
    with FailureAnnotations(**judge_options(tmp_path)) as labels:
        labels.client.chat.completions.create = lambda **kw: response(
            {"labels": [{"code": "OR-2", "quote": "Provider connection failed"}]}
        )
        result = labels.measure([artifacts])
    assert result.coverage["run"]["eligible"] == 1
    assert result.coverage["run"]["annotated"] == 1
    assert result.tables["annotations"][0]["task_id"] == "interrupted"


def test_failure_annotations_preserve_tool_and_reasoning_evidence(tmp_path):
    from alphadiana.results.store import ResultStore
    from alphadiana.benchmarks.base import BenchmarkTask
    from alphadiana.harness.base import AgentResponse
    from alphadiana.scorer.base import ScoreResult

    sources = []
    for index, command in enumerate(["python missing_a.py", "python missing_b.py"]):
        store = ResultStore(str(tmp_path), f"attempt-{index}")
        store.append(
            BenchmarkTask("task", "Run the supplied script", "done"),
            AgentResponse(
                "failed",
                trajectory=[
                    {
                        "role": "assistant",
                        "content": "I will run the script.",
                        "thinking": "The script is named main.py.",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "tool": "bash",
                                "input": {"command": command},
                            }
                        ],
                    },
                    {
                        "role": "tool_result",
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "File not found",
                        "is_error": True,
                    },
                ],
            ),
            ScoreResult(False, 0.0, "done", "failed"),
        )
        sources.append(Artifacts(store.path))

    requests = []
    with FailureAnnotations(**judge_options(tmp_path)) as labels:

        def classify(**kwargs):
            evidence = json.loads(kwargs["messages"][-1]["content"].split("\n", 1)[1])
            requests.append(evidence)
            command = ["python missing_a.py", "python missing_b.py"][len(requests) - 1]
            assert command in evidence["trace"]
            assert "The script is named main.py." in evidence["trace"]
            assert '"tool_use_id": "call-1"' in evidence["trace"]
            assert '"is_error": true' in evidence["trace"]
            return response({"labels": [{"code": "TE-2", "quote": command}]})

        labels.client.chat.completions.create = classify
        result = labels.measure(sources)
        assert (
            len(requests) == 2
        )  # Different calls must not share an annotation cache entry.
        assert (
            len({row["annotation_sha256"] for row in result.tables["annotations"]}) == 2
        )
        assert labels.measure(sources).tables == result.tables
        assert len(requests) == 2


def test_action_percentages_and_transitions_stay_within_samples(tmp_path):
    trace = json.dumps(
        {
            "steps": [
                {"role": "assistant", "content": "a"},
                {"role": "assistant", "content": "b"},
            ]
        }
    )
    artifacts = source(
        tmp_path,
        [
            record(
                "a",
                True,
                artifact_manifest={"files": {"normalized_trace": "trace.json"}},
            ),
            record(
                "b", artifact_manifest={"files": {"normalized_trace": "trace.json"}}
            ),
        ],
        {"artifacts/trace.json": trace},
    )
    attach_targets(artifacts, [fixed_item("A1", "a", 0), fixed_item("A2", "b", 1)])
    with ActionAnnotations(
        protocol="human-protocol-v2", **judge_options(tmp_path)
    ) as labels:
        labels.client.chat.completions.create = lambda **kw: response(
            {
                "actions": [
                    {
                        "action_id": json.loads(kw["messages"][1]["content"].split("\n", 1)[1])[
                            "actions"
                        ][0]["action_id"],
                        "label": "Reasoning",
                    }
                ]
            }
        )
        result = labels.measure([artifacts])
        assert sum(r["count"] for r in result.tables["transitions"]) == 2
        assert len(result.tables["outcome_transitions"]) == 2
        for outcome in ["Correct", "Incorrect"]:
            rows = [
                r
                for r in result.tables["composition_percent"]
                if r["outcome"] == outcome
            ]
            assert len(rows) == 6 and sum(r["percent"] for r in rows) == 100
        with pytest.raises(ValueError):
            labels._validate({"label": []})


def test_generic_renderer_matrix_missing_cells_and_ellipse_threshold():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    spec = asdict(
        Figure(
            "m",
            "m",
            "matrix",
            "run",
            "label",
            "Incidence",
            "Run",
            "Label",
            value="rate",
            color_limits=(0, 100),
        )
    )
    rows = [
        {"run": "a", "label": "x", "rate": 0},
        {"run": "b", "label": "y", "rate": 100},
    ]
    fig = _render(plt, spec, rows)
    matrix = fig.axes[0].images[0].get_array()
    assert matrix[0, 0] == 0 and matrix.mask[0, 1]
    plt.close(fig)
    with pytest.raises(ValueError, match="one measured value"):
        _render(plt, spec, rows + rows)
    spec = asdict(
        Figure(
            "s",
            "s",
            "scatter",
            "x",
            "y",
            "Length",
            "log10 tokens",
            "Entropy",
            ellipse=True,
        )
    )
    for n in (9, 10):
        fig = _render(plt, spec, [{"x": i, "y": i % 3} for i in range(n)])
        assert len(fig.axes[0].patches) == (1 if n == 10 else 0)
        plt.close(fig)


def test_openai_compatible_judges_and_exports(tmp_path, monkeypatch):
    """Exercise the real HTTP clients against a local protocol fixture, not an LLM."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    import alphadiana as ad
    from alphadiana.benchmarks.base import BenchmarkTask
    from alphadiana.harness.base import AgentResponse

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            prompt = body["messages"][0]["content"]
            answer = (
                {"labels": [{"code": "TE-2", "quote": "tool failed"}]}
                if "unsuccessful execution" in prompt
                else (
                    {
                        "actions": [
                            {
                                "action_id": json.loads(body["messages"][1]["content"].split("\n", 1)[1])[
                                    "actions"
                                ][0]["action_id"],
                                "label": "Reasoning",
                            }
                        ]
                    }
                    if "human-protocol alignment v2" in prompt
                    else (
                        {
                            "actions": [
                                {
                                    "source_id": b["source_id"],
                                    "start": 0,
                                    "end": b["length"],
                                    "label": (
                                        "Tool Use"
                                        if b["kind"] == "tool_call"
                                        else "Reasoning"
                                    ),
                                }
                                for b in json.loads(body["messages"][1]["content"].split("\n", 1)[1])[
                                    "sources"
                                ]
                                if b["target"]
                            ]
                        }
                        if "retrospectively divide" in prompt
                        else {"correct": False, "rationale": "Fixture judgment"}
                    )
                )
            )
            payload = json.dumps(
                {
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(answer),
                            },
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("OPENAI_MODEL", "protocol-fixture")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret")
    trace = json.dumps({"steps": [{"role": "assistant", "content": "tool failed"}]})
    tokens = [
        dict(
            call_id=str(i),
            call_token_index=0,
            follows_tool_result=True,
            top_logprobs=[{"token": "a", "logprob": 0}],
        )
        for i in range(5)
    ]
    artifacts = source(
        tmp_path,
        [
            record(
                "t",
                token_usage={"completion_tokens": 5},
                artifact_manifest={
                    "files": {
                        "normalized_trace": "trace.json",
                        "logprobs_float": "tokens.jsonl",
                    }
                },
            )
        ],
        {
            "artifacts/trace.json": trace,
            "tokens.jsonl": "".join(json.dumps(t) + "\n" for t in tokens),
        },
    )
    attach_targets(artifacts, [fixed_item("A1", "tool failed", 0)])
    try:
        scorer = LLMJudgeScorer()
        scorer.setup({})
        assert (
            scorer.score(BenchmarkTask("t", "test", "yes"), AgentResponse("no")).correct
            is False
        )
        with ActionAnnotations(cache_dir=tmp_path / "actions") as actions:
            with FailureAnnotations(cache_dir=tmp_path / "failures") as failures:
                report = ad.analyzer(
                    artifacts,
                    plugins=[ExecutionMetrics(), EntropyAnalysis(), actions, failures],
                )
                receipt = report.export(tmp_path / "export", formats=("png",))
                assert {
                    "failure_annotations-incidence.png",
                    "annotated_actions-composition_percent.png",
                    "entropy-post_tool_entropy.png",
                    "execution-reliability.png",
                    "execution-mean_tokens.png",
                } <= set(receipt["files"])
                report.compute()
        assert len(requests) == 3
        monkeypatch.setenv("ANALYSIS_MODEL", "independent-analysis-fixture")
        managed = ad.analyzer(artifacts, judge=True, cache_dir=tmp_path / "managed")
        measured = managed.compute()
        assert len(requests) == 5
        assert all(
            body["model"] == "independent-analysis-fixture"
            for _, _, body in requests[-2:]
        )
        assert measured["analyses"]["failure_annotations"]["tables"]["incidence"]
        managed.compute()
        assert len(requests) == 5  # Reuses persistent judgments across managed clients.
        assert all(
            path == "/v1/chat/completions" and auth == "Bearer fixture-secret"
            for path, auth, _ in requests
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_comparison_rejects_ambiguous_run_names(tmp_path):
    import alphadiana as ad

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    with pytest.raises(ValueError, match="run names must be unique"):
        ad.analyzer([source(first, [record("a")]), source(second, [record("b")])])


def test_analysis_environment_is_independent_and_explicit_overrides_win(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "execution")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://execution.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "execution-key")
    monkeypatch.setenv("ANALYSIS_MODEL", "analysis")
    monkeypatch.setenv("ANALYSIS_BASE_URL", "https://analysis.example/v1")
    monkeypatch.setenv("ANALYSIS_API_KEY", "analysis-key")
    judge = JudgeConfig.resolve(analysis=True)
    assert (judge.model, judge.base_url, judge.api_key) == (
        "analysis",
        "https://analysis.example/v1",
        "analysis-key",
    )
    scoring = JudgeConfig.resolve()
    assert scoring.model == "execution" and scoring.api_key == "execution-key"
    explicit = JudgeConfig.resolve(
        analysis=True,
        model="explicit",
        base_url="https://explicit.example/v1",
        api_key="explicit-key",
    )
    assert explicit.model == "explicit" and explicit.api_key == "explicit-key"
    for name in ["ANALYSIS_MODEL", "ANALYSIS_BASE_URL", "ANALYSIS_API_KEY"]:
        monkeypatch.delenv(name)
    assert JudgeConfig.resolve(analysis=True) == scoring


def test_analyzer_owns_only_its_judges_and_closes_them_on_failure(
    tmp_path, monkeypatch
):
    import alphadiana as ad
    from alphadiana.analysis.measurements import Measurement

    monkeypatch.setenv("ANALYSIS_MODEL", "analysis")
    monkeypatch.setenv("ANALYSIS_BASE_URL", "https://analysis.example/v1")
    monkeypatch.setenv("ANALYSIS_API_KEY", "analysis-key")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    clients = []

    def measure(plugin, artifacts):
        clients.append(plugin.client)
        assert plugin.model == "override"
        assert plugin.base_url == "https://analysis.example/v1"
        assert plugin.cache.is_relative_to(tmp_path / "alphadiana" / "annotations")
        return Measurement()

    monkeypatch.setattr(ActionAnnotations, "measure", measure)
    monkeypatch.setattr(FailureAnnotations, "measure", measure)
    offline = ad.analyzer([]).compute()
    assert set(offline["analyses"]) == {"execution", "request_entropy", "entropy"}
    assert not clients
    report = ad.analyzer([], model="override")
    assert {"annotated_actions", "failure_annotations"} <= set(
        report.compute()["analyses"]
    )
    assert all(client.is_closed for client in clients)

    def fail(plugin, artifacts):
        measure(plugin, artifacts)
        raise ValueError("fixture failure")

    monkeypatch.setattr(FailureAnnotations, "measure", fail)
    with pytest.raises(ValueError, match="fixture failure"):
        report.compute()
    assert all(client.is_closed for client in clients)
    with pytest.raises(ValueError, match="not both"):
        ad.analyzer([], plugins=[], judge=True)
