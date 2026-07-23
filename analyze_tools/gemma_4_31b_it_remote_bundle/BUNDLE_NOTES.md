# Gemma-4-31B-IT Remote Bundle Notes

## Scope

- Source: downloaded HF snapshot `alphadiana-mmmu-pro-gemma4-four-harness-20260624`.
- Model: Gemma-4-31B-IT.
- Benchmark: MMMU-Pro vision.
- Harnesses: DirectLLM, OpenClaw, OpenCode, ZeroClaw.
- No raw trajectories, artifacts, images, or logprob sidecars are included.

## Coverage

- DirectLLM: 1,730 observed / 1,730 expected.
- OpenClaw: 1,570 observed / 1,730 expected; 160 rows are missing from the source run.
- OpenCode: 1,730 observed / 1,730 expected.
- ZeroClaw: 1,730 observed / 1,730 expected.
- Total canonical trajectory rows: 6,760.

The local HF download does not contain the Gemma IMO-AnswerBench agent runs
listed as off-machine in `analyze_tools/data/DATA_INVENTORY.md`. GPQA, HLE,
and AIME Gemma data were already present on the paper machine per
`REMOTE_GATHER_PROTOCOL.md`; this bundle closes the downloaded MMMU-Pro gap.

## Conventions

- `correct` is `1`, `0`, or empty for null/unknown.
- `score` is derived from `correct` for exact-match MMMU-Pro records.
- Entropy is the existing natural-log token entropy stored in
  `token_entropy_stats.mean`.
- `n_tokens` uses `token_entropy_stats.n_tokens`, with completion-token fallback
  inherited from the existing six-action extractor.
- `traj_length` is the extracted six-action event count.
- `n_tool_errors` is unavailable in the standardized source and remains empty.
- `failure_taxonomy.csv` is header-only. The repository classifier is calibrated
  to GPQA/HLE and no MMMU-Pro-compatible taxonomy annotations were persisted;
  unsupported failure labels were not invented. The available aggregate failure
  evidence is included in `mmmu_pro_failure_case_summary.csv`.

## Integrity Caveats

- OpenClaw includes 163 nonvalid observed records and 160 absent expected rows.
- OpenCode includes 95 nonvalid observed records.
- ZeroClaw includes 104 nonvalid observed records.
- Null/unknown rows are retained rather than counted as wrong.
