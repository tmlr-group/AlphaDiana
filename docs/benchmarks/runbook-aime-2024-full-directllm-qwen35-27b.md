# Runbook — AIME 2024 full run on local Qwen3.5-27B + DirectLLM

Self-contained operator runbook for completing the **AIME 2024** benchmark
(30 problems from `HuggingFaceH4/aime_2024`) against a local
`Qwen/Qwen3.5-27B` vLLM endpoint via the DirectLLM agent.

> This is the "I want to run the benchmark" runbook. It deliberately
> duplicates content from the GPQA / HLE / MMMU-Pro runbooks so each can be
> followed top-to-bottom without cross-references. For the broader AIME
> background (other agents, AIME 2026, ZeroClaw walkthroughs), see
> [`docs/benchmarks/aime.md`](aime.md).

Wall-clock estimate (`num_samples=32`, `max_concurrent=10`): **~12–24 h**
on a 128K-output budget. Pilot variants (`num_samples=1`) finish in ≲ 1 h.
Plan a `tmux` session for the full sweep.

This path is **infrastructure-light**: DirectLLM only needs HTTP access to
the vLLM endpoint. No Docker, no Podman, no ROCK sandbox.

---

## 0. What "full run" means here

- Dataset: `HuggingFaceH4/aime_2024` on Hugging Face — 30 integer-answer
  competition math problems, all in the `train` split.
- Agent: `direct_llm` (single OpenAI-compatible chat-completions call per
  sample; no tool use, no sandbox).
- Sampling: `num_samples=32` per problem → 960 generations total
  (pass@K-style). Lower to 1 for a smoke or a single-pass pass@1 run.
- Scoring: integer match via the `numeric` scorer (`tolerance=1e-6`).
- Reference yardstick: this exact config has been used as the canonical
  AIME 2024 DirectLLM full-run baseline (see `metadata.notes` in the YAML).

---

## 1. Host prerequisites (one-time)

### 1.1 Choose a work root (everything else hangs off this)

Pick **one** filesystem path that has ≥ 50 GB free and is **not** under
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
mkdir -p "$WORK_ROOT"/{results,logs}
df -h "$WORK_ROOT" | tail -1     # must show ≥ 50 GB free
```

A full 30 × 32 AIME run with default DirectLLM settings produces ≲ 5 GB of
task JSON and per-sample reasoning traces. Logprob capture multiplies that
by ~3 (Int16 sidecars). The 50 GB headroom is generous.

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

`HuggingFaceH4/aime_2024` is **public** — no token required. If you have
`HF_TOKEN` set anyway, it's harmless. If you hit rate-limit errors during
the first dataset load, run `huggingface-cli login` with a read token to
authenticate.

---

## 2. vLLM endpoint

Pick the port + the GPUs you want to dedicate to this model. The runbook
uses `8011` and the host's first two GPUs by example; adjust to whatever
your hardware allows. The only **hard** constraints are
`--max-model-len ≥ 200000` (the config asks for `max_tokens=131072` =
128K output, leaving ~69K for the prompt + system message + thinking) and
the two generation-config flags below.

```bash
# Operator choices — adjust to your hardware/preference
export VLLM_PORT=8011
export VLLM_GPUS=0,1                         # comma-separated CUDA device IDs
export VLLM_TENSOR_PARALLEL=2                # must equal number of GPUs in $VLLM_GPUS
export VLLM_GPU_MEM_UTIL=0.9                 # raise on dedicated nodes, lower if you share
mkdir -p "$WORK_ROOT/logs"

CUDA_VISIBLE_DEVICES="$VLLM_GPUS" \
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-27B \
  --host 0.0.0.0 --port "$VLLM_PORT" \
  --trust-remote-code \
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL" \
  --gpu-memory-utilization "$VLLM_GPU_MEM_UTIL" \
  --max-model-len 200000 \
  --generation-config vllm \
  --override-generation-config '{"presence_penalty": 1.5}' \
  --reasoning-parser qwen3 \
  --served-model-name Qwen/Qwen3.5-27B \
  2>&1 | tee "$WORK_ROOT/logs/vllm_qwen3.5_27b.log"
