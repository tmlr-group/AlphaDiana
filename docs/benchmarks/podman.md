# Podman Experiment Runbook

This runbook covers the opt-in Podman experiment paths in the current checkout.
Podman is not a global default. Use the commands here when you want
container-backed OpenClaw, ZeroClaw, or OpenCode standard-reasoning experiments
without ROCK admin/proxy, Redis, Ray, Docker daemon, or Docker socket as the
main runtime path.

Current support boundary:

- Standard reasoning pilot path: validated for OpenClaw, ZeroClaw, and OpenCode
  across AIME, GPQA-Diamond, HLE, and IMO-AnswerBench with three tasks per
  cell. Evidence is in
  `context/podman-scale-readiness/README.md`.
- Task-container path: TerminalBench2 has historical three-agent smoke
  evidence, 128K canary evidence, a current full local-Qwen sweep, and an
  overnight high-budget follow-up for all 89 real task directories in the
  checked-out TerminalBench2 task root across OpenClaw, OpenCode, and ZeroClaw.
  Treat
  `context/podman-terminal-bench2-readiness/` as the support truth source.
  SWE-bench Verified has a Phase 9 opt-in readiness path under
  `configs/smokes/podman_swe_verified_readiness/`. The selected-task
  readiness ladder passed validation, preflight, and audit for OpenClaw,
  OpenCode, and ZeroClaw on run prefix `phase9_gap_20260519_012`. SWE-bench
  Pro and external_benchmark remain deferred.
- MMMU-Pro multimodal path: Phase 6 has a patched opt-in readiness matrix for
  OpenClaw, ZeroClaw, and OpenCode on three deterministic `vision` tasks.
  `Qwen/Qwen3.5-4B` at `http://127.0.0.1:8011/v1` has manual host and Podman
  image_url/data-URL transport evidence. The repaired automated run
  `podman_mmmu_pro_qwen35_thinking_20260516_144304` passed the 9-task
  pilot/audit.
- Full-scale standard-reasoning Podman runs are recommended only after the
  pilot audit passes. No checked-in full-scale Podman matrix is promoted as a
  default.

For coding-agent handoff and a development file map, read
`context/add-podman-handoff/README.md`.

## Development File Map

- Runtime and agent code:
  `alphadiana/container_runtime/agent_runtime.py`,
  `alphadiana/agent/openclaw.py`, `alphadiana/agent/zeroclaw.py`,
  `alphadiana/agent/opencode.py`,
  `alphadiana/agent/terminal_bench2_openclaw.py`,
  `alphadiana/agent/terminal_bench2_opencode.py`,
  `alphadiana/agent/terminal_bench2_zeroclaw.py`,
  `alphadiana/runner/runner.py`,
  `alphadiana/results/status.py`, `alphadiana/results/report.py`, and
  `alphadiana/utils/math_answer.py`.
- Configs and operators:
  `configs/smokes/podman_scale_readiness/`,
  `configs/smokes/podman_nightly_validation/`,
  `configs/smokes/podman_terminal_bench2/`,
  `configs/smokes/podman_mmmu_pro_readiness/`,
  `configs/smokes/podman_swe_verified_readiness/`,
  `scripts/run_podman_scale_readiness.sh`,
  `scripts/audit_podman_scale_readiness.py`, and
  `scripts/run_podman_nightly_validation.sh`,
  `scripts/run_podman_terminal_bench2_readiness.sh`,
  `scripts/preflight_podman_terminal_bench2_readiness.py`,
  `scripts/audit_podman_terminal_bench2_readiness.py`,
  `scripts/run_podman_mmmu_pro_readiness.sh`,
  `scripts/podman_vlm_image_preflight.py`, and
  `scripts/audit_podman_mmmu_pro_readiness.py`,
  `scripts/run_podman_swe_verified_readiness.sh`,
  `scripts/preflight_podman_swe_verified_readiness.py`, and
  `scripts/audit_podman_swe_verified_readiness.py`.
- Evidence and handoff:
  `context/add-podman-handoff/README.md`,
  `context/podman-terminal-bench2-readiness/README.md`,
  `context/podman-mmmu-pro-readiness/README.md`,
  `context/podman-swe-verified-readiness/README.md`,
  `context/podman-scale-readiness/README.md`,
  `context/podman-nightly-validation/README.md`,
  `context/phase02-podman-agent-smokes/README.md`, and
  `context/phase03-podman-task-containers/README.md`.
