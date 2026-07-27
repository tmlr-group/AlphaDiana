# Validation record

Date: 2026-07-27.

## Passed

- `python -m alphadiana.cli validate` passed for all three configs:
  - `configs/openclaw.yaml`
  - `configs/opencode.yaml`
  - `configs/zeroclaw.yaml`
- `ExperimentConfig.from_yaml` parse inspected for all three: num_samples=64,
  max_concurrent=3, temperature=0.6, top_p=0.95, max_tokens=131072,
  enable_thinking=true, capture_logprobs=true/top_logprobs=20, task_retries=2,
  strict_report=true; `presence_penalty` confirmed absent from every agent
  config.
- `AIMEBenchmark.load_tasks` executed against the local dataset cache with the
  bundle's benchmark config: 30 tasks loaded from `MathArena/aime_2026`
  (train), 1920 work items per harness at num_samples=64.
- Smoke override string set (`run_id`, `benchmark.config.max_tasks=1`,
  `num_samples=2`, `max_concurrent=2`) round-tripped through
  `parse_override`/`deep_merge` with correct int coercion.
- Runtime parameter plumbing was inspected against the current AlphaDiana
  classes: each harness consumes temperature/top_p/max_tokens/thinking from
  agent config (openclaw agent.py:1060-1063; opencode agent.py:751-769;
  zeroclaw agent.py:481-505). `presence_penalty` is consumed by none — it is
  only observed by the logprob proxy summarizer — hence its removal from the
  OpenCode config and the server-side note in RUNBOOK.md.
- ROCK instance naming inspected (`rock_ports.py`): all three runs share one
  checkout-derived instance; parallel launch does not collide.
- `bash -n` passed for all shell scripts; `verify_outputs.py` compiles and its
  CLI parser loads.
- All YAML files parse; the bundle passed the common-secret-pattern scan.

## Deliberately not performed

- No live vLLM endpoint, ROCK service, or Docker daemon was exercised while
  building the bundle; the provider/ROCK probes run in `scripts/preflight.sh`
  on the evaluation host.
- No benchmark task was executed and no sandbox image was built.
- No credentials were used; the private HF repository was not queried.
- The zip contains configuration and operational tooling, not results.

Run `bash scripts/preflight.sh` and the three `--smoke` runs on the actual
evaluation host before starting `run_all.sh`.
