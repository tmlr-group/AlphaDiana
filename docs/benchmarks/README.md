# Benchmark Runbooks

This directory contains user-facing runbooks for benchmark evaluation paths.

Start from [`context/current_eval_status.md`](../../context/current_eval_status.md)
for the current cross-benchmark support snapshot, then open the matching
runbook here.

For opt-in Podman experiments, start with
[`podman.md`](podman.md). It covers image setup, standard-reasoning pilot
commands, nightly validation, task-container smokes, result inspection, and the
current no-default-promotion boundary.

For paper-facing wording about whether a benchmark path is sandboxed or
containerized, also read
[`docs/benchmark-isolation.md`](../benchmark-isolation.md). That note keeps the
claim intentionally weaker than a formal security guarantee.

These files should answer:

- how to run a benchmark path
- which configs are supported
- what status or caveats apply right now

As of April 22, 2026, the benchmark runbooks also document current
result-semantics caveats for `qwen3vl` OpenCode errors, ZeroClaw failure
evidence, `terminal_bench2` verifier bookkeeping, the current OpenRouter
`nvidia/nemotron-nano-12b-v2-vl:free` multimodal smoke evidence, and the new
same-day early full-run caveats for `IMO-AnswerBench`, `MMMU-Pro`, and
`terminal-bench-2`.

Current main generic `zeroclaw` is now simpler than the earlier April 22
notes: normal benchmark runs use one sandbox-only CLI path
(`metadata.transport=zeroclaw_cli_sandbox`) for both text and image-backed
tasks. Historical `disable_tools`, gateway, and proxy path notes remain
useful audit evidence only.

As of May 14, 2026, opt-in Podman standard reasoning is implemented and cheap
live smokes have passed for OpenClaw, ZeroClaw, and OpenCode on one AIME task
each. Use
`configs/examples/openclaw_aime_podman_smoke.yaml`,
`configs/examples/zeroclaw_aime_podman_smoke.yaml`, and
`configs/examples/opencode_aime_podman_smoke.yaml` with
`ALPHADIANA_RUN_PODMAN_AGENT_SMOKE=1` for the cheap live smoke matrix. See
[`context/phase02-podman-agent-smokes/README.md`](../../context/phase02-podman-agent-smokes/README.md)
for the run IDs, image build commands, and current caveats. These paths remain
opt-in and are not default-promotion claims; task-container benchmark defaults
are unchanged.

Also as of May 14, 2026, task-container benchmark adapters have implemented
opt-in Podman configuration shapes. The revised Phase 3 required live-validation
scope is TerminalBench2 plus SWE-bench Verified, and both required cells have
completed opt-in Podman smoke evidence. SWE-bench Pro is deferred from Phase 3
completion and remains experimental pending validation because it still blocks
before scoring on the OpenClaw in-container provider/runtime path.
Use `agent.config.container_engine=podman` for `swebench_docker`,
`terminal_bench2_*`, and `sandbox.config.container_engine=podman` for
`swebench_container`. Example entry points are
`configs/examples/openclaw_swe_bench_podman_smoke.yaml`,
`configs/examples/swebench_pro_openclaw_podman_smoke.local.yaml`,
and `configs/examples/terminal_bench2_opencode_podman_smoke.yaml`. These paths
preserve legacy Docker/ROCK baselines and should not be described as
default-enabled or parity-proven. Current evidence lives in
[`context/phase03-podman-task-containers/README.md`](../../context/phase03-podman-task-containers/README.md):
TerminalBench2 official `db-wal-recovery` and SWE-bench Verified
`astropy__astropy-12907` completed as `valid_scored`, `score=0`, while
SWE-bench Pro reaches Podman lifecycle but fails with `agent_error` and empty
OpenClaw output. Do not claim SWE-bench Pro Podman support from that pending
run. external_benchmark Podman work is deferred to a later phase and is not part of this
Phase 3 scope.

On May 14/15, 2026, a validation-only Podman nightly campaign ran broader
cheap coverage through
`configs/smokes/podman_nightly_validation/` and
`scripts/run_podman_nightly_validation.sh`. The report is
[`context/podman-nightly-validation/README.md`](../../context/podman-nightly-validation/README.md).
This campaign does not upgrade support claims: standard reasoning rows were
mixed, and the original nightly found two standard-reasoning Podman error rows
that missed `container_engine=podman`. A May 15 follow-up repaired that
standard-reasoning metadata contract and reran the OpenClaw GPQA and ZeroClaw
IMO repro configs with Podman provenance recorded. TerminalBench2 completed
three official tasks as `valid_scored`/`score=0`, and SWE-bench Verified
broader coverage still failed on scorer-image/proxy and Podman short-name image
build issues. SWE-bench Pro and external_benchmark remained deferred, and Direct x
SWE/TB2 remains `-`.