```

Sizing notes:

- A single 24 GB GPU is **not** enough for Qwen3.5-27B at 200K context.
  Expect at least 2 × 80 GB (A100/H100) or 4 × 48 GB (RTX 6000 Ada / L40)
  for tensor-parallel sharding.
- If you only have one large GPU (1 × 80 GB), set
  `VLLM_TENSOR_PARALLEL=1` and lower `--max-model-len` to whatever fits
  (e.g. `131072`); also lower `agent.config.max_tokens` accordingly in §6
  (e.g. `120000` to leave room for the AIME prompt).

Confirm the endpoint is reachable:

```bash
curl -sS --max-time 30 "http://127.0.0.1:${VLLM_PORT}/v1/models" \
  | python -m json.tool | head -20
# Expect data[].id ∈ {"Qwen/Qwen3.5-27B"} and max_model_len ≥ 131072.
```

Why these flags matter:

- `--max-model-len 200000` — leaves room for `max_tokens=131072` plus the
  ~10K prompt and thinking trace headroom.
- `--override-generation-config '{"presence_penalty": 1.5}'` — suppresses
  Qwen3.5's repetition loops on long-thinking math traces.
- `--generation-config vllm` — ignore the model's `generation_config.json`
  which would otherwise quietly enable non-greedy defaults
  (`temperature=0.6`, `top_p=0.95`); the AIME config pins
  `temperature=0.0` for greedy pass@1, and uses sampling only via the
  client's explicit `temperature/top_p`.
- `--reasoning-parser qwen3` — emits `delta.reasoning` events. DirectLLM
  consumes them transparently; without this flag the thinking trace is
  buried in `message.content` instead of `reasoning_content`.

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

Confirm AIME is registered:

```bash
python -m alphadiana.cli list-benchmarks | grep -E '^\s+- aime$'
# expect: - aime
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
export AIME_FULL_OUTPUT_ROOT="$WORK_ROOT/results"
export AIME_RUN_ID=aime2024_full_$(date +%Y%m%d_%H%M%S)
mkdir -p "$AIME_FULL_OUTPUT_ROOT"
```

The checked-in YAML refers to the provider as `http://127.0.0.1:8011/v1`
directly in `agent.config.api_base`; the §6 launch command overrides that
to whatever your `QWEN_VLLM_API_BASE` is, so the same config works whether
your vLLM is on `8011` or another port.

---

## 5. Validate the config + dataset

```bash
# Static validation (no live vLLM call, no dataset download)
python -m alphadiana.cli validate \
  configs/full_runs/aime_directllm_qwen35_27b_logprobs.yaml
# expect: Config is valid.

# Live preflight — small dataset load + 1 vLLM round-trip
python -c "
from datasets import load_dataset
ds = load_dataset('HuggingFaceH4/aime_2024', split='train')
print(f'AIME 2024 task count: {len(ds)}')        # expect 30
"

curl -sS --max-time 30 "$QWEN_VLLM_API_BASE/models" >/dev/null \
  && echo 'vllm reachable' || echo 'VLLM UNREACHABLE'
```

Both must pass before kicking off the full sweep.

---

## 6. Smoke before the full run (≈ 2 min, 1 task, 1 sample)

Always smoke first. This catches config drift, dataset auth issues, and
endpoint misconfiguration without burning a 24-hour run.

```bash
python -m alphadiana.cli run \
  configs/full_runs/aime_directllm_qwen35_27b_logprobs.yaml \
  --redo-all \
  -o run_id=${AIME_RUN_ID}_smoke \
  -o output_dir=$AIME_FULL_OUTPUT_ROOT \
  -o agent.config.api_base=$QWEN_VLLM_API_BASE \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1 \
  -o max_concurrent=1 \
  -o agent.config.max_tokens=8000 \
  -o agent.config.capture_logprobs=false \
  2>&1 | tee "$WORK_ROOT/logs/${AIME_RUN_ID}_smoke.log"
```

Expected output (final line):
```
Run completed: aime2024_full_...._smoke
  Tasks: 1/1 completed
```

The smoke score itself can be 0 — the 8000-token budget is intentionally
too small to finish thinking on a hard AIME problem. We're checking that
the harness path is healthy (chat-completions returns 200, the scorer
runs, the task JSON is written).