- Focused tests:
  `tests/test_podman_agent_runtime.py`,
  `tests/test_podman_openclaw_runtime.py`,
  `tests/test_podman_zeroclaw_runtime.py`,
  `tests/test_podman_opencode_controller.py`,
  `tests/test_podman_scale_readiness_configs.py`,
  `tests/test_podman_scale_readiness_audit.py`, and
  `tests/test_podman_terminal_bench2_readiness_configs.py`,
  `tests/test_podman_terminal_bench2_readiness_audit.py`,
  `tests/test_podman_terminal_bench2_readiness_runner.py`,
  `tests/test_terminal_bench2_agents.py`, and
  `tests/test_podman_mmmu_pro_readiness_configs.py`,
  `tests/test_podman_mmmu_pro_readiness_runner.py`,
  `tests/test_podman_mmmu_pro_readiness_audit.py`, and
  `tests/test_podman_swe_verified_readiness_configs.py`,
  `tests/test_podman_swe_verified_readiness_audit.py`, and
  `tests/test_standard_podman_error_metadata.py`.

## Prerequisites

From the repository root:

```bash
source scripts/activate.sh

podman --version
podman info >/dev/null
```

Set the provider variables explicitly after `source scripts/activate.sh` so a
local `.env` does not point the run at the wrong backend:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-4B
```

For HLE rows, provide operator-owned Hugging Face credentials/cache if the
dataset is gated or the shared cache is read-only:

```bash
export HF_TOKEN=<token>
export HF_HOME=<writable-cache>
export HF_DATASETS_CACHE=<writable-cache>
```

### Container-Reachable Provider URLs

Do not assume a provider URL that works from the host is reachable from a
Podman container:

- Use `http://127.0.0.1:<port>/v1` or `http://localhost:<port>/v1` only when
  the provider runs in the same network namespace, for example with
  `podman_network: host`.
- For normal bridge-network containers, use a routable host/service name such
  as `http://host.containers.internal:<port>/v1`, an explicit
  `agent.config.podman_host_ip`, or a remote provider endpoint.
- Validate from a Podman container, not only with host-side `curl`.

For Podman logprob capture, AlphaDiana may start a short-lived host proxy.
OpenClaw and ZeroClaw Podman advertise the runtime capture proxy as
`127.0.0.1` when the gateway/bridge runs with Podman host networking; otherwise
they use `agent.config.podman_host_ip` (default `host.containers.internal`).
Task metadata records
`logprob_proxy_url`, `logprob_proxy_upstream`, and
`logprob_proxy_request_overrides` when this path is active.

On CN or otherwise restricted networks, direct `huggingface.co` access may be
unavailable even when a local provider is reachable. Use an operator-approved
mirror such as `HF_ENDPOINT=https://hf-mirror.com` where appropriate, keep
`HF_HOME` / `HF_DATASETS_CACHE` on writable cache storage, and still provide
`HF_TOKEN` for gated datasets. Do not commit credentials, machine-specific
cache paths, or absolute local worktree paths.

Image pulls and provider wheels can have separate CN-host failure modes. If a
Docker Hub base image such as `python:3.11` is unavailable, use an
operator-approved mirror or preloaded local image and tag it to the image name
expected by the smoke config. For local vLLM, install wheels compatible with
the host driver/CUDA runtime; the PR 39 CN report saw CUDA-13-linked wheels fail
on driver 535/CUDA 12.2 and used a tested CUDA-12-compatible vLLM environment
instead.

Build the local Podman images used by the standard-reasoning matrix:

```bash
podman build -f openclaw_deploy/Dockerfile \
  -t localhost/alphadiana-openclaw:latest .
podman tag localhost/alphadiana-openclaw:latest alphadiana-openclaw:latest

podman build -f zeroclaw_deploy/Dockerfile \
  -t localhost/zeroclaw-reasoning:0.6.9 .

podman build -f opencode_deploy/Containerfile.podman-controller \
  -t localhost/alphadiana-opencode-podman:latest .
podman tag localhost/alphadiana-opencode-podman:latest \
  alphadiana-opencode-podman:latest
```

For SWE-bench Verified paths that still use a Docker-compatible client
boundary, start the user Podman socket and point compatibility clients at it:

```bash
systemctl --user start podman.socket
export ALPHADIANA_PODMAN_SOCKET="${XDG_RUNTIME_DIR}/podman/podman.sock"
export DOCKER_HOST="unix://${ALPHADIANA_PODMAN_SOCKET}"
```

## SWE-bench Verified Podman Readiness

Phase 9 adds a dedicated SWE-bench Verified readiness matrix:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export ALPHADIANA_PODMAN_SOCKET="${XDG_RUNTIME_DIR}/podman/podman.sock"

