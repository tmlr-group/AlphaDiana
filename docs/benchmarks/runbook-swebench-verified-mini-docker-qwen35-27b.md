# Runbook — SWE-bench Verified Mini on local Qwen3.5-27B + SWE-agent + Docker

Self-contained operator runbook for completing the **SWE-bench Verified Mini**
benchmark (50 tasks from `MariusHobbhahn/swe-bench-verified-mini`) on a local
`Qwen/Qwen3.5-27B` vLLM endpoint via the official SWE-agent CLI, orchestrated
by `alphadiana.benchmark_rollout_cli`.

> This is the "I want to run the benchmark" runbook. It deliberately
> duplicates content from the TerminalBench2 runbook so each can be followed
> top-to-bottom without cross-references. For the deeper guide (parameter
> rationale, gotchas) see
> [`docs/benchmarks/guide-swebench-verified-mini.md`](guide-swebench-verified-mini.md).

Wall-clock estimate: **8–15 hours at `max_concurrent=10`** for the 50-task sweep
(provider-bound on long-thinking instances). Plan a `tmux` session.

This path is **Docker-based**, not Podman — the SWE-bench harness uses Docker
to run per-instance evaluation containers, and SWE-agent's own container
management uses Docker. The TerminalBench2 runbook uses Podman; the two
campaigns can share a host but do not share a container runtime.

---

## 0. What "Verified Mini" means here

- Dataset: `MariusHobbhahn/swe-bench-verified-mini` on Hugging Face — 50
  curated instances drawn from SWE-bench Verified.
- Agent: standalone SWE-agent (`sweagent run-batch`), not OpenClaw/OpenCode/
  ZeroClaw.
- pass@1 scoring via the official `swebench` harness.
- Reference run for parity: `T-MARS/alphadiana-benchmark-results/full_run/`
  `YYYYMMDD-swe-bench-verified-mini-sweagent-qwen35-27b-local-v1` (23/50 = 46
  % resolved). Treat as the rough yardstick, not a gate.

---

## 1. Host prerequisites (one-time)

### 1.1 Choose a work root (everything else hangs off this)

Pick **one** filesystem path that has ≥ 200 GB free and is **not** under
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
mkdir -p "$WORK_ROOT"
df -h "$WORK_ROOT" | tail -1     # must show ≥ 200 GB free
```

All later steps create subdirectories under `$WORK_ROOT`:
`$WORK_ROOT/swe-verified-mini/{.venv, SWE-agent, sweagent_results}` and
`$WORK_ROOT/logs/vllm_qwen3.5_27b.log`.

### 1.2 Docker engine + read access

```bash
docker --version              # any 20.10+
docker ps                     # must succeed without sudo
docker run --rm hello-world   # smoke
```

If `docker ps` requires `sudo`, add yourself to the `docker` group and
re-login (or set `DOCKER_HOST` for a rootless docker daemon).

### 1.3 SWE-agent root under `$WORK_ROOT`

The campaign renderer and SWE-agent CLI share a fixed layout (set up in
§4); reserve its top-level path now:

```bash
export DIRECTLLM_SWE_VERIFIED_ROOT="$WORK_ROOT/swe-verified-mini"
mkdir -p "$DIRECTLLM_SWE_VERIFIED_ROOT"
df -h "$DIRECTLLM_SWE_VERIFIED_ROOT" | tail -1   # double-check ≥ 150 GB free
```

A full mini run plus the per-instance evaluation containers, traj files,
patch files, and logprob sidecars can easily exceed 100 GB. The harness
will also build Docker images per instance — Docker's own graph driver
needs space too (see §1.2).

### 1.4 Python toolchain

SWE-agent and the swebench harness need Python 3.10 or 3.11 in a venv.
Pick whichever interpreter your host has — the runbook calls it
`$PYTHON_BIN` from here on:

```bash
# Pick one that exists on your host (run `which python3.11` etc. to check)
export PYTHON_BIN=python3.11        # or python3.10, or just python3
"$PYTHON_BIN" --version             # must report 3.10.x or 3.11.x