On May 15, 2026, Phase 5 added standard-reasoning Podman scale-readiness
configs under `configs/smokes/podman_scale_readiness/`, plus
`scripts/run_podman_scale_readiness.sh` and
`scripts/audit_podman_scale_readiness.py`. The matrix is restricted to
OpenClaw, ZeroClaw, and OpenCode across AIME, GPQA-Diamond, HLE, and
IMO-AnswerBench with three pilot tasks per cell. On this host, local vLLM
requires Podman host networking; the runner now preflights that path and the
shared Podman runtime cleans up owned host-network listeners. The rerun
`podman_scale_20260515_qwen35_4b_tool_hf` used the same 36-task matrix,
operator-provided HLE dataset credentials, and a local vLLM provider restarted
with Qwen tool-call support. It wrote all 36 task rows, all with
`metadata.container_engine=podman`, and the audit passed with 28 `clean` rows
and 8 non-Podman `agent_empty_output` rows. See
[`context/podman-scale-readiness/README.md`](../../context/podman-scale-readiness/README.md).
This supports recommending the full-scale overnight standard-reasoning Podman
campaign as the next action, but full-scale has not been run and no
task-container benchmark or global Podman default status changed.

Phase 6 added the opt-in MMMU-Pro `vision` Podman readiness matrix under
`configs/smokes/podman_mmmu_pro_readiness/`, plus
`scripts/run_podman_mmmu_pro_readiness.sh`,
`scripts/podman_vlm_image_preflight.py`, and
`scripts/audit_podman_mmmu_pro_readiness.py`. The matrix is restricted to
OpenClaw, ZeroClaw, and OpenCode on three deterministic MMMU-Pro `vision`
tasks. On May 16, 2026, manual evidence verified
`Qwen/Qwen3.5-4B` at `http://127.0.0.1:8011/v1` for remote `image_url` and
`data:image/png;base64` requests on both host and Podman `--network host`;
the prior `podman_mmmu_pro_20260515_qwen35_4b` stop is now scoped as an
automated preflight alignment gap. The patched path keeps thinking mode on,
uses at least 8192 output tokens, and gates readiness on automated
9-task Podman pilot/audit infrastructure evidence, not accuracy. The repaired
run prefix `podman_mmmu_pro_qwen35_thinking_20260516_144304` passed
validation, Podman VLM preflight, pilot, and audit with all 9 task rows written
and `audit_failure_count=0`. See
[`context/podman-mmmu-pro-readiness/README.md`](../../context/podman-mmmu-pro-readiness/README.md).

On May 16, 2026, Phase 7 was repaired into a dedicated opt-in TerminalBench2
three-agent Podman task-container readiness small matrix under
`configs/smokes/podman_terminal_bench2/`, plus
`scripts/run_podman_terminal_bench2_readiness.sh` and
`scripts/audit_podman_terminal_bench2_readiness.py`. The run prefix
`podman_tb2_three_agent_20260516_170725` validated/preflighted the matrix and
wrote 9 task rows for OpenClaw, OpenCode, and ZeroClaw x `db-wal-recovery`,
`overfull-hbox`, and `adaptive-rejection-sampler`. The audit passed with all
rows recording `metadata.container_engine=podman`, `score=0.0`, verifier
`ok`, reward observed, and discoverable logs/artifacts. See
[`context/podman-terminal-bench2-readiness/README.md`](../../context/podman-terminal-bench2-readiness/README.md).
This supports recommending a larger overnight three-agent TerminalBench2
Podman campaign, but it does not change Direct x TB2, global-default,
full-sweep, SWE-bench, external_benchmark, or full MMMU-Pro sweep status. The earlier
May 15 `podman_tb2_20260515_phase7_abslogs` run remains historical OpenCode-
only five-task evidence.

