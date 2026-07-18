#!/usr/bin/env bash
# MoltBench mini-SGLang container entrypoint.
#
# Flow:  in-situ baseline  ->  agent optimizes /workspace/repo  ->  official eval.
# Reads  /workspace/task/task.json   ;  writes /workspace/artifacts/{results.json,...}.
#
# MOLTBENCH_AGENT: openclaw (default) | none (baseline+official only, for smoke tests)
# The eval harness (/eval/eval_runner.py) owns baseline/attempts/official scoring and
# is hidden from the agent. The agent only ever runs /eval/run_eval.
set -u

ART=/workspace/artifacts; mkdir -p "$ART"
TASK=/workspace/task/task.json
log() { echo "[run_minisgl] $(date +%H:%M:%S) $*"; }

# ── toolchain: conda env (torch/flashinfer/ninja) + CUDA nvcc ──
# The env is on PATH already (image ENV via /opt/minisgl-env symlink); we just add cuda.
# PYBIN comes from the image ENV; fall back to the stable env path, then python3.
export PATH="/usr/local/cuda/bin:${MINISGL_ENV_BIN:-/opt/minisgl-env/bin}:$PATH"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
export PYBIN="${PYBIN:-/opt/minisgl-env/bin/python}"
export REPO_DIR="${REPO_DIR:-/workspace/repo}"
export PYTHONPATH_REPO="${PYTHONPATH_REPO:-/workspace/repo/python}"
# Model is pluggable, NOTHING hardcoded. Supply ONE of:
#   - MODEL_PATH=<absolute path to a HF/modelscope model dir>  (wins if set), or
#   - MODEL=<qwen3-0.6b|qwen3-1.7b|qwen3-4b|qwen3-8b>  alias resolved under MODELS_HUB.
# MODELS_HUB is the HF cache "hub" dir (e.g. /data0/models/hub on our A800 cluster).
export MODELS_HUB="${MODELS_HUB:-/data0/models/hub}"
resolve_model() {
  local hub="$MODELS_HUB" name snaps snap
  case "${1:-}" in
    qwen3-0.6b|qwen3-0_6b) name="Qwen3-0.6B" ;;
    qwen3-1.7b)            name="Qwen3-1.7B" ;;
    qwen3-4b)              name="Qwen3-4B" ;;
    qwen3-8b)              name="Qwen3-8B" ;;
    *) log "ERROR: unknown MODEL alias '${1:-}' (use qwen3-0.6b|1.7b|4b|8b, or set MODEL_PATH)."; return 1 ;;
  esac
  snaps="$hub/models--Qwen--$name/snapshots"
  if [ ! -d "$snaps" ]; then
    log "ERROR: MODEL=$1 -> '$snaps' not found. Is MODELS_HUB ($hub) the right HF cache 'hub' dir, and is $name downloaded?"; return 1
  fi
  snap="$(ls "$snaps" 2>/dev/null | head -1)"
  if [ -z "$snap" ] || [ ! -d "$snaps/$snap" ]; then
    log "ERROR: MODEL=$1 -> no snapshot under '$snaps'."; return 1
  fi
  echo "$snaps/$snap"
}
if [ -z "${MODEL_PATH:-}" ] && [ -n "${MODEL:-}" ]; then MODEL_PATH="$(resolve_model "$MODEL")" || exit 2; fi
if [ -z "${MODEL_PATH:-}" ]; then
  log "ERROR: no model specified. Set -e MODEL_PATH=<path> or -e MODEL=qwen3-4b (with MODELS_HUB)."; exit 2
fi
if [ ! -d "$MODEL_PATH" ]; then
  log "ERROR: MODEL_PATH '$MODEL_PATH' is not a directory."; exit 2
