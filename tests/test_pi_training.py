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
from alphadiana.harness._internal.training import (
    Backend,
    ModelIdentity,
    NativeCapture,
    NativeTrainingCollector,
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
        "policy_identity": {
            "policy_publication": "published-1",
            "weights_digest": DIGEST,
        },
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


def test_capture_accepts_pi_store_flag_without_forwarding_capture_state():
    current = plan()
    capture = NativeCapture(current, "task-a", 0, "fixture-v1", 1)
    prepared = capture.prepare({**request(), "store": False})
    assert "store" not in prepared
    assert prepared["return_token_ids"] is True


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
    restored = TrainingBatch.from_dict(batch.diana_dict())
    assert restored == batch
    emitted = batch.to_dict()
    assert emitted["schema"] == "alphahebe.training-batch.v2"
    assert set(emitted) == {"schema", "plan", "samples"}
    assert set(emitted["plan"]) == {
        "round_id",
        "task_ids",
        "group_size",
        "max_new_tokens",
        "temperature",
        "seed",
        "policy",
    }
    assert set(emitted["samples"][0]) == {
        "round_id",
        "task_id",
        "sample_index",
        "policy",
        "turns",
        "reward",
        "success",
    }
    TrainingBatch.from_dict(emitted).validate_for("grpo")
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
    assert evaluation["score_status"] == "valid_scored"
    assert evaluation["predicted"] == "5"
    assert evaluation["correct"] is True
    request_value = json.loads((root / "pi-training-episode-request.json").read_text())
    assert request_value["schema"] == "alphadiana.native-training-request.v1"
    assert request_value["model_identity"]["model_revision"]
    assert request_value["chat_template_kwargs"] == {"enable_thinking": False}
    result_value = json.loads((root / "pi-training-episode-result.json").read_text())
    assert result_value["schema"] == "alphadiana.native-training.v1"
    assert result_value["tokens_native"] is True
    assert result_value["trajectory_complete"] is True
    failure = json.loads((root / "pi-rollout-failure-cancellation.json").read_text())
    assert failure["score_status"] == "runtime_error"
    assert failure["predicted"] is None
    assert failure["correct"] is None
    assert failure["error"]["error_type"] == "podman_error"
    batch = json.loads((root / "pi-grpo-group.json").read_text())
    assert batch["schema"] == "alphahebe.training-batch.v2"
    assert set(batch["plan"]) == {
        "round_id",
        "task_ids",
        "group_size",
        "max_new_tokens",
        "temperature",
        "seed",
        "policy",
    }
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

    collector = NativeTrainingCollector(FakeAgent)
    with pytest.raises(TrainingCollectionError, match="sample task-a/1 failed"):
        collector.collect(
            [BenchmarkTask("task-a", "public", None)],
            current,
            {
                "image": "sha256:" + DIGEST,
                "model": "fixture-v1",
                "api_base": "http://unused",
            },
            score=lambda _task, _response: 0.5,
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
            assert "X-AlphaDiana-Policy-Publication" not in self.headers
            assert "X-AlphaDiana-Model-Identity" not in self.headers
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


def test_training_config_rejects_unsupported_keys_and_forwarded_provider_fields():
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
    }
    with pytest.raises(ValueError, match="unsupported training settings"):
        capture_config_from_dict(
            {**value, "silently_dropped": True},
            task_id="task-a",
            sample_index=0,
            model_name="fixture-v1",
            attempt_id="attempt",
        )
    capture = NativeCapture(current, "task-a", 0, "fixture-v1", 1)
    for key, payload in (
        ("reasoning_effort", {"reasoning_effort": "high"}),
        ("response_format", {"response_format": {"type": "json_object"}}),
        ("stop", {"stop": ["end"]}),
    ):
        with pytest.raises(ValueError, match=key):
            capture.prepare({**request(), **payload})


def _tool_step() -> dict:
    value = completion()
    value["choices"][0]["message"] = {
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
    value["choices"][0]["finish_reason"] = "tool_calls"
    return value


def test_policy_attestation_requires_matching_weights_digest_on_every_call():
    current = plan()
    capture = NativeCapture(
        current,
        "task-a",
        0,
        "fixture-v1",
        2,
        require_policy_attestation=True,
    )
    first = completion()
    first["policy_identity"]["weights_digest"] = "b" * 64
    with pytest.raises(ValueError, match="weights digest"):
        capture.record(capture.prepare(request()), first)
    assert capture.failed is False
    assert capture.calls == []

    # A single attested last call must not certify an earlier unattested one;
    # every model call needs the matching serving publication and weights.
    tool = _tool_step()
    tool["policy_identity"]["weights_digest"] = "b" * 64
    partial = NativeCapture(
        current,
        "task-a",
        0,
        "fixture-v1",
        2,
        require_policy_attestation=True,
    )
    with pytest.raises(ValueError, match="weights digest"):
        partial.record(partial.prepare(request()), tool)
    assert partial.calls == []

    final = completion()
    final["policy_identity"]["weights_digest"] = "b" * 64
    drift = NativeCapture(
        current,
        "task-a",
        0,
        "fixture-v1",
        2,
        require_policy_attestation=True,
    )
    drift.prepare(request())
    drift.record(drift.prepare(request()), _tool_step())
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
                            "arguments": '{"command":"pwd"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "output"},
        ],
    }
    before = list(drift.calls)
    with pytest.raises(ValueError, match="weights digest"):
        drift.record(drift.prepare(followup), final)
    assert list(drift.calls) == before
    assert drift._attested_calls == len(drift.calls)