On May 16/17, 2026 +0800, Phase 8 repaired PR review findings without changing
the Podman support boundary. Numeric answer normalization is now scoped to the
numeric scorer path, ResultStore keeps `metadata.logprob_records` as the
sidecar line count, and ZeroClaw Podman has an end-to-end host logprob proxy.
OpenClaw Podman host-network logprob capture now advertises the runtime proxy
on loopback when `network: host`. Targeted tests passed, OpenClaw live smoke
`phase8_openclaw_podman_logprob_smoke_final` captured 1064 logprob records
with matching float/int16 sidecars and predicted `204`, and ZeroClaw Podman
live smoke `phase8_zeroclaw_podman_logprob_smoke_retry` captured 64 logprob
records with matching float/int16 sidecars.
See
[`context/podman-pr-review-repair/README.md`](../../context/podman-pr-review-repair/README.md).

Generated ZeroClaw configs use `runtime_trace_mode="full"` for logprob capture
and write ZeroClaw's schema-supported permissive shell controls; ZeroClaw
0.6.9 does not expose a heredoc-specific allowlist setting.
The April 28 AIME smoke `smoke_20260428_zeroclaw_shell_config_aime2` validated
that path on one sandboxed local-Qwen task with captured logprobs and runtime
trace artifacts.
Use
[`zeroclaw-local-qwen-rerun-20260428.md`](zeroclaw-local-qwen-rerun-20260428.md)
for the five-benchmark ZeroClaw rerun parameter contract.

As of April 25, 2026, local `Qwen/Qwen3.5-27B` 3-sample smokes validated
`OpenClaw` and `ZeroClaw` on AIME 2026, IMO-AnswerBench, GPQA-Diamond, HLE,
and MMMU-Pro vision with `top_p=0.95`, `max_tokens=65536`, and stored
logprobs. See
[`context/phase12-harness-logprob-smokes/run_evidence.md`](../../context/phase12-harness-logprob-smokes/run_evidence.md)
for run IDs, token counts, sidecar checks, and local-Qwen caveats.

As of April 28, 2026, the non-coding OpenCode/OpenClaw/ZeroClaw harness images
were re-probed for Python tooling and network reachability. The shared harness
images can import the common scientific stack and can reach the local Qwen API,
PyPI, files.pythonhosted, and `HF_ENDPOINT=https://hf-mirror.com`; direct
`huggingface.co` timed out from this host. The same probe found generic web
access and Bing result pages reachable, but Google Search reset the TLS
connection and DuckDuckGo, Wikipedia, and Brave Search API timed out. OpenClaw
native `web_search` is provider-key-backed, with Brave as the default path when
credentials are available; no search provider credential was present in the
April 28 audit, so only keyless `web_fetch` prerequisites and
shell/Python-backed HTTP access were validated at the container-network level.
For local vLLM runs, OpenClaw and ZeroClaw sandbox configs use
`http://host.docker.internal:8011/v1` so the container can reach the host provider. See
[`context/harness-env-audit-20260428/`](../../context/harness-env-audit-20260428/)
for the probe output and smoke run IDs.

The same April 25 evidence now includes OpenClaw thinking-on 3-sample smokes
for GPQA-Diamond, HLE, MMMU-Pro vision, and IMO-AnswerBench in both 25K
truncation and 64K long-timeout settings. HLE and IMO include normal
partial-reasoning-only exits at the output cap with captured logprobs and
matching float/int16 sidecars.

The same evidence folder also records an April 25 thinking-on long-sample smoke
for AIME 2026 `aime_17` across OpenCode, OpenClaw, and ZeroClaw with
`max_tokens=25000`. Use `benchmark.config.dataset_index=16` for this single
task; do not combine a sliced split such as `train[16:17]` with a config that
already sets `benchmark.config.max_tasks`.

The April 25 evidence also includes local-Qwen OpenCode 3-sample follow-ups for
SWE-bench Verified mini and terminal-bench-2. SWE-bench Verified mini is clean
on the generic `opencode_swe_bench.yaml` / `swebench_container` path with
stored provider-proxy logprobs. terminal-bench-2 now stores logprobs too, but
the local stress run exposed long-tail runtime and context-window overflow
caveats under a very large output budget.

As of April 23, 2026, current GPQA-Diamond `direct_llm` local-vLLM evidence
also includes a Phase 10 top-20 Int16 logprob sidecar smoke; see
[`context/current_eval_status.md`](../../context/current_eval_status.md) for
the run ID, config, log, result paths, and current harness limitations.

