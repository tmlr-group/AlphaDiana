# Figure Revision Plan (nature-figure skill)

Revision proposals for four manuscript figures, produced with the `nature-figure`
skill (Python/matplotlib backend). The target figure list is in
[`CONTEXT-figure_revision/revision_plan.md`](CONTEXT-figure_revision/revision_plan.md).
Each figure has its own folder with the **current code**, the **original rendered
figure**, the **input data**, and a `CONTEXT.md`.

| Folder | Manuscript figure | Current generator |
|---|---|---|
| `fig4_action_count_matrix/` | Fig 4: action-count success/failure matrix (Qwen + Gemma) | `plot_count_abs_rel_qwen.py` / `_gemma.py` / `_qwen_gemma.py` |
| `fig5_action_chords_hle/` | Fig 5: action-transition chord diagrams (HLE only) | `plot_action_chords.py` |
| `fig6_post_tool_entropy/` | Fig 6: post-tool-call token-entropy dynamics | `plot_entropy_post_tool.py` |
| `fig7_entropy_token_scatter/` | Fig 7: entropy vs output-length scatter | `plot_entropy_token_scatter.py` (+ `plot_entropy_token_density.py`) |

---

## 0. Decisions that apply to all four figures

### 0.1 Backend (resolved, not a default)

The `nature-figure` skill treats Python-vs-R as a blocking gate. It is **resolved to
Python** here, not guessed: every existing generator is matplotlib, and the skill's
contract permits resolution from "a clearly language-specific input file/workflow."
All revised drawing, preview, export, and QA stays in Python/matplotlib.

### 0.2 Broken dependency to fix first (blocker)

Every script begins with:

```python
SKILL_DIR = Path("/home/xxx/academic-plot/scripts")
sys.path.insert(0, str(SKILL_DIR))
from academic_plot import set_academic_style
```

