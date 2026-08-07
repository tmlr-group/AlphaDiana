# Macro Runs — Release Smoke Matrix

These eight configs are the conservative release entry points for comparing
the complete harness conditions on one model and benchmark. They cover AIME
2026 and GPQA-Diamond across DirectLLM, OpenCode, OpenClaw, and ZeroClaw.

All cells default to one sample and one worker. Scale `num_samples` and
`max_concurrent` only after the matching one-task smoke succeeds.

## Prerequisites

From the repository root:

```bash
source scripts/activate.sh
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export OPENAI_BASE_URL=http://HOST_REACHABLE_FROM_SANDBOX:8011/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

For OpenCode, build the controller image once:

```bash
docker build --network host \
  -f alphadiana/benchmarks/terminal_bench2/deploy/dockerfiles/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
```

For OpenClaw and ZeroClaw, pull/build their images and start ROCK:

```bash
docker pull tmlrgroup/alphadiana:v1
docker build --network host \
  -f alphadiana/harness/zeroclaw/deploy/Dockerfile \
  -t zeroclaw-reasoning:0.6.9 .

bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

`OPENAI_BASE_URL` must be reachable from the ROCK sandbox for OpenClaw and
ZeroClaw. A host-loopback URL usually is not. OpenCode and DirectLLM can use a
host-loopback endpoint; override `agent.config.api_base` for those cells when
needed.

## Validate the matrix

Validation checks local controller images, so run it on the machine that will
execute the experiments:

```bash
for config in configs/macro_runs/*.yaml; do
  python -m alphadiana.cli validate "$config"
done
```

## One-task smoke

Use a fresh run ID for every smoke:

```bash
python -m alphadiana.cli run \
  configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o run_id=smoke_macro_aime_directllm \
  -o benchmark.config.max_tasks=1 --redo-all
```

Replace `directllm` with `opencode`, `openclaw`, or `zeroclaw` to test the
remaining harnesses. GPQA uses the corresponding `gpqa_*.yaml` files.

## Batch launch

After all four one-task smokes pass:

```bash
python -m alphadiana.cli batch \
  configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  configs/macro_runs/aime2026_opencode_qwen35_27b.yaml \
  configs/macro_runs/aime2026_openclaw_qwen35_27b.yaml \
  configs/macro_runs/aime2026_zeroclaw_qwen35_27b.yaml
```