bash scripts/run_podman_swe_verified_readiness.sh validate
bash scripts/run_podman_swe_verified_readiness.sh preflight
bash scripts/run_podman_swe_verified_readiness.sh auto
```

The matrix is SWE-bench Verified only and covers OpenClaw, OpenCode, and
ZeroClaw across `smoke`, `pilot32`, `long64`, and `sample128` tiers. Tasksets
are deterministic and force-include `astropy__astropy-12907` and
`astropy__astropy-13033`.

On this host, the local `Qwen/Qwen3.5-27B` provider at
`http://127.0.0.1:8011/v1` is reachable from Podman with host networking, so
the Phase 9 configs use `sandbox.config.network_mode: host`. The preflight
checks the Podman socket, docker-py API compatibility, SWE-bench dataset
access, image qualification, host provider reachability, and Podman runtime
provider reachability before any task run.

Current selected-task status: run prefix `phase9_gap_20260519_012` passed the
full Phase 9 ladder with `PODMAN_SWE_MAX_CONCURRENT=1`:

- `smoke`: 6 expected rows, audit passed with `audit_failure_count=0`.
- `pilot32`: 30 expected rows, audit passed with `audit_failure_count=0`.
- `long64`: 6 expected rows, audit passed with `audit_failure_count=0`.
- `sample128`: 6 expected rows, audit passed with `audit_failure_count=0`.

All 48 expected rows wrote task JSON and reached
`last_stage=task_json_written`. `score=0`, malformed model patches, and
ZeroClaw agent loop-detector rows are still possible; those are model/agent
behavior outcomes rather than Podman readiness failures when the audit passes.
Do not claim full SWE-bench Verified support, SWE-bench Pro support, Podman
default promotion, or a full Verified run from this selected-task evidence.
The readiness gate remains task JSON plus audit pass.

## Standard Reasoning Pilot

Use the Phase 5 script for the audited 12-cell pilot:

```bash
export PODMAN_SCALE_RUN_PREFIX=podman_scale_$(date +%Y%m%d_%H%M%S)
export PODMAN_SCALE_COMMAND_TIMEOUT_SECONDS=7200

bash scripts/run_podman_scale_readiness.sh validate
bash scripts/run_podman_scale_readiness.sh pilot
bash scripts/run_podman_scale_readiness.sh audit
```

The script runs:

- OpenClaw, ZeroClaw, and OpenCode.
- AIME, GPQA-Diamond, HLE, and IMO-AnswerBench.
- Three tasks per agent x benchmark cell.

This host's validated local-vLLM path uses Podman host networking. The script
preflights `/v1/models` from a small Podman container before launching the
pilot. If you are not using a loopback provider, keep the same script flow but
set `OPENAI_BASE_URL` to a container-reachable endpoint.

Primary outputs:

- Raw logs: `logs/<run_id>.log`
- Results: `results/<run_id>/.../tasks/*.json`
- Status TSV: `context/podman-scale-readiness/run-status-<prefix>.tsv`
- Audit JSON/table:
  `context/podman-scale-readiness/audit-<prefix>.json` and
  `context/podman-scale-readiness/audit-<prefix>.md`

Inspect task JSON files as sample lists:

```bash
python - <<'PY'
import json
from pathlib import Path

for path in sorted(Path("results").glob("podman_scale_*/*/tasks/*.json")):
    data = json.loads(path.read_text())
    row = data[0]
    print(path, row.get("score_status"), row.get("score"),
          row.get("metadata", {}).get("container_engine"))
PY
```

Each Podman-backed row should preserve `metadata.container_engine=podman`.

## Nightly Validation Matrix

Use this when you want broader validation-only coverage, including the current
TerminalBench2 and SWE-bench Verified opt-in cells:

```bash
bash scripts/run_podman_nightly_validation.sh validate
bash scripts/run_podman_nightly_validation.sh standard
bash scripts/run_podman_nightly_validation.sh task
```

`all` runs both standard and task scopes:

```bash
bash scripts/run_podman_nightly_validation.sh all
```

This matrix is evidence-gathering only. It does not promote Podman defaults.
Read `context/podman-nightly-validation/README.md` before describing support
status from those rows.

## Focused Task-Container Smokes

TerminalBench2 three-agent Podman readiness pilot:

```bash
export TERMINAL_BENCH2_DIR=<official-terminal-bench-2-task-root>
export TB2_OPENCODE_RUNTIME_IMAGE=localhost/alphadiana/tb2-opencode-controller:latest
export TB2_OPENCLAW_RUNTIME_IMAGE=localhost/alphadiana-openclaw-swebench-runtime-source:latest
export TB2_ZEROCLAW_RUNTIME_IMAGE=localhost/zeroclaw-reasoning:0.6.9
export ALPHADIANA_TB2_LOGS_DIR="$PWD/logs/podman-terminal-bench2-readiness/task-logs"
export PODMAN_TB2_RUN_PREFIX=podman_tb2_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_terminal_bench2_readiness.sh all
```

