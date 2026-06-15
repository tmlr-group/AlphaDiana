#!/usr/bin/env bash
# MoltBench container entrypoint
#
# Expects:
#   /workspace/task/task.json — task configuration:
#     {"level": 1, "problem_id": 1, "max_rounds": 10, "backend": "triton"}
#
# Environment variables:
#   MOLTBENCH_AGENT — which agent to launch: "claude-code" | "openclaw" | "opencode" | "zeroclaw"
#   ANTHROPIC_API_KEY — for Claude Code
#   OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL_NAME — for OpenClaw / OpenCode / ZeroClaw
#   OPENCLAW_GATEWAY_TOKEN — gateway auth token (default: MOLTBENCH)
#   ZEROCLAW_TIMEOUT_SEC — max seconds for zeroclaw agent (default: 1200)
#   MAX_ROUNDS — override max_rounds from task.json (optional)
#
# Produces:
#   /workspace/artifacts/ — all outputs (see collect_artifacts)

set -euo pipefail

AUTOKERNEL_DIR="/workspace/autokernel"
DEFAULT_WORKDIR="$AUTOKERNEL_DIR"
WORKDIR="$DEFAULT_WORKDIR"
ARTIFACTS_DIR="/workspace/artifacts"
TASK_FILE="/workspace/task/task.json"
RUN_INVALID=0
RUN_INVALID_REASON=""

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

log() { echo "[MoltBench] $(date +%H:%M:%S) $*"; }

die() {
    RUN_INVALID=1
    RUN_INVALID_REASON="$*"
    log "FATAL: $*"
    exit 1
}

# Parse task.json
read_task() {
    if [[ ! -f "$TASK_FILE" ]]; then
        die "Task file not found: $TASK_FILE"
    fi
    BENCHMARK_ID=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('benchmark_id', 'autokernel'))")
    LEVEL=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('level', 0))")
    PROBLEM_ID=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('problem_id', 0))")
    BACKEND=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('backend','triton'))")
    SOURCE=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('source', 'hf'))")
    WORKDIR=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('workdir', '$DEFAULT_WORKDIR'))")
    PRIMARY_EDIT_FILE=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('primary_edit_file', 'kernel.py'))")
    BENCH_LOG_PATH=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('bench_log_path', 'run.log'))")
    SNAPSHOT_DIR=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('snapshot_dir', 'workspace'))")
    RESULT_PARSER=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('result_parser', 'bench_kb'))")
    ROUND_GUARD_TARGET=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('round_guard_target', ''))")
    RESULT_JSON_PATH=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('result_json_path', ''))")
    OFFICIAL_EVAL_EXTERNAL=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(str(t.get('official_eval_external', False)).lower())")
    SETUP_CMD=$(python3 - <<'PY' "$TASK_FILE"
import json
import shlex
import sys

task = json.load(open(sys.argv[1]))
cmd = task.get("setup_cmd")
sys.stdout.write(shlex.join(cmd) if cmd else "")
PY
)
    VERIFY_CMD=$(python3 - <<'PY' "$TASK_FILE"
import json
import shlex
import sys

task = json.load(open(sys.argv[1]))
cmd = task.get("verify_cmd")
sys.stdout.write(shlex.join(cmd) if cmd else "")
PY
)
    TASK_PROMPT=$(python3 - <<'PY' "$TASK_FILE"
import json
import sys

task = json.load(open(sys.argv[1]))
sys.stdout.write(task.get("prompt", ""))
PY
)
    TASK_MAX_ROUNDS=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t.get('max_rounds', 10))")
    MAX_ROUNDS="${MAX_ROUNDS:-$TASK_MAX_ROUNDS}"
    log "Task: benchmark=$BENCHMARK_ID level=$LEVEL problem=$PROBLEM_ID backend=$BACKEND source=$SOURCE max_rounds=$MAX_ROUNDS workdir=$WORKDIR"
}

# Set up KernelBench problem
setup_problem() {
    cd "$WORKDIR"
    if [[ -n "$SETUP_CMD" ]]; then
        log "Running setup command for $BENCHMARK_ID..."
        if ! bash -lc "$SETUP_CMD"; then
            die "Setup command failed: $SETUP_CMD"
        fi
    else
        log "Setting up KernelBench L${LEVEL}_P${PROBLEM_ID}..."
        if ! python3 kernelbench/bridge.py setup \
            --level "$LEVEL" \
            --problem "$PROBLEM_ID" \
            --backend "$BACKEND" \
            --source "$SOURCE"; then
            die "KernelBench setup failed for L${LEVEL}_P${PROBLEM_ID}"
        fi
    fi
    # Fix known super() bug: replace super(Model, self) with super()
    if [[ -f "$PRIMARY_EDIT_FILE" ]] && grep -q 'super(Model, self)' "$PRIMARY_EDIT_FILE" 2>/dev/null; then
        log "Fixing super() bug in generated $PRIMARY_EDIT_FILE"
        sed -i 's/super(Model, self)/super()/g' "$PRIMARY_EDIT_FILE"
    fi
    log "Problem setup complete"
}

