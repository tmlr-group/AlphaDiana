"""Native-token training capture for the Pi harness.

The Pi process remains an ordinary CLI client.  When training capture is
enabled, the host starts a short-lived OpenAI-compatible proxy in front of the
configured serving endpoint.  The proxy records the IDs and likelihoods that
the serving endpoint returned, then sends a text/stream projection to Pi.  No
training record is reconstructed by tokenizing the final answer.

Hebe owns the sampling policy and optimizer.  This module owns only the
execution contract and the validation of one isolated Pi episode or one
complete GRPO group.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import secrets
import threading
from dataclasses import asdict, dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable, Mapping

try:
    import httpx
except ImportError:  # pragma: no cover - exercised by a minimal core install
    httpx = None  # type: ignore[assignment]


SCHEMA = "alphadiana.pi-training.v1"
BATCH_SCHEMA = "alphadiana.pi-training-batch.v1"
REQUEST_SCHEMA = "alphadiana.pi-training-request.v1"
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _integer(value: object, minimum: int, name: str) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA256 digest")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _legacy_sampling_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project the shared Hebe sampling fields for cross-repository checks."""

    return {
        key: copy.deepcopy(value[key])
        for key in (
            "round_id",
            "policy",
            "task_ids",
            "group_size",
            "max_new_tokens",
            "temperature",
            "seed",
        )
        if key in value
    }


def _first(value: Mapping[str, Any], *names: str) -> object:
    """Return the first present alias without coercing malformed values."""

    for name in names:
        if name in value:
            return value[name]
    return ""


