# Fig 7 — Entropy vs output-length

See `../REVISION_PLAN.md` §4 for the contract, problems, and proposed revision.

## Originals
- `original/fig_entropy_token_scatter_combined.pdf` — the current scatter (Qwen left,
  Gemma right; 3 benchmark rows × 4 harness cols per model).
- `original/fig_entropy_token_density.pdf`, `original/fig_entropy_token_density_hle.pdf`
  — the existing **density variant** (the recommended replacement; uses HPD tail
  clipping so there is no background bleed).

## Code
- `code/plot_entropy_token_scatter.py` — current scatter (overplotted).
- `code/plot_entropy_token_density.py` — KDE density with `clip_kde_tails` (HPD mask).
  **Promote this to the combined figure.**
- `code/extract_entropy_token_scatter.py` — builds the CSV from `tasks/*.json`
  (handles single-record lists and AIME pass@4 4-sample lists).

## Data
- `data/entropy_token_scatter.csv` —
  `model,harness,benchmark,task_id,sample,correct,mean_entropy,n_tokens`.
  `correct` ∈ {0,1}; `mean_entropy` nats; `n_tokens` int; `sample` 0 (single) or 0-3 (pass@4).

## Current encoding (what to change)
Raw scatter, green=correct / red=wrong, heavy overplotting hides the separation;
tiny 3×8 panels. Target: overlaid 2D KDE density contours **keeping the current
green/red** outcome colors (key in caption, no in-figure legend), annotate the
low-entropy×long-output "confident collapse" quadrant, enlarge panels (stack the two
models).

## Run
```bash
python3 code/plot_entropy_token_scatter.py   # current scatter
python3 code/plot_entropy_token_density.py   # density variant (recommended)
```
