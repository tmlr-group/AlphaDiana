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
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-4B
export PODMAN_MMMU_PRO_MAX_TOKENS=8192
export PODMAN_MMMU_PRO_ENABLE_THINKING=1
export PODMAN_MMMU_PRO_VLM_IMAGE_URL=https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen3.5/demo/RealWorld/RealWorld-04.png

export HF_HOME=<hf-cache-dir>
export HF_DATASETS_CACHE=<hf-datasets-cache-dir>
export PODMAN_MMMU_RUN_PREFIX=podman_mmmu_pro_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_mmmu_pro_readiness.sh all
```

The preflight must pass before `pilot`. It runs from a Podman container and
checks `/v1/models`, a remote `image_url` chat request, and a
`data:image/png;base64` chat request built from a real image using the same
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL_NAME` values that the
pilot configs use. Thinking mode remains on for the Phase 6 path, and the
default output cap is at least 8192 tokens.

`all` and `auto` are fail-fast wrappers for:

```text
validate -> preflight -> pilot -> audit
```

The pilot writes raw logs under `logs/`, task JSONs under `results/`, and
preflight/status/audit artifacts under `context/podman-mmmu-pro-readiness/`.
Latest passing evidence: run prefix
`podman_mmmu_pro_qwen35_thinking_20260516_144304` wrote all 9 task rows and
passed audit with `audit_failure_count=0`.

Local image defaults can be overridden:

- `ALPHADIANA_OPENCLAW_PODMAN_IMAGE`, default `localhost/alphadiana-openclaw:latest`
- `ALPHADIANA_ZEROCLAW_PODMAN_IMAGE`, default `localhost/zeroclaw-reasoning:0.6.9`
- OpenCode controller image is set in `opencode_mmmu_pro_pilot.yaml`

A full MMMU-Pro sweep and Podman global default promotion are out of scope for
this pilot.
