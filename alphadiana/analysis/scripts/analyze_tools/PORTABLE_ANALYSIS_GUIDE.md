# Portable Action-Transition Analysis Pipeline

This document captures the complete pipeline for chord diagrams and action-frequency
analysis so it can be reproduced on another machine with a different dataset.

---

## 1. Requirements

```bash
conda create -n alphadiana python=3.11 -y
conda activate alphadiana
pip install matplotlib numpy pandas
```

Only `matplotlib`, `numpy`, `pandas` are needed for chord plots and count tables.
No external API keys, no paid services.

---

## 2. Data format: result store layout

Every harness produces a **result store** directory with this structure:

```
result_store/
  tasks/
    task_0.json         # one JSON per task (or list per task for multi-sample)
    task_1.json
    ...
  logprobs/             # optional — per-token logprob JSONL files
    task_0.jsonl
    ...
  artifacts/            # optional — trajectory traces for agent harnesses
    task_0/
      agent/
        normalized_trace.json
```

Each `tasks/*.json` contains:

```json
{
  "task_id": "task_0",
  "correct": true,
  "trajectory": [
    {"role": "assistant", "type": "message", "content": "Let me think..."},
    {"role": "assistant", "type": "tool_use", "content": "...", "tool_calls": [...]},
    {"role": "tool", "type": "tool_result", "content": "..."}
  ],
  "token_entropy_stats": {"mean": 0.35, "n_tokens": 1234},
  "token_usage": {"completion_tokens": 1234}
}
```

**Key fields**:
- `correct`: `true`/`false`/`null` — task outcome
- `trajectory`: list of step dicts with `role`, `type`, `content`
- `token_entropy_stats.mean`: per-trajectory mean token entropy

---

## 3. Action classifier (`compute_six_action_frequencies.py`)

### 3.1 The six-action space

| Action | What it captures | Regex triggers |
|--------|-----------------|----------------|
| **Problem Framing** | goal, givens, constraints, answer format | `question asks`, `we need to`, `given`, `answer choices`, `goal is` |
| **Plan Formation** | decomposition, strategy selection | `step by step`, `first,`, `second,`, `strategy`, `approach` |
| **Solution Execution** | internal reasoning, calculation, comparison | math operators, `because`, `therefore`, `calculate`, `derive`, `option A` |
| **Tool Grounding** | tool call or observation integration | `is_tool_event=True` or `after_observation` + observation keywords |
| **Result Auditing** | check, verify, diagnose, correct | `check`, `verify`, `wait`, `actually`, `wrong`, `mistake`, `error`, `failed` |
| **Answer Delivery** | final answer, boxed result | `\boxed{...}`, `final answer`, `the answer is` |

### 3.2 Classification priority

```
Answer Delivery > Tool Grounding > Result Auditing > Solution Execution > Plan Formation > Problem Framing
```

If no rule matches, medium-length text defaults to **Solution Execution** (`low_confidence=true`);
very short text (< 5 words) defaults to **Plan Formation** (`low_confidence=true`).

### 3.3 How to run

```bash
# Stage 1 — extract action events from raw trajectories
python analyze_tools/compute_six_action_statistics.py \
  --spec GPQA:DirectLLM:gpqa_qwen_dl:results/gpqa_directllm_qwen \
  --spec GPQA:OpenClaw:gpqa_qwen_oc:results/gpqa_openclaw_qwen \
  ... (one --spec per harness)
  --output-dir analyze_tools/data/six_action_statistics
```

Each `--spec` format: `BENCHMARK:HARNESS:SOURCE_ID:PATH`

This generates:
- `action_transitions_by_outcome.csv` — **the key input for chord plots**
- `action_counts_by_outcome.csv` — absolute/relative frequency counts
- `trajectory_metrics.csv` — per-trajectory entropy, token count, tool count

---

## 4. Transition CSV schema

### `action_transitions_by_outcome.csv`

| Column | Type | Description |
|--------|------|-------------|
| `benchmark` | str | e.g. `GPQA`, `HLE`, `AIMEPass4` |
| `harness` | str | `DirectLLM`, `OpenClaw`, `OpenCode`, `ZeroClaw` |
| `outcome` | str | `success`, `failure`, `unknown` |
| `from_action` | str | source action (full name) |
| `to_action` | str | target action (full name) |
| `transition_count` | int | raw count of this transition |
| `transition_total` | int | total transitions in this (benchmark, harness, outcome) group |
| `transition_fraction` | float | `count / total` |

Actions use the full names:
```
Problem Framing, Plan Formation, Solution Execution,
Tool Grounding, Result Auditing, Answer Delivery
```

### `action_counts_by_outcome.csv`

Same grouping, but each row is one action type with `event_count`, `event_total`, `event_pct`.

---

## 5. Chord diagram script (`plot_action_chords.py`)

### 5.1 Theory

A chord diagram shows a **directed graph on a circle**. Each node is an action type.
The outer ring (ideogram arc) width is proportional to the node's total outgoing flow.
Inner ribbons (chords) connect source→target pairs; chord color matches the **source**
action's color so direction is readable at a glance.

### 5.2 Color scheme (6 actions, renamed for publication)

| Action | Hex | Description |
|--------|-----|-------------|
| Understanding | `#e41a1c` | Goal identification, constraints, input format |
| Planning | `#377eb8` | Task decomposition, strategy selection |
| Reasoning | `#4daf4a` | Model-internal derivation, calculation, comparison |
| Tool Use | `#984ea3` | External tool call or tool feedback integration |
| Verification | `#ff7f00` | Checking, diagnosing, correcting, confirming |
| Finalization | `#a65628` | Final answer, boxed result, terminal conclusion |

