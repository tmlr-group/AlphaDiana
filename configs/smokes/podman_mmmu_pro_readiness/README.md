# Podman MMMU-Pro Multimodal Readiness Smoke Matrix

This directory contains the Phase 6 opt-in Podman pilot matrix:
OpenClaw, ZeroClaw, and OpenCode on three deterministic MMMU-Pro `vision`
rows.

Selected raw dataset indices:

- `0` -> `mmmu_pro_test_History_1`
- `1` -> `mmmu_pro_test_Art_113`
- `2` -> `mmmu_pro_validation_Design_19`

Run from the repository root:

```bash
export OPENAI_BASE_URL=http://localhost:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=<real-vlm-model>

export HF_HOME=<hf-cache-dir>
export HF_DATASETS_CACHE=<hf-datasets-cache-dir>
export PODMAN_MMMU_RUN_PREFIX=podman_mmmu_pro_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_mmmu_pro_readiness.sh validate
bash scripts/run_podman_mmmu_pro_readiness.sh preflight
bash scripts/run_podman_mmmu_pro_readiness.sh pilot
bash scripts/run_podman_mmmu_pro_readiness.sh audit
```

The preflight must pass before `pilot`. It runs from a Podman container and
checks both `/v1/models` and one tiny OpenAI-compatible image
`chat/completions` request using the same `OPENAI_BASE_URL`,
`OPENAI_API_KEY`, and `OPENAI_MODEL_NAME` values that the pilot configs use.

The pilot writes raw logs under `logs/`, task JSONs under `results/`, and
preflight/status/audit artifacts under `context/podman-mmmu-pro-readiness/`.

Local image defaults can be overridden:

- `ALPHADIANA_OPENCLAW_PODMAN_IMAGE`, default `localhost/alphadiana-openclaw:latest`
- `ALPHADIANA_ZEROCLAW_PODMAN_IMAGE`, default `localhost/zeroclaw-reasoning:0.6.9`
- OpenCode controller image is set in `opencode_mmmu_pro_pilot.yaml`

A full MMMU-Pro sweep and Podman global default promotion are out of scope for
this pilot.
