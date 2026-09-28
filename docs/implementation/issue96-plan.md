## Background and goals

Deliver a usable AlphaDiana implementation of draft Fig. 4 this week, with Tian handing an integrated, validated version to the original request owner and Zongze for review.

Acceptance is based on **Fig. 4 functionality working end to end**, rather than the number of repository components migrated. This issue tracks future implementation; publishing it does not mean implementation has started.

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
- Plot generation remains in existing scripts and is not required from the unified analyzer this week.

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
- [ ] Define factory composition, configuration precedence, artifact return/reopen behavior, rescoring, and analyzer outputs.
- [ ] Resolve environment shorthand and versioned harness identifiers without silent substitution; record the support decision for the figure's Docker example.
- [ ] Pin three tasks per benchmark, dataset versions, harness versions, execution budgets, and the fixed model.
- [ ] Identify capture requirements for action transitions, entropy, costs, and environment restoration.
- [ ] Record agreed API changes requiring synchronized Fig. 4 edits and schedule implementation/review checkpoints.

**Acceptance:** Every figure capability has a concrete implementation path and test; the 28-combination validation manifest is fixed.

### M1 — Implement public composition and shared orchestration

**Owner:** Weikai. **Reviewer:** Tian. **Dependencies:** M0.

- [ ] Implement the six public interfaces and `runner.compute(...)`.
- [ ] Route Python and CLI/YAML entry points through shared validation, execution, scoring, persistence, and cleanup.
- [ ] Connect all components necessary for the acceptance matrix.
- [ ] Preserve native execution and lifecycle behavior.
- [ ] Provide runnable Fig. 4 examples with explicit configuration.

**Acceptance:** Equivalent Python and CLI/YAML configurations exercise the same execution logic and produce compatible persisted artifacts.

### M2 — Implement reusable artifacts, rescoring, and analysis

**Owner:** Weikai. **Reviewer:** Tian. **Dependencies:** M1.

- [ ] Reopen saved artifacts and rescore with compatible scorers without invoking the agent.
- [ ] Restore required environments and rerun SWE/TB2 verifiers from saved execution evidence.
- [ ] Connect accuracy, action-transition, post-tool entropy, and efficiency analysis through the public analyzer.
- [ ] Add missing probability capture, trace normalization, or annotation needed to produce actual analysis results.
- [ ] Preserve score history, analysis provenance, execution failures, and missing-score distinctions.
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
- [ ] Typed, extensible public contracts; independent extensibility audit using custom components, with no benchmark/harness-specific routing added to the composition layer.
- [ ] Analyzer exports publication-quality PDF/SVG/PNG figures and machine-readable plotted data. Cover concepts in the supplied paper figure index, not pixel matching or copied result constants.
- [ ] Update Diana website/documentation to expose the APIs and figure workflows.
- [ ] Real 27B verification may use two genuinely idle GPUs; never stop another user's processes.
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
