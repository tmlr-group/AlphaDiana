# Section 4 Failure-Mode Data: Status and Round-2 Gather Requests

**Bundle analyzed:** `analyze_tools/data/alphadiana_results_copy_failure_modes_20260723T072005Z.zip`
**Date:** 2026-07-23
**For:** the remote data-gathering agent (companion to `REMOTE_GATHER_PROTOCOL.md`)

This documents (1) what the bundle delivered, (2) how its accuracies reconcile with
the paper's main table `tab:macro-main-results`, (3) what I can now insert into
Section 4, and (4) the **precise gaps the next gather must fill**. Skip to
[Round-2 requests](#round-2-gather-requests) for the action list.

---

## 1. Coverage received

Two aggregations arrived: `failure_modes_alphadiana_results_copy/` (per-record +
summary) and `six_action_statistics_alphadiana_results_copy/` (action / entropy /
token / tool stats). Model is encoded in `run_id` (`kimi_k26`, `qwen35_27b`, `vision`).

| Model | AIME | GPQA | HLE | IMO | MMMU-Pro |
|---|:--:|:--:|:--:|:--:|:--:|
| **Kimi-K2.6** | ✅ 4 | ✅ 4 | ✅ 4 | ✅ 4 | ✅ 4 |
| **Qwen3.5-27B** | — (local) | — (local) | — (local) | ✅ 3 (merged) | ✅ 3 + Direct |
| **Gemma-4-31B-IT** | 🟢 4 (local) | 🟢 4 (local) | 🟢 4 (local) | ⚠️ Direct only | 🟡 4 acc / taxonomy pending |

`✅ n` = n of {Direct, OpenClaw, OpenCode, ZeroClaw} present in the bundle.
"local" = already on this machine, not from the bundle: Qwen GPQA/HLE/AIME are in
`data/failure_taxonomy.csv`; **Gemma** raw runs are on this machine and I processed
them locally with `gather_failure_modes.py` (GPQA/HLE/AIME, all four harnesses;
accuracies match `tab:macro-main-results` exactly). Gemma IMO has only Direct, and
Gemma MMMU-Pro Direct is a partial 455-record run.

**Net:** the bundle completes **all Kimi-K2.6** and the **MMMU-Pro column**. Combined
with local data, the only real remaining gaps are **Gemma-4-31B-IT on IMO and
MMMU-Pro (harnesses)** and the **Pass@k/Avg@k sweep**.

---

## 2. Reconciliation against `tab:macro-main-results`

I recomputed accuracy per (benchmark, model, harness) from
`failure_modes_by_record.csv` two ways: `acc_all` (operational failures counted
wrong) and `acc_exclUnk` (operational failures excluded). Comparing to the numbers
already in the paper table:

| Cell | Paper table | acc_all | acc_exclUnk | Verdict |
|---|--:|--:|--:|---|
| AIME Kimi (all 4) | 85.8 / 72.5 / 86.7 / 93.3 | **match** | — | ✅ uses acc_all |
| GPQA Kimi Direct / OCo / ZC | 77.8 / 80.8 / 87.4 | **match** | — | ✅ |
| GPQA Kimi **OpenClaw** | **31.8** | 41.4 | 42.9 | ❌ neither (scorer) |
| HLE Kimi Direct | 35.9 | 35.7 | 35.9 | ✅ exclUnk |
| HLE Kimi **OpenClaw** | 40.7 | 17.1 | **40.7** | ⚠️ excludes unknown |
| HLE Kimi **OpenCode** | 33.9 | 28.3 | 28.3 | ❌ neither (run/scorer) |
| HLE Kimi **ZeroClaw** | 33.7 | 27.6 | 31.6 | ❌ neither |
| IMO Kimi Direct / OC / OCo | 42.0 / 27.3 / 48.5 | **match** | — | ✅ acc_all |
| IMO Kimi **ZeroClaw** | 38.7 | 22.5 | 43.5 | ❌ neither (193 crashes) |
| MMMU Kimi (all 4) | 75.1 / 48.6 / 71.3 / 64.7 | **match** | — | ✅ acc_all |
| MMMU Qwen (OC/OCo/ZC + Direct) | 68.3 / 69.4 / 67.2 / 73.4 | **match** | — | ✅ acc_all |

**Two problems this exposes:**

- **Inconsistent denominator in the published table.** MMMU-Pro and AIME count
  operational failures as wrong (`acc_all`); HLE-OpenClaw only matches if they are
  **excluded**. The table currently mixes conventions across benchmarks. We must
  pick **one** (recommend `acc_all` = deployable accuracy, i.e. a crash is a wrong
  answer) and re-tabulate, or state the convention per row.
- **Scorer mismatch on a few cells.** GPQA-OpenClaw, HLE-OpenCode, HLE-ZeroClaw,
  IMO-ZeroClaw do not match under either convention. The gather's `correct` flag
  and the paper's official scorer disagree (GPQA-OpenClaw failures are 93%
  Format/Verifier, so the strict scorer likely rejects ~19 malformed answers the
  gather counts as correct). These cells need re-scoring with the paper scorer.

