# Issue 96 extensibility review

Scope: the Fig. 4 composition boundary and its effect on the existing execution,
artifact, scorer and analysis extension points. Reviewed against baseline
`f22bd993`; this is an agent-conducted review separate from implementation tests
and from Tian's and the final reviewers' decisions. It is not human approval or
full-matrix acceptance.

## Findings and resolution

| Finding | Resolution | Evidence |
| --- | --- | --- |
| A root `scorer` function would shadow the existing package and break imports. | Retain the package; expose `ad.scorer.create` and `alphadiana.api.scorer`. | Legacy submodule import regression in `test_composable_api.py`. |
| Native ZeroClaw event fields were ignored by generic message projection. | One native projector serves ordinary and TB2 paths; preserve raw events and report unmapped event kinds/usage gaps. | Captured 0.6.9 tool trace, real 27B diagnostic, trace/persistence tests. |
| Explicit tool results were overridden by the generic `role=tool` alias. | Explicit result type takes precedence; structured arguments survive persistence. | `test_persisted_tools_keep_call_arguments_and_result_role`. |
| Failed attempts did not retain binary inputs, so reopening multimodal failures lost task context. | Both success and failure paths use the same content-addressed input writer; reject corrupted reused content. | `test_failed_attempt_retains_binary_inputs_for_reopening`; cross-directory attachment rescoring regression. |
| SWE OpenCode container execution ignored native context/output limits. | Pass configured limits through its existing native provider configuration; preserve custom installation commands and optionally verify installed version. | Container contract tests; real rerun still required for acceptance. |

| Wheel omitted native runtime configuration/templates, and package-prefixed defaults depended on repository cwd. | Explicitly package required runtime assets; resolve bundled defaults relative to installed package while preserving caller overrides. | Actual wheel install outside checkout: seven extension/resource cases pass, all AlphaDiana modules loaded from installed target. |

## Extension boundaries

| Boundary | Reviewed behavior | Verification |
| --- | --- | --- |
| Component factories | Typed specs select existing registries. Dataset schema and environment backend are explicit. No benchmark/harness name branches occur in `api.py`. | Custom benchmark/agent/scorer run through the real Runner; unknown/ambiguous selections fail before runtime startup. |
| Configuration | Component-specific dictionaries remain open to plugin options. Factory inputs and Runner configurations are detached copies. | Nested-option mutation test; custom option reaches benchmark/agent unchanged. |
| Orchestration | API and CLI retain the same ExperimentConfig, validator, Runner, dispatcher, session policies and ResultStore. Built-in import inventory is centralized, not duplicated as a new dispatcher. | YAML/Python configuration parity plus existing Runner execution tests. |
| Environments | Custom backends retain control of `supports_shared_session`; `strict_isolation` does not redefine this existing capability. | Four combinations: shared/per-task sessions and success/forced failure. Every created session closes and agent/environment teardown runs. Initial audit expectation of three sessions under shared mode was corrected after inspecting the established contract. |
| Scoring/replay | Existing scorers inherit offline `rescore`; environment-dependent scorers override that contract and require explicit opt-in. API code contains no SWE/TB2 routing. Original predictions and execution failures survive repeated rescoring. | Chained rescoring and environment opt-in tests; actual SWE verifier replay and Docker/Podman snapshot integrations. |
| Native control loops | SWE-Agent and Harbor execute in their own configured environments. Optional snapshot hook captures state at native verification start. | Native runtime evidence and hook integration tests; complete final model matrix remains separate. |
| Analysis | `AnalysisPlugin.measure` returns tables/figure specifications; caller supplies plugin sequence. Taxonomy/model annotation configuration is explicit. Renderers do not execute benchmark solvers. | Custom plugin renders SVG without editing core code; no model service required. |
| Historical figures | Archive-specific field mappings belong to the archive importer. Public analysis accepts arbitrary provenance-bearing tables and custom measurements. | Archive integration covers empirical concepts without executing historical scripts or embedding result constants in the API. |

## Remaining practical limits

- This preserves the existing Runner architecture; it does not claim to remove
  all preexisting benchmark-specific branches from Runner or its validator.
- Component configuration remains heterogeneous because native harness settings
  differ. Typed composition does not pretend those settings share one schema.
- The renderer supports the declared figure kinds; a fundamentally new rendering
  primitive needs renderer work. New measurements/tables do not need Runner edits.
- Native Harbor snapshot support targets the explicitly tested Docker/Harbor
  contract. Other backends need their own restoration evidence.
- Artifacts retain source-sidecar references when rescored elsewhere. Moving a
  derived JSONL alone is not a portable archive; preserve referenced files/images.
- No load/scalability benchmark or external human review is implied by this
  extensibility check. The full 28-cell matrix and final visual review remain gates.

No unresolved blocking finding in the reviewed composition extension boundary.
Functional acceptance remains pending until its separate execution gates pass.
