---
sidebar_position: 2
---

# Memory Experiments

Memory is the deliberate counterexample to AlphaDiana's monotonicity thesis. Tool
exposure and skill loading are one-shot on/off edits, but memory adds a *scope*
dimension (how long a memory lives before it is cleared), and widening that scope
makes performance *less* monotonic, not more. These experiments elicit each
harness's **native** memory backend at three increasing scopes and measure the
marginal delta against the no-memory cell.

The mechanisms are intentionally **not** unified across harnesses. Each harness uses
its own backend ([OpenCode](../harnesses/opencode) session + per-task compaction,
[OpenClaw](../harnesses/openclaw) lancedb vector store, [ZeroClaw](../harnesses/zeroclaw)
sqlite + vector). That is the point of harness-aware evaluation: the same "memory on"
knob produces opposite signs because the harness, not the model, determines whether
memory *anchors* behavior or *injects* noise.

All study cells run **Qwen3.5-27B-local** (vLLM) on AIME, scored with `math_verify`.

## The three memory scopes

A memory study cell varies the persistence boundary, i.e. the lifetime of stored
experience before it is cleared. Each scope maps to one experiment and one column of
the paper's memory table.

| Scope | Experiment | Shape | Sampling |
|---|---|---|---|
| **Cross-Sample** | exp2 | Same problem, 4 attempts; memory accumulates across the `pass@4` samples. | avg@4 |
| **Cross-Task** | exp1 | 30 *different* AIME 2026 problems run sequentially; memory accumulates across problems. | avg@4 |
| **Transfer** | exp3 | Build a store on AIME 2025, freeze it, apply it to AIME 2026 with recall-on / store-off. | avg@1 |

The scopes are nested in increasing breadth: a single sample's context is the
smallest unit, within-task (`Cross-Sample`) is wider, and the global across-tasks
store (`Cross-Task`) is widest. `Transfer` is the cross-*year* case where the train
store is frozen before the test phase so test problems never learn from one another.

:::note Memory off ≠ Tool baseline
The no-memory reference keeps each harness's native tools (file / bash / sandbox) but
does **not** load the memory backend and does **not** add the memory-encouraging
system prompt. It is distinct from the Tool axis "filtered" baseline, which strips all
tools. The no-memory cell reuses the matched harness cell from the macro results.
:::

## Results at a glance

The headline is qualitative: **memory is the least monotonic capability.** It helps
only OpenCode at within-run scopes, is net-negative for OpenClaw and ZeroClaw at every
scope, and all three degrade under cross-year Transfer with none reaching the Direct
(raw-LLM) ceiling of **0.892** avg@4.

| Harness | Full (mem off) | Cross-Task | Cross-Sample | Transfer |
|---|---|---|---|---|
| OpenClaw | 0.642 | -0.109 | -0.092 | -0.175 |
| ZeroClaw | 0.667 | -0.067 | -0.125 | -0.267 |
| OpenCode | 0.692 | +0.208 | +0.175 | -0.092 |

Deltas are relative to the harness's own `Full` cell. The raw-LLM Direct baseline
(no agent, no memory) is **0.909** avg@1 / **0.949** pass@4, the ceiling all harness
numbers are measured against.

:::caution Numbers are in flux between artifacts
The paper table reports `Cross-Task` at n=1; an n=4 rerun (the more robust figure)
lowers OpenCode to 0.875 and OpenClaw / ZeroClaw further into the negative.
`Cross-Task` and `Transfer` single-sample cells carry ±0.2 variance, and one open
question (does frozen cross-year memory help or hurt OpenCode?) needs n=4 to settle.
Treat the signs, not the third decimal, as load-bearing.
:::

### What is robust

- **Cross-Sample repetition is the only clean positive effect.** On the *same*
  problem, seeing prior attempts lifts OpenCode (0.733 → 0.933 across attempts s0→s3)
  and OpenClaw (+0.133); ZeroClaw stays flat.
- **Across *different* problems there is no clean positive.** OpenClaw and ZeroClaw
  are slightly negative; OpenCode reaches the raw-LLM ceiling only with same-year
  chaining (≈0.900).
- **OpenCode memory is behavioral anchoring, not knowledge transfer.** The compaction
  summary carries the agent's own prior clean solves as a discipline template, which
  keeps an otherwise-rambling (50-100K char) no-memory agent from derailing.
- **OpenClaw / ZeroClaw memory is retrieval injection.** They surface low-relevance
  (45-46% similarity) snippets and stay below the raw LLM throughout.

## Per-harness memory mechanisms

The backends are harness-native, not a shared framework. There is no runner-level
snapshot/restore layer for memory; each harness wires its own.

### OpenCode — session chain + per-task compaction

OpenCode chains a single `--session` across tasks against a persistent `HOME`
(`opencode.db` sqlite) bind-mounted at `{workdir}/.controller-home`. After each task
it runs the native `/compact`, whose summary (Goal / Discoveries / Accomplished)
carries the agent's prior clean solves forward. Config flags live on the agent
(`alphadiana/harness/opencode/agent.py:765-793`):

| Key | Effect |
|---|---|
| `persistent_memory` | Master on/off. |
| `compact_after_task` | Run native `/compact` after each task. |
| `fresh_session` | Fresh session per task but keep harness prompt injection; isolates a single task's context balloon from poisoning the whole run. |
| `memory_freeze` | Transfer mode: build tasks chain the session; frozen tasks fork from a post-build `HOME` snapshot. |
| `oracle_feedback` | exp3-v2 self-grading reflection turn (see below). |
| `context_limit` / `output_limit` | Declare the token budget so OpenCode's native autocompact fires *proactively* before the provider's hard wall. |

