---
sidebar_position: 1
---

# Experiments

AlphaDiana's micro-studies isolate a single harness capability and measure its
*marginal* effect. The recipe is always the same: take a matched **Full-Harness**
cell (the harness running with all its defaults), toggle exactly one capability,
and report the delta. Because the model, benchmark, and scaffold are held fixed,
the delta is attributable to that one capability rather than to harness identity.

There are three capability axes:

| Axis | Knob | Where it is configured |
|---|---|---|
| **Tool** | expose vs filter the harness's tool surface | `agent.config` tool-filter settings on each harness (the tool-filter proxy `alphadiana/harness/proxies/tool_filter_proxy.py`) |
| **Skill** | load vs omit a skill folder | `agent.config.skill_folder` (a directory of skill files staged into the sandbox) |
| **Memory** | persist vs forget, plus a *scope* dimension | `agent.config.persistent_memory` and per-harness memory keys (see below) |

Tool and Skill are one-shot on/off edits: the capability is either present in the
sandbox or it is not. Memory is different. It adds a **scope** dimension (how long
state survives before it is cleared), which makes it the least monotonic axis and
the most interesting to study on its own.

For the full Memory micro-study, see **[Memory experiments](./memory-experiments)**.

## Methodology

Each micro-cell is a standard AlphaDiana run (`python -m alphadiana.cli run <config>`).
The baseline for every axis is the matched **Full** cell, the harness with no
capability edit, scored on the same benchmark and model. Results are persisted to the
result store (`alphadiana/analysis/io/result_store.py`), and the plotting scripts
under `super-exp/` compute deltas from the exported run jsonls/tsvs
(e.g. `super-exp/exp2_data/*.jsonl`) rather than from `result_store.py` directly.

A capability is judged by its sign and magnitude relative to that baseline:

- A positive delta means the capability earns its scaffold cost.
- A negative delta means it is a net tax. The gap between a no-capability harness
  cell and the raw `directllm` (no-agent, no-memory) ceiling is the **agent
  scaffold tax**.

## Memory in brief

The Memory micro-study is run on **Qwen3.5-27B** over **AIME 2026**, at three
increasing scopes, against the matched no-memory Full cell. Crucially, each
harness uses its **own native memory backend** rather than a shared framework:

| Harness | Backend | Sign of effect |
|---|---|---|
| [OpenCode](../harnesses/opencode) | session chain + per-task `/compact` (sqlite HOME) | positive at within-run scopes (behavioral anchoring) |
| [OpenClaw](../harnesses/openclaw) | `memory-lancedb` vector plugin | net negative at every scope |
| [ZeroClaw](../harnesses/zeroclaw) | sqlite + vector (`memory_store` / `memory_search`) | net negative at every scope |

The same "memory on" knob produces opposite signs because the harness, not the
model, decides whether memory *anchors* (OpenCode) or *injects noise*
(OpenClaw / ZeroClaw). The three scopes and their full results are covered on the
[Memory experiments](./memory-experiments) page.

```bash
# Run a memory micro-cell (Cross-Task, OpenCode, AIME 2026)
python -m alphadiana.cli run \
  configs/memory_experiments/exp1_oc_aime_memory_seq.yaml --redo-all
```

The master on/off switch is one config key shared by all three harnesses:

```yaml
agent:
  config:
    persistent_memory: true
```

Per-harness keys (e.g. `compact_after_task`, `memory_freeze`, `oracle_feedback`
for OpenCode; `memory_embedding.*` for ZeroClaw; `memory_lancedb.*` for OpenClaw)
are documented on the [Memory experiments](./memory-experiments) page.
