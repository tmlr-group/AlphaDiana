# Runbook — MMMU-Pro (vision) full run on local Qwen3.5-27B + DirectLLM

Self-contained operator runbook for completing the **MMMU-Pro vision**
slice from `MMMU/MMMU_Pro` against a local `Qwen/Qwen3.5-27B` vLLM
endpoint via the DirectLLM agent.

> This is the "I want to run the benchmark" runbook. It deliberately
> duplicates content from the AIME / GPQA / HLE runbooks so each can be
> followed top-to-bottom without cross-references. For the broader
> MMMU-Pro background (other agents, Podman multimodal readiness,
> OpenRouter VL pilots), see [`docs/benchmarks/mmmu-pro.md`](mmmu-pro.md).

Wall-clock estimate (single-pass `num_samples=1`, `max_concurrent=10`):
**~12–30 h** at 128K output budget, **plus** a 2–10 minute eager-preload
phase at startup where the loader converts every dataset image into PNG
bytes before returning the task list. Plan a `tmux` session and don't
classify the run as stuck until the preload phase clears.

This path is **infrastructure-light**: DirectLLM only needs HTTP access
to the vLLM endpoint. No Docker, no Podman, no ROCK sandbox.

**Important model note**: `Qwen/Qwen3.5-27B` is text-only, so MMMU-Pro
vision rows will score 0 unless you swap in a vision-capable model
(VLM). The harness path is fully validated end-to-end; the runbook
documents the swap-in points in §2 and §6.

---

## 0. What "full run" means here

- Dataset: `MMMU/MMMU_Pro` on Hugging Face, `data_config="vision"` slice,
  `test` split (~1730 vision-backed multiple-choice questions).
- Agent: `direct_llm` (single OpenAI-compatible chat-completions call per
  sample; no tool use, no sandbox). DirectLLM relays image attachments
  to the provider as `image_url` content blocks; the VLM consumes them
  natively.
- Sampling: `num_samples=1` per question (pass@1). Raise to ≥ 4 if you
  want a pass@K signal.
- Scoring: `exact_match` on the boxed option letter.

---

## 1. Host prerequisites (one-time)

### 1.1 Choose a work root (everything else hangs off this)

Pick **one** filesystem path that has ≥ 100 GB free and is **not** under
`/home`. The runbook never assumes a specific mount name like `/data*` or
`/scratch` — it only needs a path you can write to that won't fill up the
home partition. Common shapes:

| Host shape | Sensible `WORK_ROOT` |
| --- | --- |
| Dedicated data disk(s) under `/data0`, `/data1`, … | `/path/to/$USER/alphadiana` |
| Single NVMe under `/mnt/nvme` or `/srv/scratch` | `/mnt/nvme/$USER/alphadiana` |
| `/opt` is a separate large mount | `/opt/$USER/alphadiana` |
| Just one big root partition (no dedicated data disk) | `/var/lib/alphadiana` (root once) |

```bash
# Pick the path that matches your host
export WORK_ROOT=/path/to/large/disk/$USER/alphadiana
mkdir -p "$WORK_ROOT"/{results,logs,hf}
df -h "$WORK_ROOT" | tail -1     # must show ≥ 100 GB free
```

The vision dataset cache is ~10 GB once images are materialized; the
full-sweep output for the vision slice plus Int16 logprob sidecars adds
40–80 GB. Image attachments are also persisted into the per-task
artifacts directory.

### 1.2 Python toolchain

The AlphaDiana orchestrator needs Python 3.10 or 3.11 and a conda install
(its `scripts/activate.sh` calls `conda activate`).

```bash
python --version             # 3.10.x or 3.11.x is preferred; 3.12+ is untested
conda --version              # any recent Miniconda/Anaconda
```

If you don't already have Miniconda, install it first:
https://docs.conda.io/projects/miniconda/

### 1.3 Hugging Face access

`MMMU/MMMU_Pro` is **public** — no token required. The `vision` config
is large (image bytes), so the first dataset load can take 5–20 minutes
depending on bandwidth. Set `HF_TOKEN` to avoid anonymous rate limits.

```bash
huggingface-cli login          # optional but recommended
huggingface-cli whoami         # confirm logged in
```

---

## 2. vLLM endpoint

Pick the port + the GPUs you want to dedicate to the model. The runbook
uses `8011` and the host's first two GPUs by example; adjust to whatever
your hardware allows. The only **hard** constraints are
`--max-model-len ≥ 200000` (the config asks for `max_tokens=131072` =
128K output, leaving ~69K for the prompt + system message + thinking) and
the two generation-config flags below.

