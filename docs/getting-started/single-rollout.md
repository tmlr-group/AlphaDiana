# Single-Task Rollout

`alphadiana.single_rollout` runs exactly one task with one agent and prints a
compact JSON result. It is meant for external controllers such as AlphaHebe that
schedule individual rollouts themselves. It wraps the normal `Runner`, so
harnesses, benchmarks, scorers, and result files behave exactly as in
`alphadiana run`.

```bash
python -m alphadiana.single_rollout configs/examples/single_rollout/pi_hle_verified.yaml \
  --import alphahebe.integrations.diana.benchmarks.hle_verified \
  --output result.json
```

## Options

| Option | Meaning |
|---|---|
| `CONFIG` | A normal AlphaDiana YAML config. It must load exactly one task unless `--task-id` is given. |
| `--task-id ID` | Select one task when the config loads several. |
| `--import MODULE` | Import a plugin module before setup so it can register benchmarks, scorers, or harnesses. May be repeated. |
| `--run-id ID` | Use this run ID. By default the config `run_id` gets a random suffix. |
| `-o/--override key=value` | Same override syntax as `alphadiana run`. |
| `--output PATH` | Also write the JSON result to a file. |

## Behavior

- Every call executes the task in a fresh run directory. `redo_all` is forced,
  and an explicit `--run-id` that already has results is rejected rather than
  resumed, so a repeated call never returns an earlier record.
- `num_samples` must be `1`.
- The process exits with `0` whenever the Runner produced a record, including
  error records such as `runtime_error`; inspect `status`. It exits with `1` and
  prints `{"status": "error", ...}` when the config, plugin import, task
  selection, or setup fails.

## Result

```json
{
  "status": "complete",
  "score_status": "valid_scored",
  "outcome_category": "candidate_score",
  "run_id": "pi-hle-verified-3f2a9c1b7d4e",
  "task_id": "hle_verified_...",
  "agent": {"name": "pi", "version": "model-deployment-v1"},
  "benchmark": "hle_verified",
  "scorer": "hle_verified",
  "predicted": "D",
  "correct": true,
  "score": 1.0,
  "rationale": "",
  "cost": {"wall_seconds": 812.4, "tokens": {"prompt_tokens": 4604, "completion_tokens": 9120}},
  "evidence": {
    "result_jsonl": "/abs/results/single_rollout/<run_id>.jsonl",
    "task_record": "/abs/results/single_rollout/<run_id>/tasks/<task_id>.json",
    "run_manifest": "/abs/results/single_rollout/<run_id>/run_manifest.json",
    "artifact_manifest": {},
    "trajectory_available": true
  }
}
```

`status` is `complete` only for `valid_scored` records; otherwise it repeats the
record's canonical `score_status`. `outcome_category` distinguishes a measured
`candidate_score` from `candidate_failure`, `serving_failure`,
`infrastructure_failure`, and `evaluation_failure`. Ambiguous runtime or agent
errors use `execution_failure` until their origin is known. Remaining invalid
statuses (`unscored`, `preserved_failure`, or an unrecognized status) use
`unscored_failure`; this is not a candidate reward. For any invalid status,
top-level `score` and `correct` are `null` even if a scorer wrote a numeric
fallback; the raw record and its diagnostic fields remain under `evidence`.
Consumers must require `score_status: valid_scored` before using a value as a
training reward. A bad candidate, including an ALE compilation failure returned
by the official judge, can still be a valid measured low score. ALE
`INTERNAL_ERROR` and scorer/evaluator exceptions are not.

## Harness x benchmark templates

`configs/examples/single_rollout/` has one entry per combination:

| Harness | HLE-Verified | ALE-Bench | EvoX problems |
|---|---|---|---|
| Pi | `pi_hle_verified.yaml` | `pi_ale_bench.yaml` | `pi_evox_problems.yaml` |
| DeepSeek harness | `deepseek_hle_verified.yaml` | `deepseek_ale_bench.yaml` | `deepseek_evox_problems.yaml` |

Each file names its `--import` module in the header comment:

| Benchmark | `--import` |
|---|---|
| HLE-Verified | `alphahebe.integrations.diana.benchmarks.hle_verified` |
| ALE-Bench | `alphahebe.integrations.diana.benchmarks.ale_bench` |
| EvoX problems | `alphahebe.integrations.diana.benchmarks.evox_problems` |

For Pi, run each template as a separate rollout (set the prerequisites below
first). These commands are examples, not claims of a live end-to-end run:

```bash
python -m alphadiana.single_rollout configs/examples/single_rollout/pi_hle_verified.yaml \
  --import alphahebe.integrations.diana.benchmarks.hle_verified --output hle-result.json
python -m alphadiana.single_rollout configs/examples/single_rollout/pi_ale_bench.yaml \
  --import alphahebe.integrations.diana.benchmarks.ale_bench --output ale-result.json
python -m alphadiana.single_rollout configs/examples/single_rollout/pi_evox_problems.yaml \
  --import alphahebe.integrations.diana.benchmarks.evox_problems --output evox-result.json
```

The templates read these environment variables:

| Variable | Used for |
|---|---|
| `PI_HARNESS_IMAGE`, `DEEPSEEK_HARNESS_IMAGE` | Native harness image IDs (`sha256:<64 hex>`) |
| `OPENAI_MODEL_NAME`, `OPENAI_BASE_URL`, `OPENAI_API_KEY` | Model endpoint |
| `SKYDISCOVER_PATH` | Clean SkyDiscover checkout at the pinned revision (EvoX only) |