The freeze is host-side: `_snapshot_persistent_home` copies
`{workdir}/.controller-home` to `.frozen-home-snapshot` after build, and
`_restore_persistent_home` restores it before each frozen task, so frozen test
problems fork from train memory but never chain forward or mutate it.

### OpenClaw — lancedb vector store

Memory runs through an embedded `openclaw agent --local` two-turn flow
(`alphadiana/harness/openclaw/agent.py:1904-2010`) so the `memory-lancedb` plugin's
`before_agent_start` autoRecall and `agent_end` hooks actually fire (the
`chat/completions` path never triggers them). Turn 1 recalls and solves; Turn 2 is a
forced `memory_store` that distills one `[fact]` sentence per problem. Plugin config:
`slots.memory=memory-lancedb`, `autoCapture=False`, `autoRecall=True`, embedding
endpoint `:10087`. Recall is injected under a `<relevant-memories>` "untrusted
historical data" guard. The store-turn is skipped when `memory_mode=='frozen'`.

### ZeroClaw — sqlite + vector

ZeroClaw stores keyed `[math]` insights via `memory_store` / `memory_search`. After
solving, `_memory_store_via_agent` (`alphadiana/harness/zeroclaw/agent.py:1549-1638`)
runs a separate `zeroclaw agent -m <store_prompt>` turn capped at 120s, skipped when
`memory_mode=='frozen'`. `_build_memory_section` emits a `[memory]` TOML block
(`backend=sqlite`, `auto_save=true`, `search_mode=hybrid`) only when
`persistent_memory` is set and `memory_embedding.base_url` is configured (embedding
endpoint `:10088`); otherwise recall is FTS-only.

:::warning ZeroClaw self-pollution
ZeroClaw also stores its own system prompt as a `[conversation]` memory and recalls it
back as junk, mixed in with the genuine `[math]` insight. This is a concrete artifact
of letting the model drive native store calls, and a reason its retrieval surfaces too
much noise to help.
:::

## The `memory_mode` build/frozen metadata

Transfer is data-driven, not a CLI flag. The transfer dataset is the `CustomBenchmark`
(`alphadiana/benchmarks/custom/benchmark.py:36`): each `BenchmarkTask` carries
`metadata={"memory_mode": item.get("memory_mode", "build")}` and `ground_truth=answer`.

| `memory_mode` | Behavior |
|---|---|
| `build` (default) | Write **and** recall. The build phase accumulates the store. |
| `frozen` | Recall only; store is skipped. The test phase reads frozen train memory but never mutates it. |

All three harnesses branch on `memory_mode in ('build', 'frozen')` to gate stores, and
read `ground_truth` for oracle feedback.

## oracle-feedback v2 (the four-tuple)

exp3-v1 had two root causes of weak transfer: the build phase stored *unverified*
solutions (wrong-but-confident solves silently poisoned the store), and it stored
non-transferable fragments. The `oracle_feedback` flag (implemented in all three
agents) fixes *what you store*: after solving a `build` task, it runs one extra
same-session reflection turn that reveals `task.ground_truth` and asks the model to
self-grade, then stores a **four-tuple**:

1. the **problem**,
2. **my solution** (the attempt),
3. the **ground truth** (official answer),
4. **feedback / lesson**.

A wrong attempt is stored *labeled wrong* alongside the correct answer, so it becomes a
negative example instead of silent pollution. This is a clean methodological control:
it isolates "what you store" from "what you recall." It does not fix *what you recall*
for OpenClaw / ZeroClaw, whose similarity-based retrieval still surfaces low-relevance
cross-year snippets.

## How to run a memory config

Memory cells are ordinary AlphaDiana run configs invoked through the CLI:

```bash
python -m alphadiana.cli run \
  configs/memory_experiments/exp1_oc_aime_memory_seq.yaml \
  --redo-all
```

The exp1 (`Cross-Task`) and exp2 (`Cross-Sample`) configs exist as
`exp{1,2}_{oc,ow,zw}_aime_memory_{seq,passk}.yaml`. exp2 adds `num_samples: 4`. exp3
(`Transfer`) configs live on the A800 host.

Memory is configured under `agent.config`:

```yaml
agent:
  name: opencode
  config:
    persistent_memory: true        # master on/off, all three harnesses
    # OpenCode-only:
    compact_after_task: true
    fresh_session: false
    memory_freeze: true            # exp3 transfer
    oracle_feedback: true          # exp3-v2 four-tuple
    context_limit: 200000
    output_limit: 32000
    # ZeroClaw:
    memory_embedding:
      base_url: http://...:10088/v1 # omit => FTS-only recall
      model: ...
      search_mode: hybrid
benchmark:
  name: aime                       # MathArena/aime_2026 for Cross-Task / Cross-Sample
  num_samples: 4                   # Cross-Sample only
```

Use `sk-EMPTY` (any non-`"EMPTY"` string) as the API key for local vLLM, and a
distinct `run_id` suffix per experiment (e.g. `exp3-oc-...-v2oracle`) so older data is
preserved. The result store (`alphadiana/analysis/io/result_store.py`) records each
sample; the per-harness in-sandbox tool loop is intentionally not captured there.

## See also

- [OpenCode harness](../harnesses/opencode)
- [OpenClaw harness](../harnesses/openclaw)
- [ZeroClaw harness](../harnesses/zeroclaw)
- [Direct-LLM baseline](../harnesses/direct-llm)