pip install --user huggingface_hub[cli]   # provides huggingface-cli
huggingface-cli --version
```

Python 3.12+ is **not** recommended: some sweagent/swebench deps still
ship 3.10/3.11 wheels and source-build under 3.12 has been flaky in
practice. If your distro only has 3.12 system-wide, install miniconda
(§3) and create a 3.11 env there for the SWE-agent venv in §4.

### 1.5 Hugging Face credentials

The Verified-Mini dataset is public, but the `datasets` library still wants a
token to avoid rate limits:

```bash
huggingface-cli login          # paste a read token
huggingface-cli whoami         # confirm logged in
```

If you also intend to upload results to a private dataset (e.g.
`T-MARS/alphadiana-benchmark-results`), use a token with write access to
that repo as well.

---

## 2. vLLM endpoint

Pick the port + the GPUs you want to dedicate to this model. The runbook
uses `8011` and the host's first two GPUs by example; adjust to whatever
your hardware allows. The only **hard** constraints are the
`--max-model-len 262144` (the renderer assumes the 256K window for SWE-bench's
long problem statements + 128K output budget) and the two
generation-config flags below.

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
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL" \
  --gpu-memory-utilization "$VLLM_GPU_MEM_UTIL" \
  --max-model-len 262144 \
  --generation-config vllm \
  --override-generation-config '{"presence_penalty": 1.5}' \
  --served-model-name qwen3.5-27b Qwen/Qwen3.5-27B \
  2>&1 | tee "$WORK_ROOT/logs/vllm_qwen3.5_27b.log"
```

Sizing notes:

- A single 24 GB GPU is **not** enough for Qwen3.5-27B at 256K context.
  Expect at least 2 × 80 GB (A100/H100) or 4 × 48 GB (RTX 6000 Ada / L40)
  for tensor-parallel sharding.
- If you only have one large GPU (e.g. 1 × H100 80 GB), set
  `VLLM_TENSOR_PARALLEL=1` and lower `--max-model-len` to whatever fits
  (e.g. `131072`); but then `max_output_tokens=131072` against that
  endpoint will fail because there is no headroom for the SWE-bench
  problem statement plus tool defs. The runbook's 256K assumption is
  load-bearing.

Why these flags matter:

- `--max-model-len 262144` — 256K context. SWE-bench problem statements are
  long; this leaves room for the 128K `max_output_tokens` budget plus the
  problem text plus tool definitions.
- `--override-generation-config '{"presence_penalty": 1.5}'` — suppresses
  Qwen3.5's repetition loops on long-thinking traces.
- `--generation-config vllm` — ignore the model's `generation_config.json`
  (otherwise clients that omit sampling params silently get non-greedy
  defaults).
- **No** `--reasoning-parser`. SWE-agent reads thinking from
  `message.content` directly; don't split it into a `reasoning_content`
  field.
- `--enable-auto-tool-choice --tool-call-parser qwen3_coder` — harmless
  here (SWE-agent uses its own XML parser, not OpenAI tool calls), required
  if any other agent on the same endpoint wants function calling.
- Two `--served-model-name`s — lets clients reach the model under both the
  canonical and the short name.

Confirm the endpoint is reachable:

```bash
export QWEN_VLLM_API_BASE="http://127.0.0.1:${VLLM_PORT}/v1"
export QWEN_VLLM_API_KEY=EMPTY

curl -sS --max-time 30 "$QWEN_VLLM_API_BASE/models" | python -m json.tool | head -20
# Expect: data[].id ∈ {"qwen3.5-27b", "Qwen/Qwen3.5-27B"}
```

---

## 3. AlphaDiana repo + Python env

```bash
git clone <your-fork-or-origin> alphadiana
cd alphadiana
git checkout main
```

`scripts/activate.sh` expects a **conda** install (it runs `conda activate
<env>`). If you don't already have Miniconda/Anaconda, install it first:
https://docs.conda.io/projects/miniconda/. Then bootstrap the project
environment:

```bash
bash scripts/quickstart.sh        # creates the conda env from project files
source scripts/activate.sh        # activates it + sources .env
python -c "import alphadiana; print(alphadiana.__file__)"   # sanity
```

This conda env is the **AlphaDiana orchestrator** environment — it runs
`alphadiana.benchmark_rollout_cli`. SWE-agent itself runs in a separate
venv created in §4 under `$DIRECTLLM_SWE_VERIFIED_ROOT/.venv`.

---

## 4. SWE-agent checkout + venv

The official SWE-agent CLI runs in its own venv next to the AlphaDiana repo.
Layout the campaign renderer expects:

```
$DIRECTLLM_SWE_VERIFIED_ROOT/
├── .venv/                     # SWE-agent's venv (NOT the alphadiana venv)
└── SWE-agent/
    └── config/default.yaml    # config the manifest references
```

One-shot setup:

