# terminal-bench-2

This runbook describes the current AlphaDiana path for `terminal-bench-2`.

Use it for local AlphaDiana evaluation and smoke validation. It is not a claim of strict Harbor-equivalent leaderboard execution.

## Current Support

| Mode | Smoke config | Full-run config |
|---|---|---|
| `direct_llm` | `configs/examples/terminal_bench2_directllm_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml` |
| `opencode` | `configs/examples/terminal_bench2_opencode_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml` |
| `openclaw` | `configs/examples/terminal_bench2_openclaw_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml` |
| `zeroclaw` | `configs/examples/terminal_bench2_zeroclaw_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_zeroclaw_minimax.yaml` |

All three paths use the same local AlphaDiana `terminal_bench2` benchmark loader and scorer.

## Prerequisites

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
```

### Model-Pin Caveat

The general "some smoke configs pin the model in YAML" rule lives in
`benchmarks/index.md`. This is the concrete tb2 case.

`configs/examples/terminal_bench2_directllm_minimax.yaml` hard-pins
`agent.config.model: "minimax-m2.5"`, so it ignores `OPENAI_MODEL_NAME`
(it still reads `api_base` / `api_key` from `OPENAI_BASE_URL` /
`OPENAI_API_KEY`). To run a different model on that config, override the
agent config explicitly:

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_directllm_minimax.yaml \
  -o agent.config.model=... \
  -o agent.config.api_base=... \
  -o agent.config.api_key=...
```

The `opencode`, `openclaw`, and `zeroclaw` tb2 example configs read the model
from the environment (`${OPENAI_MODEL_NAME}`), so for those switching the env
vars is enough. The `zeroclaw` config additionally accepts
`-o agent.config.logs_base_dir=...` when redirecting log output.

You also need:

- Docker
- a local `terminal-bench` task checkout
- pre-pulled task images
- runtime source images for the native in-container agents:
  `tmlrgroup/alphadiana:v1` for `openclaw`,
  `alphadiana/tb2-opencode-controller:latest` for `opencode`,
  and `zeroclaw-reasoning:0.6.9` for `zeroclaw`

## Official DirectLLM Baseline

This section is for the official direct-LLM baseline only. It is outside the
AlphaDiana runtime, but it is the benchmark-plan meaning of
`DirectLLM x terminal-bench-2`.

Minimal Harbor invocation shape from the upstream repo:

```bash
cd /path/to/terminal-bench-2
unset ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
export OPENROUTER_API_KEY=...

uv run harbor run --path . \
  --agent terminus-2 \
  --model openrouter/qwen/qwen3.5-27b \
  --n-concurrent 3
```

- Harbor completed `3/3` trials and preserved all trial artifacts
- all verifier rewards were `0`
- `adaptive-rejection-sampler` ended with `AgentTimeoutError`
- `bn-fit-modify` and `break-filter-js-from-html` both reached the verifier and
  still scored `0`

Treat that repaired bundle as the current local smoke-valid signal for the
official `direct_llm` path on OpenRouter/Qwen.

## Official leaderboard path (Harbor + Terminus-2)

The sections above are the AlphaDiana container-agent path. To run the
*official/leaderboard* configuration instead (standalone Harbor CLI + the
upstream `terminus-2` agent, orchestrated by `alphadiana.benchmark_rollout_cli`
with backend `official_terminal_bench_2`), use the path below. Harbor owns the
system prompt; `alphadiana/agent/terminal_bench2_docker.py` is not on this path.

vLLM endpoint (Qwen3.5-27B; the serve-side flags match the official spec):

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-27B --host 0.0.0.0 --port <port> \
  --trust-remote-code --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --tensor-parallel-size 2 --gpu-memory-utilization 0.9 \
  --max-model-len 262144 --generation-config vllm \
  --override-generation-config '{"presence_penalty": 1.5}' \
  --served-model-name qwen3.5-27b Qwen/Qwen3.5-27B