### 5.3 Name mapping (old→new)

```python
OLD_TO_NEW = {
    "Problem Framing":   "Understanding",
    "Plan Formation":    "Planning",
    "Solution Execution": "Reasoning",
    "Tool Grounding":    "Tool Use",
    "Result Auditing":   "Verification",
    "Answer Delivery":   "Finalization",
}
```

### 5.4 Key drawing primitives

**IdeogramArc** — outer ring segment. Splits arcs > 90° into sub-segments for true
circular rendering (a single cubic Bezier cannot faithfully approximate arcs
above ~90°).

**ChordArc** — directional ribbon from source to target. Tapers slightly
(`r_target = radius * (1 - chordwidth * 1.4)`) so the chord is wider at the
source end than the target end — visually encoding direction. Color always
matches the source node.

**selfChordArc** — small loop on a node showing self-transitions (e.g.,
Reasoning→Reasoning).

### 5.5 Z-ordering

Chords are sorted by weight (smallest first, drawn first). The largest chords
render on top, so dominant transitions are visually prominent and not hidden
by weaker ones. All patches use `alpha=1.0` (no transparency) to avoid color
blending artifacts.

### 5.6 Handling absent actions

Some groups naturally lack certain actions (e.g., DirectLLM and ZeroClaw never
have "Tool Use"). A `pad_matrix()` function ensures every action has a visible
arc:

```python
def pad_matrix(mat):
    total = mat.sum()
    ms = max(total * 0.03, 10) if total > 0 else 10
    for i in range(6):
        rsum = mat[i].sum()
        csum = mat[:, i].sum()
        if rsum == 0 and csum == 0:
            # completely absent → distribute micro-edges bidirectionally
            for j in range(6):
                if i != j:
                    mat[i, j] += ms / 10
                    mat[j, i] += ms / 10
            mat[i, i] += ms / 2
        elif rsum < ms:
            mat[i, i] += ms - rsum
    return mat
```

### 5.7 Output structure

```
figures/chords/
  GPQA_Qwen_DirectLLM_success.pdf
  GPQA_Qwen_DirectLLM_failure.pdf
  GPQA_Qwen_DirectLLM_aggregated.pdf
  GPQA_Qwen_OpenClaw_success.pdf
  ...
  HLE_Gemma_ZeroClaw_aggregated.pdf
```

- **success** / **failure**: individual outcome chord
- **aggregated**: success + failure transition counts summed

---

## 6. Count-abs-rel analysis (`plot_count_abs_rel_*.py`)

### 6.1 What it does

Reads `action_counts_by_outcome.csv` and produces a grid of bar charts:
- **Absolute count** bars — total action events per (benchmark, harness, outcome)
- **Relative percentage** bars — `(event_count / event_total) * 100`
- Split by outcome (success vs failure)

### 6.2 Input CSV columns used

From `action_counts_by_outcome.csv`:
- `benchmark`, `harness`, `outcome`, `action`, `event_count`, `event_total`, `event_fraction`

### 6.3 How to adapt for a new dataset

1. Run `compute_six_action_statistics.py` with your result-store `--spec` entries
2. Read the generated `action_counts_by_outcome.csv`
3. Group rows by `(benchmark, harness, outcome)` and compute per-action percentages
4. Plot with `matplotlib` grouped bar charts

---

## 7. Checklist for new-machine setup

1. Copy these files to the new machine:
   - `analyze_tools/compute_six_action_frequencies.py`
   - `analyze_tools/compute_six_action_statistics.py`
   - `analyze_tools/plot_action_chords.py`
   - This guide

2. Verify result stores have `tasks/*.json` with `trajectory` and `correct` fields

3. Build `--spec` entries mapping your data:
   ```
   BENCHMARK:HARNESS:SOURCE_ID:PATH
   ```
   - `BENCHMARK`: any string, used as grouping key (e.g. `GPQA`, `HLE`)
   - `HARNESS`: `DirectLLM`, `OpenClaw`, `OpenCode`, or `ZeroClaw`
   - `SOURCE_ID`: short identifier for the source
   - `PATH`: absolute or relative path to the result store directory

4. Run the pipeline:
   ```bash
   # Step 1 — extract transitions
   python analyze_tools/compute_six_action_statistics.py \
     --spec BENCH1:DirectLLM:src1:/path/to/result1 \
     --spec BENCH1:OpenClaw:src2:/path/to/result2 \
     ... \
     --output-dir analyze_tools/data/six_action_statistics

   # Step 2 — generate chord plots
   python analyze_tools/plot_action_chords.py
   ```

5. Adapt `plot_action_chords.py`:
   - Change `DATA_DIR` paths to match your output directories
   - Change `bench` loop values to match your benchmark names
   - Change `model` loop values to match your model names
   - Change `HARNESSES` if you use different harness names

---

## 8. Common issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ModuleNotFoundError: matplotlib` | Missing dependency | `pip install matplotlib` |
| Empty chord (no arcs) | All transitions < 1 | Check `min_share` threshold in `pad_matrix` |
| Weird colors (e.g. `#9A7842`) | Alpha blending from overlapping patches | Use `alpha=1.0`, sort chords by weight |
| Arcs not circular | Single Bezier for >90° arc | `IdeogramArc` already splits — ensure your copy has the split logic |
| "Tool Use" missing in DirectLLM | Expected — DirectLLM has no tools | `pad_matrix` adds a micro-arc automatically |
| AIME multi-sample: pass@4 structure | Task JSONs are lists of 4 items | `six.load_task_records()` handles lists |