Everything else (AIME, MMMU both models, most GPQA/IMO Kimi) reconciles exactly,
so these **are** the paper's runs.

---

## 3. What is ready to insert into Section 4 now

Grounded and usable immediately (pending only the denominator decision):

- **Failure-mode taxonomy rows for Kimi-K2.6** on all 5 benchmarks, and for
  Qwen-MMMU, to extend `tab:failure-taxonomy`. Headline shares (share of *failed*
  trajectories):
  - GPQA OpenClaw Kimi: **93% Format/Verifier** (mirrors Qwen: harness breaks the
    commit/format step, not reasoning).
  - GPQA Direct **Kimi**: **82% Format/Verifier** — unlike Qwen Direct (100%
    Reasoning), Kimi even unharnessed fails GPQA mostly on answer format. Worth a sentence.
  - HLE OpenCode Kimi: 36% Format, 35% Reasoning, 8% Budget, 5% Tool Selection.
  - MMMU-Pro (both models): failures dominated by Format/Verifier (44–69%) plus
    Tool Selection under OpenCode-Qwen (38%).
- **Operational tax** is now dramatic and supports `find:operational-tax`:
  - HLE OpenClaw Kimi: **343 / 591 (58%)** runs never scored (mostly Budget).
  - IMO ZeroClaw Kimi: **193 / 400 (48%)** runtime crashes.
  - MMMU-Pro OpenClaw Kimi: **663 unknown (38%)**; ZeroClaw 262 (15%).
- **Token-on-failure** confirms the "confident long-tail failure" story across
  models: e.g. MMMU-Pro OpenClaw wrong ≈ 4348 tokens vs correct ≈ 1183; Direct
  MMMU wrong ≈ 21596 vs correct ≈ 5763.

I have **not** edited the paper with these yet, because the table would still be
missing Gemma and would inherit the denominator inconsistency above. Say the word
and I will add the Kimi + Qwen-MMMU rows under whichever convention you choose.

---

## 4. Round-2 gather requests

Ordered by priority. Please emit in the **same schemas** as this bundle
(`failure_mode_summary.csv`, `failure_modes_by_record.csv`, and the
`six_action_statistics_*` set), plus a short `BUNDLE_NOTES.md`.

### G1 — Gemma-4-31B-IT: IMO runs + MMMU-Pro failure taxonomy (revised again)
Gemma GPQA/HLE/AIME are done locally and in the paper figure. The
`Gemma-4-31B-IT_remote_bundle.zip` (unzipped to `analyze_tools/gemma_4_31b_it_remote_bundle/`)
then closed the **MMMU-Pro accuracy** gap: all four harnesses match
`tab:macro-main-results` exactly (Direct 65.8, OpenClaw 56.8, OpenCode 67.4,
ZeroClaw 66.4; total-task / crash-as-wrong denominator, confirming G4). Two things
still block the figure's Gemma facets:

1. **Gemma MMMU-Pro failure taxonomy.** That bundle ships a *header-only*
   `failure_taxonomy.csv`; its standardized `traj_*.csv` carry only
   `correct, score, n_tokens, mean_entropy, traj_length, n_tool_calls, n_tool_errors`,
   which lack the `score_status` / `predicted` / trajectory fields the classifier
   needs for the Format, Budget, Tool-Selection, and Execution branches. **Please run
   `gather_failure_modes.py` on the raw HF snapshot
   `alphadiana-mmmu-pro-gemma4-four-harness-20260624`** (you have it; it is not on
   the paper machine) and return `failure_mode_summary.csv` +
   `failure_modes_by_record.csv`, same schema as the first bundle.
2. **Gemma IMO-AnswerBench × {OpenClaw, OpenCode, ZeroClaw}** — only Direct is local.
   Search for `*imo*gemma4_31b*` harness runs; if never run, say so.

### G2 — IMO Pass@k / Avg@k sweep for Gemma and Qwen (Section 4 `find:scaling`)
The scaling finding needs a **multi-sample** IMO-AnswerBench sweep for
**Gemma-4-31B-IT and Qwen3.5-27B**: k ≥ 4 samples per problem (ideally k ∈ {1,2,4,8})
with **per-sample correctness preserved**, so Pass@k and Avg@k can both be computed.
Current IMO data is single-sample (Kimi) or merged (Qwen) with no k-axis. Emit one
row per (task_id, sample_index, correct). If these runs do not exist, say so — they
may need to be launched (this was previously flagged out-of-scope; confirm with the operator).