```

`--enable-auto-tool-choice --tool-call-parser qwen3_coder` is required for
`terminus-2` to issue tool calls; `--generation-config vllm` ignores the model's
shipped sampling defaults; omitting `--reasoning-parser` keeps thinking tokens in
`message.content`.

Install Harbor + Terminus-2:

```bash
export DIRECTLLM_TB2_ROOT=/path/to/terminal-bench-2
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install harbor          # installs the `harbor` binary
```

Run (manifest checked in at `configs/full_runs/terminal_bench_v2.yaml`):

```bash
python -m alphadiana.benchmark_rollout_cli summary     --manifest configs/full_runs/terminal_bench_v2.yaml
python -m alphadiana.benchmark_rollout_cli preflight   --manifest configs/full_runs/terminal_bench_v2.yaml --probe-vllm --check-docker
python -m alphadiana.benchmark_rollout_cli materialize --manifest configs/full_runs/terminal_bench_v2.yaml --output-dir generated/terminal_bench_v2
bash generated/terminal_bench_v2/*.run.sh
```

The generated shell runs `harbor run --dataset terminal-bench@2.0 --agent
terminus-2 --model openai/qwen3.5-27b ...` at `temperature=0.0`,
`reasoning_effort=high`, `top_p=0.95`, `max_tokens=131072`, `--n-concurrent 10`
(pass@1). Outputs land in `$DIRECTLLM_TB2_ROOT/jobs/<run_id>/`. Renderer and
dispatch live in `alphadiana/utils/rollout_campaign.py`
(`_render_official_tb2_command`, `render_run_command`); Harbor upstream is
<https://github.com/laude-institute/harbor>.

## Prepare Tasks

Clone a local task checkout:

```bash
git clone --depth=1 https://github.com/laude-institute/terminal-bench.git /tmp/terminal-bench
```

Set the full-run task root:

```bash
export TERMINAL_BENCH2_DIR=/tmp/terminal-bench/tasks
```

Prepare a deterministic smoke staging directory with one task:

```bash
rm -rf /tmp/terminal-bench-smoke-dbwal
mkdir -p /tmp/terminal-bench-smoke-dbwal
cp -a /tmp/terminal-bench/tasks/db-wal-recovery /tmp/terminal-bench-smoke-dbwal/

export TERMINAL_BENCH2_SMOKE_DIR=/tmp/terminal-bench-smoke-dbwal
```

- `db-wal-recovery`
- `fix-git`
- `break-filter-js-from-html`

Example staging flow with environment variables instead of a hardcoded local
path:

```bash
export TERMINAL_BENCH2_SOURCE_ROOT=/path/to/terminal-bench-2/tasks
export TERMINAL_BENCH2_PILOT_ROOT=/path/to/staged-terminal-bench-2-qwen-t3

rm -rf "$TERMINAL_BENCH2_PILOT_ROOT"
mkdir -p "$TERMINAL_BENCH2_PILOT_ROOT"
cp -a "$TERMINAL_BENCH2_SOURCE_ROOT"/db-wal-recovery "$TERMINAL_BENCH2_PILOT_ROOT"/
cp -a "$TERMINAL_BENCH2_SOURCE_ROOT"/fix-git "$TERMINAL_BENCH2_PILOT_ROOT"/
cp -a "$TERMINAL_BENCH2_SOURCE_ROOT"/break-filter-js-from-html "$TERMINAL_BENCH2_PILOT_ROOT"/
```

The smoke configs assume `TERMINAL_BENCH2_SMOKE_DIR` points at a directory whose immediate children are task directories. The full-run configs assume `TERMINAL_BENCH2_DIR` points at the full task root.

## Pre-pull Task Images

Before any smoke or full run:

```bash
python - <<'PY' | sort -u | xargs -r -n1 docker pull
import os, tomllib
from pathlib import Path

for task_toml in Path(os.environ["TERMINAL_BENCH2_DIR"]).glob("*/task.toml"):
    with task_toml.open("rb") as f:
        data = tomllib.load(f)
    image = data.get("environment", {}).get("docker_image")
    if image:
        print(image)