@dataclass(frozen=True, slots=True)
class PolicyIdentity:
    """Immutable policy component identities supplied by the publisher."""

    weights_digest: str
    tokenizer_digest: str
    chat_template_digest: str

    def __post_init__(self) -> None:
        for name in ("weights_digest", "tokenizer_digest", "chat_template_digest"):
            _digest(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Serving identity observed at the model boundary.

    The serving endpoint may use a mutable display name, so the tokenizer and
    chat-template digests are retained alongside the model name/revision.  A
    publisher must supply these values in the training request; the proxy also
    checks optional identity fields echoed by the endpoint.
    """

    model_name: str
    model_revision: str
    tokenizer_digest: str
    chat_template_digest: str

    def __post_init__(self) -> None:
        _nonempty(self.model_name, "model_name")
        _nonempty(self.model_revision, "model_revision")
        _digest(self.tokenizer_digest, "tokenizer_digest")
        _digest(self.chat_template_digest, "chat_template_digest")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelIdentity":
        if not isinstance(value, Mapping):
            raise TypeError("model_identity must be an object")
        # Accept the short names used by serving manifests while emitting one
        # canonical wire representation.
        return cls(
            _first(value, "model_name", "name", "model"),
            _first(
                value,
                "model_revision",
                "revision",
                "version",
                "model_version",
                "model_id",
            ),
            _first(value, "tokenizer_digest", "tokenizer", "tokenizer_id"),
            _first(
                value,
                "chat_template_digest",
                "chat_template",
                "chat_template_id",
            ),
        )


@dataclass(frozen=True, slots=True)
class SamplingPlan:
    """Hebe-owned identity and bounds for one sampling round."""

    round_id: str
    policy: PolicyIdentity
    task_ids: tuple[str, ...]
    group_size: int
    max_new_tokens: int
    temperature: float
    seed: int
    model_identity: ModelIdentity | None = None
    policy_publication: str = ""
    algorithm: str = "sft"

    def __post_init__(self) -> None:
        _nonempty(self.round_id, "round_id")
        if not isinstance(self.policy, PolicyIdentity):
            raise TypeError("policy must be a PolicyIdentity")
        if not isinstance(self.task_ids, (tuple, list)) or not self.task_ids:
            raise ValueError("task_ids must be a nonempty sequence")
        object.__setattr__(self, "task_ids", tuple(self.task_ids))
        for task_id in self.task_ids:
            _nonempty(task_id, "task_id")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("task_ids must be unique")
        _integer(self.group_size, 1, "group_size")
        _integer(self.max_new_tokens, 1, "max_new_tokens")
        _integer(self.seed, 0, "seed")
        if (
            type(self.temperature) not in (int, float)
            or not math.isfinite(self.temperature)
            or self.temperature <= 0
        ):
            raise ValueError("temperature must be positive and finite")
        if self.model_identity is not None and not isinstance(
            self.model_identity, ModelIdentity
        ):
            raise TypeError("model_identity must be a ModelIdentity")
        if self.model_identity is not None and (
            self.policy.tokenizer_digest != self.model_identity.tokenizer_digest
            or self.policy.chat_template_digest
            != self.model_identity.chat_template_digest
        ):
            raise ValueError(
                "policy and model tokenizer/chat-template identities differ"
            )
        if not isinstance(self.policy_publication, str):
            raise TypeError("policy_publication must be a string")
        if self.policy_publication:
            _nonempty(self.policy_publication, "policy_publication")
        if not isinstance(self.algorithm, str) or self.algorithm not in {"sft", "grpo"}:
            raise ValueError("algorithm must be sft or grpo")
        if self.algorithm == "grpo" and self.group_size < 2:
            raise ValueError("GRPO requires at least two samples per task")
        if self.algorithm == "grpo" and not self.policy_publication:
            raise ValueError("GRPO requires a policy publication")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SamplingPlan":
        if not isinstance(value, Mapping):
            raise TypeError("sampling_plan must be an object")
        raw_policy = value.get("policy")
        if not isinstance(raw_policy, Mapping):
            raise TypeError("sampling_plan.policy must be an object")
        raw_tasks = value.get("task_ids", ())
        if not isinstance(raw_tasks, (tuple, list)):
            raise TypeError("sampling_plan.task_ids must be a sequence")
        raw_model = value.get("model_identity")
        return cls(
            round_id=value.get("round_id", ""),
            policy=PolicyIdentity(**dict(raw_policy)),
            task_ids=tuple(raw_tasks),
            group_size=value.get("group_size"),
            max_new_tokens=value.get("max_new_tokens"),
            temperature=value.get("temperature"),
            seed=value.get("seed"),
            model_identity=ModelIdentity.from_dict(raw_model)
            if raw_model is not None
            else None,
            policy_publication=value.get("policy_publication", ""),
            algorithm=value.get("algorithm", "sft"),
        )


@dataclass(frozen=True, slots=True)
class TokenTurn:
    """Native prompt/output IDs and response-aligned training information."""

    prompt_token_ids: tuple[int, ...]
    response_token_ids: tuple[int, ...]
    response_mask: tuple[int, ...]
    behavior_log_probs: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        for name in ("prompt_token_ids", "response_token_ids", "response_mask"):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or not values:
                raise ValueError(f"{name} must be a nonempty sequence")
            object.__setattr__(self, name, tuple(values))
            for value in values:
                _integer(value, 0, name)
        if len(self.response_mask) != len(self.response_token_ids):
            raise ValueError("response_mask must align with response_token_ids")
        if set(self.response_mask) - {0, 1} or not any(self.response_mask):
            raise ValueError("response_mask must be binary with a trainable token")
        if self.behavior_log_probs is not None:
            values = self.behavior_log_probs
            if not isinstance(values, (tuple, list)) or len(values) != len(
                self.response_mask
            ):
                raise ValueError(
                    "behavior_log_probs must align with response_token_ids"
                )
            if any(
                type(value) not in (int, float) or not math.isfinite(value) or value > 0
                for value in values
            ):
                raise ValueError(
                    "behavior_log_probs must be finite nonpositive numbers"
                )
            object.__setattr__(self, "behavior_log_probs", tuple(values))


@dataclass(frozen=True, slots=True)
class TrainingSample:
    """One complete scored sample; failed episodes are never invented as rows."""

    round_id: str
    task_id: str
    sample_index: int
    policy: PolicyIdentity
    turns: tuple[TokenTurn, ...]
    reward: float
    success: bool | None = None

    def __post_init__(self) -> None:
        _nonempty(self.round_id, "round_id")
        _nonempty(self.task_id, "task_id")
        _integer(self.sample_index, 0, "sample_index")
        if not isinstance(self.policy, PolicyIdentity):
            raise TypeError("policy must be a PolicyIdentity")
        if not isinstance(self.turns, (tuple, list)) or not self.turns:
            raise ValueError("turns must be a nonempty sequence")
        if any(not isinstance(turn, TokenTurn) for turn in self.turns):
            raise TypeError("turns must contain TokenTurn records")
        object.__setattr__(self, "turns", tuple(self.turns))
        if type(self.reward) not in (int, float) or not math.isfinite(self.reward):
            raise ValueError("reward must be a finite number")
        if self.success is not None and type(self.success) is not bool:
            raise ValueError("success must be an explicit boolean or absent")


@dataclass(frozen=True, slots=True)
class TrainingBatch:
    """A complete single-policy set of SFT episodes or GRPO groups."""

    plan: SamplingPlan
    samples: tuple[TrainingSample, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan, SamplingPlan):
            raise TypeError("plan must be a SamplingPlan")
        if not isinstance(self.samples, (tuple, list)):
            raise TypeError("samples must be a sequence")
        object.__setattr__(self, "samples", tuple(self.samples))
        actual: set[tuple[str, int]] = set()
        for sample in self.samples:
            if not isinstance(sample, TrainingSample):
                raise TypeError("samples must contain TrainingSample records")
            if (
                sample.round_id != self.plan.round_id
                or sample.policy != self.plan.policy
            ):
                raise ValueError(
                    "sample round/policy identity does not match sampling plan"
                )
            identity = (sample.task_id, sample.sample_index)
            if identity in actual:
                raise ValueError("duplicate task/sample identity")
            actual.add(identity)
            if sample.task_id not in self.plan.task_ids:
                raise ValueError("sample task is outside the sampling plan")
            if not 0 <= sample.sample_index < self.plan.group_size:
                raise ValueError("sample index is outside the sampling plan")
            if any(
                len(turn.response_token_ids) > self.plan.max_new_tokens
                for turn in sample.turns
            ):
                raise ValueError(
                    "response exceeds sampling plan's per-call token limit"
                )
        expected = {
            (task_id, index)
            for task_id in self.plan.task_ids
            for index in range(self.plan.group_size)
        }
        if actual != expected:
            raise ValueError("incomplete or unexpected task/sample groups")

    def validate_for(self, algorithm: str) -> None:
        if not isinstance(algorithm, str) or algorithm not in {"sft", "grpo"}:
            raise ValueError("supported training algorithms are sft and grpo")
        if algorithm == "grpo":
            if self.plan.group_size < 2:
                raise ValueError("GRPO requires at least two samples per task")
            if any(
                turn.behavior_log_probs is None
                for sample in self.samples
                for turn in sample.turns
            ):
                raise ValueError("GRPO requires native behavior log probabilities")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": BATCH_SCHEMA,
            "plan": asdict(self.plan),
            "samples": [asdict(sample) for sample in self.samples],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrainingBatch":
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema", "plan", "samples"}
            or value.get("schema")
            not in {
                BATCH_SCHEMA,
                "alphahebe.training-batch.v1",
                "alphahebe.training-batch.v2",
            }
        ):
            raise ValueError("unsupported training batch schema")
        plan = SamplingPlan.from_dict(value["plan"])
        samples: list[TrainingSample] = []
        for raw in value["samples"]:
            if not isinstance(raw, Mapping):
                raise TypeError("training sample must be an object")
            row = dict(raw)
            row["policy"] = PolicyIdentity(**dict(row["policy"]))
            row["turns"] = tuple(TokenTurn(**dict(turn)) for turn in row["turns"])
            samples.append(TrainingSample(**row))
        return cls(plan, tuple(samples))


@dataclass(frozen=True, slots=True)
class TrainingCapture:
    """Durable result of one complete native-token Pi episode."""

    round_id: str
    task_id: str
    sample_index: int
    attempt_id: str
    policy: PolicyIdentity
    model_identity: ModelIdentity | None
    turns: tuple[TokenTurn, ...]
    termination_reason: str
    usage: dict[str, int]
    policy_publication: str = ""
    policy_attestation: str = "declared"
    calls: tuple[dict[str, Any], ...] = ()
    evidence_refs: dict[str, str] | None = None
    sampling_plan: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _nonempty(self.round_id, "round_id")
        _nonempty(self.task_id, "task_id")
        _integer(self.sample_index, 0, "sample_index")
        _nonempty(self.attempt_id, "attempt_id")
        if not isinstance(self.policy, PolicyIdentity):
            raise TypeError("policy must be a PolicyIdentity")
        if self.model_identity is not None and not isinstance(
            self.model_identity, ModelIdentity
        ):
            raise TypeError("model_identity must be a ModelIdentity")
        if not isinstance(self.turns, (tuple, list)) or not self.turns:
            raise ValueError("turns must be a nonempty sequence")
        if any(not isinstance(turn, TokenTurn) for turn in self.turns):
            raise TypeError("turns must contain TokenTurn records")
        object.__setattr__(self, "turns", tuple(self.turns))
        if not isinstance(
            self.termination_reason, str
        ) or self.termination_reason not in {
            "eos",
            "token_budget",
        }:
            raise ValueError("unsupported termination reason")
        if not isinstance(self.usage, Mapping):
            raise TypeError("usage must be an object")
        normalized_usage: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = self.usage.get(key)
            _integer(value, 0, f"usage.{key}")
            normalized_usage[key] = value
        object.__setattr__(self, "usage", normalized_usage)
        prompt_total = sum(len(turn.prompt_token_ids) for turn in self.turns)
        completion_total = sum(len(turn.response_token_ids) for turn in self.turns)
        if normalized_usage["prompt_tokens"] != prompt_total:
            raise ValueError("usage.prompt_tokens differs from captured prompt IDs")
        if normalized_usage["completion_tokens"] != completion_total:
            raise ValueError(
                "usage.completion_tokens differs from captured response IDs"
            )
        if normalized_usage["total_tokens"] != prompt_total + completion_total:
            raise ValueError("usage.total_tokens differs from captured token IDs")
        if not isinstance(self.policy_publication, str):
            raise TypeError("policy_publication must be a string")
        if not isinstance(
            self.policy_attestation, str
        ) or self.policy_attestation not in {
            "declared",
            "verified",
        }:
            raise ValueError("unsupported policy attestation state")
        if self.evidence_refs is not None:
            if not isinstance(self.evidence_refs, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in self.evidence_refs.items()
            ):
                raise TypeError("evidence_refs must map names to string references")
            object.__setattr__(self, "evidence_refs", dict(self.evidence_refs))
        if not isinstance(self.calls, (tuple, list)):
            raise TypeError("calls must be a sequence")
        if any(not isinstance(call, Mapping) for call in self.calls):
            raise TypeError("calls must contain objects")
        object.__setattr__(self, "calls", tuple(self.calls))
        if self.sampling_plan is not None:
            if not isinstance(self.sampling_plan, Mapping):
                raise TypeError("sampling_plan must be an object")
            object.__setattr__(self, "sampling_plan", dict(self.sampling_plan))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "tokens_native": True,
            "trajectory_complete": True,
            "round_id": self.round_id,
            "task_id": self.task_id,
            "sample_index": self.sample_index,
            "attempt_id": self.attempt_id,
            "policy": asdict(self.policy),
            "model_identity": asdict(self.model_identity)
            if self.model_identity
            else None,
            "turns": [asdict(turn) for turn in self.turns],
            "termination_reason": self.termination_reason,
            "usage": dict(self.usage),
            "policy_publication": self.policy_publication,
            "policy_attestation": self.policy_attestation,
            "calls": list(self.calls),
            "evidence_refs": dict(self.evidence_refs or {}),
            "sampling_plan": _legacy_sampling_plan(self.sampling_plan or {}),
            "sampling_plan_details": dict(self.sampling_plan or {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrainingCapture":
        """Restore and validate one durable native-token episode result."""

        if not isinstance(value, Mapping):
            raise TypeError("training capture must be an object")
        if (
            value.get("schema") != SCHEMA
            or value.get("tokens_native") is not True
            or value.get("trajectory_complete") is not True
        ):
            raise ValueError("unsupported or incomplete native training capture")
        raw_model = value.get("model_identity")
        turns = value.get("turns")
        if not isinstance(turns, list):
            raise TypeError("training capture turns must be a list")
        return cls(
            round_id=value["round_id"],
            task_id=value["task_id"],
            sample_index=value["sample_index"],
            attempt_id=value["attempt_id"],
            policy=PolicyIdentity(**dict(value["policy"])),
            model_identity=(
                ModelIdentity.from_dict(raw_model) if raw_model is not None else None
            ),
            turns=tuple(TokenTurn(**dict(turn)) for turn in turns),
            termination_reason=value["termination_reason"],
            usage=dict(value["usage"]),
            policy_publication=value.get("policy_publication", ""),
            policy_attestation=value.get("policy_attestation", "declared"),
            calls=tuple(value.get("calls", ())),
            evidence_refs=dict(value.get("evidence_refs", {})),
            sampling_plan=dict(
                value.get("sampling_plan_details", value.get("sampling_plan", {}))
            ),
        )


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """Validated per-episode capture settings derived from agent config."""

    plan: SamplingPlan
    task_id: str
    sample_index: int
    model_name: str
    attempt_id: str
    max_calls: int = 16
    require_logprobs: bool = False
    require_policy_attestation: bool = False
    require_model_attestation: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.plan, SamplingPlan):
            raise TypeError("plan must be a SamplingPlan")
        _nonempty(self.task_id, "task_id")
        _integer(self.sample_index, 0, "sample_index")
        _nonempty(self.model_name, "model_name")
        _nonempty(self.attempt_id, "attempt_id")
        _integer(self.max_calls, 1, "max_calls")
        if self.max_calls > 1024:
            raise ValueError("max_calls must be no greater than 1024")
        if self.task_id not in self.plan.task_ids:
            raise ValueError("task_id is outside sampling plan")
        if self.sample_index >= self.plan.group_size:
            raise ValueError("sample_index is outside sampling plan")
        if self.plan.model_identity is not None:
            if self.plan.model_identity.model_name != self.model_name:
                raise ValueError(
                    "sampling plan model identity differs from harness model"
                )
        if self.require_logprobs and self.plan.algorithm != "grpo":
            raise ValueError("native logprob capture is only required for GRPO plans")
        if self.require_policy_attestation and not self.plan.policy_publication:
            raise ValueError("policy_publication is required for policy attestation")
        for name in (
            "require_logprobs",
            "require_policy_attestation",
            "require_model_attestation",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self.plan)
        value.update(
            {
                "schema": REQUEST_SCHEMA,
                "enabled": True,
                "task_id": self.task_id,
                "sample_index": self.sample_index,
                "model_name": self.model_name,
                "attempt_id": self.attempt_id,
                "max_calls": self.max_calls,
                "require_logprobs": self.require_logprobs,
                "require_policy_attestation": self.require_policy_attestation,
                "require_model_attestation": self.require_model_attestation,
            }
        )
        return value


def capture_config_from_dict(
    value: Mapping[str, Any] | None,
    *,
    task_id: str,
    sample_index: int,
    model_name: str,
    attempt_id: str,
) -> CaptureConfig | None:
    """Parse the public ``training`` agent setting for one task/sample."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("training must be an object")
    if "schema" in value and value["schema"] != REQUEST_SCHEMA:
        raise ValueError("unsupported training request schema")
    enabled = value.get("enabled", True)
    if type(enabled) is not bool:
        raise TypeError("enabled must be a boolean")
    if enabled is False:
        return None
    nested = value.get("sampling_plan")
    raw_plan = nested if isinstance(nested, Mapping) else value
    # Top-level values are accepted for concise YAML while nested plans make
    # it explicit which fields Hebe owns.
    plan_values = dict(raw_plan)
    for key in ("model_identity", "policy_publication", "algorithm"):
        if key not in plan_values and key in value:
            plan_values[key] = value[key]
    plan = SamplingPlan.from_dict(plan_values)
    configured_task = value.get("task_id", task_id)
    configured_index = value.get("sample_index", sample_index)
    if (
        type(configured_task) is not str
        or configured_task != task_id
        or type(configured_index) is not int
        or configured_index != sample_index
    ):
        raise ValueError("training task/sample identity differs from runner dispatch")
    model_identity = plan.model_identity
    if model_identity is None:
        raise ValueError(
            "training requires model_identity with tokenizer/template digests"
        )
    if model_identity.model_name != model_name:
        raise ValueError("training model_identity differs from harness model")
    algorithm = plan.algorithm
    require_logs = value.get("require_logprobs", algorithm == "grpo")
    require_attestation = value.get("require_policy_attestation", algorithm == "grpo")
    require_model_attestation = value.get("require_model_attestation", True)
    for name, setting in (
        ("require_logprobs", require_logs),
        ("require_policy_attestation", require_attestation),
        ("require_model_attestation", require_model_attestation),
    ):
        if type(setting) is not bool:
            raise TypeError(f"{name} must be a boolean")
    max_calls = value.get("max_calls", 16)
    return CaptureConfig(
        plan=plan,
        task_id=task_id,
        sample_index=sample_index,
        model_name=model_name,
        attempt_id=attempt_id,
        max_calls=max_calls,
        require_logprobs=require_logs,
        require_policy_attestation=require_attestation,
        require_model_attestation=require_model_attestation,
    )


def validate_training_config(value: object) -> None:
    """Validate syntax that can be checked before a task is dispatched."""

    if value is None:
        return
    if not isinstance(value, Mapping):
        raise TypeError("training must be an object")
    if "schema" in value and value["schema"] != REQUEST_SCHEMA:
        raise ValueError("unsupported training request schema")
    enabled = value.get("enabled", True)
    if type(enabled) is not bool:
        raise TypeError("enabled must be a boolean")
    if enabled is False:
        return
    # Task/sample/model values are checked again at solve time against runner
    # identity.  These placeholders only validate the policy/plan structure.
    raw = value.get("sampling_plan")
    plan = dict(raw) if isinstance(raw, Mapping) else dict(value)
    for key in ("model_identity", "policy_publication", "algorithm"):
        if key not in plan and key in value:
            plan[key] = value[key]
    if "policy" not in plan or "model_identity" not in plan:
        raise ValueError("training requires policy and model_identity")
    SamplingPlan.from_dict(plan)
    max_calls = value.get("max_calls", 16)
    _integer(max_calls, 1, "max_calls")
    if max_calls > 1024:
        raise ValueError("max_calls must be no greater than 1024")
    for key in (
        "require_logprobs",
        "require_policy_attestation",
        "require_model_attestation",
    ):
        if key in value and type(value[key]) is not bool:
            raise TypeError(f"{key} must be a boolean")


def _tool_identity(tools: Any) -> tuple[tuple[str, str, Any], ...]:
    """Canonicalize tool calls while ignoring JSON argument whitespace."""

    if not isinstance(tools, list) or not tools:
        raise ValueError("missing generated tool calls")
    identities = []
    for tool in tools:
        if not isinstance(tool, Mapping) or tool.get("type") != "function":
            raise ValueError("unsupported tool type")
        function = tool.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("malformed function tool call")
        try:
            arguments = json.loads(function["arguments"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("tool arguments are not valid JSON") from exc
        identities.append(
            (str(tool.get("id", "")), str(function.get("name", "")), arguments)
        )
    if any(not identity[0] or not identity[1] for identity in identities):
        raise ValueError("tool call identity is incomplete")
    if len({identity[0] for identity in identities}) != len(identities):
        raise ValueError("duplicate tool call identity")
    return tuple(identities)


def _identity_from_response(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("model_identity", "serving_identity", "model_metadata"):
        candidate = response.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    return None


class NativeCapture:
    """Validate and retain native completion records for one Pi episode."""

    def __init__(
        self,
        plan: SamplingPlan,
        task_id: str,
        sample_index: int,
        model_name: str,
        max_calls: int,
        *,
        require_logprobs: bool = False,
        attempt_id: str = "capture",
        model_identity: ModelIdentity | None = None,
        require_policy_attestation: bool = False,
        require_model_attestation: bool | None = None,
    ) -> None:
        if not isinstance(plan, SamplingPlan):
            raise TypeError("plan must be a SamplingPlan")
        _integer(sample_index, 0, "sample_index")
        _nonempty(task_id, "task_id")
        _nonempty(model_name, "model_name")
        _nonempty(attempt_id, "attempt_id")
        _integer(max_calls, 1, "max_calls")
        if max_calls > 1024:
            raise ValueError("max_calls must be no greater than 1024")
        if task_id not in plan.task_ids:
            raise ValueError("task_id is outside sampling plan")
        if sample_index >= plan.group_size:
            raise ValueError("sample_index is outside sampling plan")
        if model_identity is None:
            model_identity = plan.model_identity
        if model_identity is not None and not isinstance(model_identity, ModelIdentity):
            raise TypeError("model_identity must be a ModelIdentity")
        if plan.model_identity is not None and model_identity != plan.model_identity:
            raise ValueError("serving model identity differs from sampling plan")
        if model_identity is not None and model_identity.model_name != model_name:
            raise ValueError("model identity differs from harness model")
        if require_model_attestation is None:
            require_model_attestation = model_identity is not None
        for name, value in (
            ("require_logprobs", require_logprobs),
            ("require_policy_attestation", require_policy_attestation),
            ("require_model_attestation", require_model_attestation),
        ):
            if type(value) is not bool:
                raise TypeError(f"{name} must be a boolean")
        self.plan = plan
        self.model_name = model_name
        self.model_identity = model_identity
        self.task_id = task_id
        self.sample_index = sample_index
        self.attempt_id = attempt_id
        self.max_calls = max_calls
        self.require_logprobs = bool(require_logprobs)
        self.require_policy_attestation = bool(require_policy_attestation)
        self.require_model_attestation = bool(require_model_attestation)
        if self.require_policy_attestation and not plan.policy_publication:
            raise ValueError("policy_publication is required for policy attestation")
        self.identity = [plan.seed, plan.round_id, task_id, sample_index]
        self.calls: list[dict[str, Any]] = []
        self.turns: list[TokenTurn] = []
        self._captured_bytes = 0
        self.failed = False
        self._last_attestation = "declared"

    def _ensure_live(self) -> None:
        if self.failed or len(self.calls) >= self.max_calls:
            raise ValueError("native capture failed or exhausted its model-call budget")

    def prepare(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_live()
        if not isinstance(request, Mapping):
            raise ValueError("provider request must be an object")
        if request.get("model") != self.model_name:
            raise ValueError("harness requested a different model")
        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("expected nonempty chat messages")
        if any(not isinstance(message, Mapping) for message in messages):
            raise ValueError("chat messages must be objects")
        if request.get("parallel_tool_calls") is False:
            # Filtering after generation would leave native IDs for actions that
            # were not executed, so such trajectories cannot train safely.
            raise ValueError("post-generation tool-call filtering is not trainable")
        if self.calls:
            previous = self.calls[-1]
            before = previous["request"]["messages"]
            choice = previous["response"]["choices"][0]
            tools = choice["message"].get("tool_calls") or []
            if choice["finish_reason"] != "tool_calls" or not tools:
                raise ValueError("unexpected model call after terminal answer")
            if messages[: len(before)] != before:
                raise ValueError(
                    "context reset/compaction is not supported by native capture"
                )
            following = messages[len(before) :]
            if not following or following[0].get("role") != "assistant":
                raise ValueError("missing previous assistant tool call")
            if _tool_identity(following[0].get("tool_calls")) != _tool_identity(tools):
                raise ValueError("harness changed the generated tool calls")
            results = following[1:]
            if len(results) != len(tools) or any(
                row.get("role") != "tool" for row in results
            ):
                raise ValueError("missing or unexpected tool observations")
            if sorted(row.get("tool_call_id", "") for row in results) != sorted(
                row["id"] for row in tools
            ):
                raise ValueError(
                    "tool result identity does not match the generated call"
                )

        allowed = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "stream",
            "stream_options",
            # Pi 0.85 sends ``store: false`` on every OpenAI-compatible
            # request.  It is provider bookkeeping rather than capture state;
            # accept it at the boundary and deliberately omit it from the
            # upstream payload below.
            "store",
            "n",
            "max_tokens",
            "max_completion_tokens",
            "temperature",
            "top_p",
            "presence_penalty",
            "frequency_penalty",
            "logprobs",
            "top_logprobs",
            "top_k",
            "min_p",
            "repetition_penalty",
            "chat_template_kwargs",
            "return_token_ids",
            "reasoning_effort",
            "response_format",
            "stop",
            "user",
        }
        unsupported = set(request) - allowed
        if unsupported:
            raise ValueError(
                f"unsupported provider request fields: {sorted(unsupported)}"
            )
        payload = {
            key: copy.deepcopy(request[key])
            for key in (
                "model",
                "messages",
                "tools",
                "tool_choice",
                "parallel_tool_calls",
            )
            if key in request
        }
        seed = int.from_bytes(
            hashlib.sha256(_json_bytes([*self.identity, len(self.calls)])).digest()[:4],
            "big",
        )
        payload.update(
            stream=False,
            n=1,
            return_token_ids=True,
            logprobs=True,
            top_logprobs=0,
            max_tokens=self.plan.max_new_tokens,
            temperature=self.plan.temperature,
            top_p=1.0,
            top_k=-1,
            min_p=0.0,
            repetition_penalty=1.0,
            presence_penalty=0.0,
            frequency_penalty=0.0,
            seed=seed,
            chat_template_kwargs={"enable_thinking": False},
        )
        return payload

    def _check_model_identity(self, response: Mapping[str, Any]) -> None:
        expected = self.model_identity
        if expected is None:
            return
        echoed = _identity_from_response(response)
        if echoed is None:
            if self.require_model_attestation:
                raise ValueError(
                    "training capture requires a serving model/tokenizer/chat-template identity"
                )
            return
        actual = ModelIdentity.from_dict(echoed)
        if actual != expected:
            raise ValueError("serving model/tokenizer/chat-template identity mismatch")

    def _check_policy_attestation(
        self, response: Mapping[str, Any], choice: Mapping[str, Any]
    ) -> None:
        expected = self.plan.policy_publication
        candidates = (
            response.get("policy_publication"),
            response.get("policy_attestation"),
            choice.get("policy_publication"),
            choice.get("policy_attestation"),
        )
        actual = ""
        for value in candidates:
            if isinstance(value, Mapping):
                value = value.get(
                    "policy_publication",
                    value.get("publication", value.get("version", "")),
                )
            if value not in (None, ""):
                actual = str(value)
                break
        if actual and expected and actual != expected:
            raise ValueError("serving policy publication does not match sampling plan")
        if self.require_policy_attestation and actual != expected:
            raise ValueError("on-policy capture requires a serving policy attestation")
        self._last_attestation = (
            "verified" if actual and actual == expected else "declared"
        )

    def record(self, request: Mapping[str, Any], response: Mapping[str, Any]) -> None:
        if self.failed:
            raise ValueError("native capture is poisoned")
        if not isinstance(response, Mapping):
            raise ValueError("completion response must be an object")
        choices = response.get("choices", [])
        if (
            response.get("model") != self.model_name
            or not isinstance(choices, list)
            or len(choices) != 1
        ):
            raise ValueError("completion model/cardinality mismatch")
        choice = choices[0]
        if not isinstance(choice, Mapping) or choice.get("index") != 0:
            raise ValueError("completion choice index mismatch")
        finish_reason = choice.get("finish_reason")
        if finish_reason not in {"stop", "tool_calls", "length"}:
            raise ValueError("partial or unsupported completion")
        message = choice.get("message", {})
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            raise ValueError("completion is not an assistant message")
        tools = message.get("tool_calls") or []
        if finish_reason == "tool_calls":
            _tool_identity(tools)
        elif tools:
            raise ValueError("tool calls require a tool_calls finish reason")
        if (
            finish_reason != "tool_calls"
            and not message.get("content")
            and finish_reason != "length"
        ):
            raise ValueError("empty final answer")
        prompt = response.get("prompt_token_ids")
        tokens = choice.get("token_ids")
        if not isinstance(prompt, list) or not prompt:
            raise ValueError("provider omitted native prompt token IDs")
        if not isinstance(tokens, list) or not tokens:
            raise ValueError("provider omitted native response token IDs")
        for name, values in (
            ("prompt_token_ids", prompt),
            ("response_token_ids", tokens),
        ):
            for token in values:
                _integer(token, 0, name)
        usage = response.get("usage")
        if not isinstance(usage, Mapping):
            raise ValueError("provider omitted native token usage")
        expected_usage = {
            "prompt_tokens": len(prompt),
            "completion_tokens": len(tokens),
        }
        for key, expected_value in expected_usage.items():
            if usage.get(key) != expected_value:
                raise ValueError("native token count differs from provider usage")
        if usage.get("total_tokens", len(prompt) + len(tokens)) != len(prompt) + len(
            tokens
        ):
            raise ValueError("native token count differs from provider usage")
        if len(tokens) > self.plan.max_new_tokens:
            raise ValueError("provider exceeded per-call token budget")
        if finish_reason == "length" and len(tokens) != self.plan.max_new_tokens:
            raise ValueError("length completion did not consume the sampling budget")
        logprobs = choice.get("native_log_probs")
        if logprobs is None:
            # A few serving implementations use a top-level extension for this
            # field; accept it only when it is explicitly aligned to this choice.
            logprobs = response.get("native_log_probs")
        if self.require_logprobs and logprobs is None:
            raise ValueError(
                "on-policy capture requires native behavior log probabilities"
            )
        turn = TokenTurn(
            tuple(prompt),
            tuple(tokens),
            (1,) * len(tokens),
            tuple(logprobs) if logprobs is not None else None,
        )
        self._check_model_identity(response)
        self._check_policy_attestation(response, choice)
        call = {
            "call_index": len(self.calls),
            "request_id": f"{self.attempt_id}:{len(self.calls)}",
            "request": copy.deepcopy(dict(request)),
            "response": copy.deepcopy(dict(response)),
        }
        call_bytes = len(_json_bytes(call))
        if self._captured_bytes + call_bytes > MAX_CAPTURE_BYTES:
            raise ValueError("native capture evidence exceeds its byte bound")
        self._captured_bytes += call_bytes
        self.turns.append(turn)
        self.calls.append(call)

    def finish(self) -> tuple[TokenTurn, ...]:
        if self.failed or not self.calls:
            raise ValueError("no complete native-token trajectory")
        last = self.calls[-1]["response"]["choices"][0]
        finish_reason = last.get("finish_reason")
        if finish_reason not in {"stop", "length"}:
            raise ValueError("no complete native-token trajectory")
        return tuple(self.turns)

    def capture(self) -> TrainingCapture:
        self.finish()
        last = self.calls[-1]["response"]["choices"][0]
        termination_reason = (
            "eos" if last["finish_reason"] == "stop" else "token_budget"
        )
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for call in self.calls:
            values = call["response"]["usage"]
            usage["prompt_tokens"] += int(values["prompt_tokens"])
            usage["completion_tokens"] += int(values["completion_tokens"])
            usage["total_tokens"] += int(
                values.get(
                    "total_tokens",
                    int(values["prompt_tokens"]) + int(values["completion_tokens"]),
                )
            )
        return TrainingCapture(
            round_id=self.plan.round_id,
            task_id=self.task_id,
            sample_index=self.sample_index,
            attempt_id=self.attempt_id,
            policy=self.plan.policy,
            model_identity=self.model_identity,
            turns=tuple(self.turns),
            termination_reason=termination_reason,
            usage=usage,
            policy_publication=self.plan.policy_publication,
            policy_attestation=self._last_attestation,
            calls=tuple(self.calls),
            evidence_refs={
                "result_metadata": "metadata.training_capture",
                "response_json": "response_json.native_training_capture",
            },
            sampling_plan=asdict(self.plan),
        )


def _completion_url(base: str) -> str:
    value = str(base or "").strip().rstrip("/")
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def _aggregate_sse(raw: bytes) -> dict[str, Any]:
    """Aggregate a standard streamed response without decoding generated text."""

    aggregate: dict[str, Any] = {"choices": [], "usage": None}
    states: dict[int, dict[str, Any]] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            item = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, Mapping):
            continue
        for key in ("id", "object", "created", "model"):
            if key in item:
                aggregate[key] = item[key]
        if isinstance(item.get("usage"), Mapping):
            aggregate["usage"] = dict(item["usage"])
        for fallback, raw_choice in enumerate(item.get("choices") or []):
            if not isinstance(raw_choice, Mapping):
                continue
            index = raw_choice.get("index", fallback)
            if type(index) is not int or index < 0:
                index = fallback
            state = states.setdefault(
                index,
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": {},
                    "finish_reason": None,
                },
            )
            delta = raw_choice.get("delta")
            if not isinstance(delta, Mapping):
                delta = (
                    raw_choice.get("message")
                    if isinstance(raw_choice.get("message"), Mapping)
                    else {}
                )
            if delta.get("role"):
                state["role"] = delta["role"]
            if delta.get("content") not in (None, ""):
                state["content"].append(str(delta["content"]))
            for call_fallback, call in enumerate(delta.get("tool_calls") or []):
                if not isinstance(call, Mapping):
                    continue
                call_index = call.get("index", call_fallback)
                current = state["tool_calls"].setdefault(call_index, {})
                if call.get("id"):
                    current["id"] = call["id"]
                if call.get("type"):
                    current["type"] = call["type"]
                if isinstance(call.get("function"), Mapping):
                    function = current.setdefault("function", {})
                    for key in ("name", "arguments"):
                        if call["function"].get(key):
                            function[key] = str(function.get(key, "")) + str(
                                call["function"][key]
                            )
            if raw_choice.get("finish_reason"):
                state["finish_reason"] = raw_choice["finish_reason"]
    choices = []
    for index in sorted(states):
        state = states[index]
        message: dict[str, Any] = {
            "role": state["role"],
            "content": "".join(state["content"]),
        }
        calls = []
        for call_index in sorted(state["tool_calls"]):
            call = dict(state["tool_calls"][call_index])
            calls.append(call)
        if calls:
            message["tool_calls"] = calls
        choices.append(
            {
                "index": index,
                "message": message,
                "finish_reason": state["finish_reason"],
            }
        )
    aggregate["choices"] = choices
    return aggregate


def completion_sse(response: Mapping[str, Any]) -> bytes:
    """Project one captured JSON completion to the stream Pi expects."""

    choice = response["choices"][0]
    message = choice["message"]
    delta = {
        key: message[key] for key in ("role", "content", "tool_calls") if key in message
    }
    if delta.get("tool_calls"):
        delta["tool_calls"] = [
            dict(tool, index=index) for index, tool in enumerate(delta["tool_calls"])
        ]
    base = {key: response.get(key, "") for key in ("id", "created", "model")}
    base["object"] = "chat.completion.chunk"
    chunks = [
        {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {
            **base,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
            ],
        },
        {**base, "choices": [], "usage": response.get("usage")},
    ]
    return (
        "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        + "data: [DONE]\n\n"
    ).encode()


@dataclass(frozen=True, slots=True)
class Backend:
    """Credentials retained by the host-side capture proxy only."""

    url: str
    key: str = ""


class TokenCaptureEndpoint:
    """Bounded, bearer-protected proxy for one isolated native episode."""

    def __init__(
        self,
        backend: Backend | Mapping[str, Any] | Any,
        capture: NativeCapture,
        timeout: float,
        *,
        complete: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
        bind_host: str = "127.0.0.1",
        advertise_host: str = "",
        max_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        if isinstance(backend, Backend):
            resolved_backend = backend
        elif isinstance(backend, Mapping):
            resolved_backend = Backend(
                str(backend.get("url", "")), str(backend.get("key", ""))
            )
        else:
            resolved_backend = Backend(
                str(getattr(backend, "url", "")), str(getattr(backend, "key", ""))
            )
        if not resolved_backend.url.strip():
            raise ValueError("capture backend URL is required")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("capture timeout must be positive and finite")
        if type(max_bytes) is not int or max_bytes < 1024:
            raise ValueError("max_bytes must be a positive bound")
        if httpx is None:
            raise RuntimeError(
                "Pi training capture requires the AlphaDiana 'agents' extra (httpx)"
            )
        self.key = secrets.token_hex(32)
        self.backend = resolved_backend
        self.capture = capture
        self.timeout = float(timeout)
        self.complete = complete
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.bind_host = str(bind_host or "127.0.0.1")
        self.advertise_host = str(advertise_host or "")
        self.client = httpx.Client(trust_env=False, timeout=self.timeout)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(owner.timeout)

            def log_message(self, *args: object) -> None:
                pass

            def _authorized(self) -> bool:
                if self.headers.get("Authorization") == "Bearer " + owner.key:
                    return True
                self.send_error(403, "Forbidden")
                return False

            def _read_json(self) -> dict[str, Any]:
                raw_length = self.headers.get("Content-Length", "")
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise ValueError("invalid request length") from exc
                if not 0 < length <= owner.max_bytes:
                    raise ValueError("request size exceeds capture bound")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("truncated capture request")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("capture request must be an object")
                return value

            def _reply(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _backend_request(self, payload: dict[str, Any]) -> dict[str, Any]:
                if owner.complete is not None:
                    response = owner.complete(payload)
                    encoded = _json_bytes(response)
                    if len(encoded) > owner.max_bytes:
                        raise ValueError("completion size exceeds capture bound")
                    if not isinstance(response, Mapping):
                        raise ValueError("completion callback returned a non-object")
                    return dict(response)
                headers = (
                    {"Authorization": "Bearer " + owner.backend.key}
                    if owner.backend.key
                    else {}
                )
                if owner.capture.plan.policy_publication:
                    headers["X-AlphaDiana-Policy-Publication"] = (
                        owner.capture.plan.policy_publication
                    )
                if owner.capture.model_identity is not None:
                    headers["X-AlphaDiana-Model-Identity"] = _json_bytes(
                        asdict(owner.capture.model_identity)
                    ).decode()
                content = bytearray()
                with owner.client.stream(
                    "POST",
                    _completion_url(owner.backend.url),
                    json=payload,
                    headers=headers,
                ) as upstream:
                    upstream.raise_for_status()
                    for chunk in upstream.iter_bytes():
                        content.extend(chunk)
                        if len(content) > owner.max_bytes:
                            raise ValueError("completion size exceeds capture bound")
                    content_type = upstream.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    return _aggregate_sse(bytes(content))
                response = json.loads(content)
                if not isinstance(response, dict):
                    raise ValueError("backend completion must be an object")
                return response

            def do_POST(self) -> None:
                if not self._authorized():
                    return
                if self.path not in {"/v1/chat/completions", "/chat/completions"}:
                    self.send_error(404, "Not found")
                    return
                try:
                    with owner.lock:
                        request = self._read_json()
                        payload = owner.capture.prepare(request)
                        response = self._backend_request(payload)
                        owner.capture.record(payload, response)
                        body = (
                            completion_sse(response)
                            if request.get("stream")
                            else _json_bytes(response)
                        )
                        if len(body) > owner.max_bytes:
                            raise ValueError(
                                "completion projection exceeds capture bound"
                            )
                        self._reply(
                            200,
                            body,
                            "text/event-stream"
                            if request.get("stream")
                            else "application/json",
                        )
                except Exception:
                    with owner.lock:
                        owner.capture.failed = True
                    try:
                        self.send_error(
                            502,
                            "native training capture failed; retry is not trainable",
                        )
                    except OSError:
                        pass

            def do_GET(self) -> None:
                if not self._authorized():
                    return
                if self.path not in {"/v1/models", "/models"}:
                    self.send_error(404, "Not found")
                    return
                # Pi normally does not query models because models.json is
                # pinned, but this response keeps the provider bridge portable.
                body = _json_bytes(
                    {
                        "object": "list",
                        "data": [{"id": owner.capture.model_name, "object": "model"}],
                    }
                )
                self._reply(200, body, "application/json")

        self.server = ThreadingHTTPServer((self.bind_host, 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "TokenCaptureEndpoint":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def start(self) -> None:
        if getattr(self, "server", None) is None:
            raise RuntimeError("capture endpoint is closed")
        if not self.thread.is_alive():
            self.thread.start()

    def close(self) -> None:
        server = getattr(self, "server", None)
        if server is None:
            return
        self.server = None
        if self.thread.is_alive():
            server.shutdown()
        server.server_close()
        self.thread.join(timeout=5)
        self.client.close()

    stop = close

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @property
    def url(self) -> str:
        server = getattr(self, "server", None)
        if server is None:
            raise RuntimeError("capture endpoint is closed")
        port = server.server_address[1]
        host = self.advertise_host or server.server_address[0]
        if host in {"", "0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    @property
    def base_url(self) -> str:
        return self.url + "/v1"


class TrainingCollectionError(RuntimeError):
    """No partial group may be handed to a trainer."""


class PiTrainingCollector:
    """Collect complete SFT/GRPO groups with one fresh agent per sample.

    The collector accepts a Hebe-provided scoring callback but never owns the
    scorer or policy update.  A failure tears down the current agent and
    invalidates the entire group, preserving sample isolation and replayable
    identities.
    """

    def __init__(self, agent_factory: Callable[[], Any]) -> None:
        self.agent_factory = agent_factory

    def collect(
        self,
        tasks: Iterable[Any],
        plan: SamplingPlan,
        base_config: Mapping[str, Any],
        *,
        score: Callable[[Any, Any], Any] | None = None,
    ) -> TrainingBatch:
        task_list = list(tasks)
        task_map = {task.task_id: task for task in task_list}
        if len(task_map) != len(task_list):
            raise TrainingCollectionError("collector tasks contain duplicate task IDs")
        if set(task_map) != set(plan.task_ids):
            raise TrainingCollectionError("collector tasks do not match sampling plan")
        samples: list[TrainingSample] = []
        for task_id in plan.task_ids:
            task = task_map[task_id]
            for sample_index in range(plan.group_size):
                agent = self.agent_factory()
                config = copy.deepcopy(dict(base_config))
                config["training"] = {
                    **asdict(plan),
                    "enabled": True,
                    "task_id": task_id,
                    "sample_index": sample_index,
                    "require_logprobs": plan.algorithm == "grpo",
                    "require_policy_attestation": plan.algorithm == "grpo",
                }
                attempt_id = secrets.token_hex(16)
                config["_execution_id"] = attempt_id
                metadata = dict(getattr(task, "metadata", {}) or {})
                metadata.update(
                    {"sample_index": sample_index, "execution_id": attempt_id}
                )
                dispatch = replace(task, metadata=metadata)
                try:
                    agent.setup(config)
                    response = agent.solve(dispatch)
                    response_metadata = getattr(response, "metadata", {}) or {}
                    capture_value = response_metadata.get(
                        "training_capture"
                    ) or response_metadata.get("hebe_training")
                    if not isinstance(capture_value, Mapping):
                        raise TrainingCollectionError(
                            "Pi response omitted training capture"
                        )
                    capture = _capture_from_dict(capture_value)
                    if (
                        capture.round_id != plan.round_id
                        or capture.task_id != task_id
                        or capture.sample_index != sample_index
                        or capture.policy != plan.policy
                        or capture.model_identity != plan.model_identity
                    ):
                        raise TrainingCollectionError(
                            "Pi capture identity differs from sampling plan"
                        )
                    if capture_value.get("trajectory_complete") is not True:
                        raise TrainingCollectionError(
                            "Pi response contains an incomplete training capture"
                        )
                    expected_sampling = _legacy_sampling_plan(asdict(plan))
                    if _json_bytes(capture_value.get("sampling_plan")) != _json_bytes(
                        expected_sampling
                    ):
                        raise TrainingCollectionError(
                            "Pi capture sampling settings differ from plan"
                        )
                    if plan.algorithm == "grpo":
                        if capture.policy_attestation != "verified":
                            raise TrainingCollectionError(
                                "GRPO capture lacks verified policy attestation"
                            )
                        if capture.policy_publication != plan.policy_publication:
                            raise TrainingCollectionError(
                                "GRPO capture policy publication differs from plan"
                            )
                    score_result = score(task, response) if score is not None else 0.0
                    reward = _score_value(score_result)
                    samples.append(
                        TrainingSample(
                            plan.round_id,
                            task_id,
                            sample_index,
                            plan.policy,
                            capture.turns,
                            reward,
                            _success_value(score_result) if score is not None else None,
                        )
                    )
                except TrainingCollectionError:
                    raise
                except Exception as exc:
                    raise TrainingCollectionError(
                        f"training sample {task_id}/{sample_index} failed"
                    ) from exc
                finally:
                    try:
                        agent.teardown()
                    except Exception:
                        pass
        batch = TrainingBatch(plan, tuple(samples))
        batch.validate_for(plan.algorithm)
        return batch


def _score_value(value: Any) -> float:
    if hasattr(value, "score"):
        value = value.score
    if type(value) not in (int, float) or not math.isfinite(value):
        raise TrainingCollectionError("score callback returned a non-finite reward")
    return float(value)


def _success_value(value: Any) -> bool | None:
    if hasattr(value, "correct"):
        result = value.correct
        return result if type(result) is bool else None
    return None


def _capture_from_dict(value: Mapping[str, Any]) -> TrainingCapture:
    try:
        return TrainingCapture.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise TrainingCollectionError(str(exc)) from exc
