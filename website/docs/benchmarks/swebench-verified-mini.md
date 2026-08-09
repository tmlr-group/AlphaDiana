# SWE-Bench Verified-Mini Guide

Run Qwen3.5-27B on `MariusHobbhahn/swe-bench-verified-mini` (50 tasks) via the
standalone SWE-agent CLI, orchestrated by `alphadiana.benchmark_rollout_cli`
with backend `official_swebench_verified`.

Wall time estimate: 8–15 h at `max_concurrent=10`.

Reference run for parity: `T-MARS/alphadiana-benchmark-results/full_run/YYYYMMDD-swe-bench-verified-mini-sweagent-qwen35-27b-local-v1`
(23 / 50 = 46 % resolved).

---

## 1. Parameter alignment

| Knob                 | Value                                           | Where it lives                                                                 |
| -------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------ |
| `temperature`        | `0.0`                                           | manifest `overrides.temperature`                                               |
| `top_p`              | `0.95`                                          | manifest `overrides.top_p`                                                     |
| `max_output_tokens`  | `131072` (128K)                                 | manifest `overrides.max_output_tokens`                                         |
| Sample K             | `1` (pass@1)                                    | `sweagent run-batch` is single-shot per instance                               |
| `max_concurrent`     | `10`                                            | manifest `path_template.max_concurrent`                                        |
| `per_instance_call_limit` | `80`                                       | manifest `overrides.per_instance_call_limit` (matches reference run)           |
| `sweagent_config`    | `config/default.yaml`                           | manifest `overrides.sweagent_config` (relative to `SWE-agent/`)                |
| thinking             | on                                              | vLLM serve default (no `--reasoning-parser`; thinking tokens stay in `content`) |
| streaming            | on                                              | sweagent internally streams; no knob needed                                    |
| `presence_penalty`   | `1.5`                                           | **vLLM serve-side** via `--override-generation-config` (every request gets it) |
| `max_model_len`      | `262144` (256K)                                 | **vLLM serve-side** via `--max-model-len`                                      |

Both serve-side params must be set in the vLLM launch command.

System prompt: **unchanged** — uses SWE-agent upstream's own config (default
`config/default.yaml`, swap via `sweagent_config` override). `alphadiana`'s
`swebench_docker._DEFAULT_SYSTEM_PROMPT` does not apply on this path; the
sweagent CLI owns its own prompting.

This guide covers the *official/leaderboard* path (standalone SWE-agent). For
the container-agent path (`openclaw` / `opencode` / `zeroclaw` via
`swebench_docker`), see [`swebench-verified.md`](swebench-verified.md).

---

## 2. Prerequisites

### 2.1 vLLM endpoint

**Launch vLLM** (adjust GPU indices, port, and local/HF model path to your setup):

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-27B \
  --host 0.0.0.0 --port <port> \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.9 \
  --max-model-len 262144 \
  --generation-config vllm \
  --override-generation-config '{"presence_penalty": 1.5}' \
  --served-model-name qwen3.5-27b Qwen/Qwen3.5-27B
```

Why each flag matters:
- `--max-model-len 262144` (256K): matches the model's native max context so long SWE-bench problem statements fit alongside 128K output budget.
- `--override-generation-config '{"presence_penalty": 1.5}'`: per-request default that suppresses Qwen3.5's repetition loops. Setting it serverside means every client request inherits it automatically.
- `--generation-config vllm`: ignores the model's shipped `generation_config.json` (which sets `temperature=0.6`, `top_k=20`, `top_p=0.95`). Without this, clients that omit sampling params silently get non-greedy defaults.
- No `--reasoning-parser`: keep thinking tokens inside `message.content` (SWE-agent reads content, not a separate reasoning field).
- `--enable-auto-tool-choice --tool-call-parser qwen3_coder`: harmless here (SWE-agent sends XML-parsed text, not OpenAI tools), required if any other agent on the same endpoint wants function calling.

**Sanity check from the client side:**

```bash
export QWEN_VLLM_API_BASE=http://127.0.0.1:<port>/v1
export QWEN_VLLM_API_KEY=EMPTY

curl -s "$QWEN_VLLM_API_BASE/models" | jq '.data[].id'
# -> "qwen3.5-27b"
# -> "Qwen/Qwen3.5-27B"
```

### 2.2 SWE-agent checkout + venv

```bash
export DIRECTLLM_SWE_VERIFIED_ROOT=/path/to/swe-bench-root
mkdir -p "$DIRECTLLM_SWE_VERIFIED_ROOT"
cd "$DIRECTLLM_SWE_VERIFIED_ROOT"

git clone https://github.com/SWE-agent/SWE-agent
python3.11 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -e SWE-agent
./.venv/bin/pip install swebench          # official harness

# sanity
./.venv/bin/sweagent --version
./.venv/bin/python -c "import swebench; print(swebench.__version__)"
```

The renderer expects exactly this layout:

```
$DIRECTLLM_SWE_VERIFIED_ROOT/
├── .venv/
└── SWE-agent/
    └── config/default.yaml    # or whichever config the manifest references
```

### 2.3 Docker + HF

- `docker ps` returns cleanly (per-instance containers live here).
- `huggingface-cli login` with read access to `MariusHobbhahn/swe-bench-verified-mini`
  and write access to `T-MARS/alphadiana-benchmark-results` (private).

---

## 3. Campaign manifest

Already checked in at `configs/full_runs/swe_verified_mini.yaml`:

```yaml
campaign_id: "swe_verified_mini"
defaults:
  run_id_prefix: "swe_verified_mini"