The checked-in config targets `Qwen/Qwen3.5-27B`, which is text-only.
For meaningful vision scoring, swap in a VLM at this step **and** in §6.
Common choices on the same machine class:

| Model | Text-only? | Notes |
| --- | --- | --- |
| `Qwen/Qwen3.5-27B` | Yes | Harness validates end-to-end; image rows score 0. |
| `Qwen/Qwen3.5-VL-27B` | No (VLM) | Drop-in if you have weights and a compatible vLLM build. |
| `Qwen/Qwen3.5-4B` (VL variant) | No (VLM) | The MMMU-Pro Podman readiness matrix uses this; fits on a single 24 GB GPU. |

```bash
# Operator choices — adjust to your hardware/preference
export VLLM_PORT=8011
export VLLM_GPUS=0,1                         # comma-separated CUDA device IDs
export VLLM_TENSOR_PARALLEL=2                # must equal number of GPUs in $VLLM_GPUS
export VLLM_GPU_MEM_UTIL=0.9                 # raise on dedicated nodes, lower if you share

# Pick the model id you want to serve. The runbook examples below all
# assume Qwen/Qwen3.5-27B (text-only).
export VLLM_MODEL=Qwen/Qwen3.5-27B
mkdir -p "$WORK_ROOT/logs"

CUDA_VISIBLE_DEVICES="$VLLM_GPUS" \
python -m vllm.entrypoints.openai.api_server \
  --model "$VLLM_MODEL" \
  --host 0.0.0.0 --port "$VLLM_PORT" \
  --trust-remote-code \
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL" \
  --gpu-memory-utilization "$VLLM_GPU_MEM_UTIL" \
  --max-model-len 200000 \
  --generation-config vllm \
  --override-generation-config '{"presence_penalty": 1.5}' \
  --reasoning-parser qwen3 \
  --served-model-name "$VLLM_MODEL" \
  2>&1 | tee "$WORK_ROOT/logs/vllm_$(basename "$VLLM_MODEL").log"
```

Sizing notes:

- A single 24 GB GPU is **not** enough for a 27B model at 200K context.
  Expect at least 2 × 80 GB (A100/H100) or 4 × 48 GB (RTX 6000 Ada / L40)
  for tensor-parallel sharding.
- If you swap to a smaller VLM (e.g. `Qwen/Qwen3.5-4B-VL`), drop
  `VLLM_TENSOR_PARALLEL=1` and lower `--max-model-len` accordingly
  (8K–32K is typical for the 4B class).

Confirm the endpoint is reachable:

```bash
curl -sS --max-time 30 "http://127.0.0.1:${VLLM_PORT}/v1/models" \
  | python -m json.tool | head -20
# Expect data[].id == "$VLLM_MODEL" and max_model_len ≥ 131072.
```

Why these flags matter:

- `--max-model-len 200000` — leaves room for `max_tokens=131072` plus the
  ~30K prompt+image+thinking trace headroom (image rows have larger
  prompt sizes than text-only).
- `--override-generation-config '{"presence_penalty": 1.5}'` — suppresses
  Qwen3.5's repetition loops on long-thinking traces.
- `--generation-config vllm` — ignore the model's `generation_config.json`
  which would otherwise quietly enable non-greedy defaults
  (`temperature=0.6`, `top_p=0.95`); the MMMU-Pro config pins
  `temperature=0.0` for greedy pass@1.
- `--reasoning-parser qwen3` — emits `delta.reasoning` events. DirectLLM
  consumes them transparently. Drop this flag for non-Qwen VLMs.

---

## 3. AlphaDiana repo + Python env

```bash
git clone <your-fork-or-origin> alphadiana
cd alphadiana
git checkout main
```

`scripts/activate.sh` expects a **conda** install. Bootstrap the
project environment:

```bash
bash scripts/quickstart.sh        # creates the conda env from project files
source scripts/activate.sh        # activates it + sources .env
python -c "import alphadiana; print(alphadiana.__file__)"   # sanity
```

Confirm MMMU-Pro is registered:

```bash
python -m alphadiana.cli list-benchmarks | grep -E '^\s+- mmmu_pro$'
# expect: - mmmu_pro
```

---

## 4. Export the runtime environment

