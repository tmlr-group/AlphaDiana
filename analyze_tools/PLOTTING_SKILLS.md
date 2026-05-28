# Plotting & Analysis Skills Reference

Portable notes for reproducing these three plot types on a new dataset/machine.

---

## 1. Chord Diagrams (`plot_action_chords.py`)

### Pipeline
```
raw trajectory text → compute_six_action_frequencies.py → action_transitions_by_outcome.csv
                                                       → action_counts_by_outcome.csv
                    → compute_six_action_statistics.py  (wraps frequencies + entropy/tool stats)
                    → plot_action_chords.py             (reads transitions CSV → 48+ PDFs)
```

### Input CSV format
`data/six_action_statistics/action_transitions_by_outcome.csv`:
```
benchmark,harness,outcome,from_action,to_action,transition_count,transition_total,transition_fraction
```
One row per directional transition pair per (benchmark, harness, outcome).

Action names use OLD convention: `Problem Framing, Plan Formation, Solution Execution, Tool Grounding, Result Auditing, Answer Delivery`. The plot script remaps them to `Understanding, Planning, Reasoning, Tool Use, Verification, Finalization`.

### Key implementation details
- **Pure matplotlib bezier patches** (no holoviews/bokeh/chord package). `matplotlib.path.Path` + `patches.PathPatch` with cubic Bezier curves (`CURVE4`).
- **Custom polar-to-cartesian**: `polar2xy(r, theta)` helper.
- **IdeogramArc**: outer donut segment, split into ≤90° sub-arcs for numerical stability.
- **ChordArc**: directional quadratic bezier from source arc → target arc, tapered target side for directionality cue.
- **selfChordArc**: self-loop as a small inner arc.
- **Colors**: 6 hardcoded hex codes, one per action. Chord color = source action color (directional).
- **White edgecolor** on all patches prevents alpha-blending artifacts between adjacent arcs.
- **Pad matrix**: zero-sum rows/cols get distributed bidirectional connections + self-loop (3% or 10 edges minimum) so no action disappears from the diagram.
- **Sort chords by weight**: smallest drawn first, largest on top (visual clarity).
- **Output**: flat filenames `figures/chords/{Bench}_{Model}_{Harness}_{outcome}.pdf` + `_aggregated.pdf`.

### Hyperparameters
| Param | Value | Notes |
|-------|-------|-------|
| `width` (ideogram) | 0.18 | Outer arc thickness |
| `pad` | 2 | Degrees gap between actions |
| `chordwidth` | 0.55 | Chord thickness |
| `figsize` | (6, 6) | Square figure |
| `dpi` | 200 | |
| `ax.set_aspect('equal')` | required | Otherwise diagram is elliptical |

### Dependencies
```python
matplotlib, numpy (no external charting libs)
```

---

## 2. Count Abs/Rel Matrix (`plot_count_abs_rel_qwen.py`)

### Pipeline
```
action_counts_by_outcome.csv → hardcoded markdown table in script → parse → plot
```
(Data is embedded as a raw string table in the Python file itself.)

### Input format (embedded markdown table)
```
| Benchmark | Harness | Outcome | Events | Problem Framing | Plan Formation | ... |
```
Each action column: `"count (fraction%)"` → parsed by regex `r"([\d,]+)\s*\(([-\d.]+)%\)"`.

### Key implementation details
- **imshow heatmap** with custom LinearSegmentedColormap from white→teal→dark teal.
- **Success fraction** = `sc / (sc + fc)` where `fc` includes `failure` + `unknown` outcomes.
- **Pie charts** inset on each cell: black = failure fraction, color = success fraction. `ax.inset_axes()` + `pie_ax.pie()`.
- **Total count text** in each cell with white rounded bbox.
- **White grid lines** on minor ticks for cell separation.
- **Horizontal separator lines** between benchmark groups (`ax.axhline`).
- **Row labels**: `AIME26\nDirectLLM`, `AIME26\nOpenClaw`, etc.
- **Column labels**: 6 action names (renamed to short versions).

### Colors & styling
```python
CMAP_COLORS = ["#F8FBFD", "#D8ECE9", "#9CCFC1", "#3E9B87"]
cmap = LinearSegmentedColormap.from_list("plot_theme", CMAP_COLORS)
norm = Normalize(vmin=0, vmax=100)  # success percentage
```
- Font: DejaVu Sans, figure dpi=180
- `pie_size = 0.055` (fraction of axes)

### Extending for multiple models
`plot_count_abs_rel_qwen_gemma.py` imports `plot_count_abs_rel_qwen` as a base and adds a Gemma table. Structure: one script per model, then a combined script that stacks them.

### Dependencies
```python
matplotlib, numpy, pandas, re
```

---

## 3. Scatter / KDE Density Plots (`plot_entropy_token_scatter.py`)

### Pipeline
```
task JSONs (tasks/*.json) → extract_entropy_token_scatter.py → entropy_token_scatter.csv
                           → plot_entropy_token_scatter.py    → 2 PDFs (one per model)
```

