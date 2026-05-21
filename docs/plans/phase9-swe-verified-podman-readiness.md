# Phase 9 PRD: SWE-bench Verified Podman Readiness and Full-Run Stabilization

## Objective

Implement and validate SWE-bench Verified Podman readiness for all three AlphaDiana agents:
- OpenClaw
- OpenCode
- ZeroClaw

SWE-bench Pro remains explicitly deferred. This phase must not promote Podman as a global default and must not remove Docker/ROCK legacy paths.

The readiness gate is infrastructure correctness, not model accuracy:
- every expected task row must be accounted for;
- task JSON must be created for every expected task or explicitly classified as no_task_json;
- audit must pass;
- score=0 is acceptable;
- task JSON + audit pass is sufficient.

This PRD is the canonical Phase 9 acceptance baseline. Operator discussion may
clarify it, but must not silently weaken it. If this PRD conflicts with a
later explicit operator decision, the operator decision wins and the PRD plus
planning docs must be updated to match.

## Provider and runtime environment

Use the operator-provided local OpenAI-compatible vLLM endpoint:
- OPENAI_BASE_URL=http://127.0.0.1:8011/v1
- OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
- vLLM log path must be supplied through ALPHADIANA_VLLM_LOG when the operator wants the preflight to verify it.
- vLLM max model len is 200000

Configs must use environment placeholders, not hard-coded secrets:
- ${OPENAI_BASE_URL}
- ${OPENAI_API_KEY}
- ${OPENAI_MODEL_NAME}
- ${ALPHADIANA_PODMAN_SOCKET}
- ${ALPHADIANA_VLLM_LOG}

## Scope

In scope:
1. SWE-bench Verified only, dataset SWE-bench/SWE-bench_Verified split test.
2. Three-agent support for OpenClaw, OpenCode, and ZeroClaw.
3. Podman-backed task-container execution and/or SWE harness compatibility repairs, using the existing AlphaDiana architecture whenever possible.
4. Explicit SWE harness compatibility fixes if needed, including:
   - Podman Docker-compatible socket handling;
   - configurable docker-py API version if needed for this host;
   - image-name qualification for Podman, especially avoiding unqualified short names such as sweb.env...:latest;
   - Hugging Face dataset/cache/proxy behavior;
   - build network/proxy behavior;
   - stable artifact/log paths.
5. Runner/preflight/audit like TB2:
   - validate
   - preflight
   - smoke
   - pilot32
   - long64
   - sample128
   - audit
   - auto
6. Dozens-scale validation:
   - smoke: 3 agents x 2 tasks
   - pilot32: 3 agents x 10 tasks = 30 task rows
   - long64: 3 agents x 2 tasks = 6 task rows
   - sample128: 3 agents x 2 tasks = 6 task rows
7. The taskset must force-include known important tasks if present:
   - astropy__astropy-12907
   - astropy__astropy-13033
   Then fill the rest deterministically from SWE-bench Verified using a stable seed.
8. Maximum-token tiers:
   - smoke and pilot32: 32768
   - long64: 65536
   - sample128: 131072
   The code must allow overriding these via env/config.

Out of scope:
- SWE-bench Pro support
- full Verified sweep
- Podman default promotion
- deleting Docker/ROCK code paths
- claiming full support beyond the audited selected-task pilot

## Empty-response semantics

Define provider empty response narrowly.

An empty response is:
- HTTP 200, and
- no final content, and
- no reasoning content, and
- no logprob records, and
- no completion_tokens, and
- possibly only [DONE] or no effective data chunk.

This must be classified as provider_empty_response or agent_empty_output, depending on where it is observed.

A reasoning-only response is not empty.
If there is reasoning but no final content, preserve it. If the stop reason is length or max_tokens, classify it as reasoning_only_length, attempt answer/patch extraction from reasoning where safe, and record stop_reason=length. Do not fail it as empty merely because final content is blank.

## Required deliverables

Create or update:

configs/smokes/podman_swe_verified_readiness/
- README.md
- openclaw_smoke.yaml
- opencode_smoke.yaml
- zeroclaw_smoke.yaml
- openclaw_pilot32.yaml
- opencode_pilot32.yaml
- zeroclaw_pilot32.yaml
- openclaw_long64.yaml
- opencode_long64.yaml
- zeroclaw_long64.yaml
- openclaw_sample128.yaml
- opencode_sample128.yaml
- zeroclaw_sample128.yaml

scripts/
- generate_podman_swe_verified_tasksets.py
- preflight_podman_swe_verified_readiness.py
- audit_podman_swe_verified_readiness.py
- run_podman_swe_verified_readiness.sh

