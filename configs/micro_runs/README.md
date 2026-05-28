# Micro Runs — Paper §5 Axis Experiments

Configurations for the three-axis ablation in the AlphaDiana paper §5:
**Tool / Memory / Skill** axes evaluated on **AIME 2026** and **GPQA-Diamond**
across **OpenClaw / ZeroClaw** harnesses and two reasoning models.

## Directory layout

```
micro_runs/
├── Tool/                   # Tool axis (clean reasoning prompt, tools available)
├── Memory/
│   ├── intra_task/         # Setting 1: prompt-level memory hint, no persistence
│   ├── cross_sample/       # Setting 2: memory persists across samples of one task (TODO)
│   └── cross_task/         # Setting 3: memory persists across all tasks (TODO)
└── Skill/                  # Skill axis (TODO, owned by Jinbo)
```

Each directory contains 8 cells: `{benchmark}_{harness}_{model}.yaml`
- `benchmark`: `aime2026` | `gpqa`
- `harness`: `openclaw` | `zeroclaw`
- `model`: `qwen35_27b` | `kimi_k26`

## Cells (Tool + Memory/intra_task = 16 cells, runnable now)

| benchmark | harness | model | Tool | Memory/intra_task |
|---|---|---|---|---|
| AIME 2026 | OpenClaw | Qwen3.5-27B | ✅ | ✅ |
| AIME 2026 | OpenClaw | Kimi-K2.6 | ✅ | ✅ |
| AIME 2026 | ZeroClaw | Qwen3.5-27B | ✅ | ✅ |
| AIME 2026 | ZeroClaw | Kimi-K2.6 | ✅ | ✅ |
| GPQA-Diamond | OpenClaw | Qwen3.5-27B | ✅ | ✅ |
| GPQA-Diamond | OpenClaw | Kimi-K2.6 | ✅ | ✅ |
| GPQA-Diamond | ZeroClaw | Qwen3.5-27B | ✅ | ✅ |
| GPQA-Diamond | ZeroClaw | Kimi-K2.6 | ✅ | ✅ |

## Axis definitions

- **Tool** (clean baseline): no memory hint; tools are present and the model may invoke
  them but the system prompt does not nudge it.
- **Memory / intra_task** (prompt-level): the system prompt explicitly nudges the model
  to call `write` (OpenClaw) or `memory_store / memory_recall` (ZeroClaw). Each task
  gets a fresh sandbox; memory does not persist beyond a single rollout.
- **Memory / cross_sample** (TODO): for `num_samples > 1`, sample N inherits the memory
  state of sample N-1 within the same task. Requires pipeline patches to
  `runner.py` and the harness agents.
- **Memory / cross_task** (TODO): memory persists across the entire benchmark run.
  Same pipeline patch as above.
- **Skill** (TODO, Jinbo): see `Skill/README.md`.

## Running a cell

The yaml expects three environment variables to be set by the launcher:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:9091/v1   # local proxy or vLLM endpoint
export OPENAI_API_KEY=sk-EMPTY                    # any non-"EMPTY" string for local
export OPENCLAW_GATEWAY_TOKEN=mytoken             # OpenClaw cells only
```

Then:

```bash
python -m alphadiana.cli run configs/micro_runs/Tool/aime2026_zeroclaw_qwen35_27b.yaml \
  -o run_id=my_test \
  -o output_dir=/tmp/runs/my_test \
  -o max_concurrent=5
```

For Kimi-K2.6 cells, point `OPENAI_BASE_URL` at a `tool_filter_proxy.py` instance that
forwards to OpenRouter. For ZeroClaw + Kimi specifically, the proxy must pass
`--rename-reasoning` so the model's reasoning is preserved through ZeroClaw's
content sanitisation.

See `alphadiana/agent/tool_filter_proxy.py --help` for proxy options.

## Running all cells of an axis

There is no committed launcher script. Use your own orchestration (tmux per cell,
one OpenRouter key per pair of cells to stay under per-key concurrency limits).

A reference launcher is kept locally in `scripts/` (not committed because key
allocation is operator-specific).

## Notes

- All cells default to `temperature=0.7`. AIME cells run `num_samples=4` (pass@4),
  GPQA cells run `num_samples=1` (pass@1).
- All cells have `capture_logprobs: true` and `top_logprobs: 20`.
- `max_concurrent: 5` is conservative; raise it if your provider tolerates more
  per-key concurrency.
- For ZeroClaw + thinking-mode models, route requests through
  `tool_filter_proxy.py --rename-reasoning` to keep the chain-of-thought visible
  in `normalized_trace.json`.
