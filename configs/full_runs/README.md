# PR25 Full Benchmark Configs

These configs are the ready-to-run full benchmark entry points for the eight PR25 combinations that passed strict smoke validation.

The smoke configs under `configs/examples/` intentionally pin one task with `dataset_index` or `max_tasks`. Do not use those files for full benchmark runs.

## Supported Matrix

| Mode | IMO-AnswerBench | HLE | terminal-bench-2 |
|---|---|---|---|
| `direct_llm` | `p25_full_directllm_minimax_imo_answerbench.yaml` | `p25_full_directllm_minimax_hle.yaml` | `p25_full_terminal_bench2_directllm_minimax.yaml` |
| `opencode` | `p25_full_opencode_minimax_imo_answerbench.yaml` | `p25_full_opencode_minimax_hle.yaml` | `p25_full_terminal_bench2_opencode_minimax.yaml` |
| `openclaw` | `p25_full_openclaw_minimax_imo_answerbench.yaml` | `p25_full_openclaw_minimax_hle.yaml` | native path implemented, no full-run config yet |

`openclaw` x `terminal-bench-2` is still absent from `configs/full_runs/` because the older relay path failed strict smoke, and the newer native CLI path has not yet completed a fresh live rerun.

## Common Setup

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

HLE uses the gated `cais/hle` dataset. On a fresh machine:

```bash
export HF_TOKEN=hf_...
```

terminal-bench-2 needs a local task checkout:

```bash
git clone --depth=1 https://github.com/harbor-framework/terminal-bench-2.git /tmp/terminal-bench-2
export TERMINAL_BENCH2_DIR=/tmp/terminal-bench-2
```

Pre-pull terminal-bench-2 task images before full runs:

```bash
python - <<'PY' | sort -u | xargs -r -n1 docker pull
import os, tomllib
from pathlib import Path

root = Path(os.environ["TERMINAL_BENCH2_DIR"])
for task_toml in root.glob("*/task.toml"):
    with task_toml.open("rb") as f:
        data = tomllib.load(f)
    image = data.get("environment", {}).get("docker_image")
    if image:
        print(image)
PY
```

## Run Commands

Validate first:

```bash
for cfg in configs/full_runs/p25_full_*.yaml; do
  python -m alphadiana.cli validate "$cfg" || exit 1
done
```

Run sequentially unless the machine has enough local Docker and ROCK capacity:

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml --redo-all

python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_hle.yaml --redo-all

python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml --redo-all
```

## Scope Notes

- IMO-AnswerBench configs run the full configured `train` split.
- HLE configs run the full supported `multipleChoice` subset. Other answer types are not included in the current exact-match scoring path.
- terminal-bench-2 configs scan all local task directories under `TERMINAL_BENCH2_DIR`.
- DirectLLM configs default to `max_concurrent: 20`.
- OpenCode configs default to `max_concurrent: 20` for IMO/HLE, and `2` for terminal-bench-2 because it also starts Docker containers.
- OpenClaw configs default to `max_concurrent: 1`; use a pre-deployed gateway pool before increasing concurrency.
