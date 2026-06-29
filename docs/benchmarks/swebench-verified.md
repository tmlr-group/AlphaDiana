# SWE-bench Verified

SWE-bench Verified (`benchmark.name: swe_bench`, dataset
`SWE-bench/SWE-bench_Verified`) runs an agentic harness **inside an official
per-task SWE container**. For each task the `swebench_container` sandbox starts
the official instance image, the agent edits the repository and emits a patch,
and the `swe_bench` scorer hands that patch to the official `swebench` evaluator
(`report.json`, `run_instance.log`, and `test_output.txt` are attached as
artifacts). There is no `direct_llm` path here; the task needs an agent acting
inside a container. For the SWE-bench **Pro** path see
[SWE-bench Pro](./swebench-pro); for the smaller set see
[SWE-bench Verified Mini](./swebench-verified-mini).

## Prerequisites

```bash
source scripts/activate.sh        # activate the environment
docker ps                         # the swebench_container sandbox needs Docker
```

Point the harness at an OpenAI-compatible endpoint. The request is issued from
**inside the task container**, so a host-loopback URL will not resolve; use the
Docker bridge address (or another reachable host):

```bash
export OPENAI_BASE_URL=http://host.docker.internal:8011/v1   # bridge, not 127.0.0.1
export OPENAI_API_KEY=<key>                          # any non-"EMPTY" string for local vLLM
export OPENAI_MODEL_NAME=<model>
```

If the dataset is slow to fetch from Hugging Face, set a mirror with
`export HF_ENDPOINT=https://hf-mirror.com`. Install the SWE extras once with
`pip install -e '.[agents,benchmarks,swebench]'`.

## Supported Modes

All three agentic harnesses run through `runtime: swebench_container` plus
`sandbox.name: swebench_container`. Each ships as a checked-in smoke config;
scale a full run by raising `benchmark.config.max_tasks`.

| Harness | How the agent runs in the task container | Smoke config |
| --- | --- | --- |
| `openclaw` | starts an `openclaw` gateway in the container; AlphaDiana drives it over the OpenAI-compatible API | `configs/examples/openclaw_swe_bench.yaml` |
| `opencode` | runs `opencode run` directly; the patch is taken from `git diff HEAD` | `configs/examples/opencode_swe_bench.yaml` |
| `zeroclaw` | runs the `zeroclaw` CLI in the container | `configs/examples/zeroclaw_swe_bench.yaml` |

> ZeroClaw on a local `Qwen/Qwen3.5-27B` endpoint currently preserves
> provider-side context overflow as a `provider_error` rather than an empty
> patch. Treat that as a known limitation, not a passing run.

`validate` only checks the config shape. `run` pulls the dataset, starts the
per-task container, runs the agent, and invokes the official evaluator, so it
needs Docker and a reachable model. A `dashboard X` means the pipeline ran but
the patch did not solve the task (a model result, not an execution failure).

## Shared config

The three configs share the benchmark, sandbox, and scorer blocks:

```yaml
benchmark:
  name: swe_bench
  config:
    dataset: SWE-bench/SWE-bench_Verified
    split: test
    include_hints: false        # do not append hints_text to the problem
    max_tasks: 1                # smoke; raise for a full run
sandbox:
  name: swebench_container      # one official instance container per task
  config:
    namespace: swebench
    keep_container: false       # remove the container after each task
    keep_logs: true
    gateway_port: 8080          # mapped port for the OpenClaw gateway
scorer:
  name: swe_bench               # official swebench harness applies + tests the patch
  config:
    timeout: 1800
```

## OpenClaw

`configs/examples/openclaw_swe_bench.yaml` sets `runtime: swebench_container` and
`openclaw_config_path: alphadiana/harness/openclaw/deploy/openclaw_swe_bench.runtime.json`.
At run time the harness installs and starts an `openclaw` gateway inside each
task container, injects the three `OPENAI_*` variables into the runtime JSON, and
talks to the gateway over `/v1/chat/completions`. Artifacts include the OpenClaw
session trajectory and gateway logs.

```bash
python -m alphadiana.cli run configs/examples/openclaw_swe_bench.yaml \
  -o run_id=swebench-openclaw-smoke -o benchmark.config.max_tasks=1
```

## OpenCode

`configs/examples/opencode_swe_bench.yaml` runs `opencode run` directly in the
container (no gateway) and extracts the final patch with `git diff HEAD`. It
reads the model endpoint from the three `OPENAI_*` variables and writes the
provider config to `opencode.json` inside the container.

```bash
python -m alphadiana.cli run configs/examples/opencode_swe_bench.yaml \
  -o run_id=swebench-opencode-smoke -o benchmark.config.max_tasks=1
```

## ZeroClaw

`configs/examples/zeroclaw_swe_bench.yaml` runs the `zeroclaw` CLI in the task
container; artifacts include `zeroclaw_output.txt` and `zeroclaw_stderr.log`. See
the limitation note above for the local Qwen path.

```bash
python -m alphadiana.cli run configs/examples/zeroclaw_swe_bench.yaml \
  -o run_id=swebench-zeroclaw-smoke -o benchmark.config.max_tasks=1
```

## Gotcha: container networking

If a task container is up and the gateway log looks healthy but the host side of
`/v1/models` resets, check the rendered `openclaw.json` `gateway.bind` /
`customBindHost` before suspecting the model. The gateway must not bind only to
`127.0.0.1`, and the `swebench_container` path deliberately does not forward
host-loopback proxy variables (a bridge-network container cannot use the host's
loopback proxy). OpenClaw is installed in-container via `npm`, backed by a
host-side `libsignal-node` git mirror so the install does not need to reach
GitHub.

## Result locations

Per-task results land under `results/<run_id>/`:

- `tasks/<task_id>.json` is the scored record (a non-`-` `dashboard` letter means
  the pipeline ran).
- `logs/swebench_container/` and `swe_bench_logs/` hold build and evaluation logs.
- the official evaluator output (`report.json`, `run_instance.log`,
  `test_output.txt`) is attached as task artifacts.

A smoke run is healthy when `results/<run_id>/tasks/<task_id>.json` exists, the
record has no `error`, and the `dashboard` letter is `O` or `X` (not `-`).
