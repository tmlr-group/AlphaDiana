# Remote Data-Gathering Protocol — AlphaDiana §4 (Macro View)

**Audience:** an autonomous agent running on the machines that hold the raw
AlphaDiana run directories (weikai / jinbo / weikaihuang boxes).
**Purpose:** produce standardized CSVs so the paper machine can reproduce the
§4 main table and every §4 analysis (failure taxonomy, entropy, token
efficiency, action composition) without shipping raw trajectories.

Last updated: 2026-07-22

---

## 0. Context — what the paper machine already has vs. needs

The paper §4 main table (`tab:macro-main-results`) reports:
**3 models** × **4 systems** (Direct + OpenClaw + ZeroClaw + OpenCode) ×
**5 benchmarks** (IMO-AnswerBench, HLE-Verifiable, GPQA-Diamond,
AIME 26 [Pass@4 + Avg@4], MMMU-Pro).

Coverage present on the paper machine today:

| | GPQA | HLE | AIME | IMO | MMMU-Pro |
|---|---|---|---|---|---|
| **Qwen3.5-27B** | OK 4/4 | OK 4/4 | OK 4/4 | 3/4 (OpenCode off-machine) | NONE |
| **Gemma-4-31B** | OK 4/4 | OK 4/4 | OK 4/4 | 1/4 (only Direct) | NONE |
| **Kimi-K2.6** | NONE | NONE | NONE | NONE | NONE |

**Numbers the paper reports but the paper machine cannot currently verify:**

- The **entire Kimi-K2.6 block** (12 cells) — no Kimi data exists locally at all.
- The **entire MMMU-Pro column** (12 cells) — no MMMU-Pro data for any model.
- **Gemma IMO agents** (OpenClaw / OpenCode / ZeroClaw) and
  **Qwen IMO OpenCode** — tracker says DONE, but off-machine.

**§4 analysis CSVs are even thinner** (mostly Qwen-only):

| Analysis / TODO | CSV | Covers | Missing |
|---|---|---|---|
| Failure taxonomy (T2) | `failure_taxonomy.csv` | Qwen; GPQA+HLE | Gemma, Kimi, AIME, IMO, **HLE-OpenClaw** |
| Entropy-length scatter (Fig 7) | `entropy_token_scatter.csv` | Qwen+Gemma; GPQA/HLE/AIME | IMO, Kimi |
| Action composition / chords | `six_action_*`, `action_transition_data.csv` | Qwen + Gemma | Kimi |
| Token efficiency (T3) | `token_count_by_outcome.csv` | 1 setting, task-level | everything else |
| ARM | `arm_macro_unified.csv` | Qwen GPQA+HLE (+Gemma segs) | Kimi, AIME, IMO |

**Priority order for this gathering task:**
1. Kimi-K2.6, all benchmarks (0% present).
2. MMMU-Pro, all models (0% present).
3. Off-machine IMO agents (Gemma OC/OCo/ZC; Qwen OCo).
4. Extend failure / entropy / token analyses to Gemma + Kimi.

---

## 1. Raw run layout (what to expect and verify)

Each run directory has some of:

```
<run_dir>/
  tasks/*.json                              # per-task score + token_usage (DirectLLM)
  logprobs/*.jsonl   or  logprobs_int16/*.jsonl   # per-token logprobs -> entropy
  logprobs/<task>/sample_k.jsonl            # AIME pass@k nested multi-sample
  artifacts/<task>/agent/normalized_trace.json    # steps, tool_calls, tokens (agents)
```

Token-usage source by harness (from `DATA_INVENTORY.md`):

| Harness | Token location | Precision |
|---|---|---|
| DirectLLM | `tasks/*.json` -> `token_usage` / `response_json.usage` | exact |
| OpenCode  | `normalized_trace.json` -> `steps[].part.tokens` (`input/output/reasoning/total`) | exact |
| OpenClaw  | `normalized_trace.json` step text length proxy (~3 chars/token) | estimated |
| ZeroClaw  | `normalized_trace.json` + `token_entropy_stats.n_tokens` (all output = reasoning) | exact via logprobs |

Known hazards to handle:
- AIME multi-sample: top-level `logprobs/aime_X.jsonl` is **sample 0 only**;
  samples 1-3 live in `logprobs/aime_X/sample_k.jsonl`. Qwen OpenClaw/ZeroClaw
  mix flat + nested; Gemma pass@4 is consistently nested.
