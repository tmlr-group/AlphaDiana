# Pi native-token SFT and GRPO capture

`agent.name: pi` can run a training-capable episode through the same disposable
rootless Podman container used by ordinary evaluation. The host starts a
single-use bearer-protected proxy in front of `agent.config.api_base`. Pi sees
the normal OpenAI chat-completion protocol; the proxy records the serving
response before projecting it to Pi. The output is therefore the model's native
token trace, rather than a re-tokenized final answer.

Hebe owns the round, task selection, policy publication and reward update.
AlphaDiana owns execution, capture validation, container cleanup and durable
result metadata. `single_rollout` remains a lightweight evaluation adapter;
use the collector or the two full-run examples when a training trace is
needed.

## Contract

The versioned episode result is stored in `AgentResponse.metadata.training_capture`
and copied to the cross-repository `metadata.hebe_training` projection.
`response_json.native_training_capture` carries only a versioned reference to
that metadata record; the full native requests/responses are stored once under
`training_capture` to avoid tripling the durable payload:

```json
{
  "schema": "alphadiana.pi-training.v1",
  "tokens_native": true,
  "trajectory_complete": true,
  "round_id": "grpo-round-001",
  "task_id": "addition",
  "sample_index": 0,
  "attempt_id": "<runner execution id>",
  "policy": {
    "weights_digest": "<sha256>",
    "tokenizer_digest": "<sha256>",
    "chat_template_digest": "<sha256>"
  },
  "model_identity": {
    "model_name": "served-model",
    "model_revision": "revision-1",
    "tokenizer_digest": "<sha256>",
    "chat_template_digest": "<sha256>"
  },
  "turns": [
    {
      "prompt_token_ids": [101, 102],
      "response_token_ids": [201, 202],
      "response_mask": [1, 1],
      "behavior_log_probs": [-0.10, -0.20]
    }
  ],
  "termination_reason": "eos",
  "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
  "policy_publication": "policy-step-001",
  "policy_attestation": "verified"
}
```

The `hebe_training` projection includes the same fields plus the shared
seven-field `sampling_plan` projection, allowing Hebe's sampler to compare
every bound setting before building a batch. `sampling_plan_details` retains
AlphaDiana's additional algorithm, publication and model-identity fields.

`termination_reason` is `eos` for a normal stop and `token_budget` only when a
length-limited response consumed the complete configured budget. A shorter
length response, missing IDs, usage disagreement, context reset, duplicate
tool ID, or incomplete tool observation is an execution error and is never a
valid training row. Every generated token receives an assistant mask of `1`;
tool observations are represented in the next call's prompt IDs.

The cross-repository batch contract is `alphahebe.training-batch.v2`: exactly
the seven shared plan fields and Hebe's standard sample rows, so AlphaHebe's
`TrainingBatch.from_dict` can load it unchanged. AlphaDiana's own archives keep
the full-fidelity `alphadiana.pi-training-batch.v1` form with algorithm,
publication, model identity and per-sample provenance. Calling
`TrainingBatch.validate_for("grpo")` additionally requires at least two samples
per task and native behavior log probabilities for every turn.

The request supplied by Hebe to a Pi episode is the following version-neutral
shape (the YAML adapter accepts the same fields either at the top level or under
`sampling_plan`):

```json
{
  "enabled": true,
  "algorithm": "grpo",
  "round_id": "grpo-round-001",
  "task_ids": ["addition"],
  "group_size": 2,
  "max_new_tokens": 128,
  "temperature": 1.0,
  "seed": 17,
  "task_id": "addition",
  "sample_index": 0,
  "policy_publication": "policy-step-001",
  "policy": {
    "weights_digest": "<sha256>",
    "tokenizer_digest": "<sha256>",
    "chat_template_digest": "<sha256>"
  },
  "model_identity": {
    "model_name": "served-model",
    "model_revision": "revision-1",
    "tokenizer_digest": "<sha256>",
    "chat_template_digest": "<sha256>"
  },
  "require_logprobs": true,
  "require_policy_attestation": true,
  "chat_template_kwargs": {"enable_thinking": false}
}
```

