# Issue 96 implementation acceptance

Implementation and local validation are complete on code commit `0919857d6117be4d8aa2cc5e6356f176d4645083` (2026-09-29). [PR #98](https://github.com/tmlr-group/AlphaDiana/pull/98) contains the integrated code; [PR #97](https://github.com/tmlr-group/AlphaDiana/pull/97) contains the website guide and downloadable examples. Subsequent handoff commits change documentation/evidence only. Tian's human review/handoff, manuscript synchronization, final reviewer approval, CI, merge and deployment remain separate.

The [machine-readable evidence index](issue96-acceptance.json) records all 28 selected cells, 84 task results, source revisions, artifact hashes, 28 rescoring receipts, figure hashes and independent analysis checks. Runtime evidence is retained at `/data0/weikaihuang/issue96-validation` on the validation machine; these absolute references are not public artifact hosting.

## Capability mapping

| Fig. 4 capability | Implementation and reuse | Acceptance evidence |
| --- | --- | --- |
| Dataset | `api.DatasetSpec` selects the existing benchmark registry; explicit schema and immutable pinned inputs | Seven fixtures, three ordered tasks each; input fingerprints compared across systems |
| Agent | `api.AgentSpec` selects existing registered harnesses; native adapters retain their own control loops | 28 cells / 84 valid scored executions with the same Qwen3.5-27B weights |
| Environment | `api.EnvironmentSpec` selects the existing sandbox registry; native container ownership remains native | Shared/per-task custom backend lifecycle tests; real Docker/Podman and native runs |
| Scorer | Existing package retained as `ad.scorer.create`; compatible base rescore contract with explicit environment opt-in | 60 offline and 24 environment-backed re-scores, original bytes/predictions/history preserved, agent resolution forbidden |
| Runner / compute | `api.EvaluationRunner` builds the same `ExperimentConfig` and calls the existing `Runner` | Python/YAML parity, persistence/failure/session tests, full matrix |
| Artifacts | `Artifacts` reopens records and portable sidecars, validates attachments and preserves derived-score provenance | All selected artifact/sidecar hashes checked; rescoring from reopened files |
| Analyzer | `AnalysisPlugin` produces tables and declarative figure specifications; existing measurements and archive data are reused | Seven four-system comparisons; 61 actual figures / 183 PDF+SVG+PNG files, plotted JSON and coverage |
| Paper concepts | Archive importer adapts actual data from the supplied paper figure index | 79 additional historical figures / 237 files; empirical concepts preserved without copying result constants or reproducing unavailable scripts |

The public guide records manuscript changes: explicit `benchmark=`, explicit harness `version=`, registered environment backends and `ad.scorer.create(...)`. The original illustrative `sandbox` shorthand is intentionally rejected rather than silently selecting a backend. Human agreement/manuscript edits are still pending.

## Executions and source selection

| Fixture | Systems | Valid scored tasks |
| --- | --- | ---: |
| AIME26 | Direct, OpenClaw, ZeroClaw, OpenCode | 12 / 12 |
| IMO-AnswerBench | Direct, OpenClaw, ZeroClaw, OpenCode | 12 / 12 |
| GPQA-Diamond | Direct, OpenClaw, ZeroClaw, OpenCode | 12 / 12 |
| HLE-Verifiable | Direct, OpenClaw, ZeroClaw, OpenCode | 12 / 12 |
| MMMU-Pro | Direct, OpenClaw, ZeroClaw, OpenCode | 12 / 12 |
| SWE Mini | SWE-Agent, OpenClaw, ZeroClaw, OpenCode | 12 / 12 |
| Terminal-Bench 2 | Terminus 2, OpenClaw, ZeroClaw, OpenCode | 12 / 12 |

All selected records are scored, not necessarily correct. Three tasks per system establish functionality, not benchmark rankings or paper-scale accuracy. Model: Qwen/Qwen3.5-27B, TP=2, vLLM 0.20.2, context 32768; exact weight manifest SHA256 `a6542ce67344c927dc3b3dc779dd4c0a63813d81d149ab5610544e66a4406b4a`. Native binary versions, image IDs, task inputs, SWE base commits and budgets are pinned in the acceptance manifests and preflight receipts.

Execution sources are deliberately recorded separately from delivery code:

- Unaffected baseline cells: `f719bbc3`.
- All seven OpenCode cells: recovery-classifier correction `882b1a38`.
- TB2/ZeroClaw: native trace-path correction `beb68351`.
- SWE-Agent: complete provider usage and native history projection `123b0c25`.
- Terminus: native role/call/result projection `d13e7baf`.
- MMMU/OpenClaw: image transport correction `0919857d`.

Replacement is by whole cell, never by successful task. `delivery-matrix-audit.json` retains superseded failures. `evidence/source-impact-0919857d.json` explains why unaffected cells remain applicable. `evidence/final-analysis-source-equivalence.json` verifies unchanged measurement/render/archive/replay implementations; annotation comparisons were all regenerated with the same explicit schema protocol.

## Checks and figure evidence

- Source tests: **202 passed, 11 skipped**, 9.20 s. Skips are opt-in external integration tests, not counted as passes. Actual native executions/restoration are documented separately.
- Installed wheel, outside checkout: **54 passed**, 5.39 s; all 128 loaded AlphaDiana modules came from the installed target. All 261 packaged Python source files match the committed revision byte-for-byte.
- Re-scoring: **60 offline + 24 environment-backed records** reproduced their original scores without resolving an agent; source bytes, predictions and score history were retained. Missing `/app/main.db-wal` in a replay snapshot is logged; the official verifier still reproduced the original valid result. No warning was hidden.
- Measurement audit independently recomputed accuracy denominators, **2,344 entropy bins** from raw probabilities, and task-local transition counts across seven comparisons.
- Annotation audit verified **1,207 model-labeled spans across all 84 task samples**, cache request hashes, a consistent pinned model/protocol, composition and task-local transition counts. Labels are model annotations, not human-reviewed truth.
- Every final current-run export has plotted-data equality, SVG category-label and output hash checks; historical exports have the same integrity checks. Representative visual checks include accuracy bars, dense entropy curves and faceted transition heatmaps. This is scoped visual inspection, not a claim that every PNG was manually inspected.
- Total deliverable: **140 conceptual figures / 420 PDF+SVG+PNG files**, plus plotted data, reports, provenance and coverage. Historical archive input was not modified.
- Website: production build/typecheck and desktop/mobile browser checks completed; both example images and all nine downloadable files were verified. Later prose updates receive separate production builds.

Current figure directories: `delivery-figures-04c5ce2d` (AIME/GPQA/HLE measurements), `delivery-figures-123b0c25` (IMO measurements), `delivery-figures-a0f941c2` (SWE measurements and the first five annotation comparisons), `delivery-figures-0919857d` (TB2/MMMU measurements and annotations). Use only the directories selected by the evidence index; older annotations are retained historical attempts. The archive export is `paper-archive-analysis-04c5ce2d`.

## Independent extensibility review and remaining limits

The [extensibility review](issue96-extensibility-review.md) separately checks custom components, configuration copying, environment lifecycle, scorer/replay contracts, native adapter boundaries and analysis plugins. No benchmark/harness dispatch was added to public composition or generic analysis. Probability capture is optional; native-specific role and transport projections stay in adapters. New measurements/taxonomies can be registered without changing Runner.

Observed limits remain explicit:

- Direct has no tools, so post-tool measurements are inapplicable. Some agent cells observe no post-tool generation in these three tasks; this is not a zero-entropy estimate.
- Native SWE/ZeroClaw usage is missing for two of three tasks; incomplete usage is excluded from complete efficiency estimates. Probability observations and model output-token usage are distinct quantities.
- All four TB2 systems scored zero correct answers. Both per-correct token/time ratios are undefined, so those two panels are skipped explicitly. Other conceptual panels and archive examples remain available.
- Entropy uses renormalized observed top-K probabilities and equal call means per bin; it is descriptive, not a causal effect or full-vocabulary entropy. Annotation spans depend on the stated taxonomy/context policy.
- Environment replay requires retained snapshot images and sidecars. A derived JSONL alone is not a portable archive. External OpenClaw gateways must enable Responses to accept images.
- Existing proxy lint findings were compared with the baseline and remain unchanged; no repository-wide lint-clean claim is made.

No unresolved blocking finding remains in the implemented and locally validated scope. As of the final GitHub check, both PRs are drafts and report no CI checks; this is **not** a CI pass. Tian and final reviewer decisions are not marked complete.
