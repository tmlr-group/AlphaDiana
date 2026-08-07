---
sidebar_position: 3
---

# Evaluation Axes: Tool, Skill, Memory

AlphaDiana can compare matched harness conditions that change tool exposure,
skill loading, or memory behavior. These are useful controlled comparisons, but
they are not always literal one-variable interventions: disabling tools can also
replace prompt text, and enabling a skill or memory mode can add instructions as
well as runtime state.
There are three axes:

- **Tool** — whether the harness exposes its native tools (file, bash, sandbox).
- **Skill** — whether a skill bundle is loaded into the harness.
- **Memory** — whether the harness keeps a persistent memory store, and at what
  scope.

For a defensible comparison, keep the model, sampling parameters, benchmark,
scorer, and unrelated harness settings fixed, then document the complete
condition bundle that changed. Treat the score delta as an association with that
bundle, not as an isolated causal contribution from a single capability.

A useful reference point is the **Direct LLM** baseline (`direct_llm` harness),
the model without an agent scaffold. A harnessed cell below a matched DirectLLM
cell may be described as an *agent scaffold tax*, provided both cells use the
same model and evaluation protocol. This checkout does not contain the raw
result artifacts needed to substantiate a particular headline delta; use the
result store from the run being reported. See [Harnesses](../harnesses/) for the
four harnesses themselves.

## What "off" means per axis

"Off" is defined per axis and the definitions are not interchangeable.

| Axis | "On" | "Off" |
|---|---|---|
| Tool | Harness exposes its native tools and uses the corresponding prompt condition | Tools are filtered and the system/user prompt condition may also be rewritten |
| Skill | A skill bundle is mounted and named in the prompt | No skill bundle is loaded and the skill-introduction prompt is absent |
| Memory | Harness's native memory backend is enabled (`persistent_memory: true`) | Harness defaults with no memory store and no memory-encouraging prompt |

Note that **Tool-off** and **Memory-off** are not the same configuration.
Tool-off strips every tool and rewrites the system prompt — it is the floor on
what the harness can do. Memory-off keeps the harness's native tools and is the
correct baseline for measuring memory's contribution. Conversely **Tool-on**
(harness default) and **Memory-off** are the same data point reported under two
names for table self-containment.

For the Skill axis mechanics (skill bundles mounted via `agent.config.skill_folder`),
see the [Harnesses overview](../harnesses/). The rest
of this page covers the Memory axis, the only axis that adds a second dimension
(scope) on top of on/off.

## The Memory axis

Tool exposure and skill loading are one-shot on/off edits. Memory adds a
**scope** dimension: how long the store survives before it is cleared. Widening
scope makes memory *less* monotonic, not more, which is why it is the
deliberate counterexample to the framework's monotonicity thesis.

### Scopes

The release reference matrix implements three persistence scopes across
OpenClaw, OpenCode, and ZeroClaw.

| Scope | What persists | n | Boundary |
|---|---|---|---|
| Intra-Task | Native scratch/memory state exists only inside one solve | one work item | Fresh state for every `(task, sample)` |
| Cross-Sample | Memory accumulates across samples of the same problem | multiple | Runner recreates the harness and sandbox when the task ID changes |
| Cross-Task | Memory accumulates across different problems | configurable | Harness and sandbox remain live for the sequential run |

Static paper figures may illustrate proposed or previously reported comparisons,
but they are not current support evidence. Do not quote exact memory deltas from
this page without the corresponding run IDs and task-level artifacts.

### Mechanisms are harness-native, not a shared framework

There is **no** runner-level snapshot framework. The runner owns only the
scope boundary and each harness uses its own native memory backend. The same
"memory on" knob can therefore produce
opposite signs because the harness, not the model, decides whether memory
anchors behavior (OpenCode) or injects low-relevance noise (OpenClaw,
ZeroClaw).

| Harness | Backend | Mechanism |
|---|---|---|
| [OpenCode](../harnesses/opencode) | OpenCode session chain + per-task `/compact`; persistent HOME (`opencode.db` sqlite) bind-mounted at `{workdir}/.controller-home` | Compaction summaries carry the agent's own prior clean solves as a behavioral template — anchoring, not knowledge transfer |
| [OpenClaw](../harnesses/openclaw) | `memory-lancedb` vector plugin, backed by an OpenAI-compatible embedding endpoint (for example a local vLLM serving an embedding model) | One distilled `[fact]` sentence per problem via a forced store-turn; recall injected under a `<relevant-memories>` "untrusted historical data" guard |
| [ZeroClaw](../harnesses/zeroclaw) | sqlite + vector (embeddings via an OpenAI-compatible endpoint), `memory_store` / `memory_search` | Keyed `[math]` insights; self-pollutes by also storing its own system prompt as a `[conversation]` memory and recalling it as junk |

