# Terminal-Bench v2 Official Guide

Run Qwen3.5-27B on Terminal-Bench v2 via the standalone Harbor CLI + Terminus-2
agent, orchestrated by `alphadiana.benchmark_rollout_cli` with backend
`official_terminal_bench_2`.

This guide covers the *official/leaderboard* path (Harbor + Terminus-2). For the
AlphaDiana container-agent path on the same benchmark, see
[`terminal-bench-2.md`](terminal-bench-2.md).

---

## 1. Parameter alignment

| Knob                 | Value                                           | Where it lives                                                                 |
| -------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------ |
| `temperature`        | `0.0`                                           | renderer (hardcoded `--ak temperature=0.0`)                                    |
| `top_p`              | `0.95`                                          | renderer (hardcoded in `llm_call_kwargs`)                                      |
| `max_tokens`         | `131072` (128K)                                 | renderer (hardcoded in `llm_call_kwargs`)                                      |
| `reasoning_effort`   | `high`                                          | renderer (hardcoded `--ak reasoning_effort=high`)                              |
| Sample K             | `1`                                             | harbor is single-shot per task                                                 |
| `max_concurrent`     | `10`                                            | manifest `path_template.max_concurrent` → `--n-concurrent`                     |
| thinking             | on                                              | vLLM serve default (no `--reasoning-parser`)                                   |
| streaming            | on                                              | harbor internally streams; no knob needed                                      |
| `presence_penalty`   | `1.5`                                           | **vLLM serve-side** via `--override-generation-config`                         |
| `max_model_len`      | `262144` (256K)                                 | **vLLM serve-side** via `--max-model-len`                                      |

If a future batch needs different values for the renderer-inlined knobs,
refactor `_render_official_tb2_command` to be override-driven (tracked as a
follow-up).

System prompt: **unchanged** — Harbor's `terminus-2` agent owns the prompt.
`alphadiana/agent/terminal_bench2_docker.py` is not on this code path.

---

## 2. Prerequisites

### 2.1 vLLM endpoint

```bash
export QWEN_VLLM_API_BASE=http://127.0.0.1:<port>/v1
export QWEN_VLLM_API_KEY=EMPTY
curl -s "$QWEN_VLLM_API_BASE/models" | jq '.data[].id'
```

### 2.2 Harbor + Terminus-2

```bash
export DIRECTLLM_TB2_ROOT=/path/to/terminal-bench-2
cd "$DIRECTLLM_TB2_ROOT"

# Harbor CLI (upstream-recommended install via uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install harbor            # installs the `harbor` binary on $PATH
harbor --version
```

If `$DIRECTLLM_TB2_ROOT` already contains the upstream Terminal-Bench v2 dataset
checkout, leave it in place and install harbor on top.

### 2.3 Docker + HF

- `docker ps` works; `docker compose version` returns v2.x (Harbor requires
  Compose v2). If missing, install the user-local plugin:
  ```bash
  mkdir -p ~/.docker/cli-plugins
  curl -sSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
    -o ~/.docker/cli-plugins/docker-compose
  chmod +x ~/.docker/cli-plugins/docker-compose
  ```
- `huggingface-cli login` with write access to
  `T-MARS/alphadiana-benchmark-results` (private).

---

## 3. Campaign manifest

Already checked in at `configs/full_runs/terminal_bench_v2.yaml`:

```yaml
campaign_id: "terminal_bench_v2"
defaults:
  run_id_prefix: "tb2"
models:
  - id: qwen35_27b
    model_name: Qwen/Qwen3.5-27B
    api_base_env: QWEN_VLLM_API_BASE
    api_key_env:  QWEN_VLLM_API_KEY
    official_model_name: openai/qwen3.5-27b
    supports_multimodal: false
path_templates:
  - id: tb2_terminus2
    benchmark: terminal_bench2
    harness: direct_llm
    backend: official_terminal_bench_2
    max_concurrent: 10
    base_wave: wave_b_official
    base_risk: official_checkout
    config_gaps: []
```

No `overrides` block — the TB2 renderer currently inlines sampling params, which
already match the target spec.

---

## 4. Run

```bash
python -m alphadiana.benchmark_rollout_cli summary \
  --manifest configs/full_runs/terminal_bench_v2.yaml

python -m alphadiana.benchmark_rollout_cli preflight \
  --manifest configs/full_runs/terminal_bench_v2.yaml \
  --probe-vllm --check-docker

python -m alphadiana.benchmark_rollout_cli materialize \
  --manifest configs/full_runs/terminal_bench_v2.yaml \
  --output-dir generated/terminal_bench_v2

tmux new -s tb2 -d \
  "bash generated/terminal_bench_v2/*.run.sh"
tmux attach -t tb2     # Ctrl+B D to detach
```

Generated shell (abridged):

```bash
cd $DIRECTLLM_TB2_ROOT
OPENAI_API_KEY=$QWEN_VLLM_API_KEY \
harbor run --dataset terminal-bench@2.0 \
  --agent terminus-2 \
  --model openai/qwen3.5-27b \
  --job-name <run_id> \
  --jobs-dir jobs \
  --n-concurrent 10 \
  --debug \
  --ak api_base=$QWEN_VLLM_API_BASE \
  --ak temperature=0.0 \
  --ak reasoning_effort=high \
  --ak 'llm_call_kwargs={"top_p":0.95,"max_tokens":131072}'
```

Outputs land in `$DIRECTLLM_TB2_ROOT/jobs/<run_id>/`. If `$DIRECTLLM_TB2_ROOT`
is not writable, override `--jobs-dir` with an absolute path in the generated
shell before running.

---

## 5. HF upload

Folder name: `YYYYMMDD-<dataset>-<agent>-<model>-v<NN>`,
e.g. `<YYYYMMDD>-terminal-bench2-terminus2-qwen35-27b-v<NN>`.

```bash
LOCAL_DIR="$DIRECTLLM_TB2_ROOT/jobs/<run_id>"
TARGET="full_run/<YYYYMMDD>-terminal-bench2-terminus2-qwen35-27b-v<NN>"

huggingface-cli upload-large-folder \
  T-MARS/alphadiana-benchmark-results \
  "$LOCAL_DIR" \
  --repo-type dataset \
  --path-in-repo "$TARGET"
```

---

## 6. Known gotchas

- **GPU contention.** If another run is using the same vLLM, drop
  `max_concurrent` or wait.
- **Docker image pulls.** First run pulls several Terminus-2 base images; expect
  several minutes before real task execution begins.
- **Jobs dir permissions.** `--jobs-dir jobs` writes under the dataset root by
  default. If that directory is read-only for your user, point
  `--jobs-dir /absolute/writable/path` in the generated shell before running.

---

## 7. References

- Renderer: `alphadiana/utils/rollout_campaign.py::_render_official_tb2_command`
- Dispatch: `alphadiana/utils/rollout_campaign.py::render_run_command`
- CLI: `alphadiana/benchmark_rollout_cli.py`
- Tests: `tests/test_rollout_campaign_swe_verified_and_tb2.py`
- Harbor upstream: <https://github.com/laude-institute/harbor>
- Terminal-Bench v2 upstream: <https://github.com/harbor-framework/terminal-bench-2>