```bash
# Pin the vLLM provider (uses the port you picked in §2)
export QWEN_VLLM_API_BASE="http://127.0.0.1:${VLLM_PORT}/v1"
export QWEN_VLLM_API_KEY=EMPTY

# Hugging Face cache lives under $WORK_ROOT, not /home (~/.cache fills fast)
export HF_HOME="$WORK_ROOT/hf"
export HF_DATASETS_CACHE="$WORK_ROOT/hf/datasets"
export HUGGINGFACE_HUB_CACHE="$WORK_ROOT/hf/hub"
mkdir -p "$HF_HOME" "$HF_DATASETS_CACHE" "$HUGGINGFACE_HUB_CACHE"

# Run id and output root
export MMMU_FULL_OUTPUT_ROOT="$WORK_ROOT/results"
export MMMU_RUN_ID=mmmu_pro_full_$(date +%Y%m%d_%H%M%S)
mkdir -p "$MMMU_FULL_OUTPUT_ROOT"
```

The checked-in YAML refers to the provider as `http://127.0.0.1:8011/v1`
directly in `agent.config.api_base`; the §6/§7 launch commands override
that to whatever your `QWEN_VLLM_API_BASE` is.

---

## 5. Validate the config + dataset

```bash
# Static validation (no live vLLM call, no dataset download)
python -m alphadiana.cli validate \
  configs/full_runs/mmmu_pro_directllm_qwen35_27b_logprobs.yaml
# expect: Config is valid.

# Live preflight — vision config load + 1 vLLM round-trip.
# WARNING: the vision config eagerly downloads every image; this can
# take 10+ minutes on a fresh cache.
python -c "
from datasets import load_dataset
ds = load_dataset('MMMU/MMMU_Pro', 'vision', split='test')
print(f'MMMU-Pro vision task count: {len(ds)}')
print(f'First row fields: {list(ds[0].keys())}')
"

curl -sS --max-time 30 "$QWEN_VLLM_API_BASE/models" >/dev/null \
  && echo 'vllm reachable' || echo 'VLLM UNREACHABLE'
```

Both must pass before kicking off the full sweep. The verified vision
config directory must contain real data files, not just `README.md` and
empty subdirectories — that's a well-known MMMU-Pro snapshot pitfall
called out in [`docs/benchmarks/mmmu-pro.md`](mmmu-pro.md).

---

## 6. Smoke before the full run (use the fast text-only path)

Always smoke first. This catches config drift, HF dataset issues, and
endpoint misconfiguration without burning a multi-day run.

**Important**: the `vision` slice eagerly preloads every image into PNG
bytes before returning the task list. On a cold HF cache this preload
can exceed 15 minutes, which is too long for a useful smoke. Smoke
against the text-only `standard (4 options)` slice first — it proves
the harness path, scorer, and provider transport are healthy in 1–3
minutes:

```bash
python -m alphadiana.cli run \
  configs/full_runs/mmmu_pro_directllm_qwen35_27b_logprobs.yaml \
  --redo-all \
  -o run_id=${MMMU_RUN_ID}_smoke \
  -o output_dir=$MMMU_FULL_OUTPUT_ROOT \
  -o agent.config.api_base=$QWEN_VLLM_API_BASE \
  -o benchmark.config.data_config='standard (4 options)' \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1 \
  -o max_concurrent=1 \
  -o agent.config.max_tokens=4000 \
  -o agent.config.capture_logprobs=false \
  2>&1 | tee "$WORK_ROOT/logs/${MMMU_RUN_ID}_smoke.log"
```

Once the text-only smoke passes, do a longer **vision** preflight (no
`--redo-all`) so the dataset cache is materialized before the full run:

```bash
# Pre-warm the vision dataset cache (one-time, can take 10–30 min on
# cold HF cache, ~2 min if already cached locally)
python -c "
from datasets import load_dataset
ds = load_dataset('MMMU/MMMU_Pro', 'vision', split='test')
print(f'preloaded {len(ds)} vision rows')
"
```

If you swapped in a VLM at §2, add the model overrides here too:

```bash
  -o agent.config.model=$VLLM_MODEL \
```

Expected output (final lines):
```
Run completed: mmmu_pro_full_...._smoke
  Tasks: 1/1 completed
```

The smoke score itself can be 0 — `Qwen/Qwen3.5-27B` is text-only and
will fail on the image row. We're checking that:

- The vision loader completes the preload and returns the task list.
- DirectLLM sends one chat-completions request with one `image_url`
  content block.
- The harness writes `tasks/mmmu_pro_*.json` with
  `metadata.num_attachments=1`.

