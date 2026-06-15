# QJL MoltBench Task

This is a Level 2 `optimization_only` task pending post-hardening GPU
revalidation. The episode has `bootstrap` and `improvement` stages; every
`/eval/run_eval` invocation is one `EvalAttempt`. Memory and drift metrics are
`N/A`.

The agent may edit only `qjl_kernel/csrc/*.cu`. The agent image contains no
benchmark, hidden inputs, official scorer, or baseline score file.
`/eval/run_eval` is a mounted client that submits the editable CUDA sources to
a host-managed verifier container. The verifier creates a clean workspace from
QJL commit `648b364`, applies only allowlisted files, rebuilds, and evaluates
host-generated per-episode seeds. Those seeds are absent from the agent task
and recorded only in the host-owned suitcase for reproducibility.

After the agent exits, the host copies the final CUDA sources and invokes
`official_eval` inside the verifier. The scorer reads only host-owned
`score.json`; agent-written `results.json` files are ignored.

## Build

```bash
podman build --runtime runc --network host \
  --build-arg TORCH_CUDA_ARCH_LIST=8.0 \
  -f moltbench/docker/Dockerfile.qjl-base \
  -t localhost/alphadiana:qjl-base .

podman build --runtime runc --network host \
  -f moltbench/docker/Dockerfile.qjl-verifier \
  -t localhost/alphadiana:qjl-verifier .

podman build --runtime runc --network host \
  -f moltbench/docker/Dockerfile.qjl-openclaw \
  -t localhost/alphadiana:qjl-openclaw .

podman build --runtime runc --network host \
  -f moltbench/docker/Dockerfile.qjl-opencode \
  -t localhost/alphadiana:qjl-opencode .

podman build --runtime runc --network host \
  -f moltbench/docker/Dockerfile.qjl-claude-code \
  -t localhost/alphadiana:qjl-claude-code .
```

Official baseline evidence currently covers NVIDIA A800 (`sm_80`) only. Build
with `TORCH_CUDA_ARCH_LIST=8.0` for leaderboard-compatible runs. Other
architectures require their own committed baseline evidence before use.
Pinned environment versions are recorded in `environment.lock.json`.

The locked correctness threshold is `0.52`. The June 8, 2026 clean A800
baseline over validation seeds 42/1234/2025 measured aggregate runtime
`3.486 ms` and maximum distortion `0.495494`. Official episodes use separate
host-generated seeds.

The agent requires network access to its configured model API. Retrieval is
therefore declared as allowed and must be controlled by deployment policy.
Attempt feedback uses seeds disjoint from official scoring. Official baseline,
best-candidate, and final evaluation run in a fresh container with no network.

## Run

```bash
python3 -m alphadiana.cli run \
  configs/examples/moltbench_qjl_openclaw_podman.yaml
```

Fast feedback and official scoring are separate. Attempt feedback is
budget-limited and abbreviated. Official scoring always performs another clean
rebuild after agent exit and is the sole source of leaderboard metrics.

OpenClaw, OpenCode, and Claude Code use the same task loader, so their prompt,
workspace, budgets, verifier, and artifact contract are identical.

For an infrastructure-only end-to-end check with no model call, use
`configs/examples/moltbench_qjl_smoke_podman.yaml`. The smoke launcher submits
the unchanged seed once, then the host performs the normal independent
official evaluation and suitcase finalization.
