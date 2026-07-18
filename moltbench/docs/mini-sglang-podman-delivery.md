# mini-SGLang × Podman — Delivery / Handoff Guide

Reproducible handoff for the MoltBench mini-SGLang serving-throughput optimization
task, running under **Podman** through AlphaDiana. **Four agent harnesses — OpenClaw,
OpenCode, ZeroClaw, and Claude Code — all run the same task** via one agent-agnostic
verifier contract (`/eval/run_eval`), selected at runtime by `MOLTBENCH_AGENT`. All four
route to the **same OpenRouter model**, so the comparison isolates the *harness*, not the
model. The agent optimizes the mini-SGLang inference engine inside a container; a hidden
verifier measures warmup-excluded steady-state throughput and scores
`OG = candidate / in-situ-baseline`.

> Verified end-to-end on the A800 (podman 3.4.4) on 2026-06-03. The thick image runs
> self-contained (no conda-env mount). All four agents reached `/eval/run_eval` and were
> scored `correctness=PASS, correct=true` (OpenClaw also validated on the Hard difficulty).

---

## 1. What is delivered

| Artifact | Location (A800) | Size | Contents |
|----------|-----------------|------|----------|
| Portable image tar | `/data3/jinbo/mini-sglang-agents-repro.tar` | ~16 GB | full self-contained image, all 4 agents |
| Image (loaded) | `localhost/mini-sglang-agents:repro` | 16.9 GB | see below |

> An OpenClaw-only image (`mini-sglang-openclaw:repro` / `mini-sglang-openclaw-repro.tar`,
> 15.8 GB) also exists from the first bake; the `*-agents:*` image supersedes it.

**Baked into the image (self-contained):**
- Validated conda env (`torch 2.9.1+cu128`, `flashinfer`, `ninja`) at
  `/data3/jinbo/envs/mini_sglang` (same path inside the container, so shebangs resolve).
- Clean **pre-PR#97 seed repo** (`commit 20fcd7f`) at `/workspace/repo` — the code the
  agent optimizes. `_EstimatePolicy` is intentionally absent (that is one of the
  optimizations the agent may rediscover).
- Hidden eval harness `/eval/{eval_runner.py,bench_serving.py,call_gateway.py,run_eval}`
  and entrypoint `/workspace/run_minisgl.sh`.
- **All four agent runtimes**, selected by `MOLTBENCH_AGENT`:
  - `openclaw` — Node + OpenClaw (gateway mode), OpenAI-compatible → OpenRouter.
  - `opencode` — `opencode-ai` CLI, OpenAI-compatible → OpenRouter.
  - `zeroclaw` — `zeroclaw` Rust CLI, OpenAI-compatible → OpenRouter.
  - `claude-code` — `@anthropic-ai/claude-code` + `claude-code-router` (CCR). CCR bridges
    Claude Code's Anthropic `/v1/messages` protocol to OpenRouter's OpenAI format, so it
    runs the same model as the others. `IS_SANDBOX=1` lets it run autonomously as root.
  - `none` — baseline + official only (smoke test).

**NOT baked — must be host-mounted (same-cluster handoff):**
- **CUDA toolkit** `/usr/local/cuda` (nvcc, for flashinfer JIT) — host has 12.x.
- **Model weights** `/data0/models` (shared HF/modelscope cache; default model
  `Qwen3-0.6B`). Mounting the whole shared dir avoids copying tens of GB into the image.

> For a *fully offline* image (different cluster, no shared `/data0` or `/usr/local/cuda`),
> additionally bake in the CUDA runtime and the model snapshot — the image grows by the
> CUDA + weights size. Not done here because handoff stays on the A800 cluster.

---

## 2. Host prerequisites

- Podman. GPU injection:
  - podman **3.4.x** (A800): `--runtime /usr/bin/nvidia-container-runtime -e NVIDIA_VISIBLE_DEVICES=<id>`
  - podman **≥ 4.1**: CDI `--device nvidia.com/gpu=<id>`
- A free NVIDIA GPU (check `nvidia-smi`; the box is shared).
- Host paths present: `/usr/local/cuda`, `/data0/models` (with the target model).
- Disk: graphroot on a large volume (A800: `/data3/jinbo/containers/storage`). When
  building/loading large images set `TMPDIR` to a large disk — the root fs `/` is only
  ~20 GB free and podman stages layer blobs in `/var/tmp` by default.

---

## 3. Load the image

```bash
TMPDIR=/data3/jinbo/tmp podman load -i /data3/jinbo/mini-sglang-agents-repro.tar
podman images | grep mini-sglang-agents:repro
```

---

## 4. Run

### 4a. Smoke test (no agent — baseline + official only)

Proves the image is self-contained and the GPU plumbing works. **No env mount, no API key.**

