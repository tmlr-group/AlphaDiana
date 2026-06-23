# Fig 5 — Action-transition chord diagrams (HLE only)

See `../REVISION_PLAN.md` §2 for the contract, problems, and proposed revision.

## Originals (HLE only — 24 PDFs)
`original/HLE_<Model>_<Harness>_<outcome>.pdf` where
Model ∈ {Qwen, Gemma}, Harness ∈ {DirectLLM, OpenClaw, OpenCode, ZeroClaw},
outcome ∈ {success, failure, aggregated}. Representative: `HLE_Qwen_OpenCode_aggregated.pdf`.

## Code
- `code/plot_action_chords.py` — pure-matplotlib Bezier chords (IdeogramArc / ChordArc
  / selfChordArc). Pipeline upstream:
  `compute_six_action_frequencies.py → compute_six_action_statistics.py → action_transitions_by_outcome.csv → plot_action_chords.py`.

## Data
- `data/action_transitions_by_outcome_qwen.csv`
- `data/action_transitions_by_outcome_gemma.csv`

Columns: `benchmark, harness, outcome, from_action, to_action, transition_count,
transition_total, transition_fraction`. Filter `benchmark == "HLE"`.
Action names use the OLD convention; `OLD_TO_NEW` in the script remaps to
Understanding / Planning / Reasoning / Tool Use / Verification / Finalization.

## Key issues to change
1. No in-figure legend/labels → arcs undecodable.
2. Reasoning self-loop (~80%+) swamps small flows.
3. `pad_matrix()` injects FAKE micro-edges for absent actions (e.g. Tool Use in
   DirectLLM/ZeroClaw) — must be replaced with honest absent-action handling.
4. 24 PDFs, no comparison.

Recommended target: 6×6 row-normalized transition-probability heatmaps as a
small-multiple (rows = model, cols = harness). Chord kept only as optional appendix
hero with ring labels (color key in caption, no in-figure legend) + sqrt arc scaling.

## Run
```bash
python3 code/plot_action_chords.py   # writes analyze_tools/figures/chords/*.pdf
```
