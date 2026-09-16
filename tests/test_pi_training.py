"""Contracts for native Pi SFT/GRPO capture."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.pi.training import (
    Backend,
    ModelIdentity,
    NativeCapture,
    PiTrainingCollector,
    PolicyIdentity,
    SamplingPlan,
    TokenCaptureEndpoint,
    TokenTurn,
    TrainingBatch,
    TrainingCollectionError,
    TrainingSample,
    TrainingCapture,
    capture_config_from_dict,
)


DIGEST = "a" * 64


def plan(*, algorithm: str = "sft", group_size: int = 1) -> SamplingPlan:
    policy = PolicyIdentity(DIGEST, DIGEST, DIGEST)
    model = ModelIdentity("fixture-v1", "revision-1", DIGEST, DIGEST)
    return SamplingPlan(
        "round-1",
        policy,
        ("task-a",),
        group_size,
        3,
        1.0,
        17,
        model,
        "published-1",
        algorithm,
    )


def completion(*, finish: str = "stop", tokens=(11, 12), logs=(-0.1, -0.2)) -> dict:
    return {
        "id": "fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "fixture-v1",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "answer"},
                "finish_reason": finish,
                "token_ids": list(tokens),
                "native_log_probs": list(logs) if logs is not None else None,
            }
        ],
        "prompt_token_ids": [1, 2, 3],
        "usage": {
            "prompt_tokens": 3,
            "completion_tokens": len(tokens),
            "total_tokens": 3 + len(tokens),
        },
        "policy_publication": "published-1",
        "model_identity": {
            "model_name": "fixture-v1",
            "model_revision": "revision-1",
            "tokenizer_digest": DIGEST,
            "chat_template_digest": DIGEST,
        },
    }


def request() -> dict:
    return {"model": "fixture-v1", "messages": [{"role": "user", "content": "task"}]}


def test_capture_forces_plan_and_keeps_native_ids_and_attestation():
    current = plan()
    capture = NativeCapture(
        current,
        "task-a",
        0,
        "fixture-v1",
        2,
        attempt_id="attempt-1",
        model_identity=current.model_identity,
        require_policy_attestation=True,
    )
    prepared = capture.prepare(request())
    assert prepared["return_token_ids"] is True
    assert prepared["stream"] is False
    assert prepared["max_tokens"] == 3
    assert prepared["seed"] != current.seed
    capture.record(prepared, completion())
    record = capture.capture().to_dict()
    assert record["tokens_native"] is True
    assert record["policy_attestation"] == "verified"
    assert (
        record["evidence_refs"]["response_json"]
        == "response_json.native_training_capture"
    )
    assert "algorithm" not in record["sampling_plan"]
    assert record["sampling_plan_details"]["algorithm"] == "sft"
    assert record["turns"][0]["prompt_token_ids"] == (1, 2, 3)
    assert record["turns"][0]["response_token_ids"] == (11, 12)


@pytest.mark.parametrize(
    "response, message",
    [
        (completion(tokens=[-1]), "token"),
        (completion(tokens=[11], logs=[-0.1]), "token count"),
        (completion(finish="length", tokens=[11, 12], logs=[-0.1, -0.2]), "length"),
    ],
)
def test_capture_rejects_non_native_or_incomplete_records(response, message):
    current = plan()
    capture = NativeCapture(current, "task-a", 0, "fixture-v1", 1)
    prepared = capture.prepare(request())
    with pytest.raises(ValueError, match=message):
        if response["choices"][0]["token_ids"] == [11]:
            response["usage"]["completion_tokens"] = 2
        capture.record(prepared, response)
    assert capture.turns == []


def test_grpo_requires_behavior_logprobs_and_serving_policy_attestation():
    current = plan(algorithm="grpo", group_size=2)
    capture = NativeCapture(
        current,
        "task-a",
        0,
        "fixture-v1",
        1,
        require_logprobs=True,
        require_policy_attestation=True,
    )
    prepared = capture.prepare(request())
    missing_logs = completion(logs=None)
    with pytest.raises(ValueError, match="log probabilities"):
        capture.record(prepared, missing_logs)

    mismatch = completion()
    mismatch["policy_publication"] = "old-policy"
    with pytest.raises(ValueError, match="policy publication"):
        capture.record(prepared, mismatch)


def test_training_capture_requires_serving_identity_echo():
    current = plan()
    capture = NativeCapture(current, "task-a", 0, "fixture-v1", 1)
    prepared = capture.prepare(request())
    response = completion()
    response.pop("model_identity")
    with pytest.raises(ValueError, match="model/tokenizer/chat-template"):
        capture.record(prepared, response)


def test_tool_call_context_must_preserve_actual_episode():
    current = plan()
    capture = NativeCapture(current, "task-a", 0, "fixture-v1", 2)
    first = capture.prepare(request())
    response = completion()
    response["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
            }
        ],
    }
    response["choices"][0]["finish_reason"] = "tool_calls"
    capture.record(first, response)
    followup = {
        "model": "fixture-v1",
        "messages": [
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command":"rm -rf /"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "output"},
        ],
    }
    with pytest.raises(ValueError, match="changed the generated tool calls"):
        capture.prepare(followup)


def test_endpoint_is_bearer_protected_and_projects_sse_without_retokenizing():
    current = plan()
    capture = NativeCapture(current, "task-a", 0, "fixture-v1", 1)
    endpoint = TokenCaptureEndpoint(
        Backend("http://unused"),
        capture,
        3,
        complete=lambda _payload: completion(),
    )
    endpoint.start()
    try:
        body = json.dumps({**request(), "stream": True}).encode()
        unauthorized = Request(
            endpoint.base_url + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(Exception):
            urlopen(unauthorized)
        authorized = Request(
            endpoint.base_url + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + endpoint.key,
            },
        )
        response = urlopen(authorized).read().decode()
        assert "data: [DONE]" in response
        assert "answer" in response
        assert capture.capture().turns[0].response_token_ids == (11, 12)
    finally:
        endpoint.close()


def test_batch_round_trip_and_grpo_completeness():
    current = plan(algorithm="grpo", group_size=2)
    turn = TokenTurn((1,), (2,), (1,), (-0.1,))
    rows = tuple(
        TrainingSample(current.round_id, "task-a", index, current.policy, (turn,), 0.5)
        for index in range(2)
    )
    batch = TrainingBatch(current, rows)
    batch.validate_for("grpo")
    restored = TrainingBatch.from_dict(batch.to_dict())
    assert restored == batch
    with pytest.raises(ValueError, match="incomplete"):
        TrainingBatch(current, rows[:1])


def test_capture_config_requires_identity_and_binds_dispatch():
    current = plan()
    value = {
        "enabled": True,
        "sampling_plan": {
            "round_id": current.round_id,
            "policy": {
                "weights_digest": DIGEST,
                "tokenizer_digest": DIGEST,
                "chat_template_digest": DIGEST,
            },
            "task_ids": ["task-a"],
            "group_size": 1,
            "max_new_tokens": 3,
            "temperature": 1.0,
            "seed": 17,
            "model_identity": {
                "model_name": "fixture-v1",
                "model_revision": "revision-1",
                "tokenizer_digest": DIGEST,
                "chat_template_digest": DIGEST,
            },
        },
        "policy_publication": "published-1",
        "sample_index": 0,
    }
    config = capture_config_from_dict(
        value,
        task_id="task-a",
        sample_index=0,
        model_name="fixture-v1",
        attempt_id="attempt",
    )
    assert config is not None and config.plan.model_identity == current.model_identity
    with pytest.raises(ValueError, match="dispatch"):
        capture_config_from_dict(
            value,
            task_id="task-a",
            sample_index=1,
            model_name="fixture-v1",
            attempt_id="attempt",
        )


def test_checked_in_versioned_contract_examples_are_parseable():
    root = Path(__file__).parents[1] / "docs/harnesses/examples"
    evaluation = json.loads((root / "pi-rollout-evaluation.json").read_text())
    assert evaluation["schema"] == "alphadiana.rollout-result.v1"
    assert evaluation["status"] == "complete"
    request_value = json.loads((root / "pi-training-episode-request.json").read_text())
    assert request_value["schema"] == "alphadiana.pi-training-request.v1"
    assert request_value["model_identity"]["model_revision"]
    result_value = json.loads((root / "pi-training-episode-result.json").read_text())
    assert result_value["schema"] == "alphadiana.pi-training.v1"
    assert result_value["tokens_native"] is True
    assert result_value["trajectory_complete"] is True
    failure = json.loads((root / "pi-rollout-failure-cancellation.json").read_text())
    assert failure["schema"] == "alphadiana.rollout-result.v1"
    assert failure["status"] == "error"
    assert failure["training_eligible"] is False
    batch = json.loads((root / "pi-grpo-group.json").read_text())
    restored = TrainingBatch.from_dict(batch)
    restored.validate_for("grpo")
    assert len(restored.samples) == 2
    capture = TrainingCapture.from_dict(
        json.loads((root / "pi-training-episode-result.json").read_text())
    )
    assert capture.model_identity is not None
    assert capture.termination_reason == "eos"


def test_collector_invalidates_partial_group_and_preserves_sample_isolation():
    current = plan(algorithm="grpo", group_size=2)
    calls = []

    class FakeAgent:
        def setup(self, config):
            calls.append(("setup", config["training"]["sample_index"]))

        def solve(self, task):
            calls.append(("solve", task.metadata["sample_index"]))
            if task.metadata["sample_index"] == 1:
                raise RuntimeError("provider failure")
            capture = NativeCapture(
                current,
                "task-a",
                0,
                "fixture-v1",
                1,
                attempt_id="attempt",
            )
            prepared = capture.prepare(request())
            capture.record(prepared, completion())
            return SimpleNamespace(
                metadata={"training_capture": capture.capture().to_dict()}
            )

        def teardown(self):
            calls.append(("teardown",))

    collector = PiTrainingCollector(FakeAgent)
    with pytest.raises(TrainingCollectionError, match="sample task-a/1 failed"):
        collector.collect(
            [BenchmarkTask("task-a", "public", None)],
            current,
            {
                "image": "sha256:" + DIGEST,
                "model": "fixture-v1",
                "api_base": "http://unused",
            },
        )
    assert calls == [
        ("setup", 0),
        ("solve", 0),
        ("teardown",),
        ("setup", 1),
        ("solve", 1),
        ("teardown",),
    ]


def test_native_pi_agent_routes_real_container_requests_through_capture_proxy(
    monkeypatch,
):
    """The host proxy sees the request emitted by the disposable runtime."""

    from alphadiana.harness._internal import podman_agent as native
    from alphadiana.harness.pi.agent import PiAgent

    requests = []
    current = plan()
    response = completion()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.headers["X-AlphaDiana-Policy-Publication"] == "published-1"
            assert (
                json.loads(self.headers["X-AlphaDiana-Model-Identity"])["model_name"]
                == "fixture-v1"
            )
            assert self.headers["Authorization"] == "Bearer backend-secret"
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    image = "sha256:" + DIGEST
    calls = []

    class Runtime:
        def rootless(self):
            return True

        def image_exists(self, value):
            return value == image

        def create(self, _image, **kwargs):
            calls.append(kwargs)

        def start_attached(self, _name, **kwargs):
            assert "backend-secret" not in kwargs["input_text"]
            request_value = json.loads(kwargs["input_text"])
            endpoint = request_value["config"]["api_base"]
            payload = json.dumps(
                {
                    "model": "fixture-v1",
                    "messages": [{"role": "user", "content": "task"}],
                    "stream": False,
                }
            ).encode()
            proxy_request = Request(
                endpoint + "/chat/completions",
                data=payload,
                headers={
                    "Authorization": "Bearer " + request_value["config"]["api_key"],
                    "Content-Type": "application/json",
                },
            )
            result = urlopen(proxy_request).read()
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "answer": "answer",
                        "finish_reason": "stop",
                        "response_json": {"proxy": result.decode()},
                    }
                )
            )

        def stop(self, *args, **kwargs):
            pass

        def rm(self, *args, **kwargs):
            pass

    monkeypatch.setattr(native, "PodmanCLI", Runtime)
    agent = PiAgent()
    config = {
        "image": image,
        "model": "fixture-v1",
        "api_base": f"http://127.0.0.1:{server.server_port}/v1",
        "api_key": "backend-secret",
        "training": {
            "enabled": True,
            "algorithm": "sft",
            "round_id": current.round_id,
            "task_ids": ["task-a"],
            "group_size": 1,
            "max_new_tokens": 3,
            "temperature": 1.0,
            "seed": 17,
            "policy_publication": current.policy_publication,
            "policy": {
                "weights_digest": DIGEST,
                "tokenizer_digest": DIGEST,
                "chat_template_digest": DIGEST,
            },
            "model_identity": {
                "model_name": "fixture-v1",
                "model_revision": "revision-1",
                "tokenizer_digest": DIGEST,
                "chat_template_digest": DIGEST,
            },
            "proxy_host": "127.0.0.1",
        },
    }
    try:
        agent.setup(config)
        result = agent.solve(
            BenchmarkTask(
                "task-a",
                "task",
                None,
                metadata={"sample_index": 0, "execution_id": "b" * 32},
            )
        )
        assert result.answer == "answer"
        assert result.metadata["training_capture"]["tokens_native"] is True
        assert (
            result.metadata["hebe_training"]["sampling_plan"]["round_id"] == "round-1"
        )
        assert result.metadata["training_capture"]["turns"][0][
            "response_token_ids"
        ] == (11, 12)
        assert requests[0]["return_token_ids"] is True
        assert requests[0]["max_tokens"] == 3
    finally:
        agent.teardown()
        server.shutdown()
        server.server_close()
        thread.join()