`/home/xxx/academic-plot/scripts` is **empty on this machine**, so none of the
scripts run as-is. The revision replaces this hard import with a **self-contained
style block** (the skill's `apply_publication_style`) committed inside
`revsion_plot_utils/`, so the figures are reproducible without the missing package.

```python
# revsion_plot_utils/_style.py  (new, shared)
import matplotlib.pyplot as plt
def apply_publication_style(font_size=7, axes_linewidth=0.8):
    plt.rcParams['font.family']      = 'sans-serif'
    plt.rcParams['font.sans-serif']  = ['Arial', 'Helvetica', 'DejaVu Sans']
    plt.rcParams['svg.fonttype']     = 'none'   # editable SVG text
    plt.rcParams['pdf.fonttype']     = 42       # editable PDF text
    plt.rcParams['font.size']        = font_size
    plt.rcParams['axes.spines.right'] = False
    plt.rcParams['axes.spines.top']   = False
    plt.rcParams['axes.linewidth']   = axes_linewidth
    plt.rcParams['legend.frameon']   = False
```

### 0.3 Color: keep the current theme (user decision)

Per the user, **keep each figure's existing main color theme and keep the current way
models are distinguished**; do not impose a new palette. Concretely:

- **Action palette** (Fig 4 + Fig 5): keep the project's existing 6-action colors
  already used by `plot_action_chords.py`, and apply the *same* mapping in Fig 4's
  redesigned bars so Fig 4 and Fig 5 share one action color vocabulary:
  `Understanding #e41a1c · Planning #377eb8 · Reasoning #4daf4a · Tool Use #984ea3 · Verification #ff7f00 · Finalization #a65628`.
- **Outcome colors** (Fig 6 + Fig 7): keep the current **green (correct) / red (wrong)**.
- **Model theme** (Qwen vs Gemma): keep the current scheme that separates the two models.

Optional, non-blocking: green/red is hard for color-vision-deficient readers, so a
redundant non-hue cue (line style, marker shape, or hatch) can be added *without
changing the hues* so the theme is preserved while staying decodable. Skip if undesired.

### 0.4 Legend and export contract (all figures)

- **No in-figure legend.** Per the user, the essential marker/color key is given in the
  **caption**, not drawn on the axes. Keep *direct labels* where they aid reading
  (e.g. segment labels, line end-labels, axis tick names), but do not add boxed legends.
- Save **`.svg` (primary, editable text) + `.pdf` + `.png` preview**, via the skill's
  `finalize_figure`. Current scripts emit PDF only (Fig 4/5/7) which blocks editing.
- Column widths: single = 89 mm, double = 183 mm. Set `figsize` in inches to these
  so on-page font size is the real ~7 pt, not shrunk-to-fit giant fonts.
- Add bold panel letters (`a`, `b`, `c`) with `add_panel_label`.
- Surface `n` and the success/failure definition in the **caption** (the user writes
  captions), per the skill's reviewer-risk checklist. Colorbars are not legends and
  may stay on the figure where a continuous scale is shown (Fig 5 heatmap).

---

## 1. Fig 4 — Action-count success/failure matrix

**Folder:** `fig4_action_count_matrix/`

### Figure contract
```
Core conclusion : Agentic harnesses (OpenClaw/OpenCode) re-allocate the model's
                  action budget away from Reasoning toward Verification and Tool
                  Use relative to DirectLLM, and that re-allocation tracks outcome.
Archetype       : quantitative grid
Backend         : Python
Final size      : 183 mm double column
Panel map       : a = AIME26   b = GPQA   c = HLE   (one composition panel each;
                  Qwen and Gemma as paired sub-rows within each panel)
Evidence        : hero  = action composition per harness (6-way share)
                  signal= success-vs-failure shift in that composition
Statistics      : event totals per (benchmark, harness); n shown as a side bar
Reviewer risk   : raw counts span 4 orders of magnitude; pies are unreadable;
                  the cell metric (success share of an action) conflates volume
                  with outcome.
```

### What's wrong now (see `original/count_abs_rel_qwen.png`)
1. **Triple, partly-redundant encoding per cell**: background color = success %,
   an inset pie = the *same* success/failure split, plus a bold raw count. The pie
   duplicates the color and is too small to read; Nature style discourages pies
   outright (angle/area judgments are imprecise).
2. **Raw counts (155 … 250,831) are not visually comparable** and dominate the cell.
3. **The encoded cell metric, `success/(success+failure)` per action, is hard to
   interpret** and is not the message; readers want *composition* (what fraction of
   behavior each action is) and how it shifts with outcome.
4. 12 stacked rows with two-line labels; Qwen and Gemma live in two separate PDFs,
   blocking the cross-model comparison.

### Proposed revision (recommended: composition small-multiples)
- Replace the heatmap-with-pies grid with **100% horizontal stacked bars of action
  composition**, one bar per (harness × outcome), faceted by benchmark (panels a/b/c).
  This is what the `_rel` data actually wants to show and is read instantly.
- Within each panel, group bars as **success vs failure pairs per harness** so the
  outcome shift is a direct visual contrast (the current "success-share" metric is
  replaced by showing both outcomes' compositions side by side).
- Use the **shared 6-action palette** (§0.3); direct-label segments ≥ 8% (the action
  color key goes in the caption, no in-figure legend, per §0.4).
- Show volume without clutter: a thin **"events (k)" bar** to the right of each row
  instead of printing a number in every cell.
- Put **Qwen and Gemma in one figure** (e.g., two columns of panels, or paired
  sub-rows) for direct cross-model reading.
- **Alternative if the grid layout must be kept:** keep `imshow`, but (a) delete the
  pies, (b) encode *action share* (sequential colormap) with one clear colorbar
  "Action share (%)", (c) add a second small diverging panel for Δshare(success −
  failure), (d) drop per-cell raw numbers in favor of a row-total sidebar.

### Files
- code: `plot_count_abs_rel_qwen.py`, `plot_count_abs_rel_gemma.py`, `plot_count_abs_rel_qwen_gemma.py`
- data: `action_counts_by_outcome_{qwen,gemma}.csv` (the scripts currently embed this
  table as a raw string; the revision reads the CSV instead).

---

## 2. Fig 5 — Action-transition chord diagrams (HLE only)

**Folder:** `fig5_action_chords_hle/`

### Figure contract
```
Core conclusion : On HLE, harness scaffolding changes the agent's transition
                  structure (adds Tool Use / Verification loops) rather than just
                  scaling a single Reasoning self-loop.
Archetype       : quantitative grid (small-multiple of 6x6 transition matrices)
Backend         : Python
Final size      : 183 mm double column
Panel map       : rows = Qwen / Gemma ; cols = DirectLLM, OpenClaw, OpenCode, ZeroClaw
Evidence        : hero  = inter-state transition structure per harness
Reviewer risk   : current chords have no in-figure legend; Reasoning self-loop
                  swamps everything; pad_matrix injects FAKE edges for absent
                  actions; 24 separate PDFs prevent comparison.
```

### What's wrong now (see `original/HLE_Qwen_OpenCode_aggregated.pdf`)
1. **No labels, no legend in the figure** — the colored arcs are undecodable
   without the caption; the reader cannot tell which color is which action.
2. **The Reasoning→Reasoning self-loop dominates (~80%+)**, compressing the
   informative small transitions (Tool Use, Verification) into invisible slivers.
3. **`pad_matrix()` injects synthetic micro-edges** for actions a harness never uses
   (e.g. Tool Use in DirectLLM/ZeroClaw) — visually implies flows that do not exist.
4. **24 PDFs for HLE alone** (2 models × 4 harnesses × {success, failure, aggregated});
   no single comparative view.

### Proposed revision (recommended: transition-matrix heatmaps)
- For readability, **replace the chord ring with a 6×6 row-normalized transition
  heatmap** `P(to | from)` per harness, laid out as a small-multiple grid (rows =
  model, cols = harness). This is precise, directly comparable across harnesses, and
  standard in agent/RL trajectory analysis.
- **Handle the Reasoning self-loop** by annotating the diagonal value but using a
  colormap normalization that does not let the diagonal saturate the off-diagonal
  signal (e.g. mask/clip the diagonal, or plot off-diagonal on its own scale).
- **Drop `pad_matrix` fake edges**: show genuinely-absent actions as a hatched/greyed
  "absent" cell, never as a fabricated transition.
- One shared sequential colorbar "Transition probability" (a colorbar is a scale, not
  a legend, so it stays on the figure per §0.4); bold panel letters; shared action
  labels on axes (short names from `SHORT`) provide the action key directly.
- **If the chord aesthetic must be retained** (e.g. as a hero/appendix panel): add
  ring labels (the action color key goes in the caption, no boxed legend); consolidate
  the 4 HLE harnesses into a single row of small chords; rescale arc widths with `sqrt`
  so non-Reasoning flows are visible; and remove pad_matrix in favor of honest
  absent-action handling.

### Files
- code: `plot_action_chords.py`
- data: `action_transitions_by_outcome_{qwen,gemma}.csv` (filter `benchmark == HLE`).

---

## 3. Fig 6 — Post-tool-call token-entropy dynamics

**Folder:** `fig6_post_tool_entropy/`

### Figure contract
```
Core conclusion : After a tool call, correct trajectories settle to lower token
                  entropy than wrong ones, and the gap widens with post-tool depth.
Archetype       : quantitative grid (trend)
Backend         : Python
Final size      : 183 mm double column
Panel map       : rows = benchmark (HLE/GPQA/AIME) ; cols = tool harness
                  (OpenClaw, OpenCode) ; one figure per model OR model as line style
Evidence        : hero  = correct-vs-wrong entropy trajectory over post-tool position
Statistics      : per-bin n; bins below MIN_BIN_COUNT faded; SE band
Reviewer risk   : interleaved bars hide the trend; no in-figure marker key (moves to
                  caption); noisy low-n tail bins shown at full weight.
```

### What's wrong now (see `original/HLE_Qwen_OpenClaw.png`)
1. **Interleaved green/red bars at every log-position bin** create a busy comb; the
   actual signal (do correct and wrong diverge?) is buried in the clutter.
2. **No correct/wrong key and no benchmark/model/harness label** anywhere (the key
   will live in the caption, but the panel still needs its setting identified).
3. **Noisy tail bins** (few tokens that deep) carry the same visual weight as
   well-populated bins.
4. 24 per-combination PDFs; no consolidated comparison.

### Proposed revision
- **Replace paired bars with two lines + SE/CI shaded bands** (correct vs wrong) over
  the log post-tool-position axis (`make_trend(..., show_shadow=True)`). The trend and
  its uncertainty become immediately legible and noisy tails self-de-emphasize.
- **Keep the current green/red outcome colors** (§0.3); identify each curve with a
  *direct end-label*, and put the full correct/wrong key in the caption (no boxed
  legend, per §0.4). Each panel still shows its benchmark/model/harness.
- Optionally add a thin **token-count histogram / rug along the bottom** to show where
  the data mass is, so sparse deep bins are visibly down-weighted.
- Consider a companion **Δ-entropy curve (wrong − correct)** to state the divergence
  explicitly as one line.
- **Consolidate** into small-multiples (rows = benchmark, cols = tool harness), one
  figure per model, shared axes + panel letters. DirectLLM/ZeroClaw have no post-tool
  tokens and are correctly excluded.

### Files
- code: `plot_entropy_post_tool.py` (rewrite only `render_entropy_plot`, lines ~216-302;
  the aggregation pipeline above it stays). `original/summary.json` records per-setting
  `n` (correct_files / wrong_files / segments) to surface in the legend.

---

## 4. Fig 7 — Entropy vs output-length

**Folder:** `fig7_entropy_token_scatter/`

### Figure contract
```
Core conclusion : Wrong answers concentrate in a low-entropy x long-output
                  "confident collapse" region that correct answers avoid.
Archetype       : quantitative grid (density)
Backend         : Python
Final size      : 183 mm double column (or full-page)
Panel map       : rows = benchmark (GPQA/HLE/AIME) ; cols = harness x model
Evidence        : hero  = 2D density of correct vs wrong in (log tokens, entropy)
Reviewer risk   : heavy overplotting hides the separation; tiny panels.
```

### What's wrong now (see `original/fig_entropy_token_scatter_combined.pdf`)
1. **Severe overplotting**: thousands of semi-transparent points pile up; the
   correct/wrong separation (the whole point) is obscured.
2. **3 × 8 tiny panels** with small markers and tick labels.

### Proposed revision (recommended: density contours — a variant already exists)
- **Switch the combined figure from raw scatter to 2D KDE density contours**, overlaid
  correct vs wrong. The repo **already has** `plot_entropy_token_density.py` with a
  working `clip_kde_tails` (HPD masking) that removes background bleed — adopt that as
  the combined figure, instead of the scatter.
- **Keep the current green/red outcome colors** (§0.3) as filled contours; the
  correct/wrong key goes in the caption (no in-figure legend, per §0.4).
- **Annotate the "confident collapse" quadrant** (low entropy + long output) with a
  light guide box/label so the reader is pointed at the conclusion.
- **Enlarge panels**: split the two models into two stacked blocks (or a full-page
  figure) so markers/contours and tick fonts read at ~7 pt on the page.
- **If scatter is required** (reviewers sometimes want raw points): keep points but
  add marginal histograms/KDE on the top and right of each panel, drop marker size and
  alpha further, and use `hexbin` for the densest panels.

### Files
- code: `plot_entropy_token_scatter.py` (current scatter), `plot_entropy_token_density.py`
  (the density variant to promote), `extract_entropy_token_scatter.py` (data builder).
- data: `entropy_token_scatter.csv`
  (`model,harness,benchmark,task_id,sample,correct,mean_entropy,n_tokens`).

---

## 5. Suggested execution order

1. Land the shared `_style.py` + the two color dicts (unblocks every script).
2. Fig 7 (highest leverage, density variant already exists → fastest win).
3. Fig 6 (bars → lines+bands; mechanical, big readability gain).
4. Fig 4 (heatmap+pies → composition small-multiples; biggest redesign).
5. Fig 5 (chords → transition heatmaps; biggest conceptual change — confirm with the
   team whether to keep chords as an appendix hero before replacing in the main text).

Items needing a human decision are flagged in each section ("recommended" vs
"alternative / if X must be kept"). Nothing here has been applied to the production
`analyze_tools/` scripts yet — this folder is the staging area for the revision.

---

## 6. Implementation status (staged in `revsion_plot_utils/`)

All four recommended revisions are implemented as new, self-contained scripts that
import the shared `_style.py` (no `academic-plot` dependency) and write `.pdf/.png/.svg`
into each figure's `revised/` folder. Run each from its own folder.

| Fig | Script | Output (in `revised/`) | Status |
|---|---|---|---|
| 4 | `fig4_action_count_matrix/plot_fig4_composition_revised.py` | `fig4_action_composition.*` | done — composition small-multiples, success/failure pairs, events sidebar, Qwen+Gemma in one figure |
| 5 | `fig5_action_chords_hle/plot_fig5_transition_heatmap_revised.py` | `fig5_hle_transition_heatmaps.*` | done — 2×4 P(to\|from) heatmaps, honest grey "absent" Tool cells, no pad_matrix, PowerNorm so off-diagonal is visible |
| 6 | `fig6_post_tool_entropy/plot_fig6_lines_revised.py` | `fig6_post_tool_entropy_{Qwen,Gemma}.*` | done — lines + SE bands, kept green/red, direct end-labels, re-aggregated from raw logprobs with sum-of-squares for SE |
| 7 | `fig7_entropy_token_scatter/plot_fig7_density_revised.py` | `fig7_entropy_token_density.*` | done — overlaid green/red KDE density (HPD clip), stacked-model blocks, low-entropy guide band |

Conventions honored: no in-figure legends (keys go in the caption), current color
theme kept (6-action palette; green/red outcomes; model distinction), editable
SVG/PDF text, bold panel letters, `n` surfaced on Fig 6 panels.

### Known minor polish (optional, not blocking)
- Fig 6: the `Correct`/`Wrong` end-labels can overlap where the two curves meet at the
  right edge (e.g. HLE OpenClaw). Could nudge vertically or label only one curve.
- Fig 6 caption must define the SE band (±1 SE of per-bin mean token entropy) and that
  bins with < 5 post-tool tokens are dropped.
- Production wiring: once approved, fold `_style.apply_publication_style` into the
  `analyze_tools/` originals (replacing the broken `academic_plot` import) and point
  the revised scripts' output at `analyze_tools/figures/`.

---

## 7. Version 1 revisions (per CONTEXT-figure_revision/revision_plan.md notes)

The version-1 notes changed three of the four figures. Canonical v1 scripts/outputs:

| Fig | v1 decision | Canonical script → output |
|---|---|---|
| 4 | unchanged (no note) | `plot_fig4_composition_revised.py` → `fig4_action_composition.*` |
| 5 | heatmap was hard to read (color + P(to\|from)); switched to **stacked next-action bars** (color = destination action, shared palette; "Next-action share" label avoids probability jargon; absent actions hatched) | `plot_fig5_stacked_revised.py` → `fig5_hle_next_action_bars.*` |
| 6 | **removed the `n=…✓/…✗` text**; uncertainty is carried by the SE bands, counts go to caption | `plot_fig6_lines_revised.py` → `fig6_post_tool_entropy_{Qwen,Gemma}.*` |
| 7 | keep **scatter** (not density); **conference two-column** landscape (Qwen block left, Gemma right); **auxiliary lines** added: per-outcome least-squares trend + faint low-entropy guide at 0.15 nat | `plot_fig7_scatter_revised.py` → `fig7_entropy_token_scatter.*` |

Superseded but kept as alternatives (not the v1 choice):
`plot_fig5_transition_heatmap_revised.py` (magma P(to\|from) heatmap) and
`plot_fig7_density_revised.py` (KDE density, Nature portrait).

Fig 6 now caches its aggregation to `revised/_fig6_agg_cache.pkl`; re-render is instant.
Pass `--fresh` to re-aggregate from raw logprobs.

### v1.1 — unified flat conference two-column layout (all figures)

Per follow-up, all four figures now share ONE flat landscape skeleton: **benchmark
rows × two model blocks side by side** (Qwen | gap | Gemma), harness columns.

| Fig | Flat layout | Cell content |
|---|---|---|
| 4 | 3 bench rows × (4 harness \| gap \| 4 harness) | success/failure pair of 100%-stacked action bars; total events annotated on top (sidebar dropped) |
| 5 | 1 row × (4 \| gap \| 4)  (HLE only) | 6 next-action stacked bars |
| 6 | 3 bench rows × (2 \| gap \| 2)  (tool harnesses) | lines + SE bands; the two per-model figures are now a single combined figure |
| 7 | 3 bench rows × (4 \| gap \| 4) | scatter + trend lines + low-entropy guide |

All output one combined PDF/PNG/SVG per figure (Fig 6 is no longer split by model).

### v1.3 — final layout pass

- **All figures**: "(Pass@4)" dropped from the AIME label (now just "AIME").
- **Fig 4 (hbars)**: tighter (smaller harness-group gaps, shorter canvas, trimmed
  events-sidebar range) for less empty space.
- **Fig 5**: split into two per-model files (`fig5_hle_sankey_{qwen,gemma}`); removed
  the bottom caption and the per-action node labels; 2 rows (Correct/Wrong) x harness,
  compact height. Correct/Wrong row labels kept (green/red).
- **Fig 6**: removed the model-name title; Correct/Wrong end-labels separated and
  clamped off the x-axis so they no longer overlap each other or the axis.
- **Fig 7**: split into two per-model files (`fig7_entropy_token_scatter_{qwen,gemma}`);
  benchmark row label and the shared "Mean token entropy (nat)" axis label moved to
  distinct x positions so they no longer overlap. Global shared scale retained across
  both files.

### v1.2 — per-figure follow-ups

- **Fig 4**: split into two per-model figures (`fig4_action_composition_{qwen,gemma}`);
  outcome bars labeled with LaTeX `\checkmark` / `\times` (mathtext) instead of S/F;
  total-count annotation removed (ratios only); larger fonts; panel-letter indices removed.
  - **Fig 4 alt** (`plot_fig4_hbars_revised.py` → `fig4_action_hbars_{qwen,gemma}`): a
    second version matching `original/fig_4_previous_version.png` — horizontal
    100%-stacked bars (harness x check/cross) with a grey events sidebar (log scale),
    per model. Both variants are kept; pick one for the manuscript.
- **Fig 6**: split back into two per-model figures (`fig6_post_tool_entropy_{Qwen,Gemma}`);
  larger fonts.
- **Fig 7**: trend lines and low-entropy guide removed; correct-vs-wrong difference now
  shown by a 2-std covariance ellipse + centroid per outcome over the scatter.
- **Fig 5**: full revision to a **per-harness Sankey flow** (`plot_fig5_sankey_revised.py`
  → `fig5_hle_sankey.*`): left = source action, right = next action, ribbon width =
  transition frequency, colored by source. Conveys the transition flow directly.
  - Split by outcome: rows = **Correct** / **Wrong** trajectories (success vs
    failure+unknown), columns = harness, two model blocks side by side.
  - Each action color gets exactly one text annotation per block (left side, first
    panel where it is a source — so Tool is labeled on OpenClaw); labels are evenly
    distributed with leader lines to avoid overlap.
  (Earlier `plot_fig5_transition_heatmap_revised.py` and `plot_fig5_stacked_revised.py`
  remain as rejected alternatives.)
