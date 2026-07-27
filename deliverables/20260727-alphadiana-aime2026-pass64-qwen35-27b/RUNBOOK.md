# Runbook: AIME 2026 pass@64 × 3 Harnesses × Qwen3.5-27B

Formalized: 2026-07-27.

## 1. Locked experiment contract

| Parameter | Value | Enforcement point |
| --- | --- | --- |
| Dataset | `MathArena/aime_2026`, `train`, 30 tasks | AlphaDiana benchmark config |
| Model | `Qwen/Qwen3.5-27B` | vLLM and each harness |
| Sample K | `64` (pass@64) | AlphaDiana `num_samples: 64` |
| Temperature | `0.6` | AlphaDiana agent config (Qwen thinking-mode recommendation; 0.0 would collapse all 64 samples into one) |
| Top-p | `0.95` | AlphaDiana agent config |
| Maximum model length | `262144` | vLLM `--max-model-len` |
| Maximum output tokens | `131072` | AlphaDiana agent config |
| Thinking | enabled | AlphaDiana agent config (`enable_thinking: true`) |
| Streaming | enabled where the harness exposes it | OpenClaw `stream`; OpenCode `streaming`; native ZeroClaw CLI transport |
| Logprobs | top-20 sidecars | `capture_logprobs: true`, `top_logprobs: 20` |
| Concurrency | 3 per harness × 3 harnesses in parallel ≈ 9 | `max_concurrent: 3` + `scripts/run_all.sh` |
| Scorer | `numeric`, tolerance `1e-6` | AlphaDiana scorer config |
| HF repo | `T-MARS/alphadiana-benchmark-results` (private dataset) | upload script |
| HF folder | `YYYYMMDD-aime-2026-<agent>-qwen35-27b-vNN` | upload script |

There is no DirectLLM row in this campaign. All three runs use the proven
`configs/micro_runs/Tool/aime2026_<agent>_qwen35_27b.yaml` cells as their base;
each bundle config documents exactly which fields differ (run_id, temperature,
num_samples, max_concurrent, metadata).