Unknown training keys, and provider fields such as `reasoning_effort`,
`response_format` or `stop` that Pi would otherwise drop, are rejected instead
of being silently forwarded. `reasoning` and `training` cannot be enabled
together.

An ordinary evaluated rollout, the training request and result, a complete
two-sample group, and a cancelled execution are checked-in as readable fixtures under
[`docs/harnesses/examples/`](examples/). They are contract examples with
synthetic digests; replace those values with the published policy manifest.

## Serving endpoint requirements

The upstream endpoint must accept the normal OpenAI chat-completion request plus
the native capture controls `return_token_ids`, `logprobs`, `top_k`, `min_p`,
`chat_template_kwargs` and `seed`. It must return the following extensions in
the non-streaming response (the proxy forces the upstream request to
`stream=false`):

```json
{
  "model": "served-model",
  "prompt_token_ids": [101, 102],
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "answer"},
    "finish_reason": "stop",
    "token_ids": [201, 202],
    "native_log_probs": [-0.10, -0.20]
  }],
  "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
  "policy_publication": "policy-step-001"
}
```

The proxy never adds policy or model headers of its own; it forwards only the
short-lived bearer credential. The serving endpoint itself must echo the policy
publication and the policy weights digest in its response. GRPO requires these
echoes to match the sampling plan on every generated call; this is the policy
attestation boundary. Model/tokenizer/template digests are retained from the
immutable plan and the serving endpoint must echo `model_identity`; the echo is
checked for equality. A generic OpenAI endpoint that cannot provide native IDs,
the identity echo, or the policy echo is not a training-capable endpoint.

## Full-run examples

Install the agent extra and build an immutable runtime image as described in
[native-podman.md](native-podman.md). Then set the endpoint, key, image and
identity values before running:

```bash
export HARNESS_IMAGE=sha256:<immutable-image-id>
export OPENAI_MODEL_NAME=served-model
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=<evaluation-key>
export POLICY_PUBLICATION=policy-step-001
export POLICY_WEIGHTS_DIGEST=<sha256>
export POLICY_TOKENIZER_DIGEST=<sha256>
export POLICY_TEMPLATE_DIGEST=<sha256>
export MODEL_REVISION=revision-1

python -m alphadiana.cli run configs/examples/pi_training_sft.yaml
python -m alphadiana.cli run configs/examples/pi_training_grpo.yaml
```

The GRPO config uses `num_samples: 2`; Runner binds each sample index to a
fresh container and a fresh capture endpoint. A controller that needs a
validated batch can instead supply a public task JSON and plan JSON to:

```bash
python scripts/pi_training_capture.py \
  --config configs/examples/pi_training_grpo.yaml \
  --tasks /path/to/public-tasks.json \
  --plan /path/to/sampling-plan.json \
  --output /path/to/native-batch.json
```

The script requires `--scores` (a JSON mapping from `task_id` to
`{"score": ..., "correct": ...}`) or `--exact-match` against the public task
`ground_truth`; it never fabricates rewards. Private scoring remains with the
controller, and the Pi container never receives ground truth through its input.

## Scope and limitations

* AlphaDiana does not load a tokenizer, decode IDs, compute rewards, or update
  policy weights. The endpoint's native response is authoritative.
* The capture endpoint is single-episode and poisoned after any failed or
  retried request. This prevents a partial trajectory from being reused.
* Model identity digests are supplied by the published sampling plan and
  attested by the serving endpoint's `model_identity` echo. Providers that need
  cryptographic proof must implement that echo from their own loaded-policy
  attestation.
* Tool calls are supported only when the next Pi request preserves the complete
  previous context and matching tool/result IDs. Post-generation tool-call
  filtering and context compaction are rejected.
* Apollo, optimizer state, checkpoint mutation, and evaluator gold data stay in
  AlphaHebe or the private evaluator boundary.