```bash
mkdir -p /tmp/mb_smoke
podman run --rm --name mb_smoke \
  --runtime /usr/bin/nvidia-container-runtime \
  -e NVIDIA_VISIBLE_DEVICES=<FREE_GPU> -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  --security-opt label=disable --network host \
  -e MOLTBENCH_AGENT=none -e MINISGL_PORT=31987 -e MEMORY_RATIO=0.6 \
  -v /usr/local/cuda:/usr/local/cuda:ro \
  -v /data0/models:/data0/models:ro \
  -v /tmp/mb_smoke:/workspace/artifacts \
  localhost/mini-sglang-agents:repro
cat /tmp/mb_smoke/results.json   # expect correctness=PASS, correct=true, og≈1.0±noise
```

### 4b. Full run with an agent (any of the four)

Same as 4a plus `MOLTBENCH_AGENT=<openclaw|opencode|zeroclaw|claude-code>` and OpenRouter
(or any OpenAI-compatible) credentials — **the same env works for all four agents**
(claude-code transparently routes through the baked claude-code-router; no extra setup).
The agent edits `/workspace/repo` and calls `/eval/run_eval` each attempt; the hidden
harness scores its final code.

```bash
podman run --rm --name mb_agent \
  --runtime /usr/bin/nvidia-container-runtime \
  -e NVIDIA_VISIBLE_DEVICES=<FREE_GPU> -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  --security-opt label=disable --network host \
  -e MOLTBENCH_AGENT=opencode -e MINISGL_PORT=31987 -e MEMORY_RATIO=0.6 \
  -e OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
  -e OPENAI_API_KEY=sk-or-... \
  -e OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507 \
  -v /usr/local/cuda:/usr/local/cuda:ro \
  -v /data0/models:/data0/models:ro \
  -v /tmp/mb_run:/workspace/artifacts \
  localhost/mini-sglang-agents:repro
```

Run WITHOUT `http(s)_proxy` set — OpenRouter is reached directly from the A800.
Switch harness by changing `MOLTBENCH_AGENT` (openclaw / opencode / zeroclaw / claude-code).

### 4c. Via AlphaDiana (the harness-eval path)

```bash
cd /data3/jinbo/alphadiana-minisgl
set -a; . /home/user/.env; set +a            # OPENAI_API_KEY
export OPENAI_BASE_URL=https://openrouter.ai/api/v1 OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507
PYTHONPATH=$(pwd) /data3/jinbo/envs/mini_sglang/bin/python -m alphadiana.cli \
  run configs/examples/moltbench_minisglang_easy.yaml --redo-all
```

The config selects `agent.name: moltbench_docker` with `container_engine: podman`,
`gpu_mode: nvidia-runtime`. To swap harnesses later, change `agent_type` — the task
prompt is harness-agnostic.

---

## 5. Output / scoring schema

`/workspace/artifacts/results.json`:

```json
{
  "correctness": "PASS | FAIL | ERROR",
  "speedup": 0.921, "og": 0.921,
  "throughput_tok_per_s": 7376.54,
  "baseline_throughput": 8009.07,
  "correct": true, "attempts_used": 0
}
```

- **baseline_throughput** — measured *in-situ* on the pristine seed at container start
  (warmup-excluded steady-state median). Never a hardcoded constant, never agent-writable.
- **OG** — candidate / baseline, both measured under identical warmup-excluded conditions.
- **correctness** gate — a candidate whose benchmark errors scores `ERROR / 0` even if
  fast (anti-hack). Also produced: `baseline.json`, `manifest.json`, `attempts.jsonl`,
  `gateway.log`, `agent.patch`, server logs.

---

## 6. Notes for the recipient

- **Serving noise is expected** (Design-5 §12). Throughput swings cold→warm; OG is only
  meaningful with the in-situ baseline + warmup exclusion this harness enforces. Run
  several repeats and read the median + min/max, not a single number.
- **Model size matters for the reference optimization.** On Qwen3-0.6B the KV cache never
  saturates, so scheduler optimizations (e.g. PR#97) show no robust gain. To make the
  reference optimization measurable, serve a larger model (Qwen3-4B/8B, present in
  `/data0/models/hub`) and pin the KV pool (`--num-pages`) so A/B comparisons are fair on
  a shared GPU. The task wrapper and scoring are independent of this choice.
- **Optimization menu** for the agent: `moltbench/docs/mini-sglang-optimization-catalog.md`
  (6 verified PRs with rationale, key files, and which are/aren't implementable).
- **Cleanup hygiene** on the shared box: one free GPU; mini-SGLang spawns multiprocessing
  workers — kill the **process group**, not just `pkill -f launch`; `--rm` removes the
  container automatically.