**presence_penalty is deliberately not set.** No AlphaDiana harness injects
`presence_penalty` from agent config (the key is only *observed* by the logprob
proxy's request summarizer), so the `presence_penalty: 1.5` in the source
OpenCode cell was a no-op and has been removed rather than left in place
looking effective. If a non-zero presence penalty is ever wanted, it must be
set server-side on vLLM (`--override-generation-config`), which would then
apply to every request from every harness sharing that server.

## 2. Required software and layout

- An AlphaDiana checkout with its Python environment installed. The scripts
  auto-detect `ALPHADIANA_ROOT` when this bundle lives in `deliverables/`
  inside the checkout; export it explicitly otherwise.
- Docker usable by the current user (OpenCode controller + ROCK sandboxes).
- ROCK services running for this checkout (OpenClaw gateway autodeploy and
  ZeroClaw sandboxes ride on them):

```bash
source scripts/rock_env.sh          # from the AlphaDiana checkout root
python -m alphadiana.cli env        # every ROCK row must be green
```

- Local images pulled/built beforehand: `tmlrgroup/alphadiana:v1` (OpenClaw),
  `alphadiana/tb2-opencode-controller:latest` (OpenCode),
  `zeroclaw-reasoning:0.6.9` (ZeroClaw).
- RAM headroom: 6 concurrent ROCK sandboxes at 4g (`rock_memory`) plus 3
  OpenCode controllers — plan for ≥32 GB free.
- Disk headroom: 5760 samples with top-20 logprob sidecars under long thinking
  chains; plan for ≥200 GB free under `results/`.

## 3. Start the shared vLLM server

```bash
CUDA_VISIBLE_DEVICES=<GPU_IDS> vllm serve Qwen/Qwen3.5-27B \
  --host 0.0.0.0 \
  --port 8011 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --enable-prefix-caching \
  --tensor-parallel-size <TP> \
  --gpu-memory-utilization 0.90 \
  --max-model-len 262144 \
  --served-model-name Qwen/Qwen3.5-27B qwen/qwen3.5-27b
```

Both served-model aliases are required: OpenClaw/ZeroClaw request
`Qwen/Qwen3.5-27B`, the OpenCode controller requests `qwen/qwen3.5-27b`.
Preflight probes both ids and fails if either is missing.

Do not add a `presence_penalty` generation override unless you explicitly
decide to change the locked contract — see section 1.

For ZeroClaw specifically, the micro-runs documentation recommends routing
thinking-mode models through
`alphadiana/harness/proxies/tool_filter_proxy.py --rename-reasoning` so the
chain-of-thought stays visible in `normalized_trace.json`. If you adopt it,
point `OPENAI_BASE_URL` for the ZeroClaw shell at the proxy instead of vLLM;
the sampling contract is unchanged because the proxy forwards it.

## 4. Export runtime settings

```bash
cd /path/to/AlphaDiana
source scripts/rock_env.sh                       # exports ROCK_BASE_URL / ROCK_PROXY_URL

export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=local-key                  # any non-empty value for local vLLM
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export HF_REPO=T-MARS/alphadiana-benchmark-results
export RUN_VERSION=v01

# If the shared dataset cache applies on this host:
export HF_DATASETS_CACHE=/path/to/datasets
# If direct HF access is unavailable:
# export HF_ENDPOINT=https://hf-mirror.com
```

Run preflight from the bundle directory:

```bash
bash scripts/preflight.sh
```

## 5. One-task smoke tests

```bash
bash scripts/run.sh openclaw --smoke
bash scripts/run.sh opencode --smoke
bash scripts/run.sh zeroclaw --smoke
```

Each smoke runs 1 task × 2 samples at the full sampling contract. Verify:

```bash
python scripts/verify_outputs.py --agent openclaw --root "$ALPHADIANA_ROOT" --smoke
python scripts/verify_outputs.py --agent opencode --root "$ALPHADIANA_ROOT" --smoke
python scripts/verify_outputs.py --agent zeroclaw --root "$ALPHADIANA_ROOT" --smoke
```

The smoke verifier also checks that the 2 samples are not byte-identical —
identical samples mean temperature never reached the server and the full
pass@64 would be meaningless. Do not start full runs until all three smokes
pass this check.

## 6. Full runs — three harnesses in parallel

```bash
bash scripts/run_all.sh
```

This launches all three full runs concurrently, each with `max_concurrent: 3`
from its config (~9 concurrent provider requests total, per the campaign
contract). Logs stream to `$ALPHADIANA_ROOT/logs/<run_id>.log`.

Sizing: 30 tasks × 64 samples = 1920 work items per harness. Wall-clock is
roughly `1920 × avg_sample_minutes ÷ 3` per harness; at 10–20 min per thinking
sample that is multiple days. The smoke gives you the per-sample latency to
plug in. If the vLLM deployment has verified headroom, raise concurrency
without editing configs:

```bash
RUN_VERSION=v01 bash scripts/run.sh openclaw   # per-harness relaunch resumes...
python -m alphadiana.cli run <config> -o run_id=... -o max_concurrent=5   # ...or override explicitly
```

Reusing the same run ID resumes from AlphaDiana checkpoints; completed samples
are not redone. To rerun from scratch, bump `RUN_VERSION` — do not combine
`--redo-all` with an already-uploaded HF destination.

## 7. Monitor

```bash
watch -n 300 'for a in openclaw opencode zeroclaw; do
  id=full_aime2026_pass64_${a}_qwen35_27b_v01
  n=$(wc -l < "$ALPHADIANA_ROOT/results/$id.jsonl" 2>/dev/null || echo 0)
  printf "%-9s %s/1920\n" "$a" "$n"
done'

tail -f "$ALPHADIANA_ROOT"/logs/full_aime2026_pass64_*_v01.log
```

Investigate immediately: repeated provider 4xx/5xx, context-length errors,
ROCK sandbox startup failures, gateway timeouts, retry storms, or a
`results.jsonl` that stops growing while GPUs stay busy.

## 8. Validate finished outputs

```bash
for a in openclaw opencode zeroclaw; do
  python scripts/verify_outputs.py --agent "$a" --root "$ALPHADIANA_ROOT" --version "$RUN_VERSION"
done
```

The verifier requires 30 unique tasks × 64 samples with `sample_index` 0..63
each, a 1920-line aggregate JSONL, all records `valid_scored`, and non-trivial
sampling diversity. It prints the pass@64 headline number per harness.
`strict_report: true` in the configs additionally makes the runner itself exit
non-zero if any sample is missing or non-valid at the end of the run.

## 9. Upload to the private HF dataset repo

```bash
hf auth login && hf auth whoami

bash scripts/upload.sh openclaw "$RUN_VERSION"
bash scripts/upload.sh opencode "$RUN_VERSION"
bash scripts/upload.sh zeroclaw "$RUN_VERSION"
```

The destination leaf is exact:

```text
YYYYMMDD-aime-2026-<agent>-qwen35-27b-vNN
```

`upload.sh` re-runs `verify_outputs.py` before staging, refuses non-empty HF
destinations (bump `RUN_VERSION` instead of overwriting), and stamps an
`UPLOAD_METADATA.json` with the full sampling contract.

## 10. Known implementation notes

1. Configs derive from the paper §5 Tool-axis cells (clean baseline prompt, no
   memory nudge); each YAML header lists the exact fields changed. Runtime,
   ROCK, and gateway settings are untouched from the proven cells.
2. `max_actions_per_hour: 200` (ZeroClaw) is per sandbox, not per run, so
   pass@64 does not need it raised.
3. All three harnesses share one ROCK service instance (derived from the
   checkout path). Running the three in parallel is supported; the ownership
   preflight in `alphadiana.cli run` guards against a stale ROCK instance from
   another checkout.
4. `task_retries: 2` with `task_retry_on_recoverable_only: true` mirrors the
   proven cells: provider blips are retried, deterministic failures are not.
5. This campaign targets the Docker/ROCK runtime path of the proven cells, not
   Podman. Re-formalize before substituting engines.

## 11. External references

- Qwen3 sampling guidance (thinking mode: temperature 0.6, top-p 0.95):
  `https://huggingface.co/Qwen/Qwen3-235B-A22B#best-practices`
- MathArena AIME 2026 dataset:
  `https://huggingface.co/datasets/MathArena/aime_2026`
- Hugging Face CLI upload guide:
  `https://huggingface.co/docs/huggingface_hub/en/guides/cli`