Expected artifacts:
- `$AIME_FULL_OUTPUT_ROOT/${AIME_RUN_ID}_smoke/.../tasks/aime_*.json`
- the JSON's `score_status` is `valid_scored` (or `score_status=parse_failed`
  if the model genuinely produced no boxed integer — still a healthy path).

---

## 7. Launch the full sweep

Detach from the smoke. Then run the full 30 × 32 sweep in `tmux`:

```bash
tmux new -d -s aime2024 "
  set -eux
  python -m alphadiana.cli run \
    configs/full_runs/aime_directllm_qwen35_27b_logprobs.yaml \
    --redo-all \
    -o run_id=${AIME_RUN_ID} \
    -o output_dir=$AIME_FULL_OUTPUT_ROOT \
    -o agent.config.api_base=$QWEN_VLLM_API_BASE \
    2>&1 | tee $WORK_ROOT/logs/${AIME_RUN_ID}.log
"

tmux attach -t aime2024            # Ctrl-B D to detach
```

The checked-in defaults baked into the YAML:
- `num_samples=32` (pass@K-style)
- `max_concurrent=10`
- `temperature=0.0`, `top_p=0.95`, `max_tokens=131072`
- `capture_logprobs=true`, `top_logprobs=20`, Int16 sidecars
- `enable_thinking=true`

Knobs you may want to override at the CLI:

| Knob | Default | When to change |
| --- | --- | --- |
| `num_samples` | 32 | Drop to `1` for a pass@1 walk, `4` for a budget pass@K. |
| `max_concurrent` | 10 | Lower if your vLLM endpoint can't sustain 10 long-thinking requests; raise only if you've benchmarked it. |
| `agent.config.max_tokens` | 131072 | Lower if you have a smaller context window or want to cap wall time. |
| `agent.config.capture_logprobs` | true | Set `false` if you don't need logprobs (saves ~3× output size and ~20% wall time). |

---

## 8. Monitor the run

```bash
# Live tail
tail -f $WORK_ROOT/logs/${AIME_RUN_ID}.log

# Per-task progress: count completed JSON rows
find "$AIME_FULL_OUTPUT_ROOT/${AIME_RUN_ID}" -name "aime_*.json" 2>/dev/null | wc -l
# climbs toward 30

# Total sample progress (one record per (task, sample_index))
find "$AIME_FULL_OUTPUT_ROOT/${AIME_RUN_ID}" -name "aime_*.json" -exec \
  python -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" {} \; \
  2>/dev/null | awk '{s+=$1} END {print s, "/ 960 samples done"}'

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

---

## 9. Result interpretation

After the run completes:

```bash
# Per-task records (one file per problem, one record per sample_index)
ls "$AIME_FULL_OUTPUT_ROOT/${AIME_RUN_ID}"/*/tasks/ | head

# Top-line metrics from the harness summary
grep -E "accuracy|pass@1|avg@" $WORK_ROOT/logs/${AIME_RUN_ID}.log | tail -5

# pass@1 by hand (greedy first sample of each task)
python - <<'PY'
import json, os, glob
root = os.environ["AIME_FULL_OUTPUT_ROOT"]
run_id = os.environ["AIME_RUN_ID"]
files = sorted(glob.glob(f"{root}/{run_id}/*/tasks/aime_*.json"))
pass1 = sum(1 for f in files if json.load(open(f))[0].get("score") == 1.0)
print(f"pass@1: {pass1}/{len(files)} ({pass1/max(len(files),1):.2%})")
PY
```

For pass@K (K ≤ 32), aggregate across `sample_index` per task — a task
passes if **any** of its 32 samples scored 1.0.

```bash
python - <<'PY'
import json, os, glob
root = os.environ["AIME_FULL_OUTPUT_ROOT"]
run_id = os.environ["AIME_RUN_ID"]
files = sorted(glob.glob(f"{root}/{run_id}/*/tasks/aime_*.json"))
passK = sum(1 for f in files if any(r.get("score") == 1.0 for r in json.load(open(f))))
print(f"pass@32: {passK}/{len(files)} ({passK/max(len(files),1):.2%})")
PY
```

---

## 10. Operational risks and how to defuse them

| Risk | Symptom | Mitigation |
| --- | --- | --- |
| vLLM long-thinking deadlock | every request times out, `$WORK_ROOT/logs/vllm_qwen3.5_27b.log` mtime stops advancing | Restart vLLM; in-flight tasks retry. Monitor `/v1/models` (not GPU-util). |
| `$WORK_ROOT` fills up | task JSON writes start to fail mid-run | §1.1 — pick a mount with ≥ 50 GB free, recheck with `df -h "$WORK_ROOT"` every 6 hours. |
| Stale `generation_config.json` defaults | non-greedy sampling, score drift across replicas | §2 — make sure `--generation-config vllm` is in the vllm launch flags. |
| HF rate limit on dataset download | dataset load errors on first run | `huggingface-cli login` with a read token; pre-cache: `python -c 'from datasets import load_dataset; load_dataset("HuggingFaceH4/aime_2024", split="train")'` |
| Provider 429 / token budget exhaustion mid-run | repeated 429s, dispatcher backs off | Pause, wait for the provider's documented recovery window, then re-run the same `run_id` **without** `--redo-all`. Completed `sample_index` rows are skipped. |
| Long-thinking truncation at `max_tokens` | many tasks have `score_status=parse_failed`, model never wrote `\boxed{...}` | Raise `agent.config.max_tokens` (up to `--max-model-len - 10K`) or drop `enable_thinking=false` for a non-thinking pass. |

---

## 11. Resuming a partial run

DirectLLM writes per-task JSON rows as it goes. Re-running the same
`run_id` **without** `--redo-all` resumes — completed `(task, sample_index)`
combinations are skipped:

```bash
python -m alphadiana.cli run \
  configs/full_runs/aime_directllm_qwen35_27b_logprobs.yaml \
  -o run_id=${AIME_RUN_ID} \
  -o output_dir=$AIME_FULL_OUTPUT_ROOT \
  -o agent.config.api_base=$QWEN_VLLM_API_BASE