install_round_guard() {
    if [[ "$OFFICIAL_EVAL_EXTERNAL" == "true" ]]; then
        log "External verifier controls EvalAttempt budget"
        return
    fi
    # Replace the eval script with a round-counting wrapper.
    # After MAX_ROUNDS benchmark invocations the wrapper refuses to run,
    # forcing the agent to stop regardless of its own planning.
    cd "$WORKDIR"
    local bench_py="${ROUND_GUARD_TARGET:-kernelbench/bench_kb.py}"
    local bench_dir
    bench_dir=$(dirname "$bench_py")
    local bench_name
    bench_name=$(basename "$bench_py")
    local impl_name="_${bench_name%.*}_impl.${bench_name##*.}"
    local impl_py="${bench_dir}/${impl_name}"

    if [[ ! -f "$bench_py" ]]; then
        log "WARNING: $bench_py not found, skipping round guard"
        return
    fi

    mv "$bench_py" "$impl_py"
    cat > "$bench_py" << GUARD
#!/usr/bin/env python3
"""Round-counting wrapper — transparently limits benchmark invocations."""
import os, sys, subprocess

_COUNTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.bench_round_counter')
_MAX = int(os.environ.get('MOLTBENCH_MAX_ROUNDS', '10'))

# Read & increment counter
try:
    n = int(open(_COUNTER).read().strip())
except Exception:
    n = 0
n += 1
with open(_COUNTER, 'w') as f:
    f.write(str(n))

if n > _MAX:
    print('=' * 60)
    print(f'ROUND LIMIT REACHED ({n - 1}/{_MAX})')
    print('No more benchmark runs allowed. Your optimization task is COMPLETE.')
    print('=' * 60)
    # Output greppable lines so partial parsing still works
    print('correctness: SKIP')
    print('speedup: 0.000x')
    sys.exit(1)

# Delegate to real implementation
_impl = os.path.join(os.path.dirname(os.path.abspath(__file__)), '${impl_name}')
os.execv(sys.executable, [sys.executable, _impl] + sys.argv[1:])
GUARD
    chmod +x "$bench_py"
    export MOLTBENCH_MAX_ROUNDS="$MAX_ROUNDS"
    log "Round guard installed: max $MAX_ROUNDS benchmark invocations"
}

init_task_repo() {
    cd "$WORKDIR"
    rm -rf .git
    git init
    git config user.email "moltbench@eval"
    git config user.name "MoltBench"
    git add -A
    git commit -m "initial: ${BENCHMARK_ID} task setup"
}

