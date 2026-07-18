# Podman Scale-Readiness Smoke Matrix

This directory contains the Phase 5 standard-reasoning Podman pilot matrix:
OpenClaw, ZeroClaw, and OpenCode across AIME, GPQA-Diamond, HLE, and
IMO-AnswerBench with three tasks per cell.

Run from the repository root:

```bash
export OPENAI_BASE_URL=http://localhost:8011/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-4B

export PODMAN_SCALE_RUN_PREFIX=podman_scale_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_scale_readiness.sh validate
bash scripts/run_podman_scale_readiness.sh pilot
bash scripts/run_podman_scale_readiness.sh audit
```

The current local-provider configs use Podman host networking and expect these
local images unless overridden:

- `localhost/alphadiana-openclaw:latest`
- `localhost/zeroclaw-reasoning:0.6.9`
- `localhost/alphadiana-opencode-podman:latest`

The pilot writes raw logs under `logs/`, task JSONs under `results/`, and audit
artifacts under `context/podman-scale-readiness/`.

For the operator runbook and support boundary, see
`docs/benchmarks/podman.md`. For the latest evidence, see
`context/podman-scale-readiness/README.md` when that reviewer archive is present
in the checkout.
