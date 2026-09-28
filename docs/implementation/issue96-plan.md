## Background and goals

Deliver a usable AlphaDiana implementation of draft Fig. 4 this week, with Tian handing an integrated, validated version to the original request owner and Zongze for review.

Acceptance is based on **Fig. 4 functionality working end to end**, rather than the number of repository components migrated. Implementation is underway; completion is determined by the evidence and unchecked gates below.

| Owner | Responsibility |
| --- | --- |
| Tian | Lead code organization, collaborate with Weikai, review logic and code throughout, integrate changes, and deliver the reviewable version |
| Weikai | Public API implementation, orchestration refactoring, and fixes |
| Original request owner + Zongze | Review Tian's delivery and acceptance evidence |

### Pinned sources and scope

- [Fig. 4, pinned draft](https://github.com/AndrewZhou924/AlphaDiana-draft/blob/d494a3e52f998a3f390962d125f808d11794351c/0-shared/figures/AlphaDiana-interface.pdf).
- [System design and interfaces](https://github.com/AndrewZhou924/AlphaDiana-draft/blob/d494a3e52f998a3f390962d125f808d11794351c/0-shared/sections/main.tex).
- Implementation baseline observed during planning: `f22bd993ad827042d153d048b74f5ab438dc618a`. Record the actual implementation base when work starts.

Fig. 4's capabilities and composition are required. Exact signatures may be refined by Weikai and Tian, with corresponding draft updates.

Non-goals: paper-scale experiment reproduction, new scoring criteria, rewriting native harness control loops, compulsory migration of unrelated components, or reproducing paper figure layouts.

## Architecture and interfaces

Provide `ad.dataset`, `ad.agent`, `ad.environment`, `ad.scorer`, `ad.runner`, and `ad.analyzer`, including the `runner.compute(...)` composition shown in Fig. 4:

```python
import alphadiana as ad

agent = ad.agent(
    harness="opencode-v1.3.2",
    model="google/gemma-4-31B-it",
)
runner = ad.runner(env="sandbox")
artifacts = runner.compute(
    agent=agent,
    data=ad.dataset("math-ai/aime26"),
    scorer=ad.scorer("numeric"),
)
report = ad.analyzer(artifacts)
```

This is the pinned figure's illustrative composition, not the acceptance model configuration. Acceptance uses the fixed model specified below. Final signatures and environment selection must be documented and synchronized with the draft.

```text
Python API                 Existing CLI / YAML
    |                             |
    +--------------+--------------+
                   v
         Shared execution orchestration
                   |
        Dataset -> Agent <-> Environment
                     |
                  Scoring
                     |
           Durable execution artifacts
              /                 \
     Independent rescoring     Offline analysis
              |                 |
       New score records    Structured results
```

- Preserve CLI/YAML compatibility and route both entry styles through shared orchestration.
- Reuse existing adapters, registries, session management, scoring, persistence, and analysis implementations.
- Preserve native harness behavior and existing isolation, memory, retry, and checkpoint semantics.
- Make artifacts reopenable across processes, with task/sample/attempt identity, outputs, traces, scoring evidence, execution status, and resource usage.
- Support rescoring without rerunning the agent. Environment-dependent verifiers require explicit environment restoration; insufficient restoration evidence must produce a clear error.
- Preserve original scores and record rescoring provenance separately.
- Provide structured accuracy, action-transition, post-tool entropy, and efficiency results. Add missing capture or annotation steps required by those capabilities.
- Additional model-based annotation uses explicit configuration and records its model/version provenance. The fixed execution model requirement applies to evaluated agents; analysis-model provenance is recorded separately.
- Updated scope: the unified analyzer must export publication-quality conceptual figures and plotted data/provenance, using the supplied historical archive where relevant.

## Milestones

```text
M0 Contract and fixtures
          |
          v
M1 Shared execution API
          |
          v
M2 Rescoring and analysis
          |
          v
M3 Full acceptance matrix and Tian delivery  <-- this week's target
          |
          v
M4 Final reviewer acceptance
```

### M0 — Freeze functional contracts and acceptance fixtures

**Owners:** Weikai + Tian. **Dependencies:** None.

- [ ] Map every Fig. 4 capability to existing implementation, required changes, and acceptance evidence.
- [x] Define factory composition, configuration precedence, artifact return/reopen behavior, rescoring, and analyzer outputs.
- [x] Resolve environment shorthand and versioned harness identifiers without silent substitution; record the support decision for the figure's Docker example.
- [ ] Pin three tasks per benchmark, dataset versions, harness versions, execution budgets, and the fixed model.
- [ ] Identify capture requirements for action transitions, entropy, costs, and environment restoration.
- [ ] Record agreed API changes requiring synchronized Fig. 4 edits and schedule implementation/review checkpoints.

**Acceptance:** Every figure capability has a concrete implementation path and test; the 28-combination validation manifest is fixed.

### M1 — Implement public composition and shared orchestration

**Owner:** Weikai. **Reviewer:** Tian. **Dependencies:** M0.

- [x] Implement the six public interfaces and `runner.compute(...)`.
- [x] Route Python and CLI/YAML entry points through shared validation, execution, scoring, persistence, and cleanup.
- [ ] Connect all components necessary for the acceptance matrix.
- [ ] Preserve native execution and lifecycle behavior.
- [x] Provide runnable Fig. 4 examples with explicit configuration.

**Acceptance:** Equivalent Python and CLI/YAML configurations exercise the same execution logic and produce compatible persisted artifacts.

### M2 — Implement reusable artifacts, rescoring, and analysis

**Owner:** Weikai. **Reviewer:** Tian. **Dependencies:** M1.

- [x] Reopen saved artifacts and rescore with compatible scorers without invoking the agent.
- [ ] Restore required environments and rerun SWE/TB2 verifiers from saved execution evidence.
- [ ] Connect accuracy, action-transition, post-tool entropy, and efficiency analysis through the public analyzer.
- [ ] Add missing probability capture, trace normalization, or annotation needed to produce actual analysis results.
- [x] Preserve score history, analysis provenance, execution failures, and missing-score distinctions.
- [ ] Report mathematically inapplicable measurements explicitly—for example, post-tool entropy for Direct with no tool calls.

**Acceptance:** Saved artifacts support independent rescoring and analysis. All four analysis capabilities produce real results on applicable new execution evidence; missing instrumentation is not accepted as completion.

### M3 — Validate all combinations and deliver Tian's integrated version

**Owner:** Tian. **Implementation/fix owner:** Weikai. **Dependencies:** M2.

- [ ] Run the complete matrix below.
- [ ] Review API boundaries, control flow, compatibility, persistence, and failure handling.
- [ ] Fix blocking findings and rerun affected checks.
- [ ] Verify offline rescoring, environment-backed rescoring, and analysis from reopened artifacts.
- [ ] Deliver one fixed commit/PR with examples, source mapping, validation manifest, logs, artifact references, and known limitations.

**Acceptance:** All 28 combinations pass functional validation, all Fig. 4 capabilities have evidence, and Tian delivers a reviewable version this week.

### M4 — Complete final review

**Owners:** Original request owner + Zongze. **Coordinator:** Tian. **Dependencies:** M3.

- [ ] Review the implementation against Fig. 4 and the agreed contracts.
- [ ] Inspect representative execution, rescoring, and analysis evidence.
- [ ] Address feedback and rerun affected validation on the final commit.
- [ ] Record final review decisions.

**Acceptance:** Both reviewers accept the delivery. Review and merge are tracked separately from this week's implementation handoff.

## Test matrix

### Real execution acceptance

| Benchmarks | Systems per benchmark | Combinations |
| --- | --- | ---: |
| AIME26, IMO-AnswerBench, GPQA-Diamond, HLE-Verifiable, MMMU-Pro | Direct, OpenClaw, ZeroClaw, OpenCode | 20 |
| SWE Mini | SWE-Agent baseline, OpenClaw, ZeroClaw, OpenCode | 4 |
| Terminal-Bench 2 | terminus-2 baseline, OpenClaw, ZeroClaw, OpenCode | 4 |
| **Total** | **Three fixed tasks per combination, one sample each** | **28 combinations / 84 planned executions** |

- Prefer the paper's **Qwen3.5-27B**. A custom model is acceptable if necessary, but one fixed model must serve the complete acceptance matrix, including required input modalities. Pin its exact identifier and configuration. A model change requires rerunning the matrix.
- Use identical task selections across systems within each benchmark.
- A pass requires successful execution, valid scoring, persisted evidence, and usable downstream analysis. An incorrect answer is acceptable; infrastructure or execution failure is not successful acceptance evidence.
- Retain failed attempts and distinguish reruns from additional samples. The 84 executions are the planned matrix, not a cap that permits hiding failures.
- Historical artifacts supplement regression coverage; they do not replace missing implementation or instrumentation.

### Additional checks

| Scenario | Required behavior |
| --- | --- |
| Python versus CLI/YAML | Equivalent deterministic fixtures preserve execution and result semantics |
| Offline rescoring | New judgments recorded without agent execution or overwriting original scores |
| Environment-backed rescoring | SWE/TB2 verifier runs after explicit restoration; unavailable restoration evidence fails clearly |
| Offline analysis | Reopened artifacts produce structured results without rerunning the agent |
| Failures and recovery | Scored wrong answers, execution errors, missing scores, retries, and resume remain distinguishable |
| Session lifecycle | Isolation, supported sharing policies, cleanup, and memory-related retry restrictions remain correct |
| Installation | Public imports and documented examples work from an installed package |

## Tracking and operational rules

- Update this issue at each milestone with commits/PRs, actual commands and results, failures/skips, blockers, and next steps.
- Check boxes only against observed evidence. Record changes to the pinned draft or agreed contract explicitly.
- Record implementation, local validation, CI, Tian's review, final reviewer approval, and merge status separately.
- Keep milestones as sections/checklists in this issue; do not create GitHub Milestone objects.
- Preserve unrelated work. Publishing this plan does not authorize claiming implementation, tests, human review, or merge complete.

## Initial observations

- Read-only inspection found existing orchestration, registries, adapters, and persistence to reuse; the inspected package initializer does not expose the Fig. 4 factories.
- No repository issue template was found during planning. Open/closed issue and PR searches for `interface`, followed by an issue search for `Fig. 4`, did not surface an apparent equivalent migration issue.
- Related work includes [process analysis #93](https://github.com/tmlr-group/AlphaDiana/issues/93) and [native execution #81](https://github.com/tmlr-group/AlphaDiana/issues/81). Reuse applicable implementations while preserving their scope and scientific definitions.
- **Current evidence:** planning and read-only inspection only. No implementation or validation was performed for this issue; CI has not been assessed.
- Record final reviewer GitHub handles when requesting review; the original request owner's identity is not inferred here.

## Final acceptance evidence

Pending. At delivery, record the exact commit/PR, completed matrix with artifact links, rescoring and analysis evidence, actual check results, unresolved limitations, CI status, and reviewer decisions. No acceptance boxes are complete at issue creation.


## Execution ledger and updated requirements

Working branch: `feat/issue96-composable-api`; base: `f22bd993`.

User-approved changes supersede the original plotting non-goal:
- [x] Typed, extensible public contracts; independent extensibility audit using custom components, with no benchmark/harness-specific routing added to the composition layer.
- [ ] Analyzer exports publication-quality PDF/SVG/PNG figures and machine-readable plotted data. Cover concepts in the supplied paper figure index, not pixel matching or copied result constants.
- [x] Update Diana website/documentation to expose the APIs and figure workflows.
- [x] Real 27B verification may use two genuinely idle GPUs; never stop another user's processes.
- [ ] Update this ledger and GitHub issue after milestones; continue work on incomplete acceptance items.

### Figure concept coverage

Source: `/data2/weikai/AlphaDiana-all-runs/paper-figures/FIGURE_INDEX.md`.
- [ ] Fig.5: macro/interactive accuracy comparison.
- [ ] Fig.6: overlapping error incidence.
- [ ] Fig.7: action composition and transitions.
- [ ] Fig.8,24,25: post-tool entropy and entropy/token relationships.
- [ ] Fig.9: macro efficiency (tokens/time per correct outcome).
- [ ] Fig.10–14: matched tool/skill/memory interventions and component utilization.
- [ ] Fig.15–16: solution cost and token allocation.
- Design diagrams/screenshots (Fig.1–4,17–23) are reference documentation, not empirical analyzer outputs.

### Checks and evidence

- Initial read-only inspection: existing Runner and result store reusable; issue comment also requests website update.
- GPU observation only: indices 2/3 empty, 6/7 nearly empty; no model launched yet.
- No implementation tests run yet. M0–M4 remain unchecked.

### Foundation checkpoint (implementation ongoing)

- Public composition delegates to the existing Runner; registry registration extracted without changing built-in inventory.
- Scorer namespace compatibility audit found and fixed an import collision: public factory is `ad.scorer.create`, retaining `alphadiana.api.scorer`.
- Initial checks: 7 public API tests passed; 64 existing native/single-rollout regression tests passed. Expanded tests underway.
- Analysis plugin contracts and declarative renderers added; custom components and plugin extension tests require no core edits.
- Archive adapter reads actual frozen data and hashes source inputs; conceptual exports undergoing visual inspection.
- Qwen3.5-27B vLLM 0.20.2 started on GPUs 2/3 at 127.0.0.1:18960, TP=2, context=32768. Real Direct validations started; no complete matrix claimed.
- Runtime outputs: `/data0/weikaihuang/issue96-validation` (own writable directory on a disk with more available space).
- GPQA shared cache lock issue: use task-owned copy; do not change shared cache permissions.
- M0–M3 remain open: full fixture pinning, true native SWE-Agent/terminus-2 adapters, remaining analysis integration, runtime matrix, and website review still required.

### Verification checkpoint

- 77 focused/regression tests passed, 1 optional replay test skipped in the default run (`foundation-tests.log`).
- Separate opt-in real rootless Podman replay: 2 tests passed in 23.50s (`verifier-replay.log`), restoring solver-written state after removing the original container; verifier drift was rejected.
- Initial archive export: 79 figures / 237 PDF+SVG+PNG files, no skipped figures; visual inspection prompted fixed-size charts, faceted transition matrices, and log-scaled post-tool token offsets. Revised archive integration test running.
- Real AIME Direct: first two tasks persisted valid scores (one correct, one wrong); correctness is not the functional pass criterion. Full 3-task cell still pending at this checkpoint.
- Public signatures documented in `docs/getting-started/python-api.md`; root scorer package imports preserved.

### Active runtime locations (continuation handoff)

- Source: `/data2/weikai/home_moved/projects/alphadiana_issue96_api`, branch `feat/issue96-composable-api`.
- Python: `.venv/bin/python` (isolated venv with inherited alphadiana dependencies, plus scipy/ruff installed locally).
- Model: Qwen/Qwen3.5-27B on GPUs 2,3; vLLM server port 18960; log `/data0/weikaihuang/issue96-validation/model/server.log`; launched unified-exec session 30888. Stop only this task's model when finished.
- Direct matrix: session 30667 (AIME/HLE/IMO/MMMU), session 41290 (GPQA using own cache). Logs under validation/evidence. Source manifest under validation/direct-manifest.json.
- Supplied historical figure archive remains unmodified.
- Remaining core gaps: attachment fidelity and portable rescoring references; fresh-run post-tool entropy and model annotation integration; all-harness matrix; native SWE-Agent/terminus-2 registry adapters; snapshot provenance/cleanup review; website page integration and full final checks.

### Evidence fidelity checkpoint — 2026-09-29

- Binary benchmark attachments now persist by content hash and are restored/hash-checked during rescoring. Derived runs retain explicit references to source sidecars and copy expected sample counts; source sidecars must remain available.
- Fixed analysis trace resolution against the actual artifact root, including rescoring into another directory; prompt messages no longer count as agent actions. Both Direct registry spellings use the same extraction path.
- Focused API/path tests: 16 passed. Combined API/native regression selection: 43 passed, 8 opt-in runtime checks skipped; `evidence/evidence-fidelity-tests.log`. Skips are not runtime acceptance.
- Direct AIME, GPQA and HLE each completed three valid scored records. IMO/MMMU remain running; these are preliminary receipts, not final-commit matrix acceptance.
- OpenCode 1.3.2 Podman GPQA validation started (session 17023); first upstream model request succeeded. Configs/manifest and log under task-owned validation directory.
- Matrix receipts now include config/artifact hashes and model identity, enforce one sample/no retry, reject duplicate tasks and check a supplied pinned task sequence. Full committed 28-cell manifest still pending.
- Native SWE-Agent and Harbor terminus-2 adapters are work in progress; no native baseline acceptance claimed.
- Archive entropy axis semantics need further audit: historical trajectory token bins must not be described as independently verified offsets after tool feedback. Fresh capture attribution remains open.

### Process-analysis and native-runtime checkpoint — 2026-09-29

- Canonical logprob capture now records call identity, request order, token position within the request and immediate tool-feedback conditioning. Mixed streaming/non-streaming captures are retained without duplicate aggregated responses; metadata does not enter client/model payloads. Explicit output budgets resolve conflicting OpenAI token-limit aliases.
- `RequestEntropy` consumes actual attributed sidecars, reports missing instrumentation, averages call means within bins, and exports plotted data/source hashes. It is part of default analysis; no attribution is fabricated for old runs or Direct.
- `ActionAnnotations` is opt-in with explicit model/version, extensible taxonomy, fixed span policy, validated structured responses and request-bound cache provenance. Reuses issue93 category definitions, not its execution transport. Labels are model-generated, not human reviewed.
- Fresh ZeroClaw GPQA pilot: 3,464/3,464 captured tokens attributed, including requests after tool feedback. `fresh-figures-v2/` exports accuracy, time efficiency, action transitions and request entropy, with PDF/SVG/PNG plus tables and source hashes. Two token-usage-dependent plots explicitly skipped because that pilot did not report total usage. Visual inspection fixed clipped heatmap titles and clarified x/y semantics.
- Runtime integration findings repaired: OpenCode lifecycle events/final-output duplication distorted traces; ZeroClaw bridge ignored configured trace mode and retained readiness after failed warmup; OpenClaw agent/runtime both started capture proxies, sending a container-only hostname from the host. These changes retain native execution ownership and have targeted regressions.
- Focused analysis/API/native regression selection: 56 passed before the additional mixed-transport ordering check; final selection (excluding the five uncommitted native-baseline tests) passed 53 tests in 6.99s; log `evidence/process-analysis-final-tests.log`. Separate request-attribution tests: 5 passed.
- Preliminary matrix receipts now show 11 distinct passing cells (33 task executions): all 5 Direct, GPQA on OpenClaw/OpenCode/ZeroClaw, AIME on OpenCode/ZeroClaw, HLE on ZeroClaw. These pilots use evolving configs/commits and are NOT final matrix acceptance.
- Failed pilots retained: ZeroClaw image without Python and rootless host-loopback routing; OpenCode context/output reservation conflict and incomplete HLE result; OpenClaw double proxy (aborted after two observed failures, own container cleaned); SWE-Agent Git ownership and shared Docker daemon's unavailable proxy.
- OpenClaw npm version 2026.3.14 was unavailable; explicitly installed/validated 2026.3.2 in task-owned image `localhost/issue96-openclaw:2026.3.2`, ID `8be401533f084148d180e962a3dd3bd5c4d5c09b365499b8f94b764981bb2791`. Build temporary storage moved to the task-owned /data0 directory after /var/tmp filled; no shared daemon configuration changed.
- SWE-Agent 1.1.0 source commit `0f4f3bba990e01ca8460b9963abdcd89e38042f2`, SWE-ReX 1.4.0. Git trust exception is isolated in task-owned `gitconfig`, not the user's global config. First official task image pulled via Podman; native Podman execution/reverification now being attempted.
- Active handles: model 30888; OpenCode remaining reasoning cells 1175; ZeroClaw remaining reasoning cells 73895; OpenClaw remaining reasoning cells 26695; task-owned Podman Docker-API service 28129; SWE-Agent pilot 66222. Inspect logs/handles before restarting any task.
- Native SWE-Agent/terminus adapters remain uncommitted work in progress. Full 28-cell fixture freeze, interactive baselines/matrix, complete fresh trace coverage, website integration, extensibility audit and final-commit reruns remain open. CI/human review/merge not claimed.

### Pinned matrix and native baseline checkpoint — 2026-09-29

- Added all 28 explicit acceptance YAMLs, a coverage manifest and 21 selected task content fingerprints under `configs/acceptance/issue96/`. Pinning covers multimodal attachment bytes, SWE reference/test metadata and complete TB2 task assets; local absolute paths do not affect hashes. The driver validates actual loaded inputs before execution, rejects unknown cell selections, and requires clean, unchanged source for final acceptance. This is input/config preparation, not completion of M0 runtime provenance.
- Optional native adapters invoke upstream SWE-Agent run-batch and Harbor terminus-2 jobs; they preserve upstream trajectories/results and reject absent/nonbinary verifier evidence. Native execution stays outside the generic composition API. Harbor actually installed: 0.6.1. Native dependencies remain external/optional.
- TB2 OpenClaw/OpenCode now consume explicit generation settings via the existing provider proxy even when logprob capture is disabled; Terminus forwards configured native LLM kwargs. OpenClaw SWE installation accepts the explicit version and preserves custom install commands.
- Found an existing dependency contract defect through actual SWE scoring: `swebench>=4` installed 5.0.2, whose constants no longer match this scorer. Bound the optional dependency to `>=4,<5`; installed 4.1.0 in this worktree and verified all required runtime imports. The first SWE-Agent v5 task solved natively but failed scoring under 5.0.2; it is not accepted and requires rerun/rescore.
- SWE-Agent Podman pilot v4 failed before solving: upstream standalone-Python image bootstrap expects a `sha256:`-prefixed build ID. Docker pilot v5 uses the official image's existing Python with explicit empty `python_standalone_dir`. The first task made real native tool calls and completed a prediction. Official images were transferred from Podman to Docker with task-owned temporary storage; shared daemon configuration was not changed.
- Harbor/Terminus v1 was correctly rejected by the normal config validator because `native` was not a version. Corrected the explicit config to `harbor-0.6.1`; v2 is executing actual native task assets. No native interactive cell has passed yet.
- Focused matrix/native/API contracts: **25 passed in 3.67s** (`evidence/fixture-native-contract-tests.log`). Native regressions and request capture: **32 passed in 3.17s** (`evidence/native-integration-regressions-v2.log`). New native/driver/test modules pass Ruff; no repository-wide lint/CI claim. All 28 configs pass the ordinary Runner config validator.
- Preliminary distinct passing cells now **17 / 51 executions**: all Direct; all GPQA; all AIME; HLE on OpenClaw/ZeroClaw; IMO on OpenClaw/ZeroClaw; MMMU on OpenCode/ZeroClaw. OpenClaw MMMU has 2/3 valid records; its empty assistant response remains a failed pilot. These evolving-source pilot runs do not satisfy final clean-revision acceptance.
- Active sessions: model 30888, owned Podman service 28129, SWE-Agent v5 36046, Terminus v2 79041, runtime-image transfer to Docker 44656. SWE-Agent/Terminus were launched before source edits finished; even if their task records become valid, source-stability checks will prevent treating them as final acceptance.
- Disk pressure remains: task-owned temporary files now use `/data0/weikaihuang/issue96-validation/build-tmp`; generated test files accidentally created in the checkout during low-space fallback were removed after confirming ownership. No shared storage prune performed.
- Still required: successful native baselines/all interactive cells, remaining reasoning failures, snapshot/replay for native Terminus, full fresh trace coverage, website changes, independent extensibility audit, final clean-commit matrix, PR/review evidence. M0–M4 remain open; human review/merge/CI separate.

### Runtime evidence audit and website checkpoint — 2026-09-29

- Website branch `feat/issue96-website`, based on `clean-website` `b8038638`, now contains the API/rescoring/figure guide, navigation and two actual pilot figure examples with PDF/SVG/PNG, plotted data and provenance hashes. Commit `8b75c9b3`; draft PR https://github.com/tmlr-group/AlphaDiana/pull/97. Worktree `../alphadiana_issue96_website` preserves the user's original dirty website checkout. Lockfile install, `npm run typecheck`, and `npm run build` passed; browser layout review, deployment and CI assessment remain pending. The guide labels the three-task examples and model annotations explicitly.
- Fixed an observed TB2 OpenClaw validity defect: a missing API key caused native exit 1 with no model call, but the old adapter classified the verifier's zero as a valid execution. Authentication errors and other nonzero native exits now produce failure metadata recognized by score-status inference. Added tests with the observed diagnostic and a nominal verifier zero. OpenClaw v2 is explicitly INVALID despite its original record labels; audit receipt `evidence/tb2-openclaw-v2-review.json` preserves this correction without rewriting original evidence.
- Native OpenCode model context/output limits and Harbor model-info settings now reach their native configuration. The initial TB2 configs otherwise allowed upstream default context assumptions to exceed the actual server. SWE-Agent accepts explicit per-instance image overrides and records the resolved image; tests ensure reference patches/tests never enter solver inputs.
- Important correction to the image diagnosis: `podman save` treats extra positional image names as additional tags unless `--multi-image-archive` is set. Our initial bulk transfer therefore incorrectly aliased Docker tags for SWE 11848, TB2 overfull-hbox, ZeroClaw and OpenCode. The missing SWE base commit was observed in the WRONG transferred image, not proof that the upstream 11848 image lacked it. Earlier speculative claims about upstream history pruning are superseded. All affected interactive campaigns are invalidated in `evidence/image-transfer-correction.json` and must be rerun.
- Re-transferred each affected image separately. `evidence/image-transfer-verified.json` now verifies **9/9 Podman/Docker immutable image IDs match**. Correct 11848 image contains the pinned original commit. Task-owned SWE images additionally reset `/testbed` to the exact selected Mini dataset base commit; no task IDs, reference patches or verifier criteria were substituted. The temporary derived 11848 image built from the wrong parent is superseded by `swe11848-correct-base` and must not be used for acceptance.
- TB2 OpenClaw runtime source required the repository's `/app` + `/usr/local` payload layout. Built task-owned `localhost/issue96-openclaw-tb2:2026.3.2` from the verified installed 3.2 image, copying the existing payload without redownloading or changing native code. Native OpenClaw v3 now makes actual provider requests with a nonempty local key. The configured `EMPTY` placeholder was intentionally treated as missing by this preexisting TB2 setting resolver; final campaign must use a nonempty local key.
- Focused runtime contract/API/native status/capture tests: **46 passed in 6.51s** (`evidence/runtime-contract-repairs-tests.log`); relevant new files pass Ruff. No CI claim.
- Active handles: model 30888; Podman service 28129; SWE-Agent v6 98437; original Terminus v2 79041; TB2 OpenClaw v3 82574; TB2 OpenCode/ZeroClaw v2 31746. Original matrix 81898 and transfer handles are finished. Source/config/image changes mean these remain diagnostic pilots. Re-check each handle before restarting.
- Remaining work is unchanged in scope: correct full interactive execution, native Terminus replay, final input/runtime freeze and all 84 executions, missing fresh trace/token coverage, independent extensibility audit, browser review and integrated PR handoff. No M0–M4 completion or human acceptance is claimed.

### SWE verifier replay checkpoint — 2026-09-29

- Real verifier execution exposed preexisting SWE scorer/sandbox behavior that rebuilt local dependency images even for remote/prebuilt instance specs. Remote image paths now use the existing cached image or pull it; local-build behavior is retained. Explicit remote refresh requests pull the image rather than deleting it and attempting an unrelated source build.
- Preserved unified-diff whitespace. The scorer previously stripped the final newline from native git patches, causing the official verifier to reject them as malformed. It now only completes a missing final newline and preserves context whitespace. This was confirmed against the saved native v5 predictions and official verifier logs.
- Repeated rescoring no longer overwrites the saved solver output with a scorer's interpreted label such as `resolved`. That label is retained separately in `rescoring.scorer_predicted`. Added a chained-rescore regression so a derived artifact can be scored again using the same raw prediction.
- Actual replay of two saved nonempty v5 predictions now produced official `report.json` and `test_output.txt` under `swe-rescore-preserved-verifier/`; both remained unresolved. The third original task had no prediction and was explicitly empty. Original execution errors remain `runtime_error` in all derived records and are NOT converted into acceptance passes. No model calls occurred in the replay script; original JSONL retained. Receipt/log: `evidence/swe-rescore-v5-patch-preserved.log`.
- Focused verifier/API/evidence/native/fixture tests: **43 passed in 3.78s**, `evidence/replay-native-contracts-final.log`. Remote sandbox tests cover cached, missing and explicit refresh paths; scorer tests cover remote-image build avoidance and significant patch whitespace. Repository-wide lint/CI still not claimed.
- Stopped the invalidated original Terminus v2 pilot with SIGINT to its owned driver, retained its partial native trajectory, and removed only its leftover container `adaptive-rejection-sampler__26omqhc-main-1`. Its receipt is `aborted`, not passed. Native Harbor's default maximum is 1,000,000 turns; the acceptance config now explicitly uses 30 turns and pinned model limits. Terminus v3 will use the corrected configuration.
- Website draft #97 remains available; no deploy/merge. All final acceptance milestones remain open.

### Native verifier snapshots and browser review checkpoint — 2026-09-29

- Native Terminus can now retain an immutable Docker snapshot through Harbor's `on_verification_started` hook, before verifier mutations. The separate configured Harbor Python owns execution; the shared snapshot primitive has no native-runtime imports. Existing replay imports remain compatible. Task/trial/container identity is checked before capture.
- Real constructed Harbor/Oracle fixture confirmed that the snapshot contains solver state, excludes verifier mutations and can be replayed successfully. This is integration evidence, not a benchmark/model pass. Tests also confirm that task containers are removed while their shared source image survives: Harbor `delete=True` previously removed shared images, so jobs now use `delete=False` while retaining native container/network teardown.
- Native timeout handling accepts only an observed `AgentTimeoutError` with completed agent execution timing and an actual binary verifier reward. Setup/environment failures and absent or nonbinary rewards remain failures.
- Snapshot/replay contracts: **23 passed in 53.53s**, including opt-in real Podman and Harbor/Docker integrations (`evidence/native-snapshot-contracts.log`). Subsequent native/unit regression: **41 passed, 2 skipped in 3.86s** (`evidence/native-snapshot-unit-final.log`); runtime opt-ins were exercised separately above. Relevant files pass Ruff. No CI claim.
- Website browser review is complete for the previous published draft revision: Chromium desktop 1440px and mobile 412px, no JavaScript errors/horizontal overflow, both real figure previews loaded. All nine downloads returned HTTP 200 and identical bytes. Evidence: `website-browser-review.json`, `website-download-check.json`, four screenshots. The newly added native snapshot documentation still requires the next website build/checkpoint. Deployment and human review remain pending.
- Native SWE-Agent v7 has an observed official-verifier pass on Django 11815; other tasks and campaigns remain diagnostic until a clean fixed-revision matrix is completed. All M0–M4 acceptance gates remain open. Next: complete ZeroClaw native trace/usage capture, pin actual runtime images/configuration, then rerun the full matrix and independently audit extensibility.

### Native trace and model-budget checkpoint — 2026-09-29

- Inspected the exact ZeroClaw 0.6.9 source and captured actual native events on the owned 27B endpoint. The current runtime writes trace files correctly; the normalization layer ignored its `event_type`/`payload` schema. A shared ZeroClaw projector now serves both ordinary tasks and TB2, preserving tool calls/results and final responses, recording unmapped event counts, and aggregating token usage only when every observed request has complete usage. Missing counts are not zero-filled. Legacy message traces retain their existing projection.
- Real native diagnostic: two provider calls and one Python calculation through the shell tool, with **25,175 prompt + 45 completion = 25,220 tokens** preserved. Captured seven-event fixture is committed with provenance; this constructed diagnostic is not a matrix task. Raw native trace remains available and no private reasoning is invented.
- Full local test directory after trace changes: **132 passed, 11 skipped in 7.20s** (`evidence/trace-full-unit-tests.log`). Skips are explicit external-runtime opt-ins. Focused trace/API/entropy checks: **24 passed**.
- SWE OpenCode pilot exposed a separate real context-limit failure: its container manager did not consume `context_limit`/`output_limit`, although the CLI path did. The container configuration now passes those settings to the native model definition. Added optional `opencode_version` installation and observed-version checks while preserving explicit custom install commands. Focused trace/budget contracts: **12 passed** (`evidence/native-trace-budget-contracts.log`); the failing native cell still requires a rerun.
- Website snapshot guide build/typecheck passed and commit `8ffdb3ab` updates draft #97. Implementation snapshot commit `8dd7b580` and observed evidence were posted in issue comment https://github.com/tmlr-group/AlphaDiana/issues/96#issuecomment-5875522753.
- Final runtime freeze, fresh full matrix, independent extensibility audit and complete final analysis evidence remain pending. No milestone completion or human review claim.

### Persistence boundary follow-up — 2026-09-29

- Fresh GPQA/ZeroClaw v5 completed all three pinned tasks on clean `ae42657e`, with complete native usage and 1,724/1,724 attributed logprob tokens. This remains a diagnostic cell: inspection of the saved artifact exposed duplicate CLI transcript appending after an observed native final event. The projector now treats that event as authoritative while retaining raw stdout separately.
- Followed the trace through ResultStore and fixed its existing canonicalization precedence: explicit `tool_result` was incorrectly overridden by the generic `role=tool` alias for calls. Native call arguments now also reach the standard structured tool-call field. Regression tests exercise the persisted boundary, not only the producer.
- Full local regression after these changes: **138 passed, 11 skipped in 7.24s**, `evidence/native-trace-persistence-tests.log`. Relevant new modules/tests pass Ruff and diff whitespace checks pass. The concurrent SWE/OpenCode rerun will be diagnostic because this source fix occurred during execution.
- Terminus v3 completed all three native tasks with observed verifier rewards (hbox resolved; the other two unresolved). It has no retained snapshots and ran while source changed, so it is not final acceptance. Its old delete policy removed all three base-image tags; restored them individually from the verified Podman images. The new job policy preserves those images.
- Recorded full SHA-256 hashes of all eleven local 27B weight shards plus model/tokenizer/configuration files in `evidence/model-weight-provenance.json`. Prepared updated 28-cell runtime YAMLs outside the checkout; they still need review/application and final validation.

### Independent extensibility review and runtime preparation — 2026-09-29

- Completed the bounded [extensibility review](issue96-extensibility-review.md) with custom component/environment execution. Four shared/per-task and success/failure lifecycle cases pass. No core routing edit is required for these extensions. Human review and full functional acceptance remain separate.
- Review found that failed attempts lost binary attachments. Both persistence paths now share a content-addressed writer and reject corrupted reused inputs. Focused API/evidence tests: **20 passed** before adding the four retained environment audit cases.
- Applied all 28 prepared runtime configs: interactive cells use the validated Docker path, exact SWE base-commit image tags/overrides, explicit SWE OpenCode 1.3.2, separate TB2 OpenClaw payload identity, and native Harbor snapshot Python. Runtime images are supplied as verified immutable IDs; local service keys are nonempty. Source changes mean concurrent diagnostic runs cannot satisfy fixed-revision acceptance.
- Checked individual implementation items only where code/tests/docs provide evidence. Full M0 runtime freeze, M1 all-cell integration, M2 final new-run analysis/replay, M3 delivery and M4 human acceptance remain open. Website work is implemented and browser/build checked in draft #97; merge/deployment not claimed.

- Final local regression for this preparation: **143 passed, 11 skipped in 7.38s**, `evidence/final-config-contract-tests.log`; targeted Ruff and diff checks pass. All 28 resolved configs and all seven fixture fingerprints passed runtime preflight. Observed versions: OpenClaw 2026.3.2, OpenCode 1.3.2, ZeroClaw 0.6.9, Harbor 0.6.1, SWE-Agent 1.1.0, SWE-ReX 1.4.0, vLLM 0.20.2, swebench 4.1.0. Docker SWE HEADs exactly match pinned task base commits.
- SWE OpenCode v2 still hit context overflow despite receiving the true context/output limits. Pinned upstream 1.3.2 source shows an explicit input limit enables earlier native compaction; all OpenCode acceptance configs now declare input_limit=24576, context_limit=32768 and output_limit=8192. The adapters pass through this native setting; actual rerun remains necessary. No task data or verifier criteria changed.
- Matrix receipts now hash the fully resolved configuration in addition to YAML bytes, and can bind a hash-checked runtime provenance file. This distinguishes environment substitutions that would otherwise share the same template hash.


### Installed-package validation checkpoint — 2026-09-29

- Actual wheel installation outside the checkout exposed omitted OpenClaw configuration. Aborted the newly started `b1ccbb16` campaign; its receipts remain under `final-b1ccbb16` with `acceptance-invalidated.json` and do not count toward final acceptance.
- Package required native configuration, container templates and SWE scripts. Resolve package-prefixed defaults from the installed package as a fallback, preserving explicit caller-relative overrides. No registry or orchestration change.
- Rebuilt/installed wheel into an isolated target. Seven resource and custom-environment lifecycle cases pass outside the checkout; every loaded AlphaDiana module comes from that installed target (`evidence/wheel-packaging-runtime.log`). The first verification used an incorrect Containerfile basename; corrected it to the repository's actual `Containerfile.podman-controller` before the passing run.
- Local regression: **146 passed, 11 skipped in 7.39s** (`evidence/packaging-regression.log`); runtime skips remain separate from actual native integration evidence. Complete fixed-revision matrix will restart after this correction. No full acceptance claim.

### Offline native usage follow-up — isolated from active matrix

- Fixed `f719bbc3` matrix exposed OpenClaw gateway summaries with empty token usage although retained task sessions contain complete per-request native counts. Prepare this analysis-only correction in `feat/issue96-usage-analysis`; the active execution checkout remains unchanged.
- Offline efficiency analysis now reads the selected, task-identity-checked session; excludes other runtime sessions, validates message identities and native input/output/cache totals, and requires every assistant usage record. Source/hash/count provenance accompanies each recovered sample. Custom usage extractors remain injectable; no Runner/harness routing changes.
- Actual retained SWE observations recovered 3,029 and 5,908 output tokens across 30 and 50 assistant messages respectively. Native totals include repeated prompt tokens. AIME/OpenClaw also recovers all three task usages. Do not equate native output totals with observed logprob count.
- Follow-up tests: **153 passed, 11 skipped in 7.54s**, including missing usage, wrong task identity/schema, duplicate messages, inconsistent totals, persisted zero counts and custom extractors (`evidence/native-usage-followup-full-tests.log`). Code passes Ruff. Integration into the delivery branch and final analysis at a pinned revision remain pending.
- Final AIME/Direct and AIME/OpenClaw cells now pass (six valid executions). OpenClaw has 7,899/7,899 observed probability records attributed, with actual post-tool bins. New model annotation exports use explicit Qwen3.5-27B weight-manifest provenance; visual/data checks remain in progress. Full matrix not complete.

### Visual review correction — categorical bar labels

- Inspecting fresh native-usage and annotation exports exposed numeric x ticks where category labels should appear. The renderer reset the x scale after installing categorical ticks, replacing Matplotlib's fixed locator/formatter. Preserve categorical bar ticks by applying numeric scales only to non-bar plots.
- Actual exported SVG regression checks grouped and ungrouped category text: **2 passed in 0.85s** (`evidence/category-label-regression.log`). Earlier render/visual evidence does not prove bar-label correctness; regenerate affected historical/new-run exports and website assets and inspect them again before final acceptance.
- This is an offline renderer correction in the isolated analysis branch; active model execution source remains `f719bbc3`.


### OpenCode offline usage and refreshed figure evidence

- OpenCode likewise stores native usage in `step_finish` rather than the gateway summary. Added an extractor through the same analysis extension point. It verifies saved task prompt/session identity, unique part IDs and complete start/finish pairing; incomplete data remains unavailable and native output semantics are preserved.
- Actual AIME/OpenCode source artifacts recover **705, 2,036 and 830** output tokens. Fourteen combined native-usage tests pass (0.45s), covering both adapters and custom extraction (`evidence/opencode-offline-usage-tests.log`); actual artifact proof is `evidence/opencode-offline-usage-real.json`.
- Corrected renderer `4b786d56` re-exported **79 historical figures / 237 files**. All plotted data match reports; all **43 bar-chart category sets** match SVG text. Macro accuracy and overlapping-error examples were visually rechecked. Fresh measurements (seven figures / 21 files) and annotations (two figures / six files) also pass export/data/category checks. This is automated full export coverage plus scoped visual review, not a claim that every PNG was manually viewed.
- Website commit `a6ebbee9` refreshes the two examples from pinned AIME/OpenClaw execution, explicit model annotations and corrected rendering. Typecheck/build pass; Chromium 1440/412px has no JS errors/overflow and both images load; all nine downloads match committed assets/provenance hashes. SVG whitespace normalization preserves XML geometry/text semantics. Draft #97 remains unmerged.
- Final matrix currently has six passing cells / 18 valid executions; re-audit live receipts before citing totals. Analysis fixes remain isolated until execution freeze ends. Full matrix/handoff and human review remain open.
- Full regression after both usage adapters and the renderer correction: **162 passed, 11 skipped in 6.99s** (`evidence/offline-usage-renderer-full-tests.log`). Latest independent matrix audit at 03:03 reports **nine passing cells / 27 valid executions**, with no completed-cell failures. Tests and runtime receipts remain separate evidence.

### OpenCode recovered-error status correction

- The baseline SWE/OpenCode cell has two valid records and one invalid `provider_error`. Inspected the failed Django 11790 native trace: 131 events, an intermediate context overflow, then two explicit same-session `step_finish(reason=stop)` responses with final text, native exit code 0. The adapter classified any historical error as final failure even after native recovery.
- Correct the existing OpenCode error classifier to require successful native exit plus explicit same-session terminal response after an error before considering it recovered. Incomplete steps, missing final text, different sessions, nonzero/unknown exit and later errors remain failures. Raw error events stay in artifacts. No native control-loop or compaction policy replacement.
- Seven recovery-status cases pass, including a check against the actual retained 131-event trace. Full local regression: **170 passed, 11 skipped in 7.16s** (`evidence/opencode-recovery-full-tests.log`). Original failed result remains invalid; real rerun is required.
- OpenCode container artifacts expose the same event file through `response_stream`; the offline usage extractor now supports that alias. Actual SWE traces recover counts across 32, 16 and 11 completed steps, with hashes/provenance. This is measured usage, not retroactive execution acceptance.
- Keep the baseline campaign running to expose remaining issues. Rerun affected OpenCode combinations on the corrected clean revision and explicitly track superseded failures/source revisions. The final handoff must verify unchanged execution paths for any retained unaffected baseline cells and recompute offline analysis on the delivered revision; do not claim old agent executions ran newer code.

### Native instrumentation follow-up — isolated from active campaigns

- Coverage inspection found that SWE-Agent and Terminus lacked provider probability capture, while TB2/ZeroClaw looked for its native trace outside the actual workspace-relative location. These are analysis gaps even where scoring is valid; the affected cells require new executions.
- Add optional shared provider capture around the two upstream-owned subprocesses. Native messages, generation settings, tool loops and retry behavior remain upstream-owned. Capture is opt-in; both acceptance configs enable it. Partial observations survive native failures.
- Terminus sends terminal output as user text. Attribute only exact SHA-256 matches to saved Harbor observations containing native `bash_command` evidence, excluding initial instructions, parser errors, completion-only and ambiguous observations. Preserve request/call/step provenance; do not label every user request as tool feedback.
- Pinned ZeroClaw 0.6.9 `observability/runtime_trace.rs::resolve_trace_path` joins relative paths to the workspace. TB2 now passes an absolute task-owned trace path; the base adapter default and all other generated configuration remain equivalent.
- Local regression: **181 passed, 11 skipped in 8.59s** (`evidence/native-instrumentation-full-tests.log`), including actual HTTP capture, failure cleanup, unchanged native request content and conservative attribution cases. External skips are not native validation.
- [x] Isolate changes from active `f719bbc3` and `882b1a38` execution checkouts.
- [x] Verify local capture/attribution/path contracts and shared-extension boundaries.
- [ ] Rerun all three affected native cells (SWE-Agent, TB2/Terminus, TB2/ZeroClaw) on a clean pinned revision.
- [ ] Inspect actual probability/action/usage coverage and regenerated figures; retain source-impact and supersession evidence.
- [ ] Integrate analysis/runtime fixes into delivery PR and perform final package/acceptance audit.

### Dense-figure review

- Visual review of fresh HLE entropy found that drawing a marker for every 32-token bin obscured long requests on a log axis. Bound marker density while retaining every measured line point and all exported data; use the neutral title `Entropy by request position` because some tasks never invoke a tool.
- Actual SVG/data regression: **3 passed in 1.10s** (`evidence/dense-line-export-tests.log`). An initial display-distance marker setting still placed 300 markers on an oscillating line; the regression caught it and the final bounded marker count passes. This correction is presentation-only in a separate delivery worktree; running execution revisions remain frozen.
- First corrected native TB2/ZeroClaw task now retains 93 native events, 23 command/result pairs, complete native usage and 853 probability observations; SWE-Agent first task retains 2,552 probability observations. These are partial-run observations, not complete-cell acceptance.

### SWE-Agent efficiency semantics correction

- Actual captured probabilities exceeded native `tokens_received`. Independent inspection of pinned SWE-Agent 1.1.0 `agent/models.py` confirms that its statistic re-tokenizes `message.content` and excludes tool-call output; it is an estimate, not provider completion usage. Preserve it as `native_token_usage_estimate`, never present it as complete measured output.
- The optional shared observer can now retain content-free provider response usage with request/response call IDs. SWE-Agent opts into totals only when every captured request has one successful response with complete consistent counters; failed/missing responses do not become zero. Other native adapters keep their existing behavior.
- Add provenance to persisted usage analysis. HTTP and stream tests verify request/response identity; eight aggregation cases exercise missing/duplicate/error/malformed/zero usage. Focused checks **60 passed in 4.41s**; full local regression **190 passed, 11 skipped in 8.95s** (`evidence/provider-usage-full-tests.log`). Changed helper/adapter/analysis tests pass Ruff; the shared proxy has nine pre-existing findings, verified unchanged against `f719bbc3` (`evidence/proxy-lint-baseline-comparison.json`).
- Rerun the whole SWE-Agent cell again after this correction; prior executions remain evidence of probability capture but are superseded for complete efficiency acceptance. This is an evidence-driven scope correction, not a model/task/solver-policy change.

### Harbor role projection correction

- Inspecting native action rows found the initial Harbor `source=user` instruction counted as an assistant action. The generic persisted schema uses `role`; native function names/observations also need an explicit projection. This affects scientific meaning, not just rendering.
- Add the conversion at the Terminus adapter boundary, preserving raw `harbor_trajectory.json`. User/system entries retain their roles, native command IDs/names/arguments become canonical calls, and terminal observations become separate tool results. Unknown native roles fail clearly. Request attribution still matches the original native observations before projection.
- An end-to-end persistence/measurement/annotation regression proves prompts and observations are excluded from assistant targets while actual command arguments survive. Focused **43 passed in 2.04s**; full **193 passed, 11 skipped in 9.15s** (`evidence/harbor-projection-full-tests.log`). Targeted Ruff passes. Rerun the complete Terminus cell with this correction; prior native control-loop evidence cannot certify corrected action projection.
- Before supersession, the first instrumented native task retained 28,080 probability observations, including 24,182 attributed after exact command-observation matches. This confirms capture/attribution, but not corrected action analysis.

### Native message-history boundary audit

- The same independent inspection found SWE-Agent trajectory rows embed each full query plus its observation. Treating a whole row as assistant text pollutes action/annotation targets. Project the separately retained native message history instead: preserve system/user/assistant/tool roles, function IDs/arguments and tool-result links; retain malformed model arguments explicitly and keep the full native trajectory sidecar unchanged.
- Full local regression: **195 passed, 11 skipped in 8.53s** (`evidence/native-history-full-tests.log`). Actual retained native evidence from all three SWE-Agent and all three baseline Terminus tasks passes independent assistant/user role-count checks (`evidence/native-history-real-projection-check.json`). No native solver loop or benchmark input changed.
- The corrected Terminus rerun is on `d13e7baf`. Superseded partial `beb68351` Terminus execution was interrupted through the owned sequential driver; retained first-task/snapshot evidence and cleaned only its label-verified container/empty network after native process exit. The completed TB2/ZeroClaw cell remains intact.
- Replace the whole SWE-Agent cell once more on the corrected history projection. Probability and provider-usage fixes are already validated, but old mixed-query analysis is not final acceptance evidence. All other selected cells retain their reviewed source-impact mapping.

### Explicit structured annotation protocol

- An actual IMO annotation returned a taxonomy-definition object instead of the required label; validation rejected it and the failed output was not cached. Preserve `evidence/delivery-imo-annotations.log`.
- Add optional provider `response_format`, detached from caller mutation and bound into cache identity and coverage. The portable JSON-object default stays available; structured-output providers can enforce a taxonomy enum without provider-specific core logic.
- Local regression: **196 passed, 11 skipped in 8.74s** (`evidence/annotation-schema-full-tests.log`); targeted Ruff and diff checks pass. A real pinned Qwen3.5-27B request returned a validated schema-constrained label (`evidence/annotation-schema-real-provider.json`). Final comparative annotations will use this same explicit protocol; previous exports remain provenance-bearing historical evidence.
- Matrix audit now accepts **26 cells / 78 executions**. Terminus is pending. MMMU/OpenClaw has one gateway-disconnect failure; image delivery will be inspected before a whole-cell rerun. No failed execution has been counted as accepted.