`all` and `auto` run `validate -> preflight -> pilot -> audit` fail-fast. The
May 16 Phase 7 run prefix `podman_tb2_three_agent_20260516_170725` remains
historical small-matrix smoke evidence for OpenClaw, OpenCode, and ZeroClaw on
`db-wal-recovery`, `overfull-hbox`, and `adaptive-rejection-sampler`.

The expanded local-Qwen run
`podman_tb2_expanded_local_qwen_tmpstore_20260516` should not be cited as
readiness evidence. It wrote 36 task rows, but current stricter audit flags
pre-fix dangling verifier paths plus ZeroClaw provider-error and empty-
assistant rows that were previously masked as `valid_scored`. Current code
preserves verifier folders and records these ZeroClaw rows as `provider_error`
or `agent_empty_output`; `podman_tb2_postfix2_local_qwen_20260516` verifies the
failure-preservation behavior. ZeroClaw provider-proxy runs preserve content-free
request/response summaries, and the readiness audit fails a row if the provider
returns a truly blank choice with empty content, empty reasoning, and no tool
call. The 128K local-Qwen canary
`podman_tb2_longcap3_local_qwen_20260517` passes the current audit for
OpenClaw, OpenCode, and ZeroClaw, and current TerminalBench2 Podman task
containers rewrite host loopback proxy environment before apt/pip commands run
inside the task container.

Current full-sweep evidence is
`podman_tb2_full8_local_qwen_20260517`: local `Qwen/Qwen3.5-4B`, 89 available
TerminalBench2 tasks x OpenClaw/OpenCode/ZeroClaw = 267 task rows,
require-local-image preflight passed, all task JSONs written, and the current
audit passed with `audit_passed=true` and `audit_failure_count=0`. The apparent
90th task-root entry was `.git`, not a task directory with `task.toml`.
Provider-level truly blank choices were `0`; ZeroClaw still produced many
CLI-level empty assistant outputs, which are preserved as abnormal scored-zero
records. The May 17 ZeroClaw follow-up
`podman_tb2_full9_zeroclaw_no_thinking_local_qwen_20260517` reran all 89
ZeroClaw tasks with `enable_thinking=false` and a 900s solver timeout; it
completed 89/89 rows with `zeroclaw_empty_assistant_output=0`, provider true
blank choices `0`, and four explicit timeout-classified rows. Treat that as
historical transport-validation evidence, not the production-style thinking-on
setting. See `context/podman-terminal-bench2-readiness/README.md`.

For production-style local-Qwen ZeroClaw TerminalBench2 runs where thinking
must stay enabled, use provider request/response summaries and the upstream
streaming proxy mode instead of `enable_thinking=false`. The focused
Qwen3.5-27B pilot
`podman_tb2_27b_zc_thinking_stream_logprob_pilot_20260518` used
`enable_thinking=true`, `max_tokens=131072`, `presence_penalty=1.5`, provider
logprobs, and `provider_proxy_upstream_stream=true`; it completed 3/3 selected
task rows with provider errors `0`, CLI final text empty rows `0`, provider
true blank choices `0`, and captured logprobs for every row. This is not full
27B TerminalBench2 readiness: a single ZeroClaw TB2 task can internally drive
roughly 10 simultaneous provider requests even when harness
`max_concurrent=1`, so full sweeps should ramp concurrency from vLLM
`Running` / `Waiting` / KV-cache evidence. The high-budget follow-up
`podman_tb2_zc_thinking_highbudget_canary_v2_20260517` showed that
`enable_thinking=true`, `max_tokens=8192`, and 1800-second budgets can still
produce ZeroClaw `agent_empty_output` without provider true blanks. The
no-thinking full high-budget run `podman_tb2_high8192_zeroclaw_20260517`
completed all 89 rows with `zeroclaw_empty_assistant_output=0`. OpenCode and
OpenClaw high-budget controls
`podman_tb2_high8192_opencode_20260517` and
`podman_tb2_high8192_openclaw_20260517` did not reproduce the ZeroClaw empty
assistant issue; OpenClaw's empty raw-output control rows were timeout rows
with preserved session traces and are timeout-classified by current code.

TerminalBench2 OpenCode Podman smoke:

```bash
export TERMINAL_BENCH2_DIR=/path/to/terminal-bench-2

python -m alphadiana.cli run \
  configs/examples/terminal_bench2_opencode_podman_smoke.yaml \
  --redo-all \
  -o run_id=podman_tb2_opencode_smoke_$(date +%Y%m%d_%H%M%S) \
  -o output_dir=./results/podman_tb2_opencode_smoke
```

SWE-bench Verified OpenClaw Podman smoke:

```bash
python -m alphadiana.cli run \
  configs/examples/openclaw_swe_bench_podman_smoke.yaml \
  --redo-all \
  -o run_id=podman_swe_verified_openclaw_smoke_$(date +%Y%m%d_%H%M%S) \
  -o output_dir=./results/podman_swe_verified_openclaw_smoke
```

## MMMU-Pro Multimodal Readiness

Use the Phase 6 script for the audited 9-task multimodal pilot. This path keeps
thinking mode on and uses Podman host networking for the local VLM endpoint:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-4B
export PODMAN_MMMU_PRO_MAX_TOKENS=8192
export PODMAN_MMMU_PRO_ENABLE_THINKING=1
export PODMAN_MMMU_PRO_VLM_IMAGE_URL=https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen3.5/demo/RealWorld/RealWorld-04.png
export PODMAN_MMMU_RUN_PREFIX=podman_mmmu_pro_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_mmmu_pro_readiness.sh all
```

`all` is equivalent to `auto` and runs
`validate -> preflight -> pilot -> audit` fail-fast. The readiness gate is
infrastructure evidence: 9 task rows, `metadata.container_engine=podman`, image
proof artifacts, no text-only fallback, and no provider VLM rejection. Accuracy
is not the Phase 6 gate. Latest passing evidence:
`podman_mmmu_pro_qwen35_thinking_20260516_144304`, with
`audit_passed=true` and `audit_failure_count=0`.

For task-container results, inspect both the task row and benchmark artifacts.
For SWE-bench Verified, Podman provenance may appear in
`artifacts/<task>/sandbox/sandbox_meta.json` in addition to the top-level task
metadata.

## Scaling Past The Pilot

After a passing pilot audit, create a new config directory from
`configs/smokes/podman_scale_readiness/`, increase the benchmark task selectors
or `max_tasks`, and use unique run IDs. Keep the same operator contract:

- run `python -m alphadiana.cli validate <config>` first;
- write raw shell logs under `logs/<run_id>.log`;
- use `--redo-all` only when you intend to replace existing task rows;
- inspect `results/<run_id>/.../tasks/*.json` via `data[0]`;
- preserve the status TSV and audit output under `context/` if the run changes
  support evidence or recommended commands.

For long overnight campaigns, run the supervisor from a named `tmux` session so
the process is not tied to an agent-operated shell.

## Known Caveats

- Podman remains opt-in. Do not describe these paths as global defaults.
- OpenClaw and ZeroClaw Podman support `capture_logprobs=true` through the
  Phase 8 provider-proxy paths. This is gated single-task smoke evidence, not a
  full campaign.
- Loopback provider URLs such as `http://127.0.0.1:8011/v1` work from the
  Phase 5 matrix because those configs use Podman host networking. Other
  Podman configs may need a non-loopback host gateway URL.
- SWE-bench Pro Podman is not supported from current evidence.
- external_benchmark Podman is deferred pending GPU/CDI validation.
- Do not expose a public TCP Podman API. Use the Unix user socket for
  Docker-compatible clients.

## Full-Run Pre-flight Checklist (China-mainland A800, May 2026)

Operational notes from the China-mainland A800 validation runs against
`Qwen/Qwen3.5-27B` at `http://127.0.0.1:8011/v1`. Apply before any 12-cell
parallel campaign.

### Provider context window vs `max_tokens`

vLLM rejects `max_tokens == max_model_len` with a misleading 400 (the body
says "prompt has N chars" instead of the real cause). `max_tokens` is a
*generation* budget and shares the context window with the prompt, the
system prompt, and (for agentic harnesses) serialized tool definitions —
so it must leave headroom for all of those, not just a fixed 8 K margin.

- **131 K-ctx model**: cap `max_tokens` at ~122 880.
- **200 K-ctx model**: do not assume the full window is free for
  generation. `max_tokens: 196608` only works when the prompt + tool
  defs are tiny; for HLE / tool-heavy ZeroClaw / long-thinking runs,
  cap at ~122 880 (or otherwise leave enough room for prompt + tools).

The framework guarantee is not "never exceed the window" — it is that a
run which legitimately exhausts its `max_tokens` budget terminates with
`finish_reason=length` and is not rejected by the integrity guard
(finding #11b). Sizing `max_tokens` correctly is a config responsibility.

ZeroClaw bridges serialize tool definitions into the prompt; the resulting
overhead can exceed 30 K tokens. If you run ZeroClaw with `max_tokens: 131072`
against a 131 072-ctx endpoint, expect immediate `Request exceeds model context
window` errors. Use a 200 K-ctx endpoint or lower `provider_max_tokens` to
~96 K.

### Logprob dual-write disk cost

`capture_logprobs=true` writes both `results/<run_id>/logprobs/*.jsonl`
(float) and `results/<run_id>/logprobs_int16/*.jsonl` by design. Single OpenCode
IMO tasks generated ~120 MB combined per task. Extrapolated for OpenCode HLE
(3 000 tasks) that is ~180–360 GB.

Always set `output_dir` to a path on a large disk (`/path/to/...`) before turning
on logprobs at scale. Never let `/home` host the logprob sidecars.

### Host networking and port collisions

`configs/smokes/podman_scale_readiness/` pilot configs use `network: host` with
`exposed_port: 8080`. Only one cell can bind that port at a time, so 12-way
parallel runs fail every cell after the first. Override to slirp4netns for
OpenClaw / ZeroClaw cells when fanning out:

```yaml
agent:
  config:
    sandbox_engine:
      podman:
        network: slirp4netns
```

OpenCode is the exception: its controller cannot reach the host vLLM through
slirp4netns (`Unable to connect`), so its cells must stay on `controller_network:
host`. They do not bind a fixed published port, so they co-exist with each
other.

### Kernel keyring quota

Rootless `podman run` allocates a session keyring per container. Default
`kernel.keys.maxkeys=200`, `kernel.keys.maxbytes=20000` is exhausted under
high task churn — the failure surfaces as
`error during container init: unable to join session keyring: disk quota
exceeded` (which looks like a disk issue but isn't).

`maxkeys` / `maxbytes` are a single global per-user quota: raising them needs
root once and then applies to every non-root user on the host. Check, then
raise + persist:

```bash
# Check current values and your uid's usage
cat /proc/sys/kernel/keys/maxkeys /proc/sys/kernel/keys/maxbytes
cat /proc/key-users | grep "$(id -u)" || true

# Raise + persist (root, once) — sysctl --system applies it live, no reboot
echo -e "kernel.keys.maxkeys=2000\nkernel.keys.maxbytes=200000" | \
  sudo tee /etc/sysctl.d/99-podman-keyring.conf
sudo sysctl --system

# Verify — expect 2000 / 200000
sysctl kernel.keys.maxkeys kernel.keys.maxbytes
```

`scripts/security_guard.py --check` warns when the values are below the
recommended floor (1000 / 200 000); it warns only — it does not block. The
TerminalBench2 readiness preflight
(`scripts/preflight_podman_terminal_bench2_readiness.py`) reuses the same
check and emits it under `warnings` in its JSON output and on stderr, so a
low quota is visible from the standard `run_podman_terminal_bench2_readiness.sh`
path without flipping the preflight to a failure.

### Recovery from a SIGKILL'd run

A `podman stop` on a still-running cell, followed by a SIGKILL on the parent
supervisor, leaves the rootless sidecar tree
(`slirp4netns`, `containers-rootlessport`, `conmon`, the agent process, and
the container's `sleep infinity` keep-alive) behind. Normal teardown
already calls `podman rm --force --volumes`; a crashed run never reaches
that path.

Restart code now reaps name-matched orphans automatically on
`PodmanAgentRuntime.start()`. For a manual clean-up:

```bash
podman ps -a --filter name=alphadiana- --format '{{.ID}}' | xargs -r podman rm -f -v
pkill -9 -f slirp4netns                # only your uid
pkill -9 -f containers-rootlessport    # only your uid
pkill -9 -f conmon                     # only your uid
```

### vLLM streaming + thinking mode

The OpenClaw gateway accepts the OpenAI `delta.reasoning_content` field name,
but vLLM with `--reasoning-parser qwen3` emits `delta.reasoning`. The host-side
logprob proxy now rewrites the field name in-place so the gateway captures
thinking output instead of treating it as empty. Keep `enable_thinking: true`
in OpenClaw configs for hard tasks — disabling thinking is no longer required
to avoid empty-stream failures.

If a stream still ends with reasoning-only output and no content, that
indicates a real token-budget exhaustion (`finish_reason=length`). The runner
will record the answer extracted from the partial reasoning instead of marking
the task as a transient empty-response failure.

### vLLM health and the deadlock risk

A vLLM hang/deadlock under long-thinking + concurrency is the single largest
full-run risk. It is an external dependency, not a framework bug — the runner
retries and eventually records `agent_error`, but a wedged endpoint stalls the
whole campaign. Mitigate operationally:

- **Probe directly, not via GPU utilisation.** GPU-util readings on these
  hosts are unreliable. Health = the endpoint answering promptly:

  ```bash
  curl -sS --max-time 30 http://127.0.0.1:8011/v1/models
  # plus a short chat/completions probe — it must return within seconds
  ```

  Treat repeated request timeouts (not GPU idle) as the wedged signal, and
  have a manual restart or a lightweight watchdog ready.

- **Confirm the vLLM log is still being written.** If the log lives on a disk
  that fills up, log writes stall silently while inference keeps serving — you
  end up monitoring a frozen log against a live server. Keep the vLLM log on a
  `/data*` mount, and during a run check its mtime is advancing, not just that
  the process is alive.

## TerminalBench2 Progressive Testing (2026-05-21)

The TB2 podman task-container path was verified through a progressive 3-phase
test across 28 tasks (3/5/20 task phases, 16K/65K/122K max_tokens) with
OpenCode on Qwen3.5-27B (200K ctx). Evidence in
`context/podman-terminal-bench2-readiness/2026-05-21-tb2-podman-progressive-validation.md`.

### TB2 task duration

Complex tasks (scientific-computing, hard difficulty) can take 30-60 minutes
per task because the agent must install dependencies, write implementation
code, and run tests. Plan capacity accordingly:

| Task count | Approx. wall time (single agent, serial) |
|---|---|
| 3 tasks | 1 hour |
| 5 tasks | 2 hours |
| 20 tasks | 7-8 hours |
| 89 tasks (full sweep) | 30+ hours |

### Concurrency recommendation

Use `max_concurrent=1` for TB2 task-container runs. Each TB2 task creates
its own container (with full build/test lifecycle), which stresses kernel
keyrings and podman networking more than standard-reasoning benchmarks.
If you increase concurrency:

- Pre-set `kernel.keys.maxkeys=2000` (see keyring section above)
- Cap cell parallelism at 4-5 rather than 11
- Avoid mixing OpenClaw (long thinking) with other agents in the same
  parallel run — a single stuck vLLM request can delay the whole batch

### Verified full-run config

The following config was verified stable across 20 TB2 tasks with **zero
empty responses** and **all logprob sidecars matching**:

```yaml
agent:
  config:
    max_tokens: 122880       # 200K ctx model, 8K headroom
    capture_logprobs: true
    top_logprobs: 20
    logprobs_format: int16
max_concurrent: 1
```

The framework now auto-reserves 8K headroom when resolving max_tokens from
the model endpoint (`max_model_len - 8192`). Explicit `max_tokens` values
in config are passed through unchanged.
## SWE-bench Verified Podman Bring-up

Operational checklist for taking a host from zero to a passing SWE-bench
Verified Podman smoke. Use the Phase 9 configs and scripts under
`configs/smokes/podman_swe_verified_readiness/` and
`scripts/run_podman_swe_verified_readiness.sh` — they are the canonical
entry point.

### Host prerequisites

1. **Podman 3.4.4+** with the user socket enabled:
   ```bash
   systemctl --user enable --now podman.socket
   export ALPHADIANA_PODMAN_SOCKET="/run/user/$(id -u)/podman/podman.sock"
   export ALPHADIANA_PODMAN_DOCKER_API_VERSION=1.41
   ```
2. **Kernel keyring raised** (root once, persistent):
   ```bash
   echo -e "kernel.keys.maxkeys=2000\nkernel.keys.maxbytes=200000" | \
     sudo tee /etc/sysctl.d/99-podman-keyring.conf
   sudo sysctl --system
   ```
   The default `200/20000` exhausts mid-run and surfaces as
   `unable to join session keyring: disk quota exceeded`.
3. **Podman storage off `/home`.** An astropy + django + sympy pilot pulls
   ~25–40 GB of layers; point `graphroot` at a large disk via
   `~/.config/containers/storage.conf`.
4. **`localhost/zeroclaw-reasoning:0.6.9` loaded** (`podman load -i …`).
   The ZeroClaw smoke copies the binary into the task container from this
   image; if it is missing the binary copy produces an empty file and the
   in-container `--version` fails.
5. **vLLM endpoint reachable** at `OPENAI_BASE_URL`. The Phase 9 smoke
   configs use `sandbox.config.network_mode: host`; the runner rewrites
   `127.0.0.1` to `host.containers.internal` automatically for non-loopback
   bases.

### Run order

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export PODMAN_SWE_MAX_CONCURRENT=1

bash scripts/run_podman_swe_verified_readiness.sh validate    # configs parse
bash scripts/run_podman_swe_verified_readiness.sh preflight   # must be ok=true
bash scripts/run_podman_swe_verified_readiness.sh auto        # smoke→pilot32→long64→sample128
```

Audit pass means `audit_passed: true` and `audit_failure_count: 0` in the
`audit-<run_prefix>-<tier>.md` reports under
`context/podman-swe-verified-readiness/`. `score=0`, malformed model
patches, and ZeroClaw `agent_loop_detector` rows are model/agent behaviour,
not readiness failures, as long as the audit closes clean.

### Failure modes that look like environment limits but are not

| Symptom | Real cause | Fix |
| --- | --- | --- |
| ZeroClaw HTTP 400 `System message must be at the beginning` | Agent-side provider proxy normalizer not active. SWE-bench mode talks to vLLM directly from inside the task container and bypasses the bridge, so `_normalize_system_messages` in `zeroclaw_deploy/zeroclaw_bridge.py` alone is not enough. | Use the Phase 9 ZeroClaw smoke config — it sets `agent.config.provider_proxy_normalize_system_messages: true`. |
| ZeroClaw binary extracts but `--version` fails inside the task container | `binary_source_image` not loaded on this host, so the in-container copy lands an empty file. | `podman load -i` the matching tar and verify with `podman images \| grep zeroclaw-reasoning`. |
| OpenClaw "Patch Apply Failed" / "model only analyzes, no patch block" | `max_tokens` too low (legacy `4096` truncates the reasoning + patch); or a stale `openclaw_swe_bench.runtime.json`. | Use the Phase 9 OpenClaw smoke config (`max_tokens: 32768`, `request_timeout: 7200`, current runtime JSON). |
| `image not found: sweb.env.py.x86_64.*:latest` or similar short-name miss | Rootless Podman cannot resolve swebench short-name / namespaced refs without a `localhost/` prefix. | `alphadiana/utils/swebench.py:podman_local_image_ref` handles this; the preflight's `image_qualification.unqualified` list must be empty. |
| vLLM returns `400 prompt has N chars` for every request | `_resolve_max_tokens()` fell through to the `/v1/models` auto-detect branch and requested `max_tokens == max_model_len`. | Set `max_tokens` explicitly in the YAML (the Phase 9 configs do this); the agent also reserves an 8 K headroom on auto-detect. |
| `unable to join session keyring: disk quota exceeded` mid-run | Kernel keyring quota at the default 200/20000. Misleading wording — not a disk issue. | Prerequisite 2 above. |
| `BuildImageError` building an astropy env image on an offline/proxied host | The astropy SWE env install runs `pip install -e .` and a Cython rebuild and cannot reach PyPI. | Ensure host-level `HTTP_PROXY` / `HTTPS_PROXY` are honoured by the podman build network. |
| Scorer fails with `docker: command not found` although the task ran | `alphadiana/scorer/swe_bench.py` did not pick up `container_engine: podman`. | The Phase 9 scorer block sets `container_engine`, `podman_socket`, `docker_api_version`, `docker_build_network` explicitly — copy those four fields verbatim into any new SWE config. |

The framework guarantees needed for these to fail cleanly already live on
`feat/add-podman`. The bring-up flow is therefore: merge latest
`feat/add-podman` into the working branch → load `zeroclaw-reasoning:0.6.9`
→ raise keyring → run the three commands above. The preflight pass is the
portable gate for "runs on another machine."

### Reference evidence

Canonical audited run: prefix `phase9_gap_20260519_012` against local
`Qwen/Qwen3.5-27B` at `http://127.0.0.1:8011/v1` with
`PODMAN_SWE_MAX_CONCURRENT=1`. All four tiers
(`smoke` / `pilot32` / `long64` / `sample128`) closed with
`audit_passed: true` and `audit_failure_count: 0`; 48 expected rows
reached `last_stage=task_json_written`. Per-agent astropy smoke task JSON
(under `results/phase9_gap_20260519_012_*/`) shows OpenClaw, OpenCode, and
ZeroClaw all scoring 1.0 on `astropy__astropy-12907` with a 501-char
unified diff. See `context/podman-swe-verified-readiness/README.md` for
the full record.

Merge-verification evidence: a working branch that had reported ZeroClaw
HTTP 400 `System message must be at the beginning` and OpenClaw
"Patch Apply Failed" was rebased onto this branch state and re-run on
2026-05-24. Result prefix `mergeverify_20260524_1715`, same host, same
local `Qwen/Qwen3.5-27B`, same `PODMAN_SWE_MAX_CONCURRENT=1`. Smoke audit
closed with `audit_passed: true`, `audit_failure_count: 0`, taxonomy
`clean: 6`. Per-row scores: OpenClaw `1.0 / 0.0`, OpenCode `1.0 / 0.0`,
ZeroClaw `1.0 / 0.0` across the two astropy tasks; every row wrote a
unified diff (501–5623 chars). The previously-reported ZeroClaw 400 did
not recur. Artifacts:
`context/podman-swe-verified-readiness/audit-mergeverify_20260524_1715-smoke.{json,md}`,
`preflight-mergeverify_20260524_1715.json`.