fi
export MODEL_PATH
# Display name shown in the agent prompt (pluggable, single source). If unset, the
# prompt builder derives it from MODEL_PATH. Set it from the MODEL alias when given.
if [ -z "${SERVED_MODEL_NAME:-}" ] && [ -n "${MODEL:-}" ]; then SERVED_MODEL_NAME="$MODEL"; fi
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-}"
export MINISGL_PORT="${MINISGL_PORT:-31000}"
export MEMORY_RATIO="${MEMORY_RATIO:-0.6}"
export STATE_DIR="${STATE_DIR:-/eval/state}"
export ARTIFACTS_DIR="$ART"
export BENCH="${BENCH:-/eval/bench_serving.py}"
mkdir -p "$STATE_DIR"

read_json() { python3 -c "import json;print(json.load(open('$TASK')).get('$1','$2'))" 2>/dev/null || echo "$2"; }
DIFFICULTY=$(read_json difficulty easy)
MAX_ATTEMPTS=$(read_json max_attempts "$(read_json max_rounds 3)")   # prefer max_attempts; fall back to legacy max_rounds
BASELINE_HINT=$(read_json baseline_throughput 4813.0)
export MAX_ATTEMPTS
AGENT="${MOLTBENCH_AGENT:-openclaw}"
log "task: difficulty=$DIFFICULTY max_attempts=$MAX_ATTEMPTS agent=$AGENT gpu_ratio=$MEMORY_RATIO"

launch_openclaw_agent() {
  log "starting OpenClaw agent (gateway mode)..."
  : "${OPENAI_API_KEY:?OPENAI_API_KEY must be set for the agent}"
  : "${OPENAI_BASE_URL:?OPENAI_BASE_URL must be set for the agent}"
  local model="${OPENAI_MODEL_NAME:-qwen/qwen3-235b-a22b-2507}"
  local token="${OPENCLAW_GATEWAY_TOKEN:-MOLTBENCH}"
  local gport; gport=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
  local gurl="http://127.0.0.1:${gport}"
  export OPENCLAW_HOME=/tmp/oc_home OPENCLAW_BUNDLED_PLUGINS_DIR=/tmp/oc_empty
  mkdir -p "$OPENCLAW_HOME" "$OPENCLAW_BUNDLED_PLUGINS_DIR"
  # Render the OpenClaw gateway config from a template (consistent with the other
  # agents / KernelBench, vs an inline heredoc). python substitution is used so API
  # keys with shell/JSON-special chars can't corrupt the config.
  local tmpl="${OPENCLAW_TEMPLATE:-/eval/openclaw_minisgl.json.template}"
  OC_BASE="$OPENAI_BASE_URL" OC_KEY="$OPENAI_API_KEY" OC_MODEL="$model" OC_TOKEN="$token" \
  OC_TIMEOUT="${OPENCLAW_AGENT_TIMEOUT_SEC:-3000}" OC_GPORT="$gport" OC_TMPL="$tmpl" \
  python3 - <<'PY'
import json, os
t = open(os.environ["OC_TMPL"]).read()
for k, v in {"__OPENAI_BASE_URL__": os.environ["OC_BASE"], "__OPENAI_API_KEY__": os.environ["OC_KEY"],
             "__MODEL__": os.environ["OC_MODEL"], "__TOKEN__": os.environ["OC_TOKEN"],
             "__AGENT_TIMEOUT__": os.environ["OC_TIMEOUT"], "__GPORT__": os.environ["OC_GPORT"]}.items():
    t = t.replace(k, v)
json.loads(t)  # fail loudly if the rendered config isn't valid JSON
open("/tmp/openclaw.json", "w").write(t)
PY
  export OPENCLAW_CONFIG_PATH=/tmp/openclaw.json
  setsid openclaw gateway >> "$ART/gateway.log" 2>&1 &
  local gw=$! gwpgid; gwpgid=$(ps -o pgid= -p $gw | tr -d ' ')
  local ok=0
  for i in $(seq 1 60); do
    [ "$(curl -sS -o /dev/null -w '%{http_code}' "${gurl}/v1/models" -H "Authorization: bearer ${token}" 2>/dev/null)" != "000" ] && { ok=1; break; }
    kill -0 $gw 2>/dev/null || { log "gateway died:"; tail -15 "$ART/gateway.log"; return 1; }
    sleep 2
  done
  [ $ok -eq 1 ] || { log "gateway not ready:"; tail -15 "$ART/gateway.log"; kill -- -$gwpgid 2>/dev/null; return 1; }
  log "gateway ready on $gport"
  local baseline; baseline=$(python3 -c "import json;print(json.load(open('$STATE_DIR/baseline.json'))['baseline_throughput'])" 2>/dev/null || echo "$BASELINE_HINT")
  local chat_timeout="${OPENCLAW_CHAT_MAX_TIME_SEC:-3300}"
  log "delivering task to agent (baseline=${baseline} difficulty=$DIFFICULTY attempts=$MAX_ATTEMPTS)..."
  "$PYBIN" /eval/call_gateway.py --url "$gurl" --token "$token" --baseline "$baseline" \
    --max-rounds "$MAX_ATTEMPTS" --difficulty "$DIFFICULTY" --timeout "$chat_timeout" \
    --output "$ART/agent_response.json" >> "$ART/call_gateway.log" 2>&1 || log "WARNING: call_gateway returned nonzero"
  kill -- -$gwpgid 2>/dev/null || true; sleep 2; kill -9 -- -$gwpgid 2>/dev/null || true
  log "agent finished."
}

