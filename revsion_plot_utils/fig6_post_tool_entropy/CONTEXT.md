# Fig 6 — Post-tool-call token-entropy dynamics

See `../REVISION_PLAN.md` §3 for the contract, problems, and proposed revision.

## Originals
`original/<benchmark>_<model>_<harness>.{pdf,png}` for the tool-capable harnesses
(OpenClaw, OpenCode) across {HLE, GPQA, AIME} × {Qwen, Gemma}.
Representative: `HLE_Qwen_OpenClaw.png`.
`original/summary.json` — per-setting `n`: files, correct_files, wrong_files,
skipped_files, post_tool_segments, tokens, run_dir. Surface these `n` in the caption.

## Code
- `code/plot_entropy_post_tool.py` — aggregates only post-tool-call assistant tokens
  (each post-tool segment re-indexed from token position 1), bins on a log axis.
  The plotting fn to rewrite is **`render_entropy_plot` (~lines 216-302)**; the
  aggregation pipeline above it is reusable as-is.

## Current encoding (what to change)
Interleaved paired bars (green=correct, red=wrong) per log-position bin. No
correct/wrong key, no benchmark/model/harness label, noisy tail bins at full weight.
`MIN_BIN_COUNT` already gates sparse bins but they still render equally.

Target: two lines + SE/CI shaded bands, **keeping the current green/red** outcome
colors (key in caption, no in-figure legend; curves get direct end-labels), optional
bottom token-count rug, optional Δ-entropy curve, consolidated into small-multiples
(rows = benchmark, cols = tool harness), one figure per model.

## Run
```bash
python3 code/plot_entropy_post_tool.py   # writes analyze_tools/figures/post_tool_entropy/*.pdf,png
```
Note: reads raw run dirs referenced in `summary.json` (e.g. `results/...`,
`/path/to/xxx/...`) — needs those result stores present to re-aggregate.