Expected artifacts:
- `$MMMU_FULL_OUTPUT_ROOT/${MMMU_RUN_ID}_smoke/.../tasks/mmmu_pro_*.json`
- the JSON's `score_status` is `valid_scored`
- `metadata.num_attachments=1` and an `artifacts/<task_id>/workspace/`
  directory with the image bytes preserved.

---

## 7. Launch the full sweep

Detach from the smoke. Then run the full vision sweep in `tmux`:

```bash
tmux new -d -s mmmu_pro "
  set -eux
  python -m alphadiana.cli run \
    configs/full_runs/mmmu_pro_directllm_qwen35_27b_logprobs.yaml \
    --redo-all \
    -o run_id=${MMMU_RUN_ID} \
    -o output_dir=$MMMU_FULL_OUTPUT_ROOT \
    -o agent.config.api_base=$QWEN_VLLM_API_BASE \
    2>&1 | tee $WORK_ROOT/logs/${MMMU_RUN_ID}.log
"

tmux attach -t mmmu_pro            # Ctrl-B D to detach
```

The checked-in defaults baked into the YAML:
- `num_samples=1` (pass@1)
- `max_concurrent=10`
- `temperature=0.0`, `top_p=0.95`, `max_tokens=131072`
- `capture_logprobs=true`, `top_logprobs=20`, Int16 sidecars
- `enable_thinking=true`
- `data_config="vision"`

Knobs you may want to override at the CLI:

| Knob | Default | When to change |
| --- | --- | --- |
| `num_samples` | 1 | Raise to `4` or `8` for a pass@K signal. |
| `max_concurrent` | 10 | Lower if your vLLM endpoint can't sustain 10 long-thinking image requests; raise only if you've benchmarked it. |
| `agent.config.max_tokens` | 131072 | Lower if you have a smaller context window or want to cap wall time. |
| `agent.config.capture_logprobs` | true | Set `false` if you don't need logprobs (saves ~3× output size and ~20% wall time). |
| `benchmark.config.data_config` | `vision` | Use `"standard (4 options)"` or `"standard (10 options)"` for the text-only slices (faster, no image preload). |
| `benchmark.config.max_tasks` | unset | Set to e.g. `200` to cap the run at 200 questions for a budget sweep. |

---

## 8. Monitor the run

```bash
# Live tail
tail -f $WORK_ROOT/logs/${MMMU_RUN_ID}.log

# Per-task progress: count completed JSON rows
find "$MMMU_FULL_OUTPUT_ROOT/${MMMU_RUN_ID}" -name "mmmu_pro_*.json" 2>/dev/null | wc -l
# climbs toward the vision row count from §5

# Periodic vLLM health probe (don't rely on GPU-util — it's misleading on
# long-thinking workloads)
while sleep 300; do
  curl -sS --max-time 30 "$QWEN_VLLM_API_BASE/models" >/dev/null \
    && echo "$(date -Is) vllm ok" \
    || echo "$(date -Is) vllm WEDGED"
done
```

If vLLM wedges (probe times out repeatedly), restart it with the same
command from §2. Tasks already partially generated will be retried on
re-run with the same `run_id` (without `--redo-all`).

**Eager preload phase**: on first launch the loader spends 2–10 minutes
converting every dataset image to PNG bytes before the dispatcher
processes the first task. The Python process stays at high CPU and
~1.6 GB RSS during this phase with no task JSONs written yet. **Do not
classify the run as stalled until preload clears** — this is documented
in [`docs/benchmarks/mmmu-pro.md`](mmmu-pro.md).

---

## 9. Result interpretation

After the run completes:

```bash
# Per-task records
ls "$MMMU_FULL_OUTPUT_ROOT/${MMMU_RUN_ID}"/*/tasks/ | head

# Top-line metrics from the harness summary
grep -E "accuracy|pass@1|avg@" $WORK_ROOT/logs/${MMMU_RUN_ID}.log | tail -5

# Accuracy by hand (excluding provider errors)
python - <<'PY'
import json, os, glob
root = os.environ["MMMU_FULL_OUTPUT_ROOT"]
run_id = os.environ["MMMU_RUN_ID"]
files = sorted(glob.glob(f"{root}/{run_id}/*/tasks/mmmu_pro_*.json"))
rows = [json.load(open(f))[0] for f in files]
scored = [r for r in rows if r.get("score_status") == "valid_scored"]
correct = sum(1 for r in scored if r.get("score") == 1.0)
print(f"scored rows: {len(scored)}/{len(files)}")
print(f"accuracy (scored only): {correct}/{len(scored)} ({correct/max(len(scored),1):.2%})")
PY
```