# Collect all artifacts from the workspace
collect_artifacts() {
    log "Collecting artifacts..."
    mkdir -p "$ARTIFACTS_DIR"

    cd "$WORKDIR"

    # Primary edited file
    if [[ -f "$PRIMARY_EDIT_FILE" ]]; then
        cp -f "$PRIMARY_EDIT_FILE" "$ARTIFACTS_DIR/" 2>/dev/null || true
    fi

    # Bench logs (if agent wrote run.log)
    mkdir -p "$ARTIFACTS_DIR/bench_logs"
    if [[ -f "$BENCH_LOG_PATH" ]]; then
        cp -f "$BENCH_LOG_PATH" "$ARTIFACTS_DIR/bench_logs/" 2>/dev/null || true
    fi

    # Workspace snapshot (benchmark-specific)
    if [[ -n "$SNAPSHOT_DIR" && -d "$SNAPSHOT_DIR" ]]; then
        cp -r "$SNAPSHOT_DIR" "$ARTIFACTS_DIR/workspace_snapshot" 2>/dev/null || true
    fi

    # Git log of primary edited file (tracks edit history)
    if git rev-parse --is-inside-work-tree &>/dev/null; then
        git log --oneline --all -- "$PRIMARY_EDIT_FILE" > "$ARTIFACTS_DIR/kernel_git_log.txt" 2>/dev/null || true
        # Export each committed version of the primary edited file
        mkdir -p "$ARTIFACTS_DIR/kernel_history"
        local i=1
        git log --reverse --format="%H" -- "$PRIMARY_EDIT_FILE" 2>/dev/null | while read -r sha; do
            git show "$sha:$PRIMARY_EDIT_FILE" > "$ARTIFACTS_DIR/kernel_history/round_$(printf '%02d' $i).py" 2>/dev/null || true
            i=$((i + 1))
        done
    fi

    # Agent trajectory sources. Concatenated session logs retain all records
    # without relying on whichever file `find` happens to return last.
    cp -f /root/.claude/projects/*/memory/*.md "$ARTIFACTS_DIR/" 2>/dev/null || true
    find /root/.claude/projects -name "*.jsonl" -type f -exec cat {} + \
        > "$ARTIFACTS_DIR/claude_sessions.jsonl" 2>/dev/null || true
    # OpenClaw session files may be in OPENCLAW_HOME or default location
    for oc_dir in /root/.openclaw /tmp/oc_home; do
        if [[ -d "$oc_dir" ]]; then
            find "$oc_dir" -name "*.jsonl" -type f -exec cat {} + \
                >> "$ARTIFACTS_DIR/openclaw_sessions.jsonl" 2>/dev/null || true
        fi
    done
    # ZeroClaw session files
    for zc_dir in "${HOME}/.zeroclaw"; do
        if [[ -d "$zc_dir" ]]; then
            find "$zc_dir" -name "*.jsonl" -exec cp {} "$ARTIFACTS_DIR/" \; 2>/dev/null || true
            cp "$zc_dir/workspace/memory/brain.db" "$ARTIFACTS_DIR/zeroclaw_memory.db" 2>/dev/null || true
            cp -r "$zc_dir/workspace/state" "$ARTIFACTS_DIR/zeroclaw_state" 2>/dev/null || true
        fi
    done

    if [[ "$OFFICIAL_EVAL_EXTERNAL" != "true" ]]; then
        run_final_verification
    else
        log "Official evaluation is host-controlled; no local score file produced"
    fi

    log "Artifacts collected at $ARTIFACTS_DIR"
    ls -la "$ARTIFACTS_DIR/"
}

# Run bench_kb.py one final time for independent scorer verification
run_final_verification() {
    log "Running final verification benchmark..."
    cd "$WORKDIR"
    local verify_cmd="$VERIFY_CMD"
    local verification_results_path="$ARTIFACTS_DIR/final_verification_results.json"
    # Bypass the round guard wrapper for final verification.
    # If the round guard created an _impl backup, use that instead.
    if [[ -n "$ROUND_GUARD_TARGET" ]]; then
        local guard_dir guard_name impl_name_final
        guard_dir=$(dirname "$ROUND_GUARD_TARGET")
        guard_name=$(basename "$ROUND_GUARD_TARGET")
        impl_name_final="_${guard_name%.*}_impl.${guard_name##*.}"
        if [[ -f "${guard_dir}/${impl_name_final}" ]]; then
            verify_cmd="${verify_cmd//$guard_name/$impl_name_final}"
        fi
    elif [[ -f "kernelbench/_bench_kb_impl.py" ]]; then
        verify_cmd="${verify_cmd//bench_kb.py/_bench_kb_impl.py}"
    fi
    if [[ -z "$verify_cmd" ]]; then
        verify_cmd="python3 kernelbench/bench_kb.py"
    fi

    # Use uv run so the bench executes in the project venv (Python 3.10)
    # matching the agent's environment. System Python 3.11 can segfault
    # when JIT-compiling complex Triton kernels.
    if command -v uv &>/dev/null && [[ -f "$WORKDIR/pyproject.toml" ]]; then
        verify_cmd="uv run ${verify_cmd}"
    fi

    set +e
    bash -lc "$verify_cmd" > "$ARTIFACTS_DIR/final_bench.log" 2>&1
    local verify_status=$?
    set -e
    if [[ $verify_status -ne 0 ]]; then
        log "Final verification command exited with status $verify_status; parsing any emitted metrics"
    fi

    if [[ -n "$RESULT_JSON_PATH" && -f "$RESULT_JSON_PATH" ]]; then
        cp -f "$RESULT_JSON_PATH" "$verification_results_path"
    elif [[ "$RESULT_PARSER" == "bench_kb" ]]; then
        # Parse results into structured JSON
        python3 -c "
import re, json, sys

log_path = '$ARTIFACTS_DIR/final_bench.log'
with open(log_path) as f:
    text = f.read()

results = {}
for line in text.splitlines():
    if line.startswith('correctness:'):
        results['correctness'] = line.split(':',1)[1].strip()
    elif line.startswith('speedup:'):
        val = line.split(':',1)[1].strip().rstrip('x')
        try: results['speedup'] = float(val)
        except: results['speedup'] = 0.0
    elif line.startswith('kernel_time_ms:'):
        try: results['kernel_time_ms'] = float(line.split(':',1)[1].strip())
        except: pass
    elif line.startswith('reference_time_ms:'):
        try: results['reference_time_ms'] = float(line.split(':',1)[1].strip())
        except: pass
    elif line.startswith('pytorch_time_ms:'):
        try:
            ref_time = float(line.split(':',1)[1].strip())
            results['reference_time_ms'] = ref_time
            results['pytorch_time_ms'] = ref_time
        except: pass

# Defaults
results.setdefault('correctness', 'FAIL')
results.setdefault('speedup', 0.0)
if 'reference_time_ms' in results and 'pytorch_time_ms' not in results:
    results['pytorch_time_ms'] = results['reference_time_ms']

with open('$verification_results_path', 'w') as f:
    json.dump(results, f, indent=2)
print(json.dumps(results, indent=2))
"
    elif [[ "$RESULT_PARSER" == "qjl" ]]; then
        # Parse QJL bench_cuda.py JSON output.
        python3 -c "
import json

log_path = '$ARTIFACTS_DIR/final_bench.log'
with open(log_path, encoding='utf-8', errors='replace') as f:
    text = f.read()

result = None
for line in reversed(text.strip().splitlines()):
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        continue
    if isinstance(obj, dict) and 'runtime_ms' in obj:
        result = obj
        break

if result is None:
    result = {
        'runtime_ms': 0.0,
        'distortion': 1.0,
        'correctness_pass': False,
        'error': 'parse_failed',
    }

with open('$verification_results_path', 'w') as f:
    json.dump(result, f, indent=2)
print(json.dumps(result, indent=2))
"
    elif [[ "$RESULT_PARSER" == "mini_sglang" ]]; then
        # Parse mini-SGLang run_eval output into MoltBench results format.
        BASELINE_THROUGHPUT_ENV="${BASELINE_THROUGHPUT:-5026.0}" python3 -c "
import json, os

log_path = '$ARTIFACTS_DIR/final_bench.log'
baseline = float(os.environ.get('BASELINE_THROUGHPUT_ENV', '5026.0'))
results = {}

try:
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('{'):
                data = json.loads(line)
                throughput = float(data.get('throughput_tok_per_s', 0))
                correct = data.get('correct', False)
                og = round(throughput / baseline, 4) if baseline > 0 else 0.0
                results = {
                    'correctness': 'PASS' if correct else 'FAIL',
                    'speedup': og,
                    'throughput_tok_per_s': throughput,
                    'baseline_throughput': baseline,
                    'og': og,
                }
                break
except Exception as e:
    results = {'correctness': 'ERROR', 'speedup': 0.0, 'error': str(e)}

if not results:
    results = {'correctness': 'ERROR', 'speedup': 0.0}

with open('$verification_results_path', 'w') as f:
    json.dump(results, f, indent=2)
print(json.dumps(results, indent=2))
"
    else
        log "WARNING: Unknown result parser '$RESULT_PARSER', defaulting to ERROR result"
        echo '{"correctness": "ERROR", "speedup": 0.0}' > "$verification_results_path"
    fi
    log "Final verification complete"

    if [[ "$RUN_INVALID" -eq 1 ]]; then
        INVALID_REASON_ENV="$RUN_INVALID_REASON" python3 - <<'PY'
import json
import os
from pathlib import Path

results = {
    "correctness": "INVALID",
    "speedup": 0.0,
}
reason = os.environ.get("INVALID_REASON_ENV", "").strip()
if reason:
    results["invalid_reason"] = reason

Path("/workspace/artifacts/results.json").write_text(
    json.dumps(results, indent=2),
    encoding="utf-8",
)
print(json.dumps(results, indent=2))
PY
        log "Recorded INVALID results.json due to agent failure"
    elif [[ -f "$verification_results_path" ]]; then
        cp -f "$verification_results_path" "$ARTIFACTS_DIR/results.json"
    fi
}

# ──────────────────────────────────────────────
# Agent launchers
# ──────────────────────────────────────────────

launch_claude_code() {
    log "Launching Claude Code agent..."
    cd "$WORKDIR"

    install_round_guard
    init_task_repo

    local prompt="$TASK_PROMPT"
    if [[ -z "$prompt" ]]; then
        prompt="Read kernelbench/program_kb.md for full optimization instructions.

Then optimize KernelBench problem Level ${LEVEL}, Problem ${PROBLEM_ID}.
The problem is already set up — kernel.py and workspace/kb_active/ are ready.
You have ${MAX_ROUNDS} optimization rounds. Backend: ${BACKEND}."
    fi

    # Claude blocks --dangerously-skip-permissions under root.
    local claude_args=(--print --max-turns "$MAX_ROUNDS")
    if [[ "$(id -u)" != "0" ]]; then
        claude_args+=(--dangerously-skip-permissions)
    fi

    # Run Claude Code in non-interactive structured-output mode.
    claude "${claude_args[@]}" --verbose --output-format stream-json "$prompt" \
        > "$ARTIFACTS_DIR/claude_output.jsonl" \
        2> "$ARTIFACTS_DIR/claude_stderr.log"
}

launch_openclaw() {
    log "Launching OpenClaw agent (gateway mode)..."
    cd "$WORKDIR"

    install_round_guard
    init_task_repo

    # ── Configure OpenClaw ──
    local gateway_token="${OPENCLAW_GATEWAY_TOKEN:-MOLTBENCH}"
    local agent_timeout_sec="${OPENCLAW_AGENT_TIMEOUT_SEC:-}"
    local chat_timeout_sec="${OPENCLAW_CHAT_MAX_TIME_SEC:-}"
    local gateway_port="${OPENCLAW_GATEWAY_PORT:-$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
PY
)}"
    local gateway_url="http://127.0.0.1:${gateway_port}"

    # Copy and configure openclaw.json (env vars are interpolated by OpenClaw at runtime)
    export OPENAI_API_KEY="${OPENAI_API_KEY:?OPENAI_API_KEY must be set}"
    export OPENAI_BASE_URL="${OPENAI_BASE_URL:?OPENAI_BASE_URL must be set}"
    export OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-deepseek-chat}"
    export OPENCLAW_GATEWAY_TOKEN="$gateway_token"
    export OPENCLAW_GATEWAY_PORT="$gateway_port"

    cp /workspace/openclaw.json.template "$WORKDIR/openclaw.json"
    export WORKDIR
    python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["WORKDIR"]) / "openclaw.json"
payload = json.loads(path.read_text(encoding="utf-8"))
payload.setdefault("gateway", {})["port"] = int(os.environ["OPENCLAW_GATEWAY_PORT"])
agent_timeout = os.environ.get("OPENCLAW_AGENT_TIMEOUT_SEC")
if agent_timeout:
    payload.setdefault("agents", {}).setdefault("defaults", {})["timeoutSeconds"] = int(agent_timeout)
path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
    export OPENCLAW_CONFIG_PATH="$WORKDIR/openclaw.json"
    export OPENCLAW_HOME="/tmp/oc_home"
    export OPENCLAW_BUNDLED_PLUGINS_DIR="/tmp/empty-bundled"
    mkdir -p "$OPENCLAW_HOME" "/tmp/empty-bundled"

    if [[ -n "$agent_timeout_sec" && -z "$chat_timeout_sec" ]]; then
        chat_timeout_sec=$((agent_timeout_sec + 60))
    fi
    chat_timeout_sec="${chat_timeout_sec:-3600}"

    log "OpenClaw config: model=$OPENAI_MODEL_NAME, baseURL=$OPENAI_BASE_URL, port=$gateway_port, agent_timeout=${agent_timeout_sec:-default}, chat_timeout=$chat_timeout_sec"

    # ── Start gateway ──
    log "Starting openclaw gateway on port $gateway_port..."
    nohup openclaw gateway >> "$ARTIFACTS_DIR/gateway.log" 2>&1 &
    local gw_pid=$!
    log "Gateway PID: $gw_pid"

    # Wait for gateway to be ready (poll /v1/models)
    local max_wait=60
    local waited=0
    local probe_code="000"
    while true; do
        probe_code=$(curl -sS -o /dev/null -w '%{http_code}' \
            "${gateway_url}/v1/models" \
            -H "Authorization: bearer ${gateway_token}" || true)
        if [[ "$probe_code" != "000" ]]; then
            break
        fi
        if ! kill -0 "$gw_pid" 2>/dev/null; then
            log "ERROR: Gateway process died. Log:"
            cat "$ARTIFACTS_DIR/gateway.log" 2>/dev/null || true
            die "OpenClaw gateway failed to start"
        fi
        if [[ $waited -ge $max_wait ]]; then
            log "ERROR: Gateway not ready after ${max_wait}s. Log:"
            tail -20 "$ARTIFACTS_DIR/gateway.log" 2>/dev/null || true
            die "OpenClaw gateway startup timeout"
        fi
        sleep 2
        waited=$((waited + 2))
        log "Waiting for gateway... (${waited}s)"
    done
    log "Gateway ready (${waited}s, probe_status=$probe_code)"

    # ── Build optimization prompt ──
    local prompt="$TASK_PROMPT"
    if [[ -z "$prompt" ]]; then
        prompt="Read kernelbench/program_kb.md for full optimization instructions.

Then optimize KernelBench problem Level ${LEVEL}, Problem ${PROBLEM_ID}.
The problem is already set up — kernel.py and workspace/kb_active/ are ready.
You have ${MAX_ROUNDS} optimization rounds. Backend: ${BACKEND}."
    fi

    # ── Send task to gateway via /v1/chat/completions ──
    log "Sending optimization task to OpenClaw gateway..."
    local request_body
    export MOLTBENCH_TASK_PROMPT="$prompt"
    request_body=$(python3 - <<'PY'
import json
import os

payload = {
    "model": "openclaw",
    "messages": [{"role": "user", "content": os.environ["MOLTBENCH_TASK_PROMPT"]}],
    "stream": True,
    "max_tokens": 65536,
}
print(json.dumps(payload))
PY
)
    unset MOLTBENCH_TASK_PROMPT

    # Stream SSE response, collect full output
    local curl_status=0
    set +e
    curl -sS --no-buffer \
        --max-time "$chat_timeout_sec" \
        "${gateway_url}/v1/chat/completions" \
        -H "Authorization: bearer ${gateway_token}" \
        -H "Content-Type: application/json" \
        -d "$request_body" \
        > "$ARTIFACTS_DIR/openclaw_sse_raw.jsonl" 2>&1
    curl_status=$?
    set -e
    if [[ $curl_status -ne 0 ]]; then
        die "OpenClaw chat request failed with curl status $curl_status"
    fi

    # Parse SSE stream into readable output
    python3 -c "
import json, sys

raw_path = '$ARTIFACTS_DIR/openclaw_sse_raw.jsonl'
out_path = '$ARTIFACTS_DIR/openclaw_output.jsonl'

content_parts = []
events = []

with open(raw_path) as f:
    for line in f:
        line = line.strip()
        if not line or line == 'data: [DONE]':
            continue
        if line.startswith('data: '):
            line = line[6:]
        try:
            obj = json.loads(line)
            events.append(obj)
            # Extract content from streaming chunks
            for choice in obj.get('choices', []):
                delta = choice.get('delta', {})
                if 'content' in delta and delta['content']:
                    content_parts.append(delta['content'])
        except (json.JSONDecodeError, ValueError):
            pass

# Write parsed events
with open(out_path, 'w') as f:
    for e in events:
        f.write(json.dumps(e) + '\n')

# Write final content
final_content = ''.join(content_parts)
with open('$ARTIFACTS_DIR/openclaw_response.txt', 'w') as f:
    f.write(final_content)

print(f'Parsed {len(events)} SSE events, {len(final_content)} chars of content')
" || log "WARNING: Failed to parse SSE output"

    local openclaw_event_count
    openclaw_event_count=$(ARTIFACTS_DIR_ENV="$ARTIFACTS_DIR" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["ARTIFACTS_DIR_ENV"]) / "openclaw_output.jsonl"
if not path.exists():
    print(0)
else:
    count = sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    print(count)
PY
)
    if [[ "${openclaw_event_count:-0}" -eq 0 ]]; then
        log "ERROR: OpenClaw produced no SSE events; likely provider/auth failure"
        tail -20 "$ARTIFACTS_DIR/gateway.log" 2>/dev/null || true
        die "OpenClaw agent produced no events"
    fi

    # ── Collect OpenClaw session trajectory ──
    if [[ -d "$OPENCLAW_HOME" ]]; then
        find "$OPENCLAW_HOME" -name "*.jsonl" -type f -exec cat {} + \
            > "$ARTIFACTS_DIR/openclaw_sessions.jsonl" 2>/dev/null || true
    fi

    # ── Shutdown gateway ──
    if kill -0 "$gw_pid" 2>/dev/null; then
        log "Stopping gateway (PID $gw_pid)..."
        kill "$gw_pid" 2>/dev/null || true
        wait "$gw_pid" 2>/dev/null || true
    fi

    log "OpenClaw agent finished"
}

launch_opencode() {
    log "Launching OpenCode agent (direct CLI mode)..."
    cd "$WORKDIR"

    install_round_guard
    init_task_repo

    # ── Configure OpenCode ──
    export OPENAI_API_KEY="${OPENAI_API_KEY:?OPENAI_API_KEY must be set}"
    export OPENAI_BASE_URL="${OPENAI_BASE_URL:?OPENAI_BASE_URL must be set}"
    export OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-deepseek-chat}"

    local opencode_timeout="${OPENCODE_TIMEOUT_SEC:-1200}"

    # Generate opencode.json from template
    python3 - <<'PY'
import json
import os
from pathlib import Path

template = Path("/workspace/opencode.json.template")
target = Path(os.environ.get("WORKDIR", "/workspace/autokernel")) / "opencode.json"

config = json.loads(template.read_text(encoding="utf-8"))

api_key = os.environ["OPENAI_API_KEY"]
base_url = os.environ["OPENAI_BASE_URL"]
model_name = os.environ["OPENAI_MODEL_NAME"]

config["provider"]["custom"]["options"]["apiKey"] = api_key
config["provider"]["custom"]["options"]["baseURL"] = base_url
config["provider"]["custom"]["models"] = {
    model_name: {"name": model_name, "tool_call": True}
}
config["model"] = f"custom/{model_name}"

target.write_text(json.dumps(config, indent=2), encoding="utf-8")
print(f"OpenCode config written: model=custom/{model_name}, baseURL={base_url}")
PY

    log "OpenCode config: model=$OPENAI_MODEL_NAME, baseURL=$OPENAI_BASE_URL, timeout=${opencode_timeout}s"

    # ── Build prompt ──
    local prompt="$TASK_PROMPT"
    if [[ -z "$prompt" ]]; then
        prompt="Read kernelbench/program_kb.md for full optimization instructions.

Then optimize KernelBench problem Level ${LEVEL}, Problem ${PROBLEM_ID}.
The problem is already set up — kernel.py and workspace/kb_active/ are ready.
You have ${MAX_ROUNDS} optimization rounds. Backend: ${BACKEND}."
    fi

    # ── Run OpenCode ──
    log "Running opencode..."
    set +e
    timeout "${opencode_timeout}" opencode run \
        --format json \
        "$prompt" \
        > "$ARTIFACTS_DIR/opencode_output.jsonl" 2>"$ARTIFACTS_DIR/opencode_stderr.log"
    local opencode_status=$?
    set -e

    if [[ $opencode_status -ne 0 ]]; then
        die "OpenCode exited with status $opencode_status"
    fi

    local opencode_error_check_status=0
    local opencode_error_msg=""
    set +e
    opencode_error_msg=$(ARTIFACTS_DIR_ENV="$ARTIFACTS_DIR" python3 - <<'PY' 2>&1
import json
import os
from pathlib import Path

path = Path(os.environ["ARTIFACTS_DIR_ENV"]) / "opencode_output.jsonl"
if not path.exists():
    print("OpenCode produced no output")
    raise SystemExit(1)

records = [json.loads(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
if not records:
    print("OpenCode produced no JSON records")
    raise SystemExit(1)

for record in records:
    if record.get("type") == "error":
        error = record.get("error", {})
        data = error.get("data", {}) if isinstance(error, dict) else {}
        message = data.get("message") or error.get("name") or "unknown error"
        print(message)
        raise SystemExit(1)

print("")
PY
)
    opencode_error_check_status=$?
    set -e
    if [[ $opencode_error_check_status -ne 0 ]]; then
        die "OpenCode agent error: ${opencode_error_msg:-unknown error}"
    fi

    # ── Collect OpenCode session files ──
    if [[ -d "$HOME/.opencode" ]]; then
        find "$HOME/.opencode" -name "*.jsonl" -type f -exec cat {} + \
            > "$ARTIFACTS_DIR/opencode_sessions.jsonl" 2>/dev/null || true
    fi

    log "OpenCode agent finished"
}

launch_zeroclaw() {
    log "Launching ZeroClaw agent (CLI mode)..."
    cd "$WORKDIR"

    install_round_guard
    init_task_repo

    # ── Configure ZeroClaw ──
    export OPENAI_API_KEY="${OPENAI_API_KEY:?OPENAI_API_KEY must be set}"
    export OPENAI_BASE_URL="${OPENAI_BASE_URL:?OPENAI_BASE_URL must be set}"
    export OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-deepseek-chat}"

    local zeroclaw_timeout="${ZEROCLAW_TIMEOUT_SEC:-1200}"
    local zeroclaw_provider="openai"
    local zeroclaw_api_base="${OPENAI_BASE_URL%/}"
    if [[ "${zeroclaw_api_base}" == *"openrouter"* ]]; then
        zeroclaw_provider="openrouter"
    elif [[ -n "${zeroclaw_api_base}" && "${zeroclaw_api_base}" != "https://api.openai.com" && "${zeroclaw_api_base}" != "https://api.openai.com/v1" ]]; then
        zeroclaw_provider="custom:${zeroclaw_api_base}"
    fi

    # ZEROCLAW_API_KEY is highest-priority env var for custom: providers
    export ZEROCLAW_API_KEY="${OPENAI_API_KEY}"
    export OPENROUTER_API_KEY="${OPENAI_API_KEY}"
    export ZEROCLAW_PROVIDER="${zeroclaw_provider}"
    mkdir -p "${HOME}/.zeroclaw"

    # ZeroClaw tools operate inside ~/.zeroclaw/workspace/.
    # Symlink it to $WORKDIR so the agent can access task files
    # (kernel.py, bench_kb.py, etc.)
    ln -sfn "$WORKDIR" "${HOME}/.zeroclaw/workspace"

    cat > "${HOME}/.zeroclaw/config.toml" << TOMLEOF
default_provider = "${zeroclaw_provider}"
default_model = "${OPENAI_MODEL_NAME}"
default_temperature = 0.7
model_routes = []
embedding_routes = []

[model_providers]

[provider]

[observability]
backend = "none"
runtime_trace_mode = "none"
runtime_trace_path = "state/runtime-trace.jsonl"
runtime_trace_max_entries = 200

[autonomy]
level = "full"
workspace_only = false
allowed_commands = ["git", "npm", "cargo", "ls", "cat", "grep", "find", "echo", "pwd", "wc", "head", "tail", "date", "python", "python3", "bash", "sh", "sed", "awk", "mkdir", "mv", "cp", "rm"]
forbidden_paths = ["/etc", "/usr", "/bin", "/sbin", "/lib", "/opt", "/boot", "/dev", "/proc", "/sys"]
max_actions_per_hour = 200
max_cost_per_day_cents = 10000

[agent]
max_tool_iterations = 100
TOMLEOF
    chmod 600 "${HOME}/.zeroclaw/config.toml"

    log "ZeroClaw config: model=${OPENAI_MODEL_NAME}, baseURL=${OPENAI_BASE_URL}, timeout=${zeroclaw_timeout}s"

    # ── Build prompt ──
    local prompt="$TASK_PROMPT"
    if [[ -z "$prompt" ]]; then
        prompt="Read kernelbench/program_kb.md for full optimization instructions.

Then optimize KernelBench problem Level ${LEVEL}, Problem ${PROBLEM_ID}.
The problem is already set up — kernel.py and workspace/kb_active/ are ready.
You have ${MAX_ROUNDS} optimization rounds. Backend: ${BACKEND}."
    fi

    # ── Run ZeroClaw agent ──
    log "Running zeroclaw agent..."
    set +e
    timeout "${zeroclaw_timeout}" zeroclaw agent \
        -m "$prompt" \
        > "$ARTIFACTS_DIR/zeroclaw_output.txt" 2>"$ARTIFACTS_DIR/zeroclaw_stderr.log"
    local zeroclaw_status=$?
    set -e

    if [[ $zeroclaw_status -eq 124 ]]; then
        die "ZeroClaw agent timed out after ${zeroclaw_timeout}s"
    elif [[ $zeroclaw_status -ne 0 ]]; then
        log "ZeroClaw exited with status $zeroclaw_status"
        tail -20 "$ARTIFACTS_DIR/zeroclaw_stderr.log" 2>/dev/null || true
        die "ZeroClaw agent failed with exit code $zeroclaw_status"
    fi

    # Check for error messages in stdout (ZeroClaw may exit 0 on some errors)
    if grep -qi "^Error:" "$ARTIFACTS_DIR/zeroclaw_output.txt" 2>/dev/null; then
        log "WARNING: ZeroClaw output contains error messages"
        grep -i "^Error:" "$ARTIFACTS_DIR/zeroclaw_output.txt" | head -5
    fi

    if [[ ! -s "$ARTIFACTS_DIR/zeroclaw_output.txt" ]]; then
        log "ERROR: ZeroClaw produced no output"
        tail -20 "$ARTIFACTS_DIR/zeroclaw_stderr.log" 2>/dev/null || true
        die "ZeroClaw agent produced no output"
    fi

    log "ZeroClaw agent finished ($(wc -c < "$ARTIFACTS_DIR/zeroclaw_output.txt") bytes output)"
}

launch_smoke() {
    log "Launching deterministic verifier smoke client..."
    cd "$WORKDIR"
    init_task_repo
    /eval/run_eval > "$ARTIFACTS_DIR/smoke_run_eval.json" \
        2> "$ARTIFACTS_DIR/smoke_run_eval.err"
    log "Verifier smoke client finished"
}

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

trap collect_artifacts EXIT

log "=== MoltBench Task Runner ==="
AGENT="${MOLTBENCH_AGENT:-claude-code}"
log "Agent: $AGENT"

read_task
setup_problem

case "$AGENT" in
    claude-code)
        launch_claude_code
        ;;
    openclaw)
        launch_openclaw
        ;;
    opencode)
        launch_opencode
        ;;
    zeroclaw)
        launch_zeroclaw
        ;;
    smoke)
        launch_smoke
        ;;
    *)
        die "Unknown agent: $AGENT"
        ;;
esac

log "Agent finished. Artifacts will be collected by EXIT trap."