def test_prepare_rejects_chat_template_override_off_plan():
    current = plan()
    capture = NativeCapture(current, "task-a", 0, "fixture-v1", 1)
    with pytest.raises(ValueError, match="chat_template_kwargs"):
        capture.prepare(
            {**request(), "chat_template_kwargs": {"enable_thinking": True}}
        )
    prepared = capture.prepare(
        {**request(), "chat_template_kwargs": {"enable_thinking": False}}
    )
    assert prepared["chat_template_kwargs"] == {"enable_thinking": False}


def test_collector_rejects_a_missing_scorer():
    current = plan()
    collector = NativeTrainingCollector(lambda: None)
    with pytest.raises(TypeError, match="score"):
        collector.collect(
            [BenchmarkTask("task-a", "public", None)],
            current,
            {
                "image": "sha256:" + DIGEST,
                "model": "fixture-v1",
                "api_base": "http://unused",
            },
        )


def test_collector_merges_base_training_settings_and_emits_hebe_batch():
    current = plan()
    collected = {}

    class FakeAgent:
        def setup(self, config):
            collected["config"] = config

        def solve(self, task):
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
            pass

    collector = NativeTrainingCollector(FakeAgent)
    batch = collector.collect(
        [BenchmarkTask("task-a", "public", None)],
        current,
        {
            "image": "sha256:" + DIGEST,
            "model": "fixture-v1",
            "api_base": "http://unused",
            "training": {
                "max_calls": 7,
                "proxy_host": "capture-proxy.internal",
                "enabled": False,
                "sample_index": 0,
            },
        },
        score=lambda _task, _response: 0.5,
    )
    training = collected["config"]["training"]
    assert training["max_calls"] == 7
    assert training["proxy_host"] == "capture-proxy.internal"
    assert training["enabled"] is True
    assert training["task_id"] == "task-a"
    emitted = batch.to_dict()
    assert emitted["schema"] == "alphahebe.training-batch.v2"
    assert "sampling_plan_details" not in emitted["samples"][0]
    assert "attempt_id" not in emitted["samples"][0]


def test_legacy_diana_batch_schema_is_still_readable():
    current = plan()
    turn = TokenTurn((1,), (2,), (1,), (-0.1,))
    batch = TrainingBatch(
        current,
        (
            TrainingSample(
                current.round_id,
                "task-a",
                0,
                current.policy,
                (turn,),
                0.5,
                True,
                attempt_id="attempt",
            ),
        ),
    )
    native = batch.diana_dict()
    assert native["schema"] == "alphadiana.native-training-batch.v1"
    restored = TrainingBatch.from_dict(native)
    assert restored == batch


def test_reasoning_and_training_cannot_be_enabled_together():
    from alphadiana.harness._internal.podman_agent import validate_config

    base = {
        "image": "sha256:" + DIGEST,
        "model": "fixture-v1",
        "api_base": "http://unused",
        "training": {
            "enabled": True,
            "algorithm": "sft",
            "round_id": "round-1",
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
            "policy": {
                "weights_digest": DIGEST,
                "tokenizer_digest": DIGEST,
                "chat_template_digest": DIGEST,
            },
        },
    }
    with pytest.raises(ValueError, match="cannot be enabled together"):
        validate_config({**base, "reasoning": "concise"})
    validate_config(base)