### Configuration

Memory is enabled per harness through `agent.config`. The master switch is the
same across all three harnesses; the remaining keys are harness-specific.

| Key | Harness | Purpose |
|---|---|---|
| `memory_scope` | all three | `intra_task`, `cross_sample`, or `cross_task`; stateful scopes are sequential |
| `persistent_memory` | all three | Master memory on/off |
| `compact_after_task` | OpenCode | Run `/compact` after each task |
| `fresh_session` | OpenCode | Fresh session per task; fills and injects the harness memory bank instead of chaining sessions |
| `memory_freeze` | OpenCode | Transfer mode: frozen tasks fork from a post-build HOME snapshot |
| `oracle_feedback` | all three | Post-solve reflection turn that reveals `ground_truth` (oracle-feedback v2) |
| `context_limit` / `output_limit` | OpenCode | Declare token budget so OpenCode's native autocompact fires before the provider's hard wall |
| `memory_embedding.{base_url, model, dimensions, search_mode}` | ZeroClaw | Vector recall; omit `base_url` to fall back to FTS-only |
| `memory_lancedb.{api_key, model, base_url, dimensions, db_path, auto_capture, auto_recall}` | OpenClaw | Flat keys in one dict: embedding endpoint (api_key, model, base_url, dimensions) and the LanceDB store path. `auto_capture` / `auto_recall` are read on the gateway path only; the local-agent path hardcodes `autoCapture: false` and `autoRecall: true` |

OpenCode's flags are parsed by `OpenCodeAgent.setup()` in
`alphadiana/harness/opencode/agent.py`. ZeroClaw's memory store-turn is
`_memory_store_via_agent` in `alphadiana/harness/zeroclaw/agent.py`, which skips the write when a task
carries `metadata['memory_mode'] == 'frozen'`. OpenClaw runs memory through an
embedded `openclaw agent --local` two-turn flow
(`alphadiana/harness/openclaw/agent.py`) so the `memory-lancedb` plugin's
`autoRecall` / `autoCapture` hooks fire; the chat/completions path never
triggers them.

The separate transfer protocol is data-driven: each task carries `metadata.memory_mode`, either
`build` (write + recall) or `frozen` (recall only). The default is `build`.

### Running a memory experiment

```bash
python -m alphadiana.cli run \
  configs/micro_runs/Memory/cross_task/aime2026_zeroclaw_qwen35_27b.yaml \
  --redo-all
```

The ZeroClaw Cross-Task reference uses sqlite FTS mode (no embedding endpoint
needed). It reads provider and ROCK environment variables including `OPENAI_MODEL_NAME`,
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `ROCK_BASE_URL`, and `ROCK_PROXY_URL`. An
unset one is blanked rather than reported. The three `OPENAI_*` are named back to
you when the run dies, but an unset `ROCK_*` fails opaquely: the runner quietly
falls back to a default localhost ROCK port and you get a connection error.

The config also pins `rock_image: zeroclaw-reasoning:0.6.9`, which makes the
runner auto-create a ROCK sandbox, so the ROCK mode prerequisites on the
[ZeroClaw](../harnesses/zeroclaw) page apply first: build that image locally (it
is not published on Docker Hub), then start the host ROCK services with
`bash scripts/start_zeroclaw.sh` and export their URLs with
`source scripts/rock_env.sh`. Both scripts need the `ref/ROCK` checkout that the
one-time setup in [Installation](../getting-started/installation) creates, and
abort without it, which is what leaves the two `ROCK_*` variables unset.

Cross-Sample is not just `num_samples > 1`: `memory_scope: cross_sample` also
enforces sequential dispatch and explicitly rebuilds harness/sandbox state at
the task boundary. The ZeroClaw gate
`_has_memories` counts successful store turns rather than inspecting the store,
so it can fire even if the model never actually called `memory_store`.

### Reading the results

Memory results land through the result store at
`alphadiana/analysis/io/result_store.py`; the run loop lives under
`alphadiana/engine/` (`alphadiana/engine/runner.py`). The plots and the scope
ladder are regenerated from the recorded results.

Report the configured sample count, run IDs, and uncertainty with every result.
Do not infer precision or robustness from an intended design when the matching
artifacts are not present in the checkout.