For vision rows on a text-only model, expect score=0 with
`score_status=valid_scored`. Swap in a VLM (see §2) for a meaningful
signal.

---

## 10. Operational risks and how to defuse them

| Risk | Symptom | Mitigation |
| --- | --- | --- |
| Eager preload misclassified as stall | run alive at high CPU but no task JSONs after 5 minutes | Wait up to 10 minutes for the vision preload. Only investigate as stalled if no task JSONs appear after 20 minutes. |
| vLLM long-thinking deadlock | every request times out, `$WORK_ROOT/logs/vllm_*.log` mtime stops advancing | Restart vLLM; in-flight tasks retry. Monitor `/v1/models` (not GPU-util). |
| `$WORK_ROOT` fills up | task JSON writes start to fail mid-run | §1.1 — pick a mount with ≥ 100 GB free, recheck with `df -h "$WORK_ROOT"` every 6 hours. |
| Empty `vision/` directory in a custom snapshot | `DataFilesNotFoundError` at load time | Don't override `benchmark.config.dataset` to a snapshot path unless the `vision` subdir actually has data files. |
| Stale `generation_config.json` defaults | non-greedy sampling, score drift across replicas | §2 — make sure `--generation-config vllm` is in the vllm launch flags. |
| Image rows score 0 on a text-only model | many score=0 rows, `metadata.num_attachments=1` | Expected for `Qwen/Qwen3.5-27B`. Use a VLM and override `agent.config.model`, `--served-model-name`, and the launch flags accordingly. |
| Provider 429 / token budget exhaustion mid-run | repeated 429s, dispatcher backs off | Pause, wait for the provider's documented recovery window, then re-run the same `run_id` **without** `--redo-all`. Completed rows are skipped. |

---

## 11. Resuming a partial run

DirectLLM writes per-task JSON rows as it goes. Re-running the same
`run_id` **without** `--redo-all` resumes — completed tasks are skipped.
The eager preload re-runs each time, which is unavoidable as long as
the loader rebuilds the task list on startup:

```bash
python -m alphadiana.cli run \
  configs/full_runs/mmmu_pro_directllm_qwen35_27b_logprobs.yaml \
  -o run_id=${MMMU_RUN_ID} \
  -o output_dir=$MMMU_FULL_OUTPUT_ROOT \
  -o agent.config.api_base=$QWEN_VLLM_API_BASE
```

To restart from scratch, pass `--redo-all` (the §7 snippet does; remove
it for resume mode).

---

## 12. Output map

```
$MMMU_FULL_OUTPUT_ROOT/
└── ${MMMU_RUN_ID}/
    └── full_mmmu_pro_directllm_qwen35_27b_logprobs/
        ├── tasks/
        │   ├── mmmu_pro_test_History_1.json
        │   ├── mmmu_pro_test_Art_113.json
        │   └── ... (one file per scored row)
        ├── artifacts/
        │   └── mmmu_pro_<id>/workspace/
        │       └── attachments/image_1.png   # preserved image bytes
        └── logprobs/
            └── mmmu_pro_<id>.jsonl

$WORK_ROOT/logs/${MMMU_RUN_ID}.log    # outer harness log
$WORK_ROOT/logs/vllm_*.log            # vllm server log
```

---

## 13. Scope of this runbook

- MMMU-Pro `vision` slice only (`MMMU/MMMU_Pro`, `data_config=vision`,
  `test` split).
- DirectLLM agent (`direct_llm`) only — no OpenClaw / OpenCode /
  ZeroClaw.
- Local Qwen3.5-27B (text-only, served via vLLM on a 200K context;
  `max_tokens=131072`). Swap to a VLM for meaningful vision scoring.
- Does not cover the Podman multimodal readiness matrix (covered in
  [`docs/benchmarks/mmmu-pro.md`](mmmu-pro.md)), the `standard`
  text-only slices (use `data_config="standard (4 options)"`),
  OpenRouter VL pilots, ZeroClaw sandbox path, or any non-loopback
  endpoint.
- For AIME 2026, GPQA-Diamond, or HLE, use their dedicated runbooks:
  - [`runbook-aime-2026-full-directllm-qwen35-27b.md`](runbook-aime-2026-full-directllm-qwen35-27b.md)
  - [`runbook-gpqa-diamond-full-directllm-qwen35-27b.md`](runbook-gpqa-diamond-full-directllm-qwen35-27b.md)
  - [`runbook-hle-full-directllm-qwen35-27b.md`](runbook-hle-full-directllm-qwen35-27b.md)
