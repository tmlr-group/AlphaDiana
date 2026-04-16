# ZeroClaw on MoltBench

This document explains how to reproduce the ZeroClaw migration in this PR.
It focuses on the **MoltBench Docker path** added by
[`moltbench/docker/Dockerfile.zeroclaw`](../docker/Dockerfile.zeroclaw) and
[`moltbench/docker/run_task.sh`](../docker/run_task.sh).

If you want the standard AlphaDiana reasoning path instead of the MoltBench
Docker path, use
[`docs/tutorial_zeroclaw_aime2026.md`](../../docs/tutorial_zeroclaw_aime2026.md).
That tutorial covers the `ROCK -> sandbox -> proxy -> zeroclaw bridge`
architecture used by [`configs/examples/zeroclaw_aime2026.yaml`](../../configs/examples/zeroclaw_aime2026.yaml).

## What Is Different From The Root README

The root [`README.md`](../../README.md) and the general getting-started docs
describe the standard AlphaDiana reasoning flow built around ROCK sandboxes
and benchmarks such as AIME. That is **not** the code path exercised by this
ZeroClaw migration.

For this PR:

- The agent is `moltbench_docker`, not the general `openclaw`/ROCK path.
- The benchmark is `moltbench`, not AIME / MATH / custom reasoning tasks.
- The image is `tmlrgroup/alphadiana:zeroclaw`.
- The container entrypoint is `/workspace/run_task.sh`.
- The agent runs inside the task container with `MOLTBENCH_AGENT=zeroclaw`.

If you want to review this PR or reproduce its results, follow the MoltBench
commands below rather than the root README examples.

### About AIME 2026

The root README and the general AlphaDiana docs can still be used for
reasoning benchmarks such as **AIME 2026**, but that is a different execution
path:

- AIME 2026 uses `benchmark.name: aime`.
- AIME 2026 writes results under `results/<run_id>.jsonl`, not under
  `moltbench_artifacts_*`.
- AIME 2026 runs through the standard reasoning/ROCK setup, not through
  `moltbench/docker/run_task.sh`.

Example config shape for AIME 2026:

```yaml
benchmark:
  name: aime
  config:
    dataset: "MathArena/aime_2026"
    split: "train"
```

This repository now also includes a dedicated ZeroClaw example for that
standard path:

- [`configs/examples/zeroclaw_aime2026.yaml`](../../configs/examples/zeroclaw_aime2026.yaml)
- [`zeroclaw_deploy/Dockerfile`](../../zeroclaw_deploy/Dockerfile)

That benchmark is useful to document for the overall project, but it is **not
the benchmark validated by this ZeroClaw image migration**. The smoke
validation in this PR is specifically the MoltBench KernelBench path below.

## Files Added By This Migration

- [`moltbench/docker/Dockerfile.zeroclaw`](../docker/Dockerfile.zeroclaw):
  builds a ZeroClaw-enabled MoltBench image on top of
  `tmlrgroup/alphadiana:gpu-base`.
- [`moltbench/docker/run_task.sh`](../docker/run_task.sh):
  adds `launch_zeroclaw()`, ZeroClaw config generation, workspace wiring, and
  artifact collection.
- [`configs/examples/moltbench_zeroclaw_smoke.local.yaml`](../../configs/examples/moltbench_zeroclaw_smoke.local.yaml):
  one-problem smoke config.
- [`configs/examples/moltbench_zeroclaw_L1_batch.yaml`](../../configs/examples/moltbench_zeroclaw_L1_batch.yaml):
  Level-1 batch config.

## Prerequisites

- Docker with NVIDIA runtime available on the host.
- `alphadiana env` passes on the host before running the benchmark.
- A reachable OpenAI-compatible provider configured through:
  - `OPENAI_API_KEY`
  - `OPENAI_BASE_URL`
  - `OPENAI_MODEL_NAME`
- The provider endpoint must be reachable from the container.
  Because the sample configs use `network_mode: "host"`, a host-local service
  can be exposed directly. If you are pointing at another machine, use its real
  IP or URL, not `localhost`.

## Build The ZeroClaw Image

From the repository root:

```bash
docker build --network host -f moltbench/docker/Dockerfile.base -t tmlrgroup/alphadiana:gpu-base .
docker build --network host -f moltbench/docker/Dockerfile.zeroclaw -t tmlrgroup/alphadiana:zeroclaw .
```

Expected result:

- `docker images | rg 'tmlrgroup/alphadiana:(gpu-base|zeroclaw)'` shows both
  images.