```bash
cd "$DIRECTLLM_SWE_VERIFIED_ROOT"

git clone https://github.com/SWE-agent/SWE-agent
"$PYTHON_BIN" -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -e SWE-agent
./.venv/bin/pip install swebench            # official harness

# Sanity
./.venv/bin/sweagent --version
./.venv/bin/python -c "import swebench; print(swebench.__version__)"
```

Pre-pull the SWE-bench harness's evaluation base image so the first
instance does not stall on a Docker Hub pull:

```bash
docker pull swebench/sweb.base.py.x86_64:latest || true
```

(Optional. The harness will pull it on demand; pre-pulling reduces
per-instance latency.)

---

## 5. Campaign manifest

A campaign manifest is checked in at `configs/full_runs/swe_verified_mini.yaml`.
Open it once to confirm the values match your target:

```yaml
campaign_id: "swe_verified_mini"
defaults:
  run_id_prefix: "swe_verified_mini"
models:
  - id: qwen35_27b
    model_name: Qwen/Qwen3.5-27B
    api_base_env: QWEN_VLLM_API_BASE
    api_key_env:  QWEN_VLLM_API_KEY
    official_model_name: openai/qwen3.5-27b
    supports_multimodal: false
path_templates:
  - id: swe_verified_mini_sweagent
    benchmark: swe_bench_verified
    harness: sweagent
    backend: official_swebench_verified
    max_concurrent: 10
    base_wave: wave_b_official
    base_risk: official_checkout
    config_gaps: []
    overrides:
      dataset: "MariusHobbhahn/swe-bench-verified-mini"
      subset: "verified"
      split: "test"
      temperature: 0.0
      top_p: 0.95
      max_output_tokens: 131072
      per_instance_call_limit: 80
      eval_max_workers: 4
```

Knobs you might want to tweak:

| Knob | Default | When to change |
| --- | --- | --- |
| `max_concurrent` | 10 | Lower if your vLLM endpoint can't sustain 10 long-thinking requests; raise only if you've benchmarked it. |
| `per_instance_call_limit` | 80 | Lower to cap wall time per instance; higher to give long-tail instances more room. |
| `eval_max_workers` | 4 | SWE-bench harness eval workers; raise if you have CPU headroom and Docker can keep up. |
| `max_output_tokens` | 131072 (128K) | Reduce if your endpoint has a smaller context (e.g. 128K total → cap at ~96K to leave prompt headroom). |
| `sweagent_config` | `config/default.yaml` | Override to a known-working benchmark config like `config/benchmarks/250522_anthropic_filemap_simple_review.yaml` if the reference run used it. |

---

## 6. Export the runtime environment

```bash
# vLLM provider (uses the port you picked in §2)
export QWEN_VLLM_API_BASE="http://127.0.0.1:${VLLM_PORT}/v1"
export QWEN_VLLM_API_KEY=EMPTY

# SWE-agent root (the layout from §4, already created in §1.3)
export DIRECTLLM_SWE_VERIFIED_ROOT="$WORK_ROOT/swe-verified-mini"

# Tag for this attempt — used only to scope the materialized-shell folder
# under generated/. The sweagent run_id baked into the shell itself is
# deterministic from the manifest: `swe_verified_mini_swe_bench_verified_sweagent_qwen35_27b`.
export SWE_MINI_ATTEMPT=qwen35_27b_$(date +%Y%m%d_%H%M%S)
```

The sweagent `run_id` baked into the materialized shell is fixed at
`swe_verified_mini_swe_bench_verified_sweagent_qwen35_27b` (derived from
`campaign_id` + path template id + model id in the manifest). Re-running the
same shell resumes that run_id; to get a fresh sweagent run_id, edit
`campaign_id` or `defaults.run_id_prefix` in
`configs/full_runs/swe_verified_mini.yaml` before materializing.

---

## 7. Validate the campaign + preflight

```bash
# Expand the manifest into a concrete run list (1 row here: 1 model × 1 path)
python -m alphadiana.benchmark_rollout_cli summary \
  --manifest configs/full_runs/swe_verified_mini.yaml

# Env + Docker preflight (non-zero exit on error)
python -m alphadiana.benchmark_rollout_cli preflight \
  --manifest configs/full_runs/swe_verified_mini.yaml \
  --probe-vllm \
  --check-docker
```

`preflight` verifies:

- `QWEN_VLLM_API_BASE` is reachable and the model is loaded.
- `docker ps` succeeds and you have permission.
- The SWE-agent checkout layout under `$DIRECTLLM_SWE_VERIFIED_ROOT` exists.