Reviewer-facing evidence, dated pilot notes, internal design notes, and
cross-benchmark status snapshots belong under `context/`.

For Hugging Face result archival rules, staging layout, and the current
DirectLLM `trajectory / artifacts / logprobs` upload contract, use
[context/hf-result-upload-spec-20260423.md](../../context/hf-result-upload-spec-20260423.md).

Each benchmark document describes prerequisites, supported execution modes,
example configs, and smoke-test commands:

- [OpenClaw Benchmark Reliability](openclaw.md)
- [IMO-AnswerBench](imo-answerbench.md)
- [AIME](aime.md)
- [HLE](hle.md)
- [GPQA-Diamond](gpqa-diamond.md)
- [MMMU-Pro](mmmu-pro.md)
- [SWE-bench Pro](swebench-pro.md)
- [SWE-bench Verified](swebench-verified.md)
- [terminal-bench-2](terminal-bench-2.md)
- [Podman Experiment Runbook](podman.md)
- [Full Local-vLLM Rollout (2026-04-19)](full-rollout-local-vllm-20260419.md)
- [ZeroClaw MiniMax Smoke Matrix (2026-04-18)](zeroclaw-minimax-smoke-20260418.md)
- [ZeroClaw Qwen OpenRouter Pilot Matrix (2026-04-19)](zeroclaw-qwen-openrouter-pilot-20260419.md)

## Find The Right Runbook

If you know the benchmark already:

- `IMO-AnswerBench`:
  [imo-answerbench.md](imo-answerbench.md) covers `direct_llm`,
  `openclaw`, `opencode`, and `zeroclaw`.
- `AIME`:
  [aime.md](aime.md) covers the checked-in local-vLLM `direct_llm` and
  `opencode` AIME 2026 multi-sample paths and points to the older ZeroClaw
  guides.
- `HLE`:
  [hle.md](hle.md) covers `direct_llm`, `openclaw`, `opencode`, and
  `zeroclaw`.
- `GPQA-Diamond`:
  [gpqa-diamond.md](gpqa-diamond.md) covers `direct_llm`, `openclaw`,
  `opencode`, and `zeroclaw`.
- `MMMU-Pro`:
  [mmmu-pro.md](mmmu-pro.md) covers `direct_llm`, `openclaw`, `opencode`,
  and `zeroclaw`.
- `terminal-bench-2`:
  [terminal-bench-2.md](terminal-bench-2.md) covers the Diana-native
  `openclaw`, `opencode`, and `zeroclaw` paths, and also points to the
  official external `direct_llm` Harbor path.
- `SWE-bench Pro`:
  [swebench-pro.md](swebench-pro.md) covers the Diana-native `openclaw`,
  `opencode`, and `zeroclaw` paths, and also points to the official external
  `direct_llm` SWE-agent path.

If you know the harness first:

- `direct_llm`:
  use the benchmark runbook directly for `AIME`, `IMO-AnswerBench`, `HLE`,
  `GPQA-Diamond`, and `MMMU-Pro`; for `terminal-bench-2` and
  `SWE-bench Pro`, the runbooks route you to the official external repos.
- `openclaw`:
  start with [OpenClaw Benchmark Reliability](openclaw.md), then use the
  matching benchmark runbook for the target benchmark.
- `opencode`:
  use the matching benchmark runbook for all six benchmarks.
- `zeroclaw`:
  use the matching benchmark runbook for all six benchmarks.

If you are searching for a real Qwen/OpenRouter pilot or a ZeroClaw-specific
smoke recipe rather than the general runbook, start from:

- [podman.md](podman.md)
- [zeroclaw-minimax-smoke-20260418.md](zeroclaw-minimax-smoke-20260418.md)
- [zeroclaw-qwen-openrouter-pilot-20260419.md](zeroclaw-qwen-openrouter-pilot-20260419.md)
- [zeroclaw-local-qwen-rerun-20260428.md](zeroclaw-local-qwen-rerun-20260428.md)
- [full-rollout-local-vllm-20260419.md](full-rollout-local-vllm-20260419.md)

Ready-to-run full benchmark configs live in
[configs/full_runs](../../configs/full_runs/README.md) where available. Use
those for full evaluations.

For the staged April 19 local-vLLM full campaign, start from
[Full Local-vLLM Rollout (2026-04-19)](full-rollout-local-vllm-20260419.md)
instead of expanding the current `60` active paths by hand.

