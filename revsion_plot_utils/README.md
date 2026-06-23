# revsion_plot_utils

Staging area for revising four manuscript figures, using the `nature-figure` skill
(Python/matplotlib backend). Nothing here is wired into the production
`analyze_tools/` pipeline yet; this folder holds the originals, the current code, the
input data, and the proposed plan so revisions can be developed and reviewed in
isolation.

## Read first
- **[`REVISION_PLAN.md`](REVISION_PLAN.md)** — the master plan: per-figure contract,
  what's wrong, and the proposed Nature-style revision. Shared decisions (backend,
  the broken `academic-plot` import, keep-current-theme color policy, caption-based
  legends, export contract) are in
  §0.
- **[`CONTEXT-figure_revision/revision_plan.md`](CONTEXT-figure_revision/revision_plan.md)**
  — the original target-figure list (the request).

## Layout
```
revsion_plot_utils/
├── REVISION_PLAN.md                  master plan (nature-figure contracts)
├── CONTEXT-figure_revision/          original request
├── fig4_action_count_matrix/         Fig 4  code/ original/ data/ CONTEXT.md
├── fig5_action_chords_hle/           Fig 5  code/ original/ data/ CONTEXT.md
├── fig6_post_tool_entropy/           Fig 6  code/ original/ CONTEXT.md
└── fig7_entropy_token_scatter/       Fig 7  code/ original/ data/ CONTEXT.md
```
Each figure folder: `code/` = current generator(s), `original/` = rendered figure(s)
as shipped, `data/` = the input CSV(s), `CONTEXT.md` = source/run notes.

## Backend (resolved)
Python/matplotlib — dictated by the existing all-matplotlib codebase, not a default.
First blocker to clear: the scripts import `from academic_plot import set_academic_style`
from `/home/xxx/academic-plot/scripts`, which is **empty on this machine**, so none
run as-is. REVISION_PLAN §0.2 replaces it with a self-contained style block.
