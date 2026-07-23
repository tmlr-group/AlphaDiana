# Macro-View Trajectory Analysis: Methods, Metrics, and Caveats

This document summarizes the analytical methodology used to produce the
`gpqa_macro_analysis.tex` and `hle_aime_macro_analysis.tex` appendix sections.
It is intended as a reproducibility reference and a guide for extending the
analysis to new benchmarks or models.

---

## 1. Benchmark Coverage and Data Sources

| Benchmark | Harnesses | Model | n (tasks) | k |
|---|---|---|---|---|
| GPQA-Diamond | DirectLLM, OpenClaw, OpenCode, ZeroClaw | Qwen3.5-27B | 198 | 1 |
| HLE multipleChoice | DirectLLM, OpenCode, ZeroClaw | Qwen3.5-27B | 323 (Direct) / 591 (agents) | 1 |
| AIME 2026 | DirectLLM (k=1), DirectLLM+all harnesses (k=32) | Qwen3.5-27B | 30 | 1 or 32 |

**Result directories:**
- GPQA: `results/full_gpqa_v2_{harness}_qwen35_27b_logprobs/`
- HLE DirectLLM: `/path/to/xxx/alphadiana_results/phase9_directllm_qwen35_27b_hle_logprobs`
- HLE OpenCode/ZeroClaw: `/path/to/xxx/alphadiana-results/20260426-hle-{harness}-qwen35_27b-v01`
- AIME k=1: `/path/to/xxx/alphadiana_results/full_20260423_qwen35_27b_aime2026_directllm_r1`
- AIME k=32: `/path/to/xxx/results/full_20260430_000332_aime2026_{harness}_qwen35_27b_k32_t600_c10_rerun`
- AIME DirectLLM k=32: `/path/to/xxx/results/full_20260430_150716_aime2026_directllm_qwen35_27b_k32_t600_c10_rerun`

**Task file convention:**
- Each result directory has a `tasks/` subdirectory with one `.json` per task.
- Files are lists when k>1 (each element = one sample); for k=1 they may be a
  list of length 1 or a plain dict. Always use `raw[-1] if isinstance(raw, list) else raw`
  to get the canonical record, **except** for k=32 where you need all elements.
- Key fields: `task_id`, `correct` (bool), `predicted`, `ground_truth`, `trajectory`.

---

## 2. Metrics Defined

### Token and Length Metrics
| Metric | Definition | Source |
|---|---|---|
| `mean tokens` | Mean output token count per trajectory | `logprobs_int16/{task_id}.jsonl` line count, or `n_tokens` in task record |
| `W/C token ratio` | `mean_tokens(wrong) / mean_tokens(correct)` | Computed from above |
| `cap rate` | Fraction of trajectories hitting context limit (32768 for GPQA/HLE, 131072 for AIME) | Token count >= threshold |

**GPQA logprob coverage caveat:** Not all task runs have logprob data.
GPQA coverage: DirectLLM=198/198, OpenClaw≈186/198, OpenCode≈193/198, ZeroClaw≈191/198.
Use `hle_entropy_by_outcome.csv` / `gpqa_entropy_by_harness.csv` for pre-computed values.

### Entropy Metrics
| Metric | Definition | Source |
|---|---|---|
| `mean entropy` | Mean per-token log-probability entropy (nats) over entire trajectory | `logprobs_int16/{task_id}[/sample_N].jsonl`, field `entropy_nats` |
| `low-entropy-long` | Trajectory at or below 25th-percentile entropy AND at or above 75th-percentile token count | Computed per-harness; thresholds are harness-specific |

**Entropy direction by harness on GPQA:**
- OpenClaw wrong: entropy collapses (0.14 vs 0.25 correct) — repetitive looping
- ZeroClaw wrong: entropy rises (0.36 vs 0.25 correct) — uncertain but commits wrongly
- OpenCode: flat (0.30 both) — length, not diversity, separates outcomes
- DirectLLM: slight drop on wrong (0.295 vs 0.322) — confident overrun

**HLE note:** Entropy patterns do not transfer from GPQA. DirectLLM entropy is flat
across outcomes (0.33 both). OpenCode correct has *higher* entropy (0.42) than wrong
(0.33) — the opposite of GPQA OpenClaw.