# ── OpenCode: OpenAI-compatible CLI agent → OpenRouter directly (no gateway) ──
launch_opencode_agent() {
  log "starting OpenCode agent (CLI mode)..."
  : "${OPENAI_API_KEY:?OPENAI_API_KEY must be set for the agent}"
  : "${OPENAI_BASE_URL:?OPENAI_BASE_URL must be set for the agent}"
  local model="${OPENAI_MODEL_NAME:-qwen/qwen3-235b-a22b-2507}"
  local baseline; baseline=$(python3 -c "import json;print(json.load(open('$STATE_DIR/baseline.json'))['baseline_throughput'])" 2>/dev/null || echo "$BASELINE_HINT")
  # opencode.json: custom OpenAI-compatible provider pointing at OpenRouter
  OPENCODE_MODEL="$model" "$PYBIN" - "$REPO_DIR/opencode.json" <<'PY'
import json, os, sys
cfg = json.load(open("/eval/opencode.json.template"))
cfg["provider"]["custom"]["options"]["apiKey"] = os.environ["OPENAI_API_KEY"]
cfg["provider"]["custom"]["options"]["baseURL"] = os.environ["OPENAI_BASE_URL"]
m = os.environ["OPENCODE_MODEL"]
cfg["provider"]["custom"]["models"] = {m: {"name": m, "tool_call": True}}
cfg["model"] = f"custom/{m}"
json.dump(cfg, open(sys.argv[1], "w"), indent=2)
PY
  local prompt; prompt=$("$PYBIN" /eval/call_gateway.py --emit-prompt --baseline "$baseline" \
    --max-rounds "$MAX_ATTEMPTS" --difficulty "$DIFFICULTY")
  local timeout_s="${OPENCODE_TIMEOUT_SEC:-3300}"
  log "delivering task to opencode (baseline=${baseline} difficulty=$DIFFICULTY attempts=$MAX_ATTEMPTS)..."
  ( cd "$REPO_DIR" && timeout "$timeout_s" opencode run --format json "$prompt" ) \
    > "$ART/opencode_output.jsonl" 2> "$ART/opencode_stderr.log" || log "WARNING: opencode returned nonzero"
  log "agent finished."
}

