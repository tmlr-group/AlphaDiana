#!/usr/bin/env bash
# QJL evaluation wrapper — placed at /eval/run_eval inside the container.
#
# Each invocation:
#   1. Counts rounds (enforces MOLTBENCH_MAX_ROUNDS)
#   2. Rebuilds CUDA extensions via pip install -e .
#   3. Runs bench_cuda.py with forwarded args
#   4. Writes /workspace/artifacts/latest_result.json
#   5. Writes /workspace/artifacts/results.json for MoltBench-compatible scoring
#   6. Prints JSON result to stdout
#
# Usage (from agent):
#   /eval/run_eval                        # default args
#   /eval/run_eval --key-quant-bits 128   # override QJL config

set -euo pipefail

COUNTER_FILE="/workspace/.bench_round_counter"
MAX_ROUNDS="${MOLTBENCH_MAX_ROUNDS:-5}"
REPO_DIR="/workspace/repo"
BENCH_SCRIPT="/eval/bench_cuda.py"
ARTIFACTS_DIR="/workspace/artifacts"

# ── Round counting ──
n=0
if [[ -f "$COUNTER_FILE" ]]; then
    n=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
fi
n=$((n + 1))
echo "$n" > "$COUNTER_FILE"

if [[ $n -gt $MAX_ROUNDS ]]; then
    echo "========================================" >&2
    echo "ROUND LIMIT REACHED ($((n - 1))/${MAX_ROUNDS})" >&2
    echo "No more benchmark runs allowed. Your optimization task is COMPLETE." >&2
    echo "========================================" >&2
    echo '{"runtime_ms": 0.0, "distortion": 1.0, "correctness_pass": false, "error": "round_limit"}'
    exit 1
fi

echo "[run_eval] Round $n/$MAX_ROUNDS" >&2

# ── Rebuild CUDA extensions ──
echo "[run_eval] Building CUDA extensions..." >&2
cd "$REPO_DIR/qjl_kernel"
python setup.py build_ext --inplace 2>&1 | tee /tmp/build.log | tail -5 >&2
echo "[run_eval] Build complete" >&2

cd "$REPO_DIR"

# ── Run benchmark ──
echo "[run_eval] Running bench_cuda.py $*" >&2
mkdir -p "$ARTIFACTS_DIR"

set +e
output=$(python3 "$BENCH_SCRIPT" "$@" 2>"$ARTIFACTS_DIR/bench_stderr.log")
bench_exit=$?
set -e

if [[ $bench_exit -ne 0 ]]; then
    echo "[run_eval] bench_cuda.py exited with code $bench_exit" >&2
    cat "$ARTIFACTS_DIR/bench_stderr.log" >&2 || true
fi

# ── Parse and save result ──
# bench_cuda.py prints a single JSON line to stdout
result_json=""
while IFS= read -r line; do
    if python3 -c "import json; json.loads('''$line''')" 2>/dev/null; then
        result_json="$line"
    fi
done <<< "$output"

if [[ -n "$result_json" ]]; then
    echo "$result_json" > "$ARTIFACTS_DIR/latest_result.json"
    echo "$result_json" > "$ARTIFACTS_DIR/results.json"
    echo "$result_json"
else
    fallback='{"runtime_ms": 0.0, "distortion": 1.0, "correctness_pass": false, "error": "no_json_output"}'
    echo "$fallback" > "$ARTIFACTS_DIR/latest_result.json"
    echo "$fallback" > "$ARTIFACTS_DIR/results.json"
    echo "$fallback"
    echo "[run_eval] WARNING: No JSON output from bench_cuda.py" >&2
    echo "[run_eval] Raw output:" >&2
    echo "$output" >&2
fi
