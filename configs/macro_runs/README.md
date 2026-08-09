# Macro runs

This directory contains the release matrix for end-to-end benchmark runs. All
ordinary configs target Qwen3.5-27B, default to one sample and one worker, and
follow `<benchmark>_<harness>_qwen35_27b.yaml`.

| Benchmark | DirectLLM | OpenClaw | OpenCode | ZeroClaw |
| --- | :---: | :---: | :---: | :---: |
| AIME 2026 | ✓ | ✓ | ✓ | ✓ |
| GPQA-Diamond | ✓ | ✓ | ✓ | ✓ |
| HLE | ✓ | ✓ | ✓ | ✓ |
| IMO AnswerBench | ✓ | ✓ | ✓ | ✓ |
| MMMU-Pro | ✓ | ✓ | ✓ | ✓ |
| SWE-bench Verified | — | ✓ | ✓ | ✓ |
| Terminal-Bench 2 | — | ✓ | ✓ | ✓ |

DirectLLM is omitted for SWE-bench and Terminal-Bench because those benchmarks
require an interactive coding/terminal harness.

## Quick smoke

From the repository root:

```bash
source scripts/activate.sh
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=sk-EMPTY

alphadiana validate configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o benchmark.config.max_tasks=1
alphadiana run configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o run_id=smoke_macro_aime_directllm \
  -o benchmark.config.max_tasks=1 --redo-all
```

For OpenClaw, also export a strong `OPENCLAW_GATEWAY_TOKEN`. For OpenCode,
ensure its controller image is present on the execution host. OpenClaw and
ZeroClaw require their runtime images and a reachable ROCK service.

MMMU-Pro requires a vision-capable model endpoint. Terminal-Bench 2 additionally
requires `TERMINAL_BENCH2_DIR` and the `TB2_*_RUNTIME_IMAGE` variables. SWE-bench
Verified requires its task images and a working Docker/Podman-compatible
container runtime. Validate these configs on the actual execution machine.

## Batch launch

After the one-task cells pass, list the desired configs explicitly:

```bash
alphadiana batch \
  configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  configs/macro_runs/aime2026_openclaw_qwen35_27b.yaml \
  configs/macro_runs/aime2026_opencode_qwen35_27b.yaml \
  configs/macro_runs/aime2026_zeroclaw_qwen35_27b.yaml
```

## Campaign manifest exception

`swe_bench_verified_sweagent_qwen35_27b_campaign.yaml` is a campaign manifest,
not an `ExperimentConfig`. Use it only with:

```bash
python -m alphadiana.benchmark_rollout_cli summary \
  --manifest configs/macro_runs/swe_bench_verified_sweagent_qwen35_27b_campaign.yaml
```

Do not include that `_campaign.yaml` file in an `alphadiana validate` loop.
