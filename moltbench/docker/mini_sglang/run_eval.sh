#!/usr/bin/env bash
# /eval/run_eval — agent-facing feedback interface (one "attempt").
# Thin wrapper over the hidden MoltBench eval harness (fast mode).
#
# The agent runs `/eval/run_eval` after editing /workspace/repo. It restarts the
# server on the current code, measures throughput, and returns normalized JSON:
#   {attempt, stage_id, correct, score, metric{...}, wall_time_sec,
#    verifier_status, feedback, error}
#
# Attempt counting, budget enforcement, the in-situ baseline, and the official
# score all live in /eval/eval_runner.py (hidden from the agent workspace).
set -u

# Ensure the build/runtime toolchain is reachable (flashinfer JIT needs ninja+nvcc).
# The conda env is exposed at the stable generic path /opt/minisgl-env (image symlink).
if [ -d "/opt/minisgl-env/bin" ]; then
  export PATH="/opt/minisgl-env/bin:$PATH"
fi
if [ -d "/usr/local/cuda/bin" ]; then
  export PATH="/usr/local/cuda/bin:$PATH"
  export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
fi

PYBIN="${PYBIN:-/opt/minisgl-env/bin/python}"
exec "$PYBIN" /eval/eval_runner.py fast