# ── ZeroClaw: OpenAI-compatible CLI agent → OpenRouter directly (no gateway) ──
launch_zeroclaw_agent() {
  log "starting ZeroClaw agent (CLI mode)..."
  : "${OPENAI_API_KEY:?OPENAI_API_KEY must be set for the agent}"
  : "${OPENAI_BASE_URL:?OPENAI_BASE_URL must be set for the agent}"
  local model="${OPENAI_MODEL_NAME:-qwen/qwen3-235b-a22b-2507}"
  local baseline; baseline=$(python3 -c "import json;print(json.load(open('$STATE_DIR/baseline.json'))['baseline_throughput'])" 2>/dev/null || echo "$BASELINE_HINT")
  local api_base="${OPENAI_BASE_URL%/}" provider="openai"
  if [[ "$api_base" == *openrouter* ]]; then provider="openrouter";
  elif [[ -n "$api_base" && "$api_base" != "https://api.openai.com" && "$api_base" != "https://api.openai.com/v1" ]]; then provider="custom:${api_base}"; fi
  export ZEROCLAW_API_KEY="$OPENAI_API_KEY" OPENROUTER_API_KEY="$OPENAI_API_KEY" ZEROCLAW_PROVIDER="$provider"
  mkdir -p "$HOME/.zeroclaw"
  # ZeroClaw tools operate inside ~/.zeroclaw/workspace -> point it at the repo.
  ln -sfn "$REPO_DIR" "$HOME/.zeroclaw/workspace"
  # NOTE: unlike KernelBench (eval inside workspace), mini-SGLang's /eval/run_eval is
  # OUTSIDE the workspace -> workspace_only=false + /eval not forbidden + run_eval allowed.
  cat > "$HOME/.zeroclaw/config.toml" <<TOMLEOF
default_provider = "${provider}"
default_model = "${model}"
default_temperature = 0.7
model_routes = []
embedding_routes = []

[model_providers]

[provider]

[observability]
backend = "none"
runtime_trace_mode = "none"

[autonomy]
level = "full"
workspace_only = false
allowed_commands = ["git", "ls", "cat", "grep", "find", "echo", "pwd", "wc", "head", "tail", "python", "python3", "bash", "sh", "sed", "awk", "mkdir", "mv", "cp", "/eval/run_eval", "run_eval"]
forbidden_paths = ["/etc", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys", "/data0/models"]
max_actions_per_hour = 500
max_cost_per_day_cents = 100000

[agent]
max_tool_iterations = 100
TOMLEOF
  chmod 600 "$HOME/.zeroclaw/config.toml"
  local prompt; prompt=$("$PYBIN" /eval/call_gateway.py --emit-prompt --baseline "$baseline" \
    --max-rounds "$MAX_ATTEMPTS" --difficulty "$DIFFICULTY")
  local timeout_s="${ZEROCLAW_TIMEOUT_SEC:-3300}"
  log "delivering task to zeroclaw (baseline=${baseline} difficulty=$DIFFICULTY attempts=$MAX_ATTEMPTS)..."
  ( cd "$REPO_DIR" && timeout "$timeout_s" zeroclaw agent -m "$prompt" ) \
    > "$ART/zeroclaw_output.txt" 2> "$ART/zeroclaw_stderr.log" || log "WARNING: zeroclaw returned nonzero"
  log "agent finished."
}

# ── Claude Code: Anthropic-protocol CLI → claude-code-router translates to OpenAI → OpenRouter ──
# (claude-code speaks /v1/messages; OpenRouter speaks /v1/chat/completions. CCR bridges them,
#  so all 4 harnesses run the SAME OpenRouter model -> fair harness comparison.)
launch_claude_code_agent() {
  log "starting Claude Code agent (via claude-code-router -> OpenRouter)..."
  : "${OPENAI_API_KEY:?OPENAI_API_KEY must be set for the agent}"
  : "${OPENAI_BASE_URL:?OPENAI_BASE_URL must be set for the agent}"
  local model="${OPENAI_MODEL_NAME:-qwen/qwen3-235b-a22b-2507}"
  local baseline; baseline=$(python3 -c "import json;print(json.load(open('$STATE_DIR/baseline.json'))['baseline_throughput'])" 2>/dev/null || echo "$BASELINE_HINT")
  local ccr_port="${CCR_PORT:-3456}"
  mkdir -p "$HOME/.claude-code-router"
  # OpenRouter wants /chat/completions; OPENAI_BASE_URL is the /v1 root.
  CCR_MODEL="$model" CCR_BASE="${OPENAI_BASE_URL%/}/chat/completions" "$PYBIN" - "$HOME/.claude-code-router/config.json" <<'PY'
import json, os, sys
m = os.environ["CCR_MODEL"]
cfg = {
  "NON_INTERACTIVE_MODE": True, "LOG": False,
  "Providers": [{
    "name": "openrouter",
    "api_base_url": os.environ["CCR_BASE"],
    "api_key": os.environ["OPENAI_API_KEY"],
    "models": [m],
    "transformer": {"use": ["openrouter"]},
  }],
  "Router": {"default": f"openrouter,{m}"},
}
json.dump(cfg, open(sys.argv[1], "w"), indent=2)
PY
  setsid ccr start >> "$ART/ccr.log" 2>&1 &
  local ccr_pgid; ccr_pgid=$(ps -o pgid= -p $! 2>/dev/null | tr -d ' ')
  local ok=0
  for i in $(seq 1 30); do
    [ "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${ccr_port}/" 2>/dev/null)" != "000" ] && { ok=1; break; }
    sleep 1
  done
  [ $ok -eq 1 ] && log "ccr router ready on $ccr_port" || log "WARNING: ccr router not responding on $ccr_port (see ccr.log)"
  local prompt; prompt=$("$PYBIN" /eval/call_gateway.py --emit-prompt --baseline "$baseline" \
    --max-rounds "$MAX_ATTEMPTS" --difficulty "$DIFFICULTY")
  local max_turns="${CLAUDE_MAX_TURNS:-80}"
  log "delivering task to claude-code (baseline=${baseline} difficulty=$DIFFICULTY attempts=$MAX_ATTEMPTS)..."
  # stream-json + --verbose => full step-by-step trajectory (tool calls, results),
  # not just the final text. One JSON object per line in claude_output.jsonl.
  ( cd "$REPO_DIR" && IS_SANDBOX=1 \
    ANTHROPIC_BASE_URL="http://127.0.0.1:${ccr_port}" \
    ANTHROPIC_API_KEY="dummy" ANTHROPIC_AUTH_TOKEN="dummy" \
    timeout "${CLAUDE_TIMEOUT_SEC:-3300}" claude --print --output-format stream-json --verbose \
    --max-turns "$max_turns" --dangerously-skip-permissions "$prompt" ) \
    > "$ART/claude_output.jsonl" 2> "$ART/claude_stderr.log" || log "WARNING: claude returned nonzero"
  ccr stop >> "$ART/ccr.log" 2>&1 || true
  [ -n "$ccr_pgid" ] && kill -- -"$ccr_pgid" 2>/dev/null || true
  log "agent finished."
}

# ── git init so the agent can revert with `git checkout -- .` ──
( cd "$REPO_DIR" && git init -q 2>/dev/null && git add -A 2>/dev/null \
  && git -c user.email=mb@eval -c user.name=mb commit -qm "baseline (pre-PR#97)" 2>/dev/null ) || true
# Pin the seed commit so per-attempt diffs (file_diffs.jsonl) are computed against the
# pristine code regardless of the agent's intermediate commits.
SEED_SHA=$( cd "$REPO_DIR" && git rev-parse HEAD 2>/dev/null || echo HEAD )
echo "$SEED_SHA" > "$STATE_DIR/seed_commit.txt"

# ── snapshots + config snapshot (Design-4 §10 artifact suitcase) ──
cp "$TASK" "$ART/task_config.json" 2>/dev/null || true
SNAP_DIR=$(read_json snapshot_dir python/minisgl)
( cd "$REPO_DIR" && tar -cf "$ART/initial_workspace.tar" "$SNAP_DIR" pyproject.toml 2>/dev/null ) || true

# ── verifier integrity baseline (anti-tamper — Design-5 §5/§6) ──
# Record hashes of the hidden verifier files NOW; re-check before the official eval.
# If the agent modified them mid-run, the run is marked INVALID (not silently scored).
"$PYBIN" - "$STATE_DIR/verifier_hashes.json" <<'PYEOF' 2>/dev/null || true
import hashlib, json, os, sys
files = ["/eval/eval_runner.py", "/eval/bench_serving.py", "/eval/run_eval", "/eval/call_gateway.py"]
h = {}
for p in files:
    try: h[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    except Exception: h[p] = None
json.dump(h, open(sys.argv[1], "w"), indent=2)
PYEOF

# ── 1) in-situ baseline on pristine code ──
log "measuring in-situ baseline (pristine repo)..."
"$PYBIN" /eval/eval_runner.py baseline > "$ART/baseline_eval.json" 2> "$ART/baseline_eval.err" \
  && log "baseline done" || log "WARNING: baseline failed (see baseline_eval.err)"

# ── 1b) reproducibility manifest (Design-5 §3) ──
"$PYBIN" - "$ART/manifest.json" <<PYEOF 2>/dev/null || true
import json, os, subprocess, sys
def sh(c):
    try: return subprocess.check_output(c, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception: return ""
def ver(mod):
    try:
        import importlib; return getattr(importlib.import_module(mod), "__version__", "")
    except Exception: return ""
def sha(p):
    import hashlib
    try: return hashlib.sha256(open(p,"rb").read()).hexdigest()
    except Exception: return ""
baseline = ""
try: baseline = json.load(open("$STATE_DIR/baseline.json"))["baseline_throughput"]
except Exception: pass
try: model_rev = open("$STATE_DIR/seed_commit.txt").read().strip()
except Exception: model_rev = ""
m = {
  "task": "mini_sglang", "difficulty": "$DIFFICULTY",
  "moltbench_task_level": "optimization_only",
  "scoring": {"correctness_metric": "num_error==0", "gain_metric": "OG=candidate/baseline",
              "stability_metric": "N/A", "memory_metric": "N/A", "drift_metric": "N/A",
              "cost_metric": "attempts+wall_time+tokens"},
  # ── ConfigLock (Design-5 §3): everything needed to trust/repro a leaderboard number ──
  "config_lock": {
    "benchmark_version": os.environ.get("BENCHMARK_VERSION", "moltbench-mini_sglang"),
    "verifier_version": os.environ.get("VERIFIER_VERSION", "1.1.0"),
    "run_eval_version": "1.1.0", "scoring_version": "1.1.0",
    "seed_commit": os.environ.get("SEED_COMMIT", "20fcd7f"),
    "seed_repo_head": "$SEED_SHA",
    "image_ref": os.environ.get("IMAGE_REF", ""),
    "image_digest": os.environ.get("IMAGE_DIGEST", ""),
    "model_name": os.environ.get("SERVED_MODEL_NAME", "") or os.path.basename("$MODEL_PATH".rstrip("/")),
    "model_path": "$MODEL_PATH",
    "model_revision": os.path.basename("$MODEL_PATH".rstrip("/")),  # HF snapshot hash dir
    "agent_adapter": "$AGENT",
    "verifier_hashes": {p: sha(p) for p in
        ["/eval/eval_runner.py","/eval/bench_serving.py","/eval/run_eval","/eval/call_gateway.py"]},
    "hardware_fingerprint": sh("nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | head -1"),
  },
  "model_path": "$MODEL_PATH",
  "agent": "$AGENT", "max_attempts": int("$MAX_ATTEMPTS"),
  "memory_ratio": "$MEMORY_RATIO",
  "trace": {"num_prompts": os.environ.get("EVAL_NUM_PROMPTS","64"),
            "input_len": os.environ.get("EVAL_INPUT_LEN","128"),
            "output_len": os.environ.get("EVAL_OUTPUT_LEN","128"),
            "warmup": os.environ.get("EVAL_WARMUP","1"), "repeats": os.environ.get("EVAL_REPEATS","3")},
  "baseline_throughput": baseline,
  "versions": {"python": sys.version.split()[0], "torch": ver("torch"),
               "flashinfer": ver("flashinfer"),
               "nvcc": sh("nvcc --version | grep release"),
               "gpu": sh("nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1")},
}
json.dump(m, open(sys.argv[1], "w"), indent=2)
print("[manifest] written")
PYEOF
log "manifest written"

# ── 2) agent optimization loop ──
if [ "$AGENT" = "openclaw" ]; then
  launch_openclaw_agent
elif [ "$AGENT" = "opencode" ]; then
  launch_opencode_agent
elif [ "$AGENT" = "zeroclaw" ]; then
  launch_zeroclaw_agent
elif [ "$AGENT" = "claude-code" ] || [ "$AGENT" = "claude_code" ]; then
  launch_claude_code_agent
elif [ "$AGENT" = "none" ]; then
  log "agent=none: skipping optimization (smoke mode)"
else
  log "WARNING: unknown agent '$AGENT'; skipping optimization"
fi

# ── 2a) collect harness trajectory (model_calls.jsonl + model_cost.json) ──
# Must run BEFORE official eval so the verifier can fold token/model-call cost into K.
"$PYBIN" /eval/collect_artifacts.py "$ART" "$AGENT" "$STATE_DIR/model_cost.json" \
  >> "$ART/collect_artifacts.log" 2>&1 || log "WARNING: collect_artifacts returned nonzero"

# ── 2b) final workspace snapshot (the code the official eval will score) ──
( cd "$REPO_DIR" && tar -cf "$ART/final_workspace.tar" "$SNAP_DIR" pyproject.toml 2>/dev/null ) || true
# The agent reverts any non-improving attempt (git checkout -- .), so the final tree IS
# the best tree it kept; record that mapping explicitly rather than re-deriving it.
cp "$ART/final_workspace.tar" "$ART/best_workspace.tar" 2>/dev/null || true

# ── 2c) defensive cleanup before official eval ──
# Agents may spawn/orphan minisgl servers or benches (despite the prompt telling them not
# to), which would contend for the GPU and corrupt the official measurement. The container
# has its OWN PID namespace (no --pid=host), so pkill only ever sees THIS container's
# processes — it cannot touch other users' servers on a shared host. The pattern is also
# anchored to our exact launch module + port to avoid matching unrelated python in-container.
log "killing any stray minisgl servers / benches in this container before official eval..."
pkill -9 -f "minisgl\.server\.launch.*--port ${MINISGL_PORT}" 2>/dev/null || true
pkill -9 -f "/eval/bench_serving\.py"      2>/dev/null || true
sleep 3

# ── 2d) verifier integrity re-check (anti-tamper — Design-5 §5/§6) ──
"$PYBIN" - "$STATE_DIR/verifier_hashes.json" "$ART/verifier_integrity.json" <<'PYEOF' 2>/dev/null || true
import hashlib, json, sys
before = json.load(open(sys.argv[1]))
now = {}
for p in before:
    try: now[p] = hashlib.sha256(open(p,"rb").read()).hexdigest()
    except Exception: now[p] = None
tampered = sorted(p for p in before if before[p] != now[p])
json.dump({"status": "TAMPERED" if tampered else "OK", "tampered_files": tampered,
           "before": before, "after": now}, open(sys.argv[2], "w"), indent=2)
print("[integrity] " + ("TAMPERED: "+", ".join(tampered) if tampered else "OK"))
PYEOF

# ── 3) official eval on the agent's final code ──
log "official eval on final code..."
"$PYBIN" /eval/eval_runner.py official > "$ART/official_eval.json" 2> "$ART/official_eval.err" \
  && log "official done" || log "WARNING: official failed (see official_eval.err)"

# ── 4) collect artifacts ──
# Exclude editable-install bytecode so the patch shows only real source edits.
( cd "$REPO_DIR" && git diff HEAD -- . ':(exclude)**/__pycache__/**' ':(exclude)**/*.pyc' > "$ART/agent.patch" 2>/dev/null; \
  git log --oneline > "$ART/agent_gitlog.txt" 2>/dev/null ) || true

# ── 4b) audit report (anti-hack / leakage integrity — Design-5 §5/§6) ──
# The agent edits /workspace/repo only; /eval is hidden. Flag if the patch touched
# anything outside the repo, or if /eval files were modified during the run.
"$PYBIN" - <<PYEOF 2>/dev/null || true
import json, os, subprocess
ART="$ART"; REPO="$REPO_DIR"
def sh(c):
    try: return subprocess.check_output(c, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception: return ""
res={}
try: res=json.load(open(os.path.join(ART,"results.json")))
except Exception: pass
integ={}
try: integ=json.load(open(os.path.join(ART,"verifier_integrity.json")))
except Exception: pass
# files the agent changed (relative to seed HEAD), all should be under the repo
# (exclude editable-install bytecode churn so the list shows real source edits)
changed=sh(f"cd {REPO} && git diff --name-only HEAD -- . ':(exclude)**/__pycache__/**' ':(exclude)**/*.pyc'").splitlines()
eval_writable = os.access("/eval/eval_runner.py", os.W_OK)
integrity_ok = integ.get("status") == "OK"
# A tampered verifier INVALIDATES the run (Design-5 §5: mark run invalid if the agent
# modified private verifier files) — stamp results.json so the score isn't trusted.
if integ.get("status") == "TAMPERED":
    res["verifier_integrity"] = "TAMPERED"; res["run_valid"] = False
    try: json.dump(res, open(os.path.join(ART,"results.json"),"w"), indent=2)
    except Exception: pass
present = lambda n: os.path.exists(os.path.join(ART, n))
arts = ["events.jsonl","model_calls.jsonl","file_diffs.jsonl","initial_workspace.tar",
        "final_workspace.tar","best_workspace.tar","task_config.json","manifest.json"]
lines=[
 "# MoltBench mini-SGLang — Run Audit Report","",
 f"- difficulty: $DIFFICULTY   agent: $AGENT   max_attempts: $MAX_ATTEMPTS",
 f"- correctness: {res.get('correctness','?')}   OG: {res.get('og','?')}   "
 f"throughput: {res.get('throughput_tok_per_s','?')} ±{res.get('throughput_ci95','?')} tok/s",
 f"- baseline (in-situ, not agent-writable): {res.get('baseline_throughput','?')} tok/s",
 f"- attempts_used: {res.get('attempts_used','?')}   cost (K): {res.get('cost',{})}","",
 "## Integrity checks (anti-hack — Design-5 §5/§6)","",
 f"- verifier integrity (hidden /eval files unchanged during run): {integ.get('status','?')}"
 + (f"  ⚠️ TAMPERED: {integ.get('tampered_files')} — RUN INVALID" if not integrity_ok and integ else ""),
 f"- agent-changed files (should all be under /workspace/repo): {changed or '[]'}",
 f"- /eval writable by agent user (convention; integrity hash enforces tamper-detection): {eval_writable}",
 f"- official score computed by hidden verifier from a clean server restart: True",
 f"- baseline measured in-situ on pristine seed (not hardcoded, not agent-writable): True",
 "","## Artifact suitcase","",
] + [f"- {'[x]' if present(n) else '[ ]'} {n}" for n in arts]
open(os.path.join(ART,"audit_report.md"),"w").write("\n".join(lines)+"\n")
print("[audit] written")
PYEOF
log "audit_report written"

log "results.json:"; cat "$ART/results.json" 2>/dev/null || log "(no results.json)"
log "all done."
