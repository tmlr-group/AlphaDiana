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
record's `score_status`. The full record, including the trajectory, stays in the
files listed under `evidence`.

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

## Known limitations

- The DeepSeek harness requests `reasoning_effort: high`. Endpoints that accept
  only other effort values, such as Qwen3.8 chat templates, reject the request.
- The native harness timeout covers queueing at the model endpoint. On a busy
  endpoint a long reasoning request can time out before any answer is produced.
