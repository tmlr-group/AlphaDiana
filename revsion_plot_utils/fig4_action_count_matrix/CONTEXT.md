# Fig 4 — Action-count success/failure matrix

See `../REVISION_PLAN.md` §1 for the contract, problems, and proposed revision.

## Originals
- `original/count_abs_rel_qwen.{pdf,png}` — Qwen3.5-27B
- `original/count_abs_rel_gemma.{pdf,png}` — Gemma4-31B
- `original/count_abs_rel_qwen_gemma.pdf` — combined

## Code
- `code/plot_count_abs_rel_qwen.py` — base; **data is embedded as a raw markdown
  string** (`raw_table`) in the file, derived from `action_counts_by_outcome.csv`.
- `code/plot_count_abs_rel_gemma.py` — Gemma table.
- `code/plot_count_abs_rel_qwen_gemma.py` — imports the qwen script as a base module
  and stacks Gemma (see prior note: `_qwen_gemma` imports `_qwen`).

## Data (canonical source the embedded tables came from)
- `data/action_counts_by_outcome_qwen.csv`
- `data/action_counts_by_outcome_gemma.csv`

Columns: `benchmark, harness, outcome, action, event_count, event_total, event_pct`.
Current cell metric = `success_count / (success_count + failure_count + unknown_count)`
per action. Revision reads these CSVs directly instead of the embedded string.

## Current encoding (what to change)
imshow heatmap (white→teal = success %) + per-cell inset pie (success/failure) +
bold raw count. Pie duplicates the color; raw counts span 4 orders of magnitude.
Target: 100% stacked action-composition small-multiples, success-vs-failure pairs,
shared 6-action palette, Qwen+Gemma in one figure.

## Run (after fixing the academic-plot import — N/A here, scripts are self-contained)
```bash
python3 code/plot_count_abs_rel_qwen.py   # writes analyze_tools/figures/count_abs_rel_qwen.pdf
```