The configs under `configs/examples/` are smoke/debug configs. They intentionally pin one task with `dataset_index` or `max_tasks` and should not be used for full benchmark runs.

Common setup for all examples:

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

## Switching Models

For most example configs, switching to another OpenAI-compatible backend only
requires changing the three standard environment variables before running
`python -m alphadiana.cli run ...`:

```bash
export OPENAI_BASE_URL='https://api-inference.modelscope.cn/v1'
export OPENAI_API_KEY='ms-...'
export OPENAI_MODEL_NAME='Qwen/Qwen3.5-27B'
```

This works directly for configs that use `${OPENAI_MODEL_NAME}` or read the
provider settings from the environment, such as:

- `configs/examples/zeroclaw_imo_answerbench.yaml`
- `configs/examples/zeroclaw_hle.yaml`
- `configs/examples/zeroclaw_gpqa_diamond.yaml`
- `configs/examples/zeroclaw_mmmu_pro.yaml`

Some smoke/debug configs pin the model in YAML instead of reading
`OPENAI_MODEL_NAME`. For those configs, environment variables alone are not
enough; override the agent config explicitly with `-o`.

Example for `terminal-bench-2`:

```bash
TERMINAL_BENCH2_SMOKE_DIR=/tmp/terminal-bench-2-smoke-dbwal \
TMPDIR=/tmp/alphadiana-tb2-qwen \
python -m alphadiana.cli run configs/examples/terminal_bench2_zeroclaw_minimax.yaml \
  -o run_id=tb2_zeroclaw_qwen35b_smoke \
  -o output_dir=./results/tb2_zeroclaw_qwen35b_smoke \
  -o num_samples=1 \
  -o agent.config.model='Qwen/Qwen3.5-27B' \
  -o agent.config.api_base='https://api-inference.modelscope.cn/v1' \
  -o agent.config.api_key='ms-...' \
  -o agent.config.logs_base_dir=/tmp/alphadiana-tb2-qwen/tb2_logs
```

Reason: `configs/examples/terminal_bench2_zeroclaw_minimax.yaml` currently pins
`agent.config.model: "minimax-m2.5"`.

