# mini-SGLang on MoltBench via Podman + AlphaDiana

The **documentation hub** for the mini-SGLang serving-throughput optimization task in
MoltBench/AlphaDiana. Start here to *understand* the task; follow
[mini-sglang-reproduce.md](./mini-sglang-reproduce.md) to *run/reproduce* it.

- **Understand** → this file (what it is, architecture, design rationale, file map)
- **Run / reproduce** → [mini-sglang-reproduce.md](./mini-sglang-reproduce.md)
- **What can be optimized** → [mini-sglang-optimization-catalog.md](./mini-sglang-optimization-catalog.md)
- **Hand-off / delivery image** → [mini-sglang-podman-delivery.md](./mini-sglang-podman-delivery.md)

---

## 1. What the task is

A long-horizon, verifiable **project-evolution** task for evaluating agent harnesses.
The agent is given the real **mini-SGLang** inference engine and must edit it
(`/workspace/repo/python/minisgl/`) to **maximize serving throughput** (tokens/sec)
without breaking correctness. It runs under **Podman** (or Docker) through AlphaDiana.

**Four agent harnesses — OpenClaw, OpenCode, ZeroClaw, Claude Code — run the *same*
task** via one agent-agnostic verifier contract, selected at runtime by
`MOLTBENCH_AGENT`, all driven by the **same** OpenRouter model. So the comparison
isolates the *harness*, not the model. Targets MoltBench admission **Level 3**
(optimization-only: C/G/K defined; stability/memory stages = N/A).

Each `/eval/run_eval` call is one **attempt** (not "round"): it restarts the server on
the agent's current code and measures **warmup-excluded steady-state** throughput.
Score = optimization gain `OG = candidate_throughput / baseline_throughput`, where the
baseline is measured **in-situ** on the pristine seed at container start — never a
hardcoded constant, never agent-writable (see §5).

## 2. Architecture

```
python -m alphadiana.cli run configs/examples/moltbench_minisglang_easy.yaml
  └─ MoltBenchDockerAgent  (container_engine: docker|podman, gpu_mode: cdi|nvidia-runtime)
       └─ podman/docker run  <image>   (GPU inject + model/cuda[/env] mounts, --network host)
            └─ /workspace/run_minisgl.sh  (container entrypoint)
                 ├─ 1) eval_runner.py baseline   → in-situ baseline → hidden /eval/state/baseline.json
                 ├─ 2) manifest.json             → reproducibility record (versions, GPU, seed)
                 ├─ 3) launch_<agent>_agent      → edits /workspace/repo, calls /eval/run_eval (fast)
                 ├─ 4) pkill stray servers        → defensive cleanup before official
                 ├─ 5) eval_runner.py official     → results.json (clean restart, scored)
                 └─ 6) collect artifacts + audit_report.md
  └─ MoltBenchScorer  → correctness gate (C) + OG (G)
```

- **Hidden verifier**: `/eval/eval_runner.py` + `bench_serving.py` own baseline / attempts /
  official scoring and live *outside* the agent's `/workspace/repo`. The agent only ever
  runs `/eval/run_eval`. Official OG comes from an independent clean restart, never an
  agent-written file (anti-hack).
- **Agent-agnostic contract**: an agent just (a) reads the task prompt, (b) edits
  `/workspace/repo`, (c) runs `/eval/run_eval`. Adding a harness = launch it + feed it the
  prompt; the task/budget/scoring are unchanged. The prompt has a single source
  (`call_gateway.py --emit-prompt`), so all four harnesses get byte-identical instructions.

## 3. Pluggable — change the task without touching code or rebuilding

Everything below is set via the config `env:` / `volume_mounts:` / `gpus:` (or `-e` at
`podman run`). Nothing is hardcoded:

