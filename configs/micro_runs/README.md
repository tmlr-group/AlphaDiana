# Micro runs

Runnable micro experiments for AlphaDiana. The release distinguishes the
experiments reported in the [paper](https://openreview.net/forum?id=4vARlk9o95)
from later framework extensions.

## Paper coverage

| Paper axis | Paper conditions | Reference cells | Config coverage |
|---|---|---:|---:|
| Tool (Table 2) | Full Harness, Minimal Harness | 2 harnesses × 2 models × 2 benchmarks = 8 | 16/16 |
| Skill (Table 3) | Full Harness, Math Skill, General Skill | the same 8 cells | 16/16 skill configs; Full baselines shared with Tool |
| Memory | Not reported as a paper micro ablation | — | repository extension only |

The paper micro matrix uses:

- harnesses: ZeroClaw and OpenCode;
- models: Qwen3.5-27B and Kimi-K2.6;
- benchmarks: GPQA-Diamond (Avg@1) and AIME 2026 (Pass@4 / Avg@4).

OpenClaw is intentionally absent from the paper Tool and Skill directories.

## Layout

```text
micro_runs/
├── Tool/                   # Table 2: 8 Full + 8 Minimal configs
├── Skill/                  # Table 3: 8 Math + 8 General configs
└── Memory/                 # non-paper extension
    ├── intra_task/
    ├── cross_sample/
    └── cross_task/
```

The Skill study reuses the 8 `_tool_full.yaml` files as its Full Harness
baselines. This keeps the matched baseline identical across Tables 2 and 3.

## Environment

Build or pull the harness images on the execution host, start ROCK for
ZeroClaw, and export provider settings:

```bash
docker build --network host \
  -f alphadiana/benchmarks/terminal_bench2/deploy/dockerfiles/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
docker build --network host \
  -f alphadiana/harness/zeroclaw/deploy/Dockerfile \
  -t zeroclaw-reasoning:0.6.9 .

bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh

export OPENAI_BASE_URL=http://HOST_REACHABLE_FROM_SANDBOX:PORT/v1
export OPENAI_API_KEY=sk-EMPTY
```

For Kimi-K2.6, use a compatible OpenAI-style provider endpoint. For
ZeroClaw + Kimi, route through the supplied proxy with `--rename-reasoning` so
thinking content survives normalization.

## Validate

```bash
find configs/micro_runs -name '*.yaml' -print0 | sort -z | \
  while IFS= read -r -d '' config; do
    python -m alphadiana.cli validate "$config" || exit 1
  done
```

## Run paper Tool conditions

Full Harness points directly at `OPENAI_BASE_URL`:

```bash
python -m alphadiana.cli run \
  configs/micro_runs/Tool/aime2026_zeroclaw_qwen35_27b_tool_full.yaml \
  --redo-all
```

Minimal Harness must point at a filtering proxy:

```bash
python -m alphadiana.harness.proxies.tool_filter_proxy \
  --upstream "$OPENAI_BASE_URL" --api-key "$OPENAI_API_KEY" --port 9050 \
  --block '.*' --harness-strip zeroclaw
export TOOL_FILTER_BASE_URL=http://HOST_REACHABLE_FROM_SANDBOX:9050/v1

python -m alphadiana.cli run \
  configs/micro_runs/Tool/aime2026_zeroclaw_qwen35_27b_tool_minimal.yaml \
  --redo-all
```

Use `--harness-strip opencode` for OpenCode. Do not omit `--block '.*'`:
`--harness-strip` rewrites the prompt but does not itself remove tool schemas.
A Minimal YAML without this correctly configured proxy does not implement the
paper intervention.

## Run paper Skill conditions

```bash
python -m alphadiana.cli run \
  configs/micro_runs/Skill/aime2026_zeroclaw_qwen35_27b_skill_math.yaml \
  --redo-all

python -m alphadiana.cli run \
  configs/micro_runs/Skill/aime2026_zeroclaw_qwen35_27b_skill_general.yaml \
  --redo-all
```

The bundles are checked in under `alphadiana/harness/skills/`; no private
absolute path is required.

## Memory extension

Memory configs remain available for AlphaDiana's `intra_task`, `cross_sample`,
and `cross_task` scopes. They are useful follow-up experiments but are not
evidence for Tables 2 or 3. See `Memory/README.md` for their state and resume
semantics.