For the April 18, 2026 OpenRouter-backed Qwen pilot configs, use:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
```

That slug is the OpenRouter model ID for the logical target
`Qwen/Qwen3.5-27B`.

Dedicated 3-task OpenRouter pilot YAMLs now exist for `GPQA-Diamond`,
`IMO-AnswerBench`, and `HLE`. The April 19/20, 2026 `terminal-bench-2` and
`SWE-bench Pro` Qwen pilots reused the checked-in minimax smoke YAMLs with CLI
overrides instead of adding new example configs. The latest `MMMU-Pro` sandbox
follow-up also reused the checked-in smoke YAMLs plus explicit CLI overrides.
See the benchmark-specific runbooks for the exact commands.

When running from a local checkout, prefer `python -m alphadiana.cli ...` so the
current workspace code is used.

For GPQA-Diamond and MMMU-Pro, dedicated smoke/debug example configs now exist
for all four modes:

- `direct_llm`
- `openclaw`
- `opencode`
- `zeroclaw`

Dedicated 3-task OpenRouter pilot configs also exist for:

- `IMO-AnswerBench x direct_llm`
- `IMO-AnswerBench x openclaw`
- `IMO-AnswerBench x opencode`
- `GPQA-Diamond x direct_llm`
- `GPQA-Diamond x openclaw`
- `GPQA-Diamond x opencode`
- `HLE x direct_llm`
- `HLE x openclaw`
- `HLE x opencode`

OpenRouter/Qwen pilot coverage on April 19 also includes:

- `GPQA-Diamond x opencode`
- `IMO-AnswerBench x opencode`
- `HLE x direct_llm`
- `HLE x openclaw`
- `HLE x opencode`
- `MMMU-Pro x opencode`
- `terminal-bench-2 x direct_llm` via the official Harbor `terminus-2` path
- `terminal-bench-2 x opencode`
- `terminal-bench-2 x openclaw`
- `SWE-bench Pro x direct_llm` via the official `SWE-agent` path
- `SWE-bench Pro x opencode`
- `SWE-bench Pro x openclaw`

The two official `direct_llm` follow-ups were repaired and re-audited inside
the upstream benchmark checkouts rather than through AlphaDiana YAMLs. See the
benchmark-specific runbooks for the exact caveats and accepted archive IDs.

The checked-in plain-benchmark `opencode` configs for `GPQA-Diamond`,
`IMO-AnswerBench`, `HLE`, and the default `MMMU-Pro` smoke path now set
`controller_mode: docker` by default. Build
`alphadiana/tb2-opencode-controller:latest` before using those configs. The
host-process path is still available for debugging via
`-o agent.config.controller_mode=host`.

For `opencode`, answers are extracted only from assistant/model text events.
Raw OpenCode JSONL, tool events, provider error bodies, and lifecycle metadata
are preserved as artifacts but are not valid answer sources. A lifecycle-only
stream is reported as `agent_empty_output` with `predicted=null` and `score=0`,
not as a valid answer of `0`; aggregate accuracy and mean-score denominators
still count it as a zero-contribution sample.

The same answer-source rule applies to the other standard agents: OpenClaw
provider error payloads do not become assistant text, and ZeroClaw clears
`answer` / `answer_source` on partial failure records. ZeroClaw stderr and
runtime traces remain artifacts, not answer-extraction input.

On April 25, 2026, latest-code local `Qwen/Qwen3.5-27B` OpenCode smokes
completed 3/3 tasks on GPQA-Diamond, IMO-AnswerBench, HLE, and MMMU-Pro vision
with `agent.config.enable_thinking=true`, `top_p=0.95`, `max_tokens=25000`,
and captured logprob sidecars. See
[`context/current_eval_status.md`](../../context/current_eval_status.md) for
the current run IDs and caveats.

For ROCK-backed OpenClaw long runs, set `agent.config.request_timeout`,
`agent.config.stream_idle_timeout`, and, when the stream can remain active for a
long time, `agent.config.stream_total_timeout`. Current AlphaDiana also writes
`request_timeout` into the generated OpenClaw `agents.defaults.timeoutSeconds`
and `tools.exec.timeoutSec`, sets ROCK `agent_run_timeout`, and patches the
prebuilt OpenClaw embedded-provider undici stream watchdog through
`OPENCLAW_UNDICI_STREAM_TIMEOUT_MS`. When debugging unexpected `~1800s`
OpenClaw empty-response retries, inspect both the sandbox `openclaw.json` and
the generated ROCK `run_cmd` patch for `opts?.timeoutMs ?? 18e5`.

Smoke-test success means the evaluation path loads tasks, invokes the selected agent mode, and writes scored results. It does not mean the model answered correctly.

## ZeroClaw Note

For the benchmark runbooks in this folder, ZeroClaw smoke validation is documented only for sandboxed execution:

- `IMO-AnswerBench` and `HLE`: ROCK sandbox with in-sandbox ZeroClaw CLI
- `AIME 2026`, `GPQA-Diamond`, and `MMMU-Pro`: ROCK sandbox with in-sandbox
  ZeroClaw CLI for the April 25 local-Qwen logprob smokes
- `terminal-bench-2`: Docker task container with an in-container derived runtime image

The host-local `_run_locally()` path is useful for debugging, but it is not counted as the formal benchmark smoke path in these runbooks.

For PR-scoped local evidence from the latest ZeroClaw benchmark smokes and the
Qwen OpenRouter pilot repair audit, see
[`context/pr23-zeroclaw-smoke-20260418/README.md`](../../context/pr23-zeroclaw-smoke-20260418/README.md)
and
[`context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/README.md`](../../context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/README.md).
The `2026-04-20` rerun addendum for the three previously pending `zeroclaw`
items lives at
[`context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/rerun_20260420_pending_recheck.md`](../../context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/rerun_20260420_pending_recheck.md).

Related non-runbook references kept outside this folder:

- Main April 18-20 Qwen/OpenRouter benchmark pilot context:
  [`context/qwen-openrouter-pilots/README.md`](../../context/qwen-openrouter-pilots/README.md)
- HLE multimodal deep-dive note:
  [`context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md`](../../context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md)
- SWE-bench Verified PR26 evidence bundle:
  [`context/pr26-swebench-verified/README.md`](../../context/pr26-swebench-verified/README.md)
- SWE-bench Pro PR29 reproduction context:
  [`context/pr29-add-swebench-pro/README.md`](../../context/pr29-add-swebench-pro/README.md)
