# Micro Runs — Paper §5 Axis Experiments

Configurations for the three-axis ablation in the AlphaDiana paper §5:
**Tool / Memory / Skill** axes evaluated on **AIME 2026** and **GPQA-Diamond**.
The release memory seed covers **OpenClaw / OpenCode / ZeroClaw** with
Qwen3.5-27B; expand it to other benchmarks and models only after validating the
seed on the target infrastructure.

## Directory layout

```
micro_runs/
├── Tool/                   # Tool axis (clean reasoning prompt, tools available)
├── Memory/
│   ├── intra_task/         # Native memory is isolated to one work item
│   ├── cross_sample/       # Samples of one task share state; tasks are isolated
│   └── cross_task/         # All work items in the run share state
└── Skill/                  # Skill axis
```

The nine release reference cells use
`aime2026_{openclaw|opencode|zeroclaw}_qwen35_27b.yaml`, one per memory scope.
Historical data may contain a larger 36-cell matrix; those files are not proof
that the current release can reproduce every provider/model combination.

## Memory reference cells

| Harness | Intra-Task | Cross-Sample | Cross-Task |
|---|---:|---:|---:|
| OpenClaw | ✅ | ✅ | ✅ |
| OpenCode | ✅ | ✅ | ✅ |
| ZeroClaw | ✅ | ✅ | ✅ |

## Axis definitions

- **Tool** (clean baseline): no memory hint; tools are present and the model may invoke
  them but the system prompt does not nudge it.
- **Memory / intra_task**: `persistent_memory: false`; native scratch state is
  confined to one `(task, sample)` work item.
- **Memory / cross_sample**: `persistent_memory: true`; work items run in task-major
  order and the runner rebuilds the harness and sandbox when the task ID changes.
- **Memory / cross_task**: `persistent_memory: true`; the harness and sandbox remain
  live for the complete sequential run.
- **Skill**: see `Skill/README.md`.

Every memory reference config declares `agent.config.memory_scope`. Stateful
scopes are forced to effective concurrency 1 even if a caller supplies a larger
value. A partial Cross-Task run and a partially sampled Cross-Sample task cannot
reconstruct earlier native memory, so the runner requires a new `run_id` or
`--redo-all`; Cross-Sample may resume only at a complete task boundary.

## Running a cell

The YAML expects provider variables to be set by the launcher:

```bash
export OPENAI_BASE_URL=http://HOST_REACHABLE_FROM_SANDBOX:9091/v1
export OPENAI_API_KEY=sk-EMPTY                    # any non-"EMPTY" string for local
export OPENCLAW_GATEWAY_TOKEN=mytoken             # OpenClaw cells only
export MEMORY_EMBEDDING_BASE_URL=http://127.0.0.1:10087/v1  # OpenClaw memory cells
export MEMORY_EMBEDDING_MODEL=qwen3-embed-0.6b             # OpenClaw memory cells
export MEMORY_EMBEDDING_API_KEY=sk-EMPTY                   # OpenClaw memory cells
```

For ROCK-backed OpenClaw and ZeroClaw, `OPENAI_BASE_URL` (and the OpenClaw
embedding URL) must be reachable from inside the sandbox. A host-loopback URL
such as `127.0.0.1` may point back at the sandbox rather than the model host;
use the host address reported by ROCK or an equivalent routable endpoint.

Then:

```bash
python -m alphadiana.cli run \
  configs/micro_runs/Memory/cross_sample/aime2026_zeroclaw_qwen35_27b.yaml \
  -o run_id=my_memory_smoke \
  -o output_dir=/tmp/runs/my_memory_smoke \
  -o benchmark.config.max_tasks=1 -o num_samples=2 --redo-all
```

For Kimi-K2.6 cells, point `OPENAI_BASE_URL` at a `tool_filter_proxy.py` instance that
forwards to OpenRouter. For ZeroClaw + Kimi specifically, the proxy must pass
`--rename-reasoning` so the model's reasoning is preserved through ZeroClaw's
content sanitisation.

See `python -m alphadiana.harness.proxies.tool_filter_proxy --help` for proxy options.

## Notes

- The reference cells use `temperature=0.0`; cross-sample uses `num_samples=4`,
  while cross-task uses one sample per task.
- OpenClaw persistent memory requires a compatible embedding endpoint for its
  LanceDB plugin. ZeroClaw reference cells use sqlite FTS and need no embedding
  endpoint.
- Keep stateful scopes sequential. Parallelizing them changes the intervention.
- For ZeroClaw + thinking-mode models, route requests through
  `tool_filter_proxy.py --rename-reasoning` to keep the chain-of-thought visible
  in `normalized_trace.json`.
