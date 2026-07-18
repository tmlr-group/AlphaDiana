# mini-SGLang × Podman — Reproduction Guide

Step-by-step reproduction of the MoltBench mini-SGLang serving-throughput optimization
task. **Environment-agnostic**: set the variables below for your machine. Concrete
values from our A800 cluster are shown as `# example`.

> The agent optimizes the mini-SGLang inference engine inside a container; a hidden
> verifier measures warmup-excluded steady-state throughput and scores
> `OG = candidate / in-situ-baseline`. Four agent harnesses
> (OpenClaw / OpenCode / ZeroClaw / Claude Code) run the **same** task, selected by
> `MOLTBENCH_AGENT`, all on one OpenRouter model — so the comparison isolates the harness.

---

## 0. Prerequisites

- **Podman** (or Docker). GPU injection differs by version:
  - podman **3.4.x**: `--runtime /usr/bin/nvidia-container-runtime -e NVIDIA_VISIBLE_DEVICES=<id>`
  - podman **≥ 4.1** / Docker: CDI `--device nvidia.com/gpu=<id>` / `--gpus`
- A free **NVIDIA GPU** (`nvidia-smi`).
- Host **CUDA toolkit** (provides `nvcc` for flashinfer JIT) — mounted read-only.
- A **conda env** with `torch` (CUDA build) + `flashinfer` + `ninja` — for the thin/dev
  image it is mounted; for the thick `:repro` image it is baked in.
- A **HF model cache** containing the served model (e.g. Qwen3-4B).
- An **OpenAI-compatible API key** for the agent's LLM (we use OpenRouter). Not needed
  for the no-agent smoke test.

### Variables you set (with our A800 example values)
| Variable | Meaning | A800 example |
|---|---|---|
| `GPU` | free GPU id | `6` |
| `CUDA_DIR` | host CUDA toolkit | `/usr/local/cuda` |
| `MODELS_DIR` | host HF/model cache (mounted) | `/data0/models` |
| `MODELS_HUB` | HF cache "hub" dir (for `MODEL` alias) | `/data0/models/hub` |
| `ENV_DIR` | host conda env (dev image only) | `/data3/jinbo/envs/mini_sglang` |
| `MODEL` or `MODEL_PATH` | served model (alias or abs path) | `MODEL=qwen3-4b` |
| `OPENAI_*` | agent LLM creds (agent runs only) | OpenRouter, qwen3-235b |

---

## 1. Get the image

### Option A — load the prebuilt thick image (self-contained, recommended for handoff)
```bash
TMPDIR=<big-disk>/tmp podman load -i mini-sglang-agents-repro2.tar   # example tar name
podman images | grep mini-sglang-agents
```
The thick image bakes the conda env (exposed at the stable path `/opt/minisgl-env`),
the clean pre-PR#97 seed repo, the hidden eval harness, and all 4 agent runtimes.
It still needs the host **CUDA** and **model cache** mounted (not baked).

### Option B — build from source (this repo)
```bash
# 1) base (clone + deps + eval scripts). The conda env lives in the FROM base image;
#    point --build-arg MINISGL_ENV at its real path (a symlink exposes it at /opt/minisgl-env).
TMPDIR=<big-disk>/tmp podman build \
  -f moltbench/docker/Dockerfile.mini-sglang-base \
  --build-arg MINISGL_ENV=<your conda env path> \
  -t localhost/mini-sglang-base:dev .
# 2) agent layer(s): see moltbench/docker/Dockerfile.mini-sglang-openclaw (+ opencode/zeroclaw/claude-code)
```
> Build/load gotcha: podman stages layer blobs in `/var/tmp` on the root fs by default.
> If `/` is small, set `TMPDIR=<big-disk>/tmp` or you get "no space left on device".

---

## 2. Smoke test (no agent) — proves image + GPU + flow, NO API key

```bash
GPU=6                      # set to a free GPU
mkdir -p /tmp/mb_smoke
podman run --rm --name mb_smoke \
  --runtime /usr/bin/nvidia-container-runtime \
  -e NVIDIA_VISIBLE_DEVICES=$GPU -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  --security-opt label=disable --network host \
  -e MOLTBENCH_AGENT=none \
  -e MODEL=qwen3-4b -e MODELS_HUB=/data0/models/hub \
  -e MEMORY_RATIO=0.2 -e NUM_PAGES=8000 \
  -v /usr/local/cuda:/usr/local/cuda:ro \
  -v /data0/models:/data0/models:ro \
  -v /tmp/mb_smoke:/workspace/artifacts \
  localhost/mini-sglang-agents:repro2
cat /tmp/mb_smoke/results.json     # expect: correctness=PASS, correct=true, og≈1.0±noise
```
Notes:
- `MODEL=qwen3-4b` resolves to `$MODELS_HUB/models--Qwen--Qwen3-4B/snapshots/<latest>`.
  Or pass an absolute `-e MODEL_PATH=<dir>` instead.
- On a busy shared GPU, pin a small KV pool with `-e NUM_PAGES=8000` so the server
  fits; first 4B flashinfer JIT can take minutes — raise `-e SERVER_READY_TIMEOUT=700`.

## 3. Full run with an agent (any of the four)

