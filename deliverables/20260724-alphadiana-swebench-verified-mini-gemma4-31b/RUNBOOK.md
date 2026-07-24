# Runbook: SWE-Bench Verified-Mini × 4 Harnesses × Gemma 4 31B

Formalized: 2026-07-24.

## 1. Locked experiment contract

| Parameter | Value | Enforcement point |
| --- | --- | --- |
| Dataset | `MariusHobbhahn/swe-bench-verified-mini`, `test`, 50 tasks | SWE-agent CLI or AlphaDiana benchmark config |
| Model | `google/gemma-4-31B-it` | vLLM and each harness |
| Temperature | `0.0` | SWE-agent CLI / AlphaDiana agent config |
| Top-p | `0.95` | SWE-agent CLI / AlphaDiana agent config |
| Presence penalty | `1.5` | vLLM server generation default |
| Maximum model length | `262144` | vLLM `--max-model-len` |
| Maximum output tokens | `131072` | SWE-agent CLI / AlphaDiana agent config |
| Thinking | enabled | vLLM server default and AlphaDiana agent config |
| Streaming | enabled | SWE-agent internal streaming; OpenClaw `stream`; OpenCode `streaming`; native ZeroClaw CLI transport |
| Sample K | `1` | one SWE-agent attempt / AlphaDiana `num_samples: 1` |
| Maximum concurrency | `4` | SWE-agent `--num_workers`; AlphaDiana `max_concurrent` |
| HF repo | `T-MARS/alphadiana-benchmark-results` (private dataset) | upload script |
| HF folder | `YYYYMMDD-swe-bench-verified-mini-<agent>-gemma-4-31b-it-vNN` | upload script |

DirectLLM intentionally has no AlphaDiana experiment YAML. It uses the
standalone official SWE-agent path with upstream `config/default.yaml`.
OpenClaw, OpenCode, and ZeroClaw use AlphaDiana's task-bound
`swebench_container` path.

Logprob capture is disabled because it was not part of the requested contract
and the official SWE-agent path does not produce AlphaDiana logprob sidecars.

## 2. Required software and layout

AlphaDiana path:

```text
$ALPHADIANA_ROOT/
├── alphadiana/
├── configs/
├── scripts/
└── results/
```

Official SWE-agent path:

```text
$DIRECTLLM_SWE_VERIFIED_ROOT/
├── .venv/
├── SWE-agent/
└── sweagent_results/
```

Install the official tools if the second layout does not exist:

```bash
mkdir -p "$DIRECTLLM_SWE_VERIFIED_ROOT"
cd "$DIRECTLLM_SWE_VERIFIED_ROOT"
git clone https://github.com/SWE-agent/SWE-agent
python3.11 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -e SWE-agent
./.venv/bin/pip install swebench huggingface_hub
```

Docker must be usable by the current user. The AlphaDiana checkout must have
its Python environment installed and activated according to that checkout's
normal setup.

## 3. Start the shared vLLM server

Gemma 4 tool calling requires vLLM's Gemma 4 tool chat template. Set
`GEMMA4_CHAT_TEMPLATE` to the installed/downloaded
`examples/tool_chat_template_gemma4.jinja` from the matching vLLM release.

```bash
export GEMMA4_CHAT_TEMPLATE=/path/to/examples/tool_chat_template_gemma4.jinja

CUDA_VISIBLE_DEVICES=<GPU_IDS> vllm serve google/gemma-4-31B-it \
  --host 0.0.0.0 \
  --port 8011 \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 \
  --chat-template "$GEMMA4_CHAT_TEMPLATE" \
  --default-chat-template-kwargs '{"enable_thinking":true}' \
  --enable-prefix-caching \
  --tensor-parallel-size <TP> \
  --gpu-memory-utilization 0.90 \
  --max-model-len 262144 \
  --generation-config vllm \
  --override-generation-config '{"presence_penalty":1.5}' \
  --served-model-name gemma-4-31b-it google/gemma-4-31B-it
```

`--default-chat-template-kwargs` is important for DirectLLM because the default
SWE-agent config does not expose AlphaDiana's `enable_thinking` field.
Request-level thinking remains explicitly enabled in the three AlphaDiana
configs.

## 4. Export runtime settings

From the extracted bundle directory:

```bash
export ALPHADIANA_ROOT=/path/to/AlphaDiana
export DIRECTLLM_SWE_VERIFIED_ROOT=/path/to/swe-bench-root
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export SWE_CONTAINER_OPENAI_BASE_URL=http://host.docker.internal:8011/v1
export OPENAI_API_KEY=local-key
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export HF_REPO=T-MARS/alphadiana-benchmark-results
export RUN_VERSION=v01
```

`SWE_CONTAINER_OPENAI_BASE_URL` must be reachable from a normal Docker bridge
container. `127.0.0.1` inside a task container refers to that container, not the
host. On Linux the default bridge gateway is often `host.docker.internal`; verify it on
the run host instead of assuming.

Run preflight:

```bash
bash scripts/preflight.sh
```

For a deep container-to-provider probe, which may pull `curlimages/curl`:

```bash
DEEP_CONTAINER_PROBE=1 bash scripts/preflight.sh
```

## 5. One-task smoke tests

The native paths support a one-task smoke override:

```bash
bash scripts/run.sh openclaw --smoke
bash scripts/run.sh opencode --smoke
bash scripts/run.sh zeroclaw --smoke
```

Do not start the full 50-task runs until each native smoke produces:

- one task JSON under `results/<smoke-run-id>/tasks/`;
- a non-empty trajectory or preserved runtime trace;
- a patch or an explicitly classified no-patch result;
- a SWE-bench score status rather than an unclassified infrastructure error.

The DirectLLM command below is already the official default-agent path. If a
DirectLLM smoke is required, run it against an explicit one-instance dataset
slice supported by the installed SWE-agent version; do not edit the shared
50-task dataset or silently change its order.

## 6. Full runs

Run one harness at a time. This keeps the effective model concurrency at four
and avoids turning four nominally identical configs into different
resource-contention conditions.

### 6.1 DirectLLM: official SWE-agent

```bash
bash scripts/run.sh directllm
```

The script runs the following contract without creating an AlphaDiana config:

```text
sweagent run-batch
  --config config/default.yaml
  --num_workers 4
  --instances.type swe_bench
  --instances.path_override MariusHobbhahn/swe-bench-verified-mini
  --instances.subset verified
  --instances.split test
  --instances.evaluate=False
  --instances.deployment.type docker
  --agent.model.name openai/gemma-4-31b-it
  --agent.model.temperature 0.0
  --agent.model.top_p 0.95
  --agent.model.max_output_tokens 131072
```

It then invokes `swebench.harness.run_evaluation` with `--max_workers 4`.

### 6.2 OpenClaw / OpenCode / ZeroClaw

```bash
bash scripts/run.sh openclaw
bash scripts/run.sh opencode
bash scripts/run.sh zeroclaw
```

Each launch passes a versioned run ID derived from `RUN_VERSION`. Reusing the
same run ID resumes AlphaDiana checkpoints. To intentionally rerun from
scratch, bump `RUN_VERSION`; do not combine `--redo-all` with an already
uploaded HF destination.

## 7. Monitor

Native run:

```bash
RUN_ID=full_swe_bench_verified_mini_opencode_gemma4_31b_v01
watch -n 300 "find '$ALPHADIANA_ROOT/results/$RUN_ID/tasks' -maxdepth 1 -name '*.json' 2>/dev/null | wc -l"
tail -f "$ALPHADIANA_ROOT/logs/$RUN_ID.log"
```

DirectLLM:

```bash
RUN_ID=full_swe_bench_verified_mini_directllm_gemma4_31b_v01
find "$DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/$RUN_ID" -name '*.pred' | wc -l
tail -f "$ALPHADIANA_ROOT/logs/$RUN_ID.log"
```

Investigate repeated provider 4xx/5xx responses, empty streams, context-length
errors, task-container build failures, and retry storms immediately. A loaded
GPU alone does not prove forward progress.

## 8. Validate finished outputs

Native:

```bash
python scripts/verify_outputs.py \
  --agent opencode \
  --root "$ALPHADIANA_ROOT" \
  --version "$RUN_VERSION"
```

DirectLLM:

```bash
python scripts/verify_outputs.py \
  --agent directllm \
  --root "$DIRECTLLM_SWE_VERIFIED_ROOT" \
  --version "$RUN_VERSION"
```

The verifier requires 50 unique tasks and one sample/prediction per task. It
reports non-`valid_scored` native records separately so infrastructure failures
cannot be mistaken for completed evaluations.

## 9. Upload to the private HF dataset repo

Authenticate with a token that has write access:

```bash
hf auth login
hf auth whoami
```

Upload each run:

```bash
bash scripts/upload.sh directllm "$RUN_VERSION"
bash scripts/upload.sh openclaw "$RUN_VERSION"
bash scripts/upload.sh opencode "$RUN_VERSION"
bash scripts/upload.sh zeroclaw "$RUN_VERSION"
```

The destination leaf is exact:

```text
YYYYMMDD-swe-bench-verified-mini-<agent>-gemma-4-31b-it-vNN
```

The upload script checks the private dataset repository before writing and
refuses to reuse a non-empty destination folder. Set a new `RUN_VERSION`
instead of overwriting prior evidence.

## 10. Known implementation notes

1. OpenCode's current `swebench_container` adapter installs
   `opencode-ai@latest`. The config records this limitation; preserve the
   effective CLI version in uploaded logs/artifacts.
2. ZeroClaw's native AlphaDiana path does not expose a separate `streaming`
   YAML key. Provider streaming is owned by the native CLI. The config enables
   the provider proxy without logprob injection so `top_p`, `max_tokens`, and
   thinking are enforced on upstream requests without adding an ignored field
   or producing logprob sidecars.
3. Presence penalty is server-side so the same value applies to official
   SWE-agent and all three AlphaDiana agents.
4. The official SWE-agent path may not aggregate per-instance `.pred` files
   into `preds.json` in every upstream revision. If evaluation reports a
   missing file, use that checkout's documented gather helper, then rerun only
   the official evaluation step.
5. This bundle targets Docker. Port, gateway, and socket settings must be
   re-formalized before substituting Podman.

## 11. External references

- vLLM Gemma 4 recipe:
  `https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html`
- SWE-agent model configuration:
  `https://swe-agent.com/latest/config/models/`
- Hugging Face CLI upload guide:
  `https://huggingface.co/docs/huggingface_hub/en/guides/cli`