### Outcome Labels
All paired-outcome analysis pairs each (harness, task) trajectory with the DirectLLM
run on the same `task_id`.

| Label | Meaning |
|---|---|
| `both_correct` | DirectLLM correct, harness correct |
| `harness_gains` | DirectLLM wrong, harness correct |
| `harness_loses` | DirectLLM correct, harness wrong |
| `both_wrong` | DirectLLM wrong, harness wrong |

- **Loss rate** = `|harness_loses| / |DirectLLM_correct|`
- **Recovery rate** = `|harness_gains| / |DirectLLM_wrong|`

### Format Metrics (GPQA / HLE)
| Metric | Definition | Notes |
|---|---|---|
| `malformed` | Predicted answer not in valid option set | GPQA: {A,B,C,D}; HLE: single alpha char (A-Z) |
| `no_boxed` | Raw output contains no `\boxed{}` token | GPQA only; 0% for HLE (different format) |
| `TokRatio` | `harness_tokens / direct_tokens` on same task | Values >1 = harness used more tokens than DirectLLM |

### Tool Quality Metrics (GPQA OpenCode/OpenClaw)
| Metric | Definition |
|---|---|
| `Any call %` | Trajectories with ≥1 tool invocation |
| `Substantive %` | Trajectories whose tool result is non-empty, >100 chars, and contains no error string |
| `Err/empty %` | Trajectories with empty or error-containing tool result |
| `Mean calls` | Average tool invocations per trajectory |

Source: GPQA `artifacts/{task_id}/agent/normalized_trace.json` + tool result content.
**HLE caveat:** Substantive/Err breakdown not computed for HLE; only `Any call %` is reliable.

### AIME Metrics
| Metric | Definition |
|---|---|
| `pass@1` | Mean per-sample accuracy across all `n_tasks × k` samples |
| `pass@k` | Fraction of tasks where at least one of k samples is correct |
| `majority@k` | Fraction of tasks where majority-vote prediction is correct |
| `no-prediction rate` | Fraction of samples producing no extractable answer (`predicted is None`) |

**AIME k=32 logprob structure:** `logprobs_int16/{task_id}/sample_{N}.jsonl`
(nested directory per task, then per sample). Line count = token count per sample.

---

## 3. Turn-Level Action Extraction

### Action-space revision

The earlier labels
`plan`, `reason`, `tool_use`, `verify`, `recover`, and `answer` should not be
used as the main turn-level action taxonomy. They mix concrete agent moves
(`tool_use`, `answer`) with broad cognitive states (`reason`) and rhetorical
intents (`plan`, `verify`, `recover`), so a single turn can often match several
labels. In particular, `reason` is a residual text bucket rather than a valid
action taken by the agent.

For turn clustering, use a mutually exclusive operational action label: each
assistant turn is assigned to the most specific observable move it performs.

| Action | Definition | Typical evidence |
|---|---|---|
| `parse_task` | Restates givens, constraints, answer format, or target quantity | "We need...", extracts options, identifies requested output |
| `decompose` | Breaks the task into cases, subproblems, or ordered steps | numbered approach, case split, option checklist setup |
| `derive` | Applies domain rules, equations, mechanisms, or calculations to advance the solution | formulas, mechanistic claims, algebra, causal/domain principles |
| `compare_options` | Contrasts candidate answers or eliminates alternatives | option-by-option comparison, "A is wrong because..." |
| `compute` | Performs explicit arithmetic, symbolic manipulation, or executable calculation in text | numeric substitution, equation solving, tabulated calculations |
| `consult_tool` | Requests an external tool, search, shell command, browser action, or code execution | trajectory `tool_use` / `tool_call` event |
| `integrate_observation` | Uses a returned tool result or environmental observation to update the solution state | cites result/output, changes next step based on observation |
| `revise` | Corrects, abandons, or replaces a prior candidate/path | "wait", "actually", "that was wrong", retry after error |
| `validate_candidate` | Checks a current candidate against constraints or verifies consistency without changing it yet | sanity check, substitution back, constraint confirmation |
| `commit_answer` | Selects or formats the final answer | `\boxed{}`, "final answer", single option/number emission |
| `stall` | Repeats prior content, loops, or emits low-information continuation without state progress | repeated n-grams, restated plan, no new evidence or decision |