PY
```

For the default smoke task specifically:

```bash
docker pull alexgshaw/db-wal-recovery:20251031
```

## Prepare Runtime Source Images

`direct_llm` still uses the helper-workspace controller path. The native agents
`opencode`, `openclaw`, and `zeroclaw` now run inside a derived task image, so
they need runtime source images instead of controller containers.

Prepare the checked-in sources once:

```bash
docker pull tmlrgroup/alphadiana:v1
docker image inspect alphadiana/tb2-opencode-controller:latest >/dev/null
docker pull zeroclaw-reasoning:0.6.9
```

The first native-agent smoke/full run automatically builds a derived
`alphadiana-tb2-runtime:<agent>-<fingerprint>` image from the task image plus
the selected runtime source image.

For ZeroClaw, prefer putting large temporary files on a data disk before running:

```bash
export TMPDIR=/path/to/$USER/tmp/alphadiana-tb2
mkdir -p "$TMPDIR"
```

## Runtime Model

AlphaDiana now uses two different TB2 execution contracts:

- `direct_llm`: helper-workspace controller mode.
  The model sees `tb2-exec`, `tb2-copy-from`, `tb2-copy-to`, and `tb2-test`.
- `opencode`, `openclaw`, `zeroclaw`: native in-container mode.
  AlphaDiana derives a runtime image from the task image, starts that task
  container directly, and runs the agent CLI inside it.

For the native agents:

- the model sees the live task filesystem directly
- `tb2-exec` / `tb2-copy-*` are not exposed to the model
- `/tests/test.sh` and `reward.txt` stay unchanged
- the outer harness still runs verification once at the end

## Smoke Runs

Validate the smoke configs first:

```bash
python -m alphadiana.cli validate configs/examples/terminal_bench2_directllm_minimax.yaml
python -m alphadiana.cli validate configs/examples/terminal_bench2_opencode_minimax.yaml
python -m alphadiana.cli validate configs/examples/terminal_bench2_openclaw_minimax.yaml
python -m alphadiana.cli validate configs/examples/terminal_bench2_zeroclaw_minimax.yaml
```

Run the three smoke configs:

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/examples/terminal_bench2_opencode_minimax.yaml --redo-all
python -m alphadiana.cli run configs/examples/terminal_bench2_openclaw_minimax.yaml --redo-all
python -m alphadiana.cli run configs/examples/terminal_bench2_zeroclaw_minimax.yaml --redo-all
```

```bash
python -m alphadiana.cli validate configs/examples/terminal_bench2_openclaw_minimax.yaml \
  -o run_id=<run_id> \
  -o output_dir=./results \
  -o benchmark.config.tasks_dir="$TERMINAL_BENCH2_PILOT_ROOT" \
  -o benchmark.config.max_tasks=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b

python -m alphadiana.cli run configs/examples/terminal_bench2_opencode_minimax.yaml \
  -o run_id=<run_id> \
  -o output_dir=./results \
  -o benchmark.config.tasks_dir="$TERMINAL_BENCH2_PILOT_ROOT" \
  -o benchmark.config.max_tasks=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b \
  -o agent.config.model=custom/qwen/qwen3.5-27b \
  -o agent.config.streaming=true \
  -o max_concurrent=2 \
  2>&1 | tee logs/<run_id>.log
```

Smoke success means:

- the task loads
- the selected agent path runs
- `/tests/test.sh` runs
- a scored JSONL result is written

It does not mean the agent is competitive across the full benchmark.

## Full Runs

Validate the full-run configs first:

```bash
python -m alphadiana.cli validate configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml
python -m alphadiana.cli validate configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml
python -m alphadiana.cli validate configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml
python -m alphadiana.cli validate configs/full_runs/p25_full_terminal_bench2_zeroclaw_minimax.yaml
```

Run them:

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_zeroclaw_minimax.yaml --redo-all
```

Recommended concurrency:

- `direct_llm`: `max_concurrent: 4`
- `opencode`: `max_concurrent: 2`
- `openclaw`: `max_concurrent: 1`
- `zeroclaw`: `max_concurrent: 1`

Adjust only if the local machine has enough Docker and API capacity.

## Result Interpretation

The current AlphaDiana `terminal_bench2` scorer is binary:

- `reward.txt == "1"` means pass
- missing or non-`1` reward means fail

The JSONL `score` comes from that reward path.

## Current Config Semantics

Smoke configs:

- live under `configs/examples/`
- use `TERMINAL_BENCH2_SMOKE_DIR`
- intentionally run one staged task

Full-run configs:

- live under `configs/full_runs/`
- use `TERMINAL_BENCH2_DIR`
- scan all task directories under that root

For the current checked-in smoke setup, the canonical staged task is `db-wal-recovery`.

## ZeroClaw Reproduction Notes

`terminal-bench-2` does not use ROCK. The formal ZeroClaw smoke path is now
the TB2 native in-container path:

- AlphaDiana starts a derived TB2 runtime image for the selected task
- ZeroClaw runs directly inside that task container
- the task JSON is normal as long as the run writes a scored record, even if
  the verifier reward is `0`