models:
  - id: qwen35_27b
    model_name: Qwen/Qwen3.5-27B
    api_base_env: QWEN_VLLM_API_BASE
    api_key_env:  QWEN_VLLM_API_KEY
    official_model_name: openai/qwen3.5-27b
    supports_multimodal: false
path_templates:
  - id: swe_verified_mini_sweagent
    benchmark: swe_bench_verified
    harness: sweagent
    backend: official_swebench_verified
    max_concurrent: 10
    base_wave: wave_b_official
    base_risk: official_checkout
    config_gaps: []
    overrides:
      # sweagent_config: "config/default.yaml"   # optional; default shown
      dataset: "MariusHobbhahn/swe-bench-verified-mini"
      subset: "verified"
      split: "test"
      temperature: 0.0
      top_p: 0.95
      max_output_tokens: 131072
      per_instance_call_limit: 80
      eval_max_workers: 4
```

---

## 4. Run

```bash
# Sanity (prints the expanded run list)
python -m alphadiana.benchmark_rollout_cli summary \
  --manifest configs/full_runs/swe_verified_mini.yaml

# Env + docker preflight (non-zero exit on error)
python -m alphadiana.benchmark_rollout_cli preflight \
  --manifest configs/full_runs/swe_verified_mini.yaml \
  --probe-vllm --check-docker

# Materialize the run script
python -m alphadiana.benchmark_rollout_cli materialize \
  --manifest configs/full_runs/swe_verified_mini.yaml \
  --output-dir generated/swe_verified_mini

# Execute in tmux (50 tasks @ max_concurrent=10 → ~8-15h wall)
tmux new -s swe-verified-mini -d \
  "bash generated/swe_verified_mini/*.run.sh"
tmux attach -t swe-verified-mini     # Ctrl+B D to detach
```

The generated shell does:

1. `cd $DIRECTLLM_SWE_VERIFIED_ROOT/SWE-agent`
2. `sweagent run-batch --config config/default.yaml --instances.type swe_bench --instances.path_override MariusHobbhahn/swe-bench-verified-mini --instances.subset verified --instances.split test --agent.model.name openai/qwen3.5-27b --agent.model.api_base $QWEN_VLLM_API_BASE --agent.model.temperature 0.0 --agent.model.top_p 0.95 --agent.model.max_output_tokens 131072 --agent.model.per_instance_call_limit 80 ...`
3. `python -m swebench.harness.run_evaluation --dataset_name MariusHobbhahn/swe-bench-verified-mini --split test --predictions_path sweagent_results/<run_id>/preds.json --run_id <run_id> --max_workers 4`

Outputs:

- `$DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/<run_id>/<instance_id>/...`
  (`.traj`, `.patch`, `.pred`, `.trace.log`)
- `$DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/<run_id>/preds.json`
- SWE-bench harness report at `logs/run_evaluation/<run_id>/`

---

## 5. HF upload

Folder name: `YYYYMMDD-<dataset>-<agent>-<model>-v<NN>`,
e.g. `<YYYYMMDD>-swe-bench-verified-mini-sweagent-qwen35-27b-v<NN>`.

```bash
LOCAL_DIR="$DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/<run_id>"
TARGET="full_run/<YYYYMMDD>-swe-bench-verified-mini-sweagent-qwen35-27b-v<NN>"

huggingface-cli upload-large-folder \
  T-MARS/alphadiana-benchmark-results \
  "$LOCAL_DIR" \
  --repo-type dataset \
  --path-in-repo "$TARGET"
```

Use `upload-large-folder`, not plain `upload` — sweagent produces 5k+ small files
which time out the single-shot uploader.

---

## 6. Known gotchas

- **`config/default.yaml` vs known-working Qwen config.** The default manifest
  uses `config/default.yaml`, which is upstream SWE-agent's generic config with
  `function_calling` parsing. Qwen3.5 served via vLLM is not a perfect
  function-calling client; the reference 46 %-resolved run used
  `config/benchmarks/250522_anthropic_filemap_simple_review.yaml` (anthropic
  filemap tool bundle) combined with an XML parse override. If you want the
  closest match to that reference run, override:
  ```yaml
  overrides:
    sweagent_config: "config/benchmarks/250522_anthropic_filemap_simple_review.yaml"
  ```
  and pass `--agent.tools.parse_function.type xml_thought_action` as an extra
  flag (manual edit of the materialized shell today; renderer-level override
  tracked as a follow-up).
- **preds.json aggregation.** Some SWE-agent versions don't emit a single
  `preds.json`. If the harness step fails to open it, use SWE-agent's gather
  helper (`python helper_code/gather_patches.py` if present in your checkout) or
  roll a small script that joins per-instance `.pred` files.
- **Long-tail instances.** A handful of instances hit `per_instance_call_limit`
  and never submit a patch — they count as unresolved. Reference run saw ~7
  such cases out of 50.
- **GPU contention.** If another run is already using the same vLLM endpoint,
  drop `max_concurrent` or wait for it to finish.

---

## 7. References

- Reference HF run summary: `T-MARS/alphadiana-benchmark-results/full_run/YYYYMMDD-swe-bench-verified-mini-sweagent-qwen35-27b-local-v1/`
- Renderer: `alphadiana/utils/rollout_campaign.py::_render_official_swebench_verified_command`
- Dispatch: `alphadiana/utils/rollout_campaign.py::render_run_command`
- CLI: `alphadiana/benchmark_rollout_cli.py`
- SWE-agent upstream: https://github.com/SWE-agent/SWE-agent
- SWE-bench harness: https://github.com/SWE-bench/SWE-bench