`tool_result` / observation-only steps are not agent actions. If a pipeline needs
one row per trajectory step, keep them as `observation` records, then label the
next assistant turn as `integrate_observation` only when it actually uses the
observation.

### Tie-break order

When a turn contains multiple signals, assign the first matching label in this
priority order:

```text
commit_answer
consult_tool
revise
integrate_observation
validate_candidate
compare_options
compute
derive
decompose
parse_task
stall
```

This order favors externally visible state transitions over generic prose. For
example, "let me verify by calculating..." is `compute` if it performs a
calculation, `validate_candidate` if it only checks a candidate, and
`consult_tool` if it calls a tool. It is not a separate `reason` or generic
`verify` action.

### Relationship to ARM modes

The ARM labels (`SE`, `PD`, `IC`, `UN`, `RR`) are semantic modes of reasoning,
not turn-level actions. They can be retained as auxiliary features:

- `compare_options` often corresponds to `SE`.
- `derive` / `compute` often correspond to `PD`.
- `commit_answer` can coincide with `IC`, but only when the turn actually
  commits to an answer.
- `revise` corresponds to `RR`.
- `UN` is a confidence/uncertainty marker, not an action; attach it as a
  modifier such as `uncertain=true`.

Do not report `reason` as an ARM/action rate in final tables. If an action
classifier needs a default for substantive but uncategorized text, call it
`derive` only when it advances the solution, otherwise `stall`.

### GPQA (pre-computed)
Legacy action rates use `degradation_task_features.csv` columns:
`looping_marker_count`, `self_correction_marker_count`, `uncertainty_marker_count`,
`harness_repeated_ngram_rate`, `harness_n_tokens`, `tool_use_count`, etc.
Answer rate uses `artifacts/{task_id}/agent/normalized_trace.json` step types.
Treat these as legacy heuristic features, not the recommended turn-clustering
taxonomy above.

### HLE (keyword heuristic)
HLE trajectories have `type` field per step: `tool_use` / `tool_result` / `message`.
- `tool_use` step type → **consult_tool** action (precise, from event logs)
- Keyword regex on assistant `content` (first 3000 chars):
  - revise: `Error|failed|let me try again|wait|actually|reconsider`
  - decompose: `let me (break|outline)|first|second|case`
  - validate_candidate: `let me (check|verify|confirm|double.check)`
  - derive: `therefore|thus\b|because|since\b|by the|from the equation`
  - compare_options: `option [A-Z]|choice [A-Z]|eliminate|rule out`
  - commit_answer: `\boxed{|the answer is|final answer`

**HLE caveats:**
- revise/decompose/validate/derive/compare are keyword heuristics and may over-
  or under-count.
- DirectLLM single-step trajectories have high spurious keyword hits (generic English).
- Only `consult_tool` (from step type) is reliable for all harnesses.
- Present in captions: *"Text actions from keyword/turn clustering (heuristic);
  consult\_tool from event logs (precise)."*

### AIME
AIME trajectories follow the same `type` structure. No action rate table was computed
for AIME because the open-ended numerical format does not map cleanly to the
multiple-choice action taxonomy. If action rates are added later, prefer the
operational labels above and avoid multiple-choice-specific labels such as
`compare_options` when no options are present.

---

## 4. Oracle Ceiling

```python
oracle_correct = sum(
    1 for task_id in all_tasks
    if direct_correct[task_id] or any(harness_correct[h][task_id] for h in harnesses)
)
oracle_acc = oracle_correct / n_tasks
```

**Recovery%** for the oracle row = `(oracle_correct - direct_correct) / direct_wrong`.

---

## 5. Entropy-Token Quadrant Analysis

Used for Finding 5 in GPQA. Per-harness thresholds:

```python
q25_ent = df[df['harness']==h]['mean_entropy'].quantile(0.25)
q75_tok = df[df['harness']==h]['n_tokens'].quantile(0.75)
low_ent_long = (mean_entropy <= q25_ent) & (n_tokens >= q75_tok)
```