### G3 — Re-score the mismatched cells with the paper's official scorer
For **GPQA-OpenClaw-Kimi, HLE-OpenCode-Kimi, HLE-ZeroClaw-Kimi, IMO-ZeroClaw-Kimi**:
identify the canonical run-id used for the paper table and re-emit accuracy using the
**paper's exact-match scorer** (not the loader's `correct` flag), reporting both
`valid_scored` count and the scored-correct count. Goal: explain the 31.8 vs 41.4
(GPQA-OpenClaw) and the HLE gaps.

### G4 — Confirm the accuracy denominator convention (decision needed from operator)
State, per the paper's intended definition, whether operational failures
(`runtime_error`, `provider_error`, `agent_error`, budget `unknown`) count as **wrong**
(`acc_all`, recommended) or are **excluded**. Then re-tabulate every cell uniformly.
The current table mixes both (see §2).

### G5 — Human-validation sample for auto-labeled failure modes (`find:failure-mode`, TODO item 2)
Emit a **stratified random sample** (e.g. 10–15 trajectories per
benchmark × harness × failure_mode, capped) with `task_id`, the assigned
`failure_mode`, `failure_reason`, and a short trajectory excerpt, as a CSV ready for
human coding. This lets us report human–auto agreement (e.g. Cohen's κ) on the
taxonomy, which reviewers will expect.

### G6 — (minor) Split MMMU-Pro by model in the six-action report
`six_action_statistics_report.md` groups by (benchmark, harness) only, so MMMU-Pro
merges Qwen + Kimi under each harness (e.g. "MMMU-Pro DirectLLM" N=3460 = both).
The per-record CSV keeps `run_id`, so this is recoverable, but please add `model`
to the group key when regenerating so the summary tables are model-separated.

---

## 5b. Naming + multi-label update (decided 2026-07-23)

Two changes to the failure taxonomy:

1. **Renamed to Error-phrased labels** (more direct): `Reasoning -> Reasoning Error`,
   `Tool Selection -> Tool Misuse`, `Format/Verifier -> Format Error`,
   `Budget -> Budget Exhaustion`, `Other -> Execution/State Error`. The paper caption
   and prose already use these; the figure colors are unchanged.

2. **Multi-label per trajectory.** A single trajectory may now carry several failure
   modes (e.g. `{Budget Exhaustion, Format Error}`): one "output" failure (Format
   Error if no valid extractable answer, else Reasoning Error) plus zero or more
   "process" failures (Budget Exhaustion, Tool Misuse = tool harness with 0 tool
   calls, Execution/State Error = crash or ≥2 tool errors). Shares no longer sum to
   100%. The updated classifier is **`analyze_tools/gather_failure_modes.py`**
   (`classify_failure_multi`); it emits a `failure_modes` column in
   `failure_modes_by_record.csv` and a new `failure_mode_multi_summary.csv`
   (`share` = fraction of non-success trajectories exhibiting each mode).

Validated locally on Gemma GPQA/HLE/AIME. The single-label figure in the paper is
interim; the multi-label figure (grouped bars, "% of failures exhibiting each mode")
will be rebuilt once every cell has multi-label data.

### G7 — re-run the multi-label classifier (needs raw trajectories)
Multi-label requires raw runs, which the earlier bundles did not include. **Please
re-run the updated `gather_failure_modes.py`** (in the repo; ship it to the remote)
on the raw runs for **Kimi-K2.6 (all 5 benchmarks), Qwen3.5-27B IMO + MMMU-Pro, and
Gemma-4-31B-IT MMMU-Pro + IMO**, and return `failure_mode_multi_summary.csv` +
the `failure_modes`-augmented `failure_modes_by_record.csv`. Qwen/Gemma GPQA/HLE/AIME
are computable locally and do not need re-sending.

## 5. Notes / schema reference

- Outcome field: `success` (correct), `failure` (wrong but scored), `unknown`
  (no scorable answer). `score_status` distinguishes `valid_scored` /
  `runtime_error` / `provider_error` / `agent_error`.
- Failure taxonomy buckets used: Reasoning, Tool Selection, Execution, Observation,
  Recovery, Memory/State, Budget, Format/Verifier. The paper's `tab:failure-taxonomy`
  collapses Execution/Observation/Recovery/Memory into an "Other" column.
- `rollout_full_directllm_mmmu_pro_vision` is the Qwen MMMU-Pro Direct run
  (matches table Direct = 73.4); the `*_kimi_k26` MMMU Direct is separate.