### Input CSV format
`data/entropy_token_scatter.csv`:
```
model,harness,benchmark,task_id,sample,correct,mean_entropy,n_tokens
```
- `correct`: 0 or 1
- `mean_entropy`: float, trajectory-level mean token entropy (nats)
- `n_tokens`: int, total output tokens
- `sample`: 0 for single-sample, 0-3 for pass@4

### Data extraction notes
- **Task JSON structure**: Some are lists (AIME pass@4 has 4 samples per file; GPQA/HLE have single-element lists). Handle both.
- **Validation**: reject records where `correct` is null/non-boolean, `n_tokens ≤ 0`, `mean_entropy` is NaN.
- **Path registration**: `_reg(model, harness, benchmark, path)` — paths can be relative (to repo root) or absolute. Only registered if path exists.

### KDE implementation
```python
from scipy.stats import gaussian_kde
# Small jitter on x to avoid degenerate covariances:
x_jitter = x + np.random.uniform(0, 0.005, size=len(x))
kde = gaussian_kde(np.vstack([x_jitter, y]))
Z = kde(positions).reshape(X.shape)
```

### Tail clipping (HPD masking)
```python
def clip_kde_tails(Z, mass=0.85):
    # Sort Z descending, cumsum, find threshold containing `mass` of probability
    # Set Z < threshold → NaN so contourf skips low-density background
```
This is the key technique to avoid "background color bleed" — only the core density region is filled.

### Two-layer rendering
1. `ax.contourf()` — filled density with gradient colormap (white→color), alpha=0.45
2. `ax.contour()` — line boundaries on top, alpha=0.80, lw=0.7
No scatter points (removed for cleaner density view).

### Colormaps
```python
CMAP_CORRECT = LinearSegmentedColormap.from_list("kde_correct",
    [(1,1,1), (0.75,0.93,0.75), (0.40,0.83,0.40), (0.17,0.63,0.17)])  # white→green
CMAP_WRONG = LinearSegmentedColormap.from_list("kde_wrong",
    [(1,1,1), (0.96,0.75,0.75), (0.88,0.40,0.40), (0.84,0.15,0.16)])  # white→red
```

### Hyperparameters
| Param | Value | Notes |
|-------|-------|-------|
| `KDE_GRID` | 100 | Grid resolution |
| `KDE_LEVELS` | 10 | Contour levels |
| `KDE_ALPHA` | 0.45 | Fill opacity |
| `KDE_LW` | 0.7 | Contour line width |
| `KDE_MIN_PTS` | 10 | Min points for KDE (else skip) |
| `HPD_MASS` | 0.85 | Fraction of mass retained |
| `figsize` | (22, 14) | 3×4 subplot grid |
| `dpi` | 300 | |

### Layout
- 3 rows (benchmarks) × 4 columns (harnesses)
- Harness order: DirectLLM, OpenClaw, ZeroClaw, OpenCode
- Row labels: vertical (rotation=90), centered
- Column headers: fig.text at top
- Shared Y label: fig.text left, vertical
- Shared X label: fig.text bottom
- Merged tick labels: only left column has Y ticks, only bottom row has X ticks
- No suptitle, no legend

---

## Common Conventions

### Academic plot style
```python
SKILL_DIR = Path("/home/xxx/academic-plot/scripts")
sys.path.insert(0, str(SKILL_DIR))
from academic_plot import set_academic_style
set_academic_style(font_size=18)
```
If unavailable on new machine: use `plt.style.use("default")` + manual `rcParams`.

### Output organization
```
analyze_tools/
  figures/
    chords/     ← 48+ individual chord PDFs, flat naming
    scatter/    ← 2 KDE density PDFs
  data/
    six_action_statistics/       ← chord input CSVs
    entropy_token_scatter.csv    ← scatter input CSV
```

### LaTeX rules
- Never use `---` or `--` in LaTeX prose. Use commas, colons, parentheses instead.
- These rules are project-wide (from CLAUDE.md).

### Git safety
- Always create NEW commits (never amend) unless explicitly asked.
- Stage specific files, not `git add -A`.
- Never skip hooks or bypass signing unless asked.

---

## Quick Start on New Machine

1. **Install deps**: `pip install matplotlib numpy scipy pandas`
2. **Academic plot**: Either install the `academic-plot` package or replace `set_academic_style()` with manual `plt.rcParams` settings.
3. **Chord**: Update data paths in `load_transitions()`, update benchmark/model/harness lists, run `plot_action_chords.py`.
4. **Count abs/rel**: Replace the embedded markdown table with your data, update `BENCH_ORDER`/`HARNESS_ORDER`/`ACTIONS` lists.
5. **Scatter/KDE**: Write an extraction script matching your data format → CSV with columns `model,harness,benchmark,task_id,sample,correct,mean_entropy,n_tokens`. Then update `BENCH_ORDER`, `HARNESS_ORDER`, model names in the plot script.