- Thinking tokens inflate logprob line counts vs `n_tokens` (~15% on some AIME).
- `correct: null` tasks (timeouts / unscored) — count them, do not treat as wrong.

---

## 2. Phase 0 — Inventory & manifest

Search roots (add any others you know):
`/path/to/weikai`, `/path/to/xxx/alphadiana_offload`, `/path/to/jinbo`,
`/home/xxx/projects/422_full/results`,
`/hd1/models/siatmri_alphadiana_results`, and any HuggingFace sync dir.

Emit **`run_manifest.csv`**:

```
model,harness,benchmark,run_path,n_tasks,n_scored,null_count,acc,pass_at_k,
has_logprobs,has_artifacts,trace_format,notes
```

- Report **every Kimi-K2.6 and every MMMU-Pro** directory explicitly.
- If Kimi / MMMU-Pro runs do not exist anywhere, say so in `notes` — the paper
  table numbers would then need a different source.
- `trace_format` = `flat` / `nested` / `mixed` (for logprobs).

## 3. Phase 1 — Canonical per-trajectory table (Tier 1)

For each run, one row per (task, sample) →
**`traj_<model>_<harness>_<bench>.csv`**:

```
model,harness,benchmark,task_id,sample_id,correct,score,
n_tokens,mean_entropy,wall_time_sec,traj_length,n_tool_calls,n_tool_errors
```

- `correct` in {0,1,null}; keep null rows (do not drop).
- `mean_entropy`: mean per-token entropy from logprobs (natural log or match
  existing `imo_answerbench_direct_summary.csv` convention — note which).
- `sample_id`: 0 for single-sample benchmarks; 0..k-1 for AIME pass@k.

This one table lets the paper machine regenerate the **main table**, the
**entropy-length scatter (Fig 7)**, and **token efficiency (T3)** locally.

## 4. Phase 2 — Failure taxonomy (T2) and entropy scatter

Reuse the existing scripts rather than reinventing:

1. Copy `analyze_tools/compute_failure_taxonomy.py` into the remote checkout.
2. Extend its `GPQA_RUNS` / `HLE_RUNS` (and add `AIME_RUNS`, `IMO_RUNS`,
   plus Gemma/Kimi entries) with the paths from Phase 0.
3. Run it; ship back `failure_taxonomy.csv` in the **existing schema**:
   `model,harness,benchmark,failure_mode,n,share_of_failed` (8 modes:
   Reasoning, Tool Selection, Execution, Observation, Recovery, Memory/State,
   Budget, Format/Verifier).
4. Do the same with `extract_entropy_token_scatter.py` (registry `_reg(...)`
   calls) to extend `entropy_token_scatter.csv` to Kimi + IMO.

Keep schemas byte-compatible so the CSVs drop into the existing plotting.

## 5. Phase 3 — Action-level (Tier 2, only if IMO/Kimi process figures wanted)

Per run emit **`steps_<model>_<harness>_<bench>.csv`**:

```
model,harness,benchmark,task_id,sample_id,step_idx,
action_type,is_tool,tool_name,post_tool_token_entropies
```

from `normalized_trace.json` + logprobs. `action_type` ∈ {Understanding,
Planning, Reasoning, Tool Use, Verification, Finalization}. `post_tool_token_entropies`
= JSON list of the per-token entropies for tokens emitted after each tool call.
Feeds action composition, transition chords, and post-tool entropy figures.

## 6. Phase 4 — Ship back

- Package **only CSVs** into `s4_remote_bundle/`:
  `run_manifest.csv`, all `traj_*.csv`, regenerated `failure_taxonomy.csv` /
  `entropy_token_scatter.csv`, and (if Phase 3) `steps_*.csv`.
- **Do not** ship raw trajectories, logprobs, or artifacts.
- Include a short `BUNDLE_NOTES.md`: what was found, what was missing, entropy
  convention used, and any null/timeout counts.

---

## Out of scope for this agent (flag, do not attempt)

- **T1 pass@K IMO scaling** needs **new multi-sample IMO runs** for Gemma +
  Qwen (current IMO runs are single-sample). That is an experiment, not an
  extraction. In `run_manifest.csv`, note whether any multi-sample IMO runs
  already exist so the paper machine can decide.
- **Human study** for the failure taxonomy (T2) is a manual annotation effort,
  not covered here.