- `docker run --rm --entrypoint zeroclaw tmlrgroup/alphadiana:zeroclaw --version`
  prints a ZeroClaw version string.

Do not use a bare `docker run tmlrgroup/alphadiana:zeroclaw` as a quick check:
the image entrypoint is the MoltBench runner, which expects
`/workspace/task/task.json` to be mounted by AlphaDiana.

## Required Runtime Configuration

Export provider settings before invoking AlphaDiana:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
export OPENAI_MODEL_NAME=your-model-name
```

Optional knobs specific to this integration:

```bash
export ZEROCLAW_TIMEOUT_SEC=1200
```

Notes:

- `ZEROCLAW_TIMEOUT_SEC` limits `zeroclaw agent -m` inside the container.
- `request_timeout` in the YAML config must be larger than
  `ZEROCLAW_TIMEOUT_SEC`.
- The MoltBench smoke config already passes `MOLTBENCH_AGENT=zeroclaw` through
  `MoltBenchDockerAgent`; reviewers do not need to set it manually.
- If you use OpenRouter, prefer a fully qualified model ID such as
  `qwen/qwen-2.5-7b-instruct` instead of a shorthand like `deepseek-chat`.
  `MoltBenchDockerAgent` does not override non-empty values already present in
  the YAML file, so reviewers should explicitly update the smoke config if they
  are not using the default model baked into the example file.

## Smoke Run Command

The recommended reproduction command is:

```bash
alphadiana run configs/examples/moltbench_zeroclaw_smoke.local.yaml
```

This config runs:

- Benchmark: `moltbench`
- Task: `KernelBench L1 P001`
- Agent image: `tmlrgroup/alphadiana:zeroclaw`
- Max optimization rounds: `2`
- Container network mode: `host`
- Default ZeroClaw timeout: `1200s`

If you prefer a single self-contained shell command:

```bash
OPENAI_API_KEY=... \
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1 \
OPENAI_MODEL_NAME=your-model-name \
alphadiana run configs/examples/moltbench_zeroclaw_smoke.local.yaml
```

Expected high-level outcome:

- AlphaDiana completes a single MoltBench task.
- The artifact directory contains `results.json`,
  `final_verification_results.json`, and container logs.
- A successful smoke should yield a parsed MoltBench result with
  `correctness: PASS` or `correctness: FAIL`.
  `FAIL` is still an execution success; `INVALID` is not.

## Validated Local Smoke Recipe

The exact local smoke recipe that was successfully re-run on **2026-04-15**
used:

- Provider: `https://openrouter.ai/api/v1`
- Model: `qwen/qwen-2.5-7b-instruct`
- `max_rounds: 1`
- `ZEROCLAW_TIMEOUT_SEC: 600`
- `request_timeout: 1200`

Because the checked-in example config currently hardcodes
`OPENAI_MODEL_NAME: "deepseek-chat"`, the validated run used a temporary copy
with a corrected model name and per-run output directories:

```bash
ts=$(date +%Y%m%d_%H%M%S)
cfg=/tmp/moltbench_zeroclaw_smoke_${ts}.yaml
cp configs/examples/moltbench_zeroclaw_smoke.local.yaml "$cfg"
sed -i "s|run_id: .*|run_id: \"moltbench-zeroclaw-smoke-local-L1-P001-${ts}\"|" "$cfg"
sed -i "s|output_dir: \"./moltbench_artifacts_zeroclaw_smoke_local\"|output_dir: \"/tmp/moltbench_artifacts_zeroclaw_smoke_${ts}\"|" "$cfg"
sed -i "s|output_dir: \"./results_zeroclaw_smoke_local\"|output_dir: \"/tmp/results_zeroclaw_smoke_${ts}\"|" "$cfg"
sed -i 's|request_timeout: 1500|request_timeout: 1200|' "$cfg"
sed -i 's|OPENAI_MODEL_NAME: "deepseek-chat"|OPENAI_MODEL_NAME: "qwen/qwen-2.5-7b-instruct"|' "$cfg"
sed -i 's|ZEROCLAW_TIMEOUT_SEC: "1200"|ZEROCLAW_TIMEOUT_SEC: "600"|' "$cfg"
sed -i 's|max_rounds: 2|max_rounds: 1|' "$cfg"

export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen-2.5-7b-instruct

env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u http_proxy -u https_proxy \
  PYTHONPATH=$(pwd) \
  python -m alphadiana.cli run "$cfg" --redo-all
```

On this machine, the successful re-run produced:

- Run file:
  `/tmp/results_zeroclaw_smoke_20260415_171900/moltbench-zeroclaw-smoke-local-L1-P001-20260415_171900.jsonl`
