---
sidebar_position: 3
---

# Evaluation Axes: Tool, Skill, Memory

AlphaDiana measures how a single harness capability changes a model's score by
varying that capability **one at a time** and holding everything else fixed.
There are three axes:

- **Tool** — whether the harness exposes its native tools (file, bash, sandbox).
- **Skill** — whether a skill bundle is loaded into the harness.
- **Memory** — whether the harness keeps a persistent memory store, and at what
  scope.

Each axis is a harness-level on/off knob. The model weights, prompt template,
sampling parameters, benchmark, and scorer stay constant; only the harness
configuration moves. The marginal delta against the matched no-capability cell
is the capability's contribution.

A useful reference point sits above all three axes: the **Direct LLM** baseline
(`direct_llm` harness), the raw model with no agent scaffold. On AIME 2026 it
scores avg@1 = 0.909 (pass@4 = 0.949). Any harnessed cell below that line is
paying an *agent scaffold tax*; the axes isolate which knob pays it down (or
adds to it). See [Harnesses](../harnesses/) for the four harnesses themselves.

## What "off" means per axis

"Off" is defined per axis and the definitions are not interchangeable.

| Axis | "On" | "Off" |
|---|---|---|
| Tool | Harness exposes its native tools; default system prompt | All tools stripped, system prompt replaced — pure chain-of-thought, the lower bound on harness behavior |
| Skill | A skill bundle is mounted and named in the prompt | No skill bundle loaded |
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

Memory is studied at three increasing scopes on Qwen3.5-27B / AIME 2026,
mapping to the three columns of the paper's memory table.

| Scope | What persists | n | Boundary |
|---|---|---|---|
| Cross-Sample | Memory accumulates across the 4 pass@4 attempts of the **same** problem | 4 | Cleared between problems |
| Cross-Task | Memory accumulates across 30 different AIME 2026 problems run sequentially | 1 (paper); n=4 rerun available | Never within the run |
| Transfer | A store built on AIME 2025 is **frozen**, then applied to AIME 2026 with recall-on / store-off | 1 | Frozen between build and test |

The headline finding: memory helps **only** OpenCode at within-run scopes
(Cross-Task +20.8, Cross-Sample +17.5 vs the no-memory cell); it is net-negative
for OpenClaw and ZeroClaw at every scope; and all three degrade under cross-year
Transfer with none reaching the Direct ceiling of 89.2. Cross-Sample is the only
clean positive memory effect in the whole study.

### Mechanisms are harness-native, not a shared framework

There is **no** runner-level snapshot framework. Each harness uses its own
native memory backend, which is the point: the same "memory on" knob produces
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

Transfer is data-driven: each task carries `metadata.memory_mode`, either
`build` (write + recall) or `frozen` (recall only). The default is `build`.

### Running a memory experiment

```bash
python -m alphadiana.cli run \
  configs/memory_experiments/exp1_zw_aime_memory_seq.yaml \
  --redo-all
```

The shipped config is a ZeroClaw Cross-Task run in sqlite FTS mode (no embedding
endpoint needed). It reads five environment variables: `OPENAI_MODEL_NAME`,
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

The remaining Cross-Task and Cross-Sample experiment configs are not shipped in
the repository. The design they encode is small: a Cross-Sample config differs
from a Cross-Task config by the sample count (`num_samples: 4`, a top-level key
rather than a `benchmark.config` one) and by its system prompt. The ZeroClaw
Cross-Sample prompt mandates a `memory_search` before and a `memory_store` after
every problem; its Cross-Task counterpart carries no such instruction, because
the harness appends its own "use memory_search" line once at least one store turn
has run. That gate uses `_has_memories` in
`alphadiana/harness/zeroclaw/agent.py` and counts store turns that exited 0, not an
inspection of the store, so it can fire even if the model never actually called
`memory_store`.

### Reading the results

Memory results land through the result store at
`alphadiana/analysis/io/result_store.py`; the run loop lives under
`alphadiana/engine/` (`alphadiana/engine/runner.py`). The plots and the scope
ladder are regenerated from the recorded results.

When reading a single-sample cell, treat Cross-Task and Transfer as n=1: their
variance is roughly +/-0.2, so per-cell gaps should be read as directional, not
precise. Cross-Sample (n=4) and the no-memory Full cell (avg@4) are the more
robust comparisons.