Both commands must exit `0` before continuing.

---

## 8. Materialize and launch

```bash
mkdir -p generated logs

# Render the run script into a directory you control
python -m alphadiana.benchmark_rollout_cli materialize \
  --manifest configs/full_runs/swe_verified_mini.yaml \
  --output-dir generated/${SWE_MINI_ATTEMPT}

# One shell per concrete run row. The filename is
# `run__<sweagent_run_id>.sh`.
ls generated/${SWE_MINI_ATTEMPT}/

# Pin the exact rendered shell path. This is the file you will actually
# execute, monitor, and resume from.
export SWE_MINI_RUN_SH=$(ls generated/${SWE_MINI_ATTEMPT}/run__*.sh | head -1)
echo "Will execute: $SWE_MINI_RUN_SH"

# Quick smoke: inspect the rendered shell so you see exactly what's about to
# happen (which sweagent flags, which harness flags).
head -60 "$SWE_MINI_RUN_SH"
bash -n "$SWE_MINI_RUN_SH" && echo "shell syntax ok"

# Launch in tmux (the 50-task sweep takes 8-15 hours wall time).
# The shell already tees to logs/<sweagent_run_id>.log via its own block;
# the tmux pipeline below just captures the outer harness stderr.
tmux new -d -s swe-mini "bash '$SWE_MINI_RUN_SH' 2>&1 \
  | tee logs/${SWE_MINI_ATTEMPT}.outer.log"
tmux attach -t swe-mini             # Ctrl-B D to detach
```

The generated shell does three things in order:

1. `cd $DIRECTLLM_SWE_VERIFIED_ROOT/SWE-agent`.
2. `../.venv/bin/sweagent run-batch --config config/default.yaml
    --instances.type swe_bench
    --instances.path_override MariusHobbhahn/swe-bench-verified-mini
    --instances.subset verified --instances.split test
    --agent.model.name openai/qwen3.5-27b
    --agent.model.api_base $QWEN_VLLM_API_BASE
    --agent.model.temperature 0.0 --agent.model.top_p 0.95
    --agent.model.max_output_tokens 131072
    --agent.model.per_instance_call_limit 80 ...`
3. `../.venv/bin/python -m swebench.harness.run_evaluation
    --dataset_name MariusHobbhahn/swe-bench-verified-mini
    --split test
    --predictions_path sweagent_results/<run_id>/preds.json
    --run_id <run_id>
    --max_workers 4`

If your `sweagent` version doesn't emit a single `preds.json` (some upstream
versions don't), the rendered shell prints a hint to use SWE-agent's gather
helper. The simpler path is to upgrade SWE-agent (`pip install -e
SWE-agent` from a recent commit).

---

## 9. Monitor the run

```bash
# The sweagent run_id is deterministic from the manifest:
export SWE_RUN_ID=swe_verified_mini_swe_bench_verified_sweagent_qwen35_27b

# Live tail of the inner sweagent + harness log (written by the shell itself)
tail -f logs/${SWE_RUN_ID}.log

# Outer tmux capture (smaller, easier to scan for tee-level errors)
tail -f logs/${SWE_MINI_ATTEMPT}.outer.log

# Per-instance progress: count completed predictions
ls -d $DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/${SWE_RUN_ID}/*__* 2>/dev/null | wc -l
# expect that count to climb toward 50

# Periodic vLLM health probe (don't rely on GPU-util — it's misleading on
# long-thinking workloads)
while sleep 300; do
  curl -sS --max-time 30 "$QWEN_VLLM_API_BASE/models" >/dev/null \
    && echo "$(date -Is) vllm ok" \
    || echo "$(date -Is) vllm WEDGED"
done
```

If vLLM wedges (probe times out repeatedly), kill the vllm process, restart
it with the same command as §2, and `sweagent` retries the in-flight
instances on its own.

If a Docker per-instance container leaks (`docker ps` shows
`swebench-eval-*` containers from a previous abort):

```bash
docker ps -a --filter name=swebench- --format '{{.ID}}' | xargs -r docker rm -f
```

---

## 10. Result interpretation

After both `sweagent run-batch` and `swebench.harness.run_evaluation`
complete:

```bash
# Per-instance trajectories, patches, traces
ls $DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/${SWE_RUN_ID}/

# Predictions JSON consumed by the harness
cat $DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/${SWE_RUN_ID}/preds.json \
  | python -m json.tool | head -40

# SWE-bench harness report
ls $DIRECTLLM_SWE_VERIFIED_ROOT/SWE-agent/logs/run_evaluation/${SWE_RUN_ID}/

# Resolved rate (the top-line number)
python - <<'PY'
import json, os
root = os.environ["DIRECTLLM_SWE_VERIFIED_ROOT"]
run_id = os.environ["SWE_RUN_ID"]
report = json.load(open(
    f"{root}/SWE-agent/logs/run_evaluation/{run_id}/report.json"))
print(f"total={report['total_instances']} resolved={report['resolved_instances']}"
      f" rate={report['resolved_instances']/report['total_instances']:.2%}")
PY
```

Reference run yardstick: 23 / 50 (46 %) resolved on the
`YYYYMMDD-swe-bench-verified-mini-sweagent-qwen35-27b-local-v1` HF run.
Anything close to that range is operationally aligned.

---

## 11. Operational risks and how to defuse them

| Risk | Symptom | Mitigation |
| --- | --- | --- |
| vLLM long-thinking deadlock | every request times out, log mtime stops advancing | Restart vLLM; sweagent retries in-flight instances. Monitor `/v1/models`, not GPU-util. |
| `$WORK_ROOT` fills up | predictions / harness containers fail with ENOSPC | §1.1 — pick a mount with ≥ 200 GB free, recheck with `df -h "$WORK_ROOT"` every 6 hours. |
| Docker daemon left zombies after an abort | `docker ps` lists `swebench-eval-*` containers | `docker ps -a --filter name=swebench- --format '{{.ID}}' \| xargs -r docker rm -f` |
| `per_instance_call_limit` hit on a long-tail instance | instance writes no patch and is recorded as unresolved | Acceptable as long as the rate is ≲ 7/50; raise the limit only if patches are clearly converging at step ≈80. |
| Stale `generation_config.json` defaults | non-greedy sampling, score drift across replicas | §2 — make sure `--generation-config vllm` is in the vllm launch flags. |
| HF `403`/`429` on dataset download | sweagent or harness errors on dataset load | `huggingface-cli login` with a token; pre-cache: `python -c 'from datasets import load_dataset; load_dataset("MariusHobbhahn/swe-bench-verified-mini", split="test")'` |

---

## 12. Resuming a partial run

`sweagent run-batch` writes per-instance results as it goes and skips
instances that already have a recorded prediction in the run directory.
Re-running the same `bash "$SWE_MINI_RUN_SH"` will pick
up where it left off as long as `$DIRECTLLM_SWE_VERIFIED_ROOT/`
`sweagent_results/${SWE_RUN_ID}/` is intact.

To restart from scratch, delete that directory first:

```bash
rm -rf $DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/${SWE_RUN_ID}
```

---

## 13. Output map

```
$DIRECTLLM_SWE_VERIFIED_ROOT/
├── .venv/                                              # SWE-agent venv
├── SWE-agent/
│   └── logs/run_evaluation/${SWE_RUN_ID}/
│       ├── report.json                                 # top-line metrics
│       └── ...
└── sweagent_results/${SWE_RUN_ID}/
    ├── preds.json                                      # combined predictions
    └── <instance_id>/
        ├── *.traj            # full trajectory
        ├── *.patch           # final patch
        ├── *.pred            # per-instance prediction
        └── *.trace.log       # raw model exchange

generated/${SWE_MINI_ATTEMPT}/run__${SWE_RUN_ID}.sh # materialized shell
logs/${SWE_RUN_ID}.log                              # inner sweagent + harness log
logs/${SWE_MINI_ATTEMPT}.outer.log                  # outer tmux tee
```

---

## 14. Scope of this runbook

- SWE-bench Verified Mini (50 tasks) only.
- Standalone SWE-agent path (`sweagent run-batch`) plus the official
  `swebench.harness.run_evaluation` scorer.
- Docker-based per-instance containers (NOT Podman).
- Local Qwen3.5-27B via vLLM on a 256K context.
- Does not cover SWE-bench Verified (full 500 tasks), SWE-bench Pro,
  TerminalBench2, external_benchmark, MMMU-Pro, GPQA, AIME, HLE, IMO-AnswerBench,
  ROCK, OpenClaw/OpenCode/ZeroClaw agents, or any non-loopback provider.
- For the TerminalBench2 full sweep, use
  [`runbook-terminal-bench-2-full-podman-qwen35-27b.md`](runbook-terminal-bench-2-full-podman-qwen35-27b.md).