Thresholds are harness-specific (do not compare across harnesses).
The quadrant accuracy for OpenClaw: 7.3% (38/41 wrong).
**Do not reuse GPQA thresholds for HLE** — the distributions are different.

---

## 6. LaTeX Output Conventions

All `.tex` files in this directory follow these rules:

1. **No `--` or `---` in prose.** Restructure with commas, colons, semicolons,
   or parentheses. Check with: `grep -n "\-\-" file.tex | grep -v midrule|bottomrule|toprule|tabcolsep|%`

2. **All table values in percent integers** (not decimals like `0.22`).
   Use `\%` suffix in column headers, integer values in cells.

3. **Finding boxes state general principles, not system-specific failures.**
   - Wrong: "On OpenClaw, wrong trajectories are 12× longer..."
   - Right: "Agent loops without a commit deadline produce systematic trajectory inflation..."

4. **Finding box format:** `\findingbox{one or two sentences, principle + key evidence direction}`
   Body paragraphs: 2–3 sentences each. Mechanism paragraph + one-line case anchor.
   Case anchors: one sentence max. No multi-sentence debugging reports.

5. **`\findingbox` command and `\newcounter{finding}`** are defined in
   `gpqa_macro_analysis.tex` and auto-increment. Do not redefine in other files.

6. **Table captions must define every column** that is not self-evident.
   Metric definitions belong in prose the first time they appear, then in captions
   for reference. Do not rely on table titles alone.

---

## 7. Key Cross-Benchmark Observations

| Signal | GPQA | HLE | AIME |
|---|---|---|---|
| Entropy direction (wrong vs correct) | OpenClaw: lower; ZeroClaw: higher | Flat (DirectLLM); OpenCode correct = higher | N/A (no classification task) |
| Dominant failure mode | Extraction failure (OpenClaw); premature commit (OpenCode); genuine error (ZeroClaw) | Premature commit (both harnesses); DirectLLM cap | No prediction (budget exhaustion) |
| Tool use as outcome predictor | Yes (GPQA OpenCode: 55% vs 15%) | No (HLE OpenCode: 23% vs 24%) | Not applicable |
| W/C token ratio | OpenClaw: 11.9×; others: 1–3.3× | OpenCode: 5.0×; ZeroClaw: 0.9× (inverted) | DirectLLM k=1: 3.9×; k=32 systems: 1.1–3.4× |
| Loss rate | 9–23% | 66–67% | N/A (no DirectLLM pairing for k=32) |
| Oracle ceiling above DirectLLM | +8.1 pp | +9.6 pp | N/A |

**Core principle:** Entropy, token inflation, tool-use gaps, and failure signatures
are harness-conditional AND benchmark-conditional. Do not assert a signal is
universal based on one (harness, benchmark) combination.

---

## 8. Scripts Reference

| Script | Purpose | Output |
|---|---|---|
| `extract_data.py` | GPQA entropy + action features | `data/degradation_task_features.csv`, etc. |
| `extract_hle_data.py` | HLE accuracy, entropy, paired gain | `data/hle_*.csv` |
| `extract_aime_imo_data.py` | AIME/IMO DirectLLM summary | `data/aime2026_direct_summary.csv` |
| `compute_gpqa_action_rates.py` | GPQA action rates by outcome | `data/gpqa_action_rates.csv` |
| `compute_gpqa_goal_loss.py` | GPQA token/cap by outcome | `data/gpqa_goal_loss.csv` |
| `compute_gpqa_oracle.py` | GPQA oracle ceiling | `data/gpqa_oracle_ceiling.csv` |
| `analyze_gpqa_tool_quality.py` | GPQA tool quality (substantive/err) | `data/gpqa_tool_quality.csv` |
| `build_gpqa_subdomain_map.py` | GPQA task→subdomain mapping | `data/gpqa_subdomain_map.csv` |
| `compute_trajectory_stats.py` | Cross-benchmark trajectory stats | `data/trajectory_stats.csv` |
| `plot_entropy_token_density.py` | 2D KDE density plots | `figures/fig_entropy_token_density*.pdf` |