- Artifact root:
  `/tmp/moltbench_artifacts_zeroclaw_smoke_20260415_171900/moltbench_L1_P001/sample_0/407c16bf342f43288478f8670a557d6c/`
- Parsed result:

```json
{
  "correctness": "PASS",
  "speedup": 0.999,
  "kernel_time_ms": 7.4916,
  "reference_time_ms": 7.4813,
  "pytorch_time_ms": 7.4813
}
```

This is the main expected reviewer signal for the MoltBench path:

- the ZeroClaw image starts correctly
- the agent can edit the KernelBench workspace
- the final independent verification pass produces a parseable
  `final_verification_results.json`
- AlphaDiana records the run instead of leaving the task in `INVALID`

## What The Smoke Run Actually Exercises

Inside the MoltBench container, `run_task.sh` will:

1. Read `/workspace/task/task.json`.
2. Set up the KernelBench problem under `/workspace/autokernel`.
3. Install the benchmark round guard.
4. Initialize a git repo in the task workspace.
5. Create `~/.zeroclaw/config.toml` with full autonomy enabled.
6. Symlink `~/.zeroclaw/workspace` to the MoltBench task workdir so ZeroClaw
   can read and edit the real benchmark files.
7. Run `zeroclaw agent -m "$prompt"`.
8. Collect artifacts and run a final independent benchmark pass.

This means a successful smoke run demonstrates that ZeroClaw can:

- read files in the MoltBench task directory,
- write edits back into the benchmark workspace,
- execute shell commands such as benchmark runs, and
- leave behind parseable artifacts for AlphaDiana scoring.

## Expected Outputs

The main artifact directory will be created under:

```text
moltbench_artifacts_zeroclaw_smoke_local/moltbench_L1_P001/sample_0/<execution_id>/
```

In the validated local run above, the artifact directory was:

```text
/tmp/moltbench_artifacts_zeroclaw_smoke_20260415_171900/moltbench_L1_P001/sample_0/407c16bf342f43288478f8670a557d6c/
```

Important files to check:

- `results.json`
- `final_bench.log`
- `final_verification_results.json`
- `container_stdout.log`
- `container_stderr.log`
- `zeroclaw_output.txt`
- `zeroclaw_stderr.log`
- `kernel.py` (if it was edited)
- `kernel_git_log.txt`

Expected scoring shape:

```json
{
  "correctness": "PASS | FAIL | INVALID | ERROR",
  "speedup": 0.0
}
```

For a healthy smoke run, the minimum expectation is:

- AlphaDiana completes without Docker/runtime errors.
- `zeroclaw_output.txt` is non-empty.
- `results.json` exists and is parseable.
- The artifact directory contains benchmark logs from the final verification
  pass.

For the validated recipe on this machine, the expected successful outcome is:

- `container_stdout.log` contains `source=file`.
- `container_stdout.log` contains `Problem setup complete`.
- `container_stdout.log` contains `Launching ZeroClaw agent`.
- `container_stdout.log` contains `ZeroClaw agent finished`.
- `container_stdout.log` contains `Final verification complete`.
- `results.json` is `PASS` with speedup close to `1.0` rather than an exact
  speedup target.

The exact numbers can vary slightly between runs. The local successful re-run
recorded `speedup = 0.999`.

## Quick Verification Commands

After the run, reviewers can verify the artifacts with:

```bash
find moltbench_artifacts_zeroclaw_smoke_local -name results.json -print
find moltbench_artifacts_zeroclaw_smoke_local -name zeroclaw_output.txt -print
```

And inspect the final parsed result with:

```bash
python - <<'PY'
from pathlib import Path
import json

paths = sorted(Path("moltbench_artifacts_zeroclaw_smoke_local").glob("**/results.json"))
assert paths, "results.json not found"
print(paths[-1])
print(json.dumps(json.loads(paths[-1].read_text()), indent=2))
PY
```

For the validated run on this machine:

```bash
sed -n '1,160p' /tmp/results_zeroclaw_smoke_20260415_171900/moltbench-zeroclaw-smoke-local-L1-P001-20260415_171900.jsonl
sed -n '1,260p' /tmp/moltbench_artifacts_zeroclaw_smoke_20260415_171900/moltbench_L1_P001/sample_0/*/container_stdout.log
```

## L1 Batch Command

After the smoke run passes, Level-1 batch reproduction uses:

```bash
alphadiana run configs/examples/moltbench_zeroclaw_L1_batch.yaml
```

That config keeps the same agent/image path and expands the task set to all
KernelBench Level-1 problems.
