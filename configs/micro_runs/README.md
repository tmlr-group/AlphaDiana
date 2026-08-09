# Micro runs

Runnable configurations related to the AlphaDiana paper's **Tool**, **Skill**,
and **Memory** micro studies. Coverage is intentionally not presented as
uniform: Tool and Skill contain partial experiment cells, while Memory contains
the complete release reference matrix.

## Coverage status

| Axis | Release status | What is complete |
| --- | --- | --- |
| Tool | Partial | Runnable cells are preserved, but they are not evidence of a complete paper matrix |
| Skill | Partial | Two mechanism/smoke cells only; no matched three-harness comparison |
| Memory | Complete reference matrix | AIME 2026 × Qwen3.5-27B × 3 harnesses × 3 scopes = 9 cells |

Do not infer paper coverage from the number of YAML files. A checked-in config
proves that a launch definition exists, not that the corresponding paper cell
was executed, audited, or reported.

## Directory layout

```
micro_runs/
├── Tool/                   # Partial Tool cells; see Tool/README.md
├── Memory/
│   ├── intra_task/         # Native memory is isolated to one work item
│   ├── cross_sample/       # Samples of one task share state; tasks are isolated
│   └── cross_task/         # All work items in the run share state
└── Skill/                  # Partial Skill cells; see Skill/README.md
```

The nine canonical Memory reference cells use
`aime2026_{openclaw|opencode|zeroclaw}_qwen35_27b.yaml`, one per memory scope.
Six additional Kimi/GPQA files under `Memory/intra_task/` are supplemental
historical launch definitions. They are not part of the complete 9-cell
reference matrix and are not a second complete matrix.

## Memory reference cells

| Harness | Intra-Task | Cross-Sample | Cross-Task |
|---|---:|---:|---:|
| OpenClaw | ✅ | ✅ | ✅ |
| OpenCode | ✅ | ✅ | ✅ |
| ZeroClaw | ✅ | ✅ | ✅ |

## Axis definitions

- **Tool** (partial coverage): no memory hint; tools are present and the model may
  invoke them but the system prompt does not nudge it. See `Tool/README.md`.
- **Memory / intra_task**: `persistent_memory: false`; native memory is enabled
  but its store is confined to one `(task, sample)` work item.
- **Memory / cross_sample**: `persistent_memory: true`; work items run in task-major
  order and the runner rebuilds the harness and sandbox when the task ID changes.
- **Memory / cross_task**: `persistent_memory: true`; the harness and sandbox remain
  live for the complete sequential run.
- **Skill** (partial coverage): see `Skill/README.md`.

Every memory reference config declares `agent.config.memory_scope`. Stateful
scopes are forced to effective concurrency 1 even if a caller supplies a larger
value, reject task retries, and stop after the first failed work item. A partial
Cross-Task run and a partially sampled Cross-Sample task cannot
reconstruct earlier native memory, so the runner requires a new `run_id` or
`--redo-all`; Cross-Sample may resume only at a complete task boundary.
`memory_enabled: true` activates the native path and `strict_memory: true`
invalidates a work item if the harness cannot verify its memory operation.

## Running a cell

Build or pull the three harness images on the execution host:

```bash
docker build --network host \
  -f alphadiana/benchmarks/terminal_bench2/deploy/dockerfiles/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
docker pull tmlrgroup/alphadiana:v1
docker build --network host \
  -f alphadiana/harness/zeroclaw/deploy/Dockerfile \
  -t zeroclaw-reasoning:0.6.9 .
```

Start ROCK before OpenClaw or ZeroClaw cells, then load the generated URLs into
the current shell:

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
python -m alphadiana.cli env
```

The YAML expects provider variables to be set by the launcher:

```bash
export OPENAI_BASE_URL=http://HOST_REACHABLE_FROM_SANDBOX:9091/v1
export OPENAI_API_KEY=sk-EMPTY                    # any non-"EMPTY" string for local
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export MEMORY_EMBEDDING_BASE_URL=http://HOST_REACHABLE_FROM_SANDBOX:10087/v1
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

Representative Tool and Skill smokes use the same environment:

```bash
python -m alphadiana.cli run \
  configs/micro_runs/Tool/aime2026_zeroclaw_qwen35_27b.yaml \
  -o run_id=smoke_micro_tool_zeroclaw \
  -o benchmark.config.max_tasks=1 -o num_samples=1 --redo-all

python -m alphadiana.cli run \
  configs/micro_runs/Skill/aime2026_zeroclaw_qwen35_27b_skill_math.yaml \
  -o run_id=smoke_micro_skill_zeroclaw \
  -o benchmark.config.max_tasks=1 -o num_samples=1 --redo-all
```

Validate every micro YAML on the execution host before launching:

```bash
find configs/micro_runs -name '*.yaml' -print0 | sort -z | \
  while IFS= read -r -d '' config; do
    python -m alphadiana.cli validate "$config" || exit 1
  done
```

For Kimi-K2.6 cells, point `OPENAI_BASE_URL` at a `tool_filter_proxy.py` instance that
forwards to OpenRouter. For ZeroClaw + Kimi specifically, the proxy must pass
`--rename-reasoning` so the model's reasoning is preserved through ZeroClaw's
content sanitisation.

See `python -m alphadiana.harness.proxies.tool_filter_proxy --help` for proxy options.

## Notes

- All nine reference cells use `temperature=0.0`, `max_tokens=32768`, and
  `num_samples=4`.
- OpenClaw persistent memory requires a compatible embedding endpoint for its
  LanceDB plugin. ZeroClaw reference cells use sqlite FTS and need no embedding
  endpoint.
- Keep stateful scopes sequential. Parallelizing them changes the intervention.
- Cross-Sample and Cross-Task samples are dependent. Reports label their
  aggregate as `Sequential Any@k` / `Sequential Mean@k`, not standard pass@k.
- For ZeroClaw + thinking-mode models, route requests through
  `tool_filter_proxy.py --rename-reasoning` to keep the chain-of-thought visible
  in `normalized_trace.json`.
