# Validation record

Date: 2026-07-24.

## Passed

- `python -m alphadiana.cli validate` passed for:
  - `configs/openclaw.yaml`
  - `configs/opencode.yaml`
  - `configs/zeroclaw.yaml`
- All three YAML files parse successfully and match the shared contract for
  dataset, split, model, temperature, top-p, maximum tokens, sample count,
  maximum concurrency, HF repository, and HF folder pattern.
- DirectLLM's run script contains the official SWE-agent default config and the
  requested worker/sampling/output-token overrides.
- Runtime parameter plumbing was inspected against the current AlphaDiana
  classes:
  - OpenClaw request payload: temperature, top-p, max tokens, streaming, and
    thinking are present.
  - OpenCode provider options: temperature, top-p, max tokens, streaming, and
    thinking are present.
  - ZeroClaw non-logprob provider proxy: temperature, top-p, max tokens, and
    thinking overrides are present while logprob injection remains disabled.
- `bash -n` passed for all shell scripts.
- `scripts/verify_outputs.py` compiles and its CLI parser loads.
- The generated bundle passed the configured common-secret-pattern scan.

## Deliberately not performed

- No live vLLM endpoint was available as part of artifact construction, so the
  model request probe was not executed.
- Docker task images were not built and no benchmark task was run.
- No credentials were available or requested, so the private HF repository was
  not queried or modified.
- The final zip contains configuration and operational tooling, not benchmark
  results.

Run `bash scripts/preflight.sh` on the actual evaluation host before starting a
smoke or full run.