## Prerequisites

- AlphaDiana and AlphaHebe installed in the same environment, with the
  `ale-bench` and `evox-problems` extras when those benchmarks are used (see
  AlphaHebe `docs/benchmarks.md`).
- Rootless Podman and built native harness images; see
  [Native Podman harnesses](../harnesses/native-podman.md). A local model service
  is reachable from the container at `host.containers.internal`.
- A model endpoint that accepts tool calls. Pi and the DeepSeek harness send
  tools with `tool_choice: auto`; a vLLM server must be started with
  `--enable-auto-tool-choice` and a matching `--tool-call-parser`, or every
  request fails with HTTP 400 and the record is a `runtime_error`.
- HLE-Verified: access to the Hugging Face dataset (set `HF_ENDPOINT` for a
  mirror and `HF_HOME` for the cache location).
- ALE-Bench: Docker and the official judge image.
- EvoX problems: a clean SkyDiscover checkout at the revision in the template.

Set the `agent.config.timeout` in each template to the per-task budget. Native
Pi timeouts after container creation are completed scored-zero attempts;
container setup/cleanup failures and provider errors are invalid. The native
agent force-removes its task container in `finally`, and `single_rollout`
tears down the runner in `finally`. A hard kill (`SIGKILL`/machine loss) cannot
run these handlers; the outer worker must clean containers with the
`alphadiana.execution` label before retrying. Do not interpret process exit
code `0` alone as a valid score.

For cancellation, persist a fresh 32-hex execution ID **before** launching and
pass it as `--override agent.config._execution_id=<id>`. This is the container
ownership label, not the Runner's separate task execution ID. After stopping
the worker (and its process group), the supervisor can call:

```bash
python -c 'import sys; from alphadiana.harness._internal.podman_agent import cleanup_execution; cleanup_execution(sys.argv[1])' "$execution_id"
```

Retrying this cleanup is safe. Never clean another live execution's label or
run global container pruning. `KeyboardInterrupt` propagates through the
native solve's cleanup; `SIGTERM` has no graceful handler in this CLI, and
`SIGKILL` cannot run `finally`, so both require the supervisor's cleanup.
The opt-in test below creates two lightweight real Podman containers and
verifies that orphan cleanup removes only its execution, including on retry:

```bash
ALPHADIANA_LIFECYCLE_IMAGE=sha256:<installed-image-with-sleep> \
  python -m pytest tests/test_native_cleanup_runtime.py -q
```

This tests the real cleanup primitive, not a killed Pi process or a live model.

## Known limitations

- The DeepSeek harness requests `reasoning_effort: high`. Endpoints that accept
  only other effort values, such as Qwen3.8 chat templates, reject the request.
- The native harness timeout covers queueing at the model endpoint. On a busy
  endpoint a long reasoning request can time out before any answer is produced.

## Evaluator coverage snapshot for issue #14 (2026-09-17)

The following checks are narrower than a Pi/Qwen end-to-end benchmark run:

| Family | Checked on pinned dependencies | Not yet established |
|---|---|---|
| ALE-Bench | Source commit `f7d927906dc1dcd860ee086e4560d576438b1354`; fresh A800 official `ahc027` public judge replay: 5 seeds, `ACCEPTED`, absolute score `49649610`, reward `1.0070571544210363e-07`. | This replays a stored DFS candidate, not fresh Pi/Qwen generation. No private seeds were evaluated. |
| EvoX | SkyDiscover commit `700711ec4c37d0be677456f66d38461310c0bcf8`: original Python `circle_packing/evaluator.py` seed **0.3642368945** (A100-2); default `third_autocorr_ineq/evaluator` Docker seed **0.5783624315** (A800). Both deliberately failing sources were rejected with no reward using Hebe #16. | Only these selected evaluator paths were checked. The autocorrelation evaluator ignores `mode`; no independent held-out split is established. Resolver, Harbor, GPU, and multi-file problems are outside this adapter's scope. |
| HLE-Verified | The Pi template and plugin are present; this change tests the result contract. | No new live HLE/Pi run was made for this change. Keep final-audit item-level evidence out of optimization. |

To reproduce the EvoX seed smoke after installing both repositories in one
environment, use a clean checkout at the commit above:

```bash
python scripts/validate_evox_evaluator.py /path/to/skydiscover
python scripts/validate_evox_evaluator.py /path/to/skydiscover --invalid-source
```

These commands require the companion [Hebe EvoX error-handling fix](https://github.com/AndrewZhou924/AlphaHebe-dev/pull/16). They invoke
the original SkyDiscover evaluator on trusted seed code and a deliberately
failing source, respectively; the latter must raise a typed verifier failure,
not return a reward. They do not exercise the Pi model. To select the default
container evaluator instead, add `--problem third_autocorr_ineq`. That
evaluator ignores `mode`, so `train`/`test` is not an independent held-out split.

To replay the accepted ALE candidate using the pinned installed ALE checkout,
cached published data (`ahc027.zip` and `problem_ids_lite.txt`), and Docker:

```bash
python scripts/validate_ale_evaluator.py /path/to/ALE-Bench /path/to/data \
  scripts/fixtures/ahc027_dfs.cpp
```

The script verifies the clean source commit and dataset digest and reports the
judge image ID, candidate digest, seed-set digest, and official score. The DFS
fixture is derived from the earlier model-generated DFS candidate and is not
a model-quality baseline. See the
[validation record](../validation/issue14-evaluators.md) for exact provenance.