context/podman-swe-verified-readiness/
- README.md
- tasksets/*.json
- run-status-<run_prefix>.tsv
- preflight-<run_prefix>.json
- audit-<run_prefix>.json
- audit-<run_prefix>.md

Planning/docs:
- .planning/phases/09-swe-bench-verified-podman-readiness/
- 09-CONTEXT.md
- 09-RESEARCH.md
- 09-01-PLAN.md, etc.
- 09-VERIFICATION.md
- 09-UAT.md
- 09-SUMMARY.md
- docs/benchmarks/podman.md
- docs/benchmarks/swebench-verified.md
- context/current_eval_status.md
- context/README.md if new context is added

## Runner contract

scripts/run_podman_swe_verified_readiness.sh must support:

bash scripts/run_podman_swe_verified_readiness.sh validate
bash scripts/run_podman_swe_verified_readiness.sh preflight
bash scripts/run_podman_swe_verified_readiness.sh smoke
bash scripts/run_podman_swe_verified_readiness.sh pilot32
bash scripts/run_podman_swe_verified_readiness.sh long64
bash scripts/run_podman_swe_verified_readiness.sh sample128
bash scripts/run_podman_swe_verified_readiness.sh audit
bash scripts/run_podman_swe_verified_readiness.sh auto

auto must fail fast and run:
validate -> preflight -> smoke -> audit -> pilot32 -> audit -> long64 -> audit -> sample128 -> audit

The script must write a status TSV and raw logs under logs/.
It must never silently skip a config or expected task.
It should default to conservative concurrency, but may raise max_concurrent as
high as the host can safely support, such as 4, when preflight/runtime evidence
shows capacity is available. The actual concurrency used must be recorded in
status and evidence artifacts.

## Preflight requirements

Preflight must check:
- podman binary exists;
- Podman socket is reachable;
- docker Python package exists;
- docker-py can connect to Podman via DOCKER_HOST;
- configurable Docker API version path works if required;
- swebench package exists;
- datasets/HuggingFace access works or fails with actionable HF_ENDPOINT/cache message;
- selected task metadata can be loaded;
- provider /models or lightweight chat probe succeeds against http://127.0.0.1:8011/v1;
- vLLM log path exists if ALPHADIANA_VLLM_LOG is set;
- model context window is recorded as 200000 if discoverable or from env;
- selected max token tiers are below model context window;
- Podman image-name qualification probe proves no unqualified sweb.env...:latest path remains;
- all three agents can reach the provider from their Podman runtime context.

## Audit requirements

Audit must derive expected rows from configs/tasksets, not only from observed task JSONs.

For every expected agent x task x tier row, audit must report:
- expected_task_id
- agent
- tier
- run_id
- task_json_path
- task_json_exists
- JSON record loaded from data[0] when JSON is a list
- score_status
- score
- correct/resolved when available
- metadata.container_engine == podman or sandbox_metadata.container_engine == podman
- sandbox/session artifact existence
- sandbox_meta.json or equivalent runtime metadata existence
- patch/report/run-log/test-output existence where available
- raw log path
- failure_category
- gating boolean

Missing task JSON must be a first-class no_task_json failure, not invisible.
Score `0`, `resolved=false`, and model-solving failure are acceptable for
readiness when the row has task JSON, raw log, Podman metadata, artifact
pointers, and a clear audit classification. Infrastructure failures remain
gating.

Hard audit failures include:
- missing task JSON for an expected row;
- missing raw log for an expected row;
- missing Podman runtime metadata;
- unclassified provider failure;
- unclassified agent/runtime failure;
- unqualified Podman short-name image failure;
- silently skipped task row;
- expected row absent from audit output.

Failure taxonomy must include at least:
- podman_socket
- docker_api_version
- podman_short_name_image
- image_pull_or_proxy
- hf_dataset_access
- swebench_env_build
- swebench_instance_build
- agent_runtime
- provider_failure
- provider_empty_response
- reasoning_only_length
- scorer_failure
- no_task_json
- metadata_missing
- artifact_missing
- timeout
- other

## Tests

Add focused regression tests for:
1. Podman Docker API/socket env handling.
2. Podman SWE image-name qualification for env and instance images.
3. Config validation for all three agents and all token tiers.
4. Taskset generation force-includes astropy__astropy-12907 and astropy__astropy-13033 when present.
5. Audit catches no_task_json.
6. Audit reads list JSON via data[0].
7. Audit validates Podman metadata and artifacts.
8. Empty provider stream is classified as empty.
9. Reasoning-only length output is preserved and not classified as empty.
10. Existing Docker/SWE tests remain compatible.

## Validation order

1. Static/focused tests first.
2. Validate configs.
3. Preflight.
4. Smoke: 3 agents x 2 tasks.
5. If smoke passes audit, run pilot32: 3 agents x 10 tasks.
6. If pilot32 passes audit, run long64: 3 agents x 2 tasks.
7. If long64 passes audit, run sample128: 3 agents x 2 tasks.
8. Final audit must cover all tiers actually run.

If a code defect prevents task JSON creation, stop, classify the defect, repair minimally, add regression test, and rerun from the failed tier.
If pilot32 exposes infrastructure issues, repair those issues before long64 or
sample128. Do not expand to a full Verified sweep in Phase 9.

## Support language

Allowed after success:
"SWE-bench Verified Podman readiness pilot passed for selected audited tasks across OpenClaw, OpenCode, and ZeroClaw."

Not allowed:
"SWE-bench Verified is fully supported on Podman."
"SWE-bench Pro is supported."
"Podman is the default."
"Full run passed."