Same as §2 plus `MOLTBENCH_AGENT=<openclaw|opencode|zeroclaw|claude-code>` and
OpenAI-compatible credentials (the same env works for all four; claude-code routes
through a baked claude-code-router automatically):
```bash
podman run --rm --name mb_agent \
  --runtime /usr/bin/nvidia-container-runtime \
  -e NVIDIA_VISIBLE_DEVICES=$GPU -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  --security-opt label=disable --network host \
  -e MOLTBENCH_AGENT=opencode \
  -e MODEL=qwen3-4b -e MODELS_HUB=/data0/models/hub -e MEMORY_RATIO=0.3 \
  -e OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
  -e OPENAI_API_KEY=sk-or-... \
  -e OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507 \
  -v /usr/local/cuda:/usr/local/cuda:ro \
  -v /data0/models:/data0/models:ro \
  -v /tmp/mb_run:/workspace/artifacts \
  localhost/mini-sglang-agents:repro2
```
Run WITHOUT `http(s)_proxy` set (OpenRouter is reached directly).

## 4. Via AlphaDiana (the harness-eval path)

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1 OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507
export OPENAI_API_KEY=sk-or-...
PYTHONPATH=$(pwd) python -m alphadiana.cli \
  run configs/examples/moltbench_minisglang_easy.yaml --redo-all
```
Edit the YAML's `env:` / `volume_mounts:` / `gpus:` for your machine first (the
committed values are A800 examples and are labelled "replace with yours").

---

## 5. Output / verify

`/workspace/artifacts/`:
- `results.json` — `{correctness, og, throughput_tok_per_s, throughput_ci95, baseline_throughput, correct, cost}`
- `score.json`, `baseline_eval.json`, `official_eval.json`, `manifest.json` (incl. `config_lock`), `audit_report.md`
- `throughput_metrics.json` / `latency_metrics.json` / `warmup_policy.json` (serving metrics + CI)
- **Trajectory suitcase** (Design-1 §11/§14): `events.jsonl` (MoltEvent stream),
  `file_diffs.jsonl` (per-attempt diff vs the pristine seed), `model_calls.jsonl`
  (+ `model_cost.json` summary), `attempts.jsonl` (agent runs, success **and** failure).
- **Snapshots / config**: `task_config.json`, `initial_workspace.tar`, `final_workspace.tar`,
  `best_workspace.tar`, `agent.patch`, `agent_gitlog.txt`, server logs.
- **Integrity**: `verifier_integrity.json` (`status: OK` — the hidden verifier was not
  modified during the run; a `TAMPERED` status marks the run invalid).

> K (cost): `results.json.cost` carries verifier-owned `attempts_used` + `wall_time_sec`
> plus harness-aggregated `model_calls` / `total_tokens` and `cost_per_improvement`.
> Token counts are an **approximation** (`chars/4`, see `warmup_policy.json.token_count`):
> a relative measure whose bias cancels in the candidate/baseline OG ratio.

Self-check the admission criteria + artifact completeness:
```bash
python moltbench/validate_task.py static                 # 29/29 PASS expected
python moltbench/validate_task.py artifacts /tmp/mb_smoke # PASS (smoke: 0 attempts)
```

## 6. Cleanup hygiene (shared GPU box)

- Containers use `--rm` (auto-removed on stop).
- mini-SGLang spawns multiprocessing workers — the harness kills the **process group**
  (not just `pkill -f launch`); after a run, `pgrep -f minisgl` should be empty and
  `nvidia-smi` should show your GPU memory freed.
- Pick one **free** GPU (`nvidia-smi`); never assume.

---

## 7. Scoring & validity notes

- `baseline_throughput` is measured **in-situ** on the pristine seed at container start
  (warmup-excluded steady-state median) — never a hardcoded constant, never agent-writable.
- `OG = candidate / baseline`; `correctness` is a hard gate (a fast-but-broken candidate
  scores ERROR/0 — anti-hack).
- **Serving noise is large**: read the median + `throughput_ci95`, run repeats. On a small
  model (Qwen3-0.6B) the KV cache never saturates, so scheduler optimizations show no
  robust gain; serve a larger model (4B/8B) and pin `NUM_PAGES` for a fair A/B. The task
  wrapper and scoring are independent of this choice. Details:
  `moltbench/docs/mini-sglang-podman-delivery.md`.

---

## 8. Anti-hacking probes (Design-4 §8 / Design-5 §6)

Level-3 admission requires the verifier to be robust to reward hacking. Run each probe
against the image and confirm the expected outcome; results from our A800 run are
committed under `moltbench/docker/mini_sglang/evidence/`.

| Probe | How to inject | Expected outcome |
|---|---|---|
| **A — fake score file** | Agent writes a bogus `/workspace/repo/results.json` / `score.json` | Official eval **ignores** it: the score comes from a clean server restart measured by the hidden verifier, not any agent-written file. |
| **B — bypass the gate** | Make the candidate return errors / break the server | `correctness=FAIL/ERROR` → `score=0` (G gated by C). |
| **C — public-only solution** | (N/A for serving) | No hidden test set; throughput is measured on a fixed trace. Documented as N/A with rationale. |
| **D — fast but wrong** | Edit the scheduler so it is faster but drops/corrupts outputs (`num_error>0`) | `correctness=FAIL`, `score=0` — speed never beats correctness. |
| **E — timeout candidate** | Make the server hang | Bench `BENCH_TIMEOUT` / `SERVER_READY_TIMEOUT` fires, the process **group** is killed (no orphan), the attempt is recorded with an error, the run does **not** report success. |
| **F — tamper the verifier** | Modify `/eval/eval_runner.py` mid-run | `verifier_integrity.json.status=TAMPERED` → `results.json.run_valid=false`; audit report flags **RUN INVALID**. |

Each probe is a `MOLTBENCH_AGENT=none` run plus a small injected edit to `/workspace/repo`
(or `/eval` for F) before the official eval; inspect `results.json` + `audit_report.md`.