| Knob | How |
|---|---|
| **Optimized model** | `MODEL_PATH=<abs dir>` or alias `MODEL=qwen3-0.6b\|1.7b\|4b\|8b` resolved under `MODELS_HUB` |
| **Prompt model name** | `SERVED_MODEL_NAME` (else derived from MODEL_PATH); distinct from `OPENAI_MODEL_NAME` (the agent's LLM) |
| **GPU** | `gpus: "<id>"` + `gpu_mode` (cdi for podman≥4.1 / docker, nvidia-runtime for podman 3.4.x) |
| **KV pool / scheduler** | `NUM_PAGES`, `MAX_RUNNING_REQUESTS` (pin for fair cross-model/GPU A/B) |
| **Workload / budget** | `EVAL_NUM_PROMPTS`, `EVAL_INPUT_LEN`, `EVAL_OUTPUT_LEN`, `MEMORY_RATIO`, `max_attempts` |

The conda env (torch/flashinfer) is exposed inside the image at a stable generic path
`/opt/minisgl-env` (a symlink to the real path, set via `--build-arg MINISGL_ENV=…`), so
the committed code/configs reference only `/opt/minisgl-env`.

## 4. Key files

| File | Role |
|---|---|
| `alphadiana/benchmarks/moltbench/harness.py` | `MoltBenchDockerAgent`: docker/podman, GPU inject, mounts |
| `alphadiana/benchmarks/moltbench/benchmark.py` | `task: mini_sglang` loader (builds task.json) |
| `alphadiana/benchmarks/moltbench/scorer.py` | correctness gate + OG scoring |
| `moltbench/docker/mini_sglang/run_minisgl.sh` | container entrypoint (baseline→agent→official→audit) |
| `moltbench/docker/mini_sglang/eval_runner.py` | hidden verifier: baseline/fast/official, attempts, OG, CI, cost |
| `moltbench/docker/mini_sglang/bench_serving.py` | concurrent load gen, warmup exclusion, steady-state median + 95% CI |
| `moltbench/docker/mini_sglang/call_gateway.py` | task prompt (single source) + gateway delivery |
| `moltbench/docker/Dockerfile.mini-sglang-*` | layered image build |
| `moltbench/validate_task.py` | admission validator (static package + post-run artifacts) |
| `configs/examples/moltbench_minisglang_{easy,hard}.yaml` | example runs (paths labelled "replace with yours") |
| `tests/test_moltbench_podman.py` | unit tests (engine selection, CDI mapping, argv, aggregation) |

## 5. Measurement integrity & anti-hack (why the design is what it is)

- **In-situ baseline (mandatory).** Throughput swings ~0.6k (cold) → 14k → 20k tok/s on a
  small model purely from warmup; a constant baseline makes OG meaningless. Baseline and
  candidate are measured under identical warmup-excluded steady-state on the same GPU/run.
- **Warmup-excluded steady-state median + 95% CI** (`bench_serving.py`) — serving noise is
  large; report the interval, not a single number.
- **Correctness is a hard gate**: a fast-but-broken candidate scores ERROR/0.
- **C/G/K vector**: correctness, optimization gain, and cost (attempts + wall-time) are
  recorded; serving artifacts (`throughput_metrics.json` / `latency_metrics.json` /
  `warmup_policy.json`) + `audit_report.md` make every number auditable.

## 6. Task-validity note (read before drawing conclusions)

The reference optimization (mini-sglang#97, "Better Estimation Policy") shows **no robust
gain** on Qwen3-0.6B *or* Qwen3-4B: a rigorous interleaved dual-server A/B (pinned KV pool,
8 rounds, 95% CI) on 4B gave OG median 1.0006 ±0.045 (CI spans 1.0). A small model never
saturates the KV cache, so scheduler/admission optimizations have nothing to bite on; even
4B at heavy saturation showed no robust effect. **The harness/measurement is correct
regardless** — the task is a valid *harness comparator* (the 4-agent matrix differentiates
clearly). To give the task a measurable *reference* headroom, use a larger model (8B/14B),
a different optimization (e.g. sampling fusion / cudaStreamSync removal), or a different
metric. This is a task-design choice, independent of the implementation.

## 7. Status

Complete: podman/docker engine switch; GPU inject (cdi + nvidia-runtime); agent-agnostic
eval contract (attempts, normalized JSON, in-situ baseline, warmup-excluded steady-state +
CI, cost); all four harnesses end-to-end; manifest + audit + serving metrics; admission
validator; pluggable model/GPU/KV; portable image (env via `/opt/minisgl-env`); unit tests;
reproduction guide. See [mini-sglang-reproduce.md](./mini-sglang-reproduce.md).