```

To restart from scratch, pass `--redo-all` (the §7 snippet does; remove
it for resume mode).

---

## 12. Output map

```
$AIME_FULL_OUTPUT_ROOT/
└── ${AIME_RUN_ID}/
    └── full_aime2024_directllm_qwen35_27b_logprobs/
        ├── tasks/
        │   ├── aime_1.json      # 32 records per file (one per sample_index)
        │   ├── aime_2.json
        │   └── ... (30 files)
        ├── artifacts/
        │   └── aime_<id>/...    # raw chat-completions payloads, reasoning
        └── logprobs/
            ├── aime_<id>.jsonl              # sample_index=0
            └── aime_<id>/sample_<N>.jsonl   # sample_index>0

$WORK_ROOT/logs/${AIME_RUN_ID}.log    # outer harness log
$WORK_ROOT/logs/vllm_qwen3.5_27b.log  # vllm server log
```

---

## 13. Scope of this runbook

- AIME 2024 only (`HuggingFaceH4/aime_2024`, 30 problems, `train` split).
- DirectLLM agent (`direct_llm`) only — no OpenClaw / OpenCode / ZeroClaw.
- Local Qwen3.5-27B via vLLM on a 200K context; `max_tokens=131072`
  (128K output budget).
- Does not cover AIME 2026, ZeroClaw walkthroughs, OpenRouter providers,
  Kimi K2.6, or any non-loopback endpoint.
- For AIME 2026, use `configs/full_runs/aime2026_*.yaml` and the older
  `docs/zeroclaw_aime2026_runbook.md`.
- For GPQA-Diamond, HLE, or MMMU-Pro, use their dedicated runbooks:
  - [`runbook-gpqa-diamond-full-directllm-qwen35-27b.md`](runbook-gpqa-diamond-full-directllm-qwen35-27b.md)
  - [`runbook-hle-full-directllm-qwen35-27b.md`](runbook-hle-full-directllm-qwen35-27b.md)
  - [`runbook-mmmu-pro-full-directllm-qwen35-27b.md`](runbook-mmmu-pro-full-directllm-qwen35-27b.md)
