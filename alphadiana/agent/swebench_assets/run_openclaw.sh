#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[alphadiana-openclaw] %s\n' "$*"
}

find_repo_root() {
  local root=""
  root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "$root" ]]; then
    printf '%s\n' "$root"
    return 0
  fi
  for base in /workspace /repo /app /project /workdir /root/project /root/repo /tmp; do
    if [[ -d "$base" ]]; then
      root="$(git -C "$base" rev-parse --show-toplevel 2>/dev/null || true)"
      if [[ -n "$root" ]]; then
        printf '%s\n' "$root"
        return 0
      fi
    fi
  done
  return 1
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    log "Missing required environment variable: $name"
    exit 1
  fi
}

die() {
  log "FATAL: $*"
  exit 1
}

show_gateway_log() {
  if [[ -s "$ARTIFACTS_DIR/gateway.log" ]]; then
    log "Gateway log:"
    tail -20 "$ARTIFACTS_DIR/gateway.log" 1>&2 || true
  fi
}

require_env OPENAI_API_KEY
require_env OPENAI_BASE_URL
require_env OPENAI_MODEL_NAME
require_env ALPHADIANA_PROMPT_FILE
require_env ALPHADIANA_ARTIFACTS_DIR
require_env ALPHADIANA_CONFIG_TEMPLATE

REPO_ROOT="$(find_repo_root)"
if [[ -z "$REPO_ROOT" ]]; then
  log "Failed to detect the git repo root"
  exit 1
fi

ARTIFACTS_DIR="$ALPHADIANA_ARTIFACTS_DIR"
mkdir -p "$ARTIFACTS_DIR"
WORKDIR="$(mktemp -d /tmp/alphadiana-openclaw.XXXXXX)"
export ARTIFACTS_DIR
export WORKDIR
GATEWAY_PID=""

write_openclaw_attempt_artifacts() {
  python3 - <<'PY'
import json
import os
from pathlib import Path

artifacts = Path(os.environ["ARTIFACTS_DIR"])
artifacts.mkdir(parents=True, exist_ok=True)

candidate_models_path = artifacts / "openclaw_candidate_models.txt"
if not candidate_models_path.exists():
    candidate_models = [
        item.strip()
        for item in os.environ.get("OPENCLAW_SMOKE_MODEL_CANDIDATES", "").replace("\n", ",").split(",")
        if item.strip()
    ]
    candidate_models_path.write_text(
        ("\n".join(candidate_models) + "\n") if candidate_models else "",
        encoding="utf-8",
    )

prompt_profile_path = artifacts / "openclaw_prompt_profile.txt"
if not prompt_profile_path.exists():
    prompt_profile_path.write_text(
        "\n".join(
            [
                f"prompt_profile: {os.environ.get('OPENCLAW_PROMPT_PROFILE', 'edit_first')}",
                f"problem_statement_max_chars: {os.environ.get('OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS', '12000')}",
                f"resolved_model_alias: {os.environ.get('OPENAI_MODEL_NAME', '')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}

convergence = read_json(artifacts / "openclaw_edit_convergence.json")
verdict = read_json(artifacts / "openclaw_tool_verdict.json")
patch_path = artifacts / "patch.diff"
patch_size = patch_path.stat().st_size if patch_path.exists() else 0
classification = str(
    convergence.get("classification")
    or verdict.get("classification")
    or ("patch_created" if patch_size > 0 else "provider_failure")
).strip()
gateway_log_path = artifacts / "gateway.log"
reason = str(
    convergence.get("reason")
    or verdict.get("reason")
    or (gateway_log_path.read_text(encoding="utf-8", errors="replace").strip() if gateway_log_path.exists() else "")
    or "unknown OpenClaw outcome"
).strip()
record = {
    "attempt_index": 1,
    "resolved_model_alias": os.environ.get("OPENAI_MODEL_NAME", "").strip(),
    "prompt_profile": os.environ.get("OPENCLAW_PROMPT_PROFILE", "edit_first").strip() or "edit_first",
    "classification": classification,
    "patch_size_bytes": patch_size,
    "tool_call_count": int(convergence.get("tool_call_count") or verdict.get("tool_call_count") or 0),
    "tool_result_count": int(convergence.get("tool_result_count") or verdict.get("tool_result_count") or 0),
    "tracked_repo_change_count": int(convergence.get("tracked_repo_change_count") or verdict.get("tracked_repo_change_count") or 0),
    "reason": reason or "unknown OpenClaw outcome",
    "artifacts_dir": str(artifacts),
}
(artifacts / "openclaw_attempt_matrix.json").write_text(
    json.dumps(
        {
            "attempts": [record],
            "selected_attempt_index": 1,
            "tried_aliases": [
                item.strip()
                for item in candidate_models_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if item.strip()
            ],
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
(artifacts / "openclaw_selected_attempt.json").write_text(
    json.dumps(record, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
}

cleanup() {
  write_openclaw_attempt_artifacts || true
  if [[ -n "$GATEWAY_PID" ]] && kill -0 "$GATEWAY_PID" 2>/dev/null; then
    kill "$GATEWAY_PID" 2>/dev/null || true
    wait "$GATEWAY_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

export OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-OPENCLAW}"
export OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)}"
export OPENCLAW_HOME="${OPENCLAW_HOME:-/tmp/oc_home}"
export OPENCLAW_BUNDLED_PLUGINS_DIR="${OPENCLAW_BUNDLED_PLUGINS_DIR:-/tmp/empty-bundled}"
export OPENCLAW_AGENT_ID="${OPENCLAW_AGENT_ID:-main}"
export OPENCLAW_TOOLS_PROFILE="${OPENCLAW_TOOLS_PROFILE:-coding}"
export OPENCLAW_REQUIRE_PATCH="${OPENCLAW_REQUIRE_PATCH:-1}"
export OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT="${OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT:-20}"
export OPENCLAW_MAX_NO_EDIT_SECONDS="${OPENCLAW_MAX_NO_EDIT_SECONDS:-300}"
export OPENCLAW_SMOKE_MODEL_CANDIDATES="${OPENCLAW_SMOKE_MODEL_CANDIDATES:-${OPENAI_MODEL_NAME}}"
export OPENCLAW_PROMPT_PROFILE="${OPENCLAW_PROMPT_PROFILE:-edit_first}"
export OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS="${OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS:-12000}"
export ALPHADIANA_REPO_ROOT="$REPO_ROOT"
export OPENCLAW_SESSION_KEY="${OPENCLAW_SESSION_KEY:-${ALPHADIANA_INSTANCE_ID:-}}"
if [[ -z "$OPENCLAW_SESSION_KEY" ]]; then
  OPENCLAW_SESSION_KEY="swebench-$(basename "$ARTIFACTS_DIR")"
fi
export OPENCLAW_CHAT_USER="${OPENCLAW_CHAT_USER:-$OPENCLAW_SESSION_KEY}"

cp "$ALPHADIANA_CONFIG_TEMPLATE" "$WORKDIR/openclaw.json"
python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["WORKDIR"]) / "openclaw.json"
payload = json.loads(path.read_text(encoding="utf-8"))
payload.setdefault("gateway", {})["port"] = int(os.environ["OPENCLAW_GATEWAY_PORT"])
payload.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = os.environ["ALPHADIANA_REPO_ROOT"]
payload.setdefault("tools", {})["profile"] = os.environ.get("OPENCLAW_TOOLS_PROFILE", "coding")
timeout = os.environ.get("OPENCLAW_AGENT_TIMEOUT_SEC", "").strip()
if timeout:
    payload.setdefault("agents", {}).setdefault("defaults", {})["timeoutSeconds"] = int(timeout)
completion_cap = os.environ.get("OPENCLAW_COMPLETION_MAX_TOKENS", "").strip()
if completion_cap:
    payload.setdefault("agents", {}).setdefault("defaults", {}).setdefault("models", {}).setdefault(
        f"local/{os.environ['OPENAI_MODEL_NAME']}",
        {"params": {}},
    ).setdefault("params", {})["maxTokens"] = int(completion_cap)
path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY

export OPENCLAW_CONFIG_PATH="$WORKDIR/openclaw.json"
mkdir -p "$OPENCLAW_HOME" "$OPENCLAW_BUNDLED_PLUGINS_DIR"
cd "$REPO_ROOT"

CHAT_TIMEOUT="${OPENCLAW_CHAT_MAX_TIME_SEC:-}"
if [[ -z "$CHAT_TIMEOUT" && -n "${OPENCLAW_AGENT_TIMEOUT_SEC:-}" ]]; then
  CHAT_TIMEOUT="$((OPENCLAW_AGENT_TIMEOUT_SEC + 60))"
fi
CHAT_TIMEOUT="${CHAT_TIMEOUT:-3600}"
export CHAT_TIMEOUT
GATEWAY_URL="http://127.0.0.1:${OPENCLAW_GATEWAY_PORT}"
export GATEWAY_URL

git -C "$REPO_ROOT" status --short >"$ARTIFACTS_DIR/git_status_before.txt" || true

log "OpenClaw config: model=$OPENAI_MODEL_NAME baseURL=$OPENAI_BASE_URL port=$OPENCLAW_GATEWAY_PORT repo_root=$REPO_ROOT"
nohup openclaw gateway >"$ARTIFACTS_DIR/gateway.log" 2>&1 &
GATEWAY_PID="$!"

waited=0
max_wait=60
while true; do
  probe_code="$(
    python3 - "$GATEWAY_URL" "$OPENCLAW_GATEWAY_TOKEN" <<'PY'
import sys
import urllib.error
import urllib.request

url = f"{sys.argv[1]}/v1/models"
token = sys.argv[2]
request = urllib.request.Request(
    url,
    headers={"Authorization": f"bearer {token}"},
)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        print(response.getcode())
except urllib.error.HTTPError as exc:
    print(exc.code)
except Exception:
    print("000")
PY
  )"
  if [[ "$probe_code" != "000" ]]; then
    break
  fi
  if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
    show_gateway_log
    die "OpenClaw gateway failed to start"
  fi
  if [[ "$waited" -ge "$max_wait" ]]; then
    show_gateway_log
    die "OpenClaw gateway readiness timeout"
  fi
  sleep 2
  waited="$((waited + 2))"
done

REQUEST_BODY_FILE="$WORKDIR/request.json"
export REQUEST_BODY_FILE
python3 - <<'PY'
import json
import os
from pathlib import Path

artifacts = Path(os.environ["ARTIFACTS_DIR"])
prompt = Path(os.environ["ALPHADIANA_PROMPT_FILE"]).read_text(encoding="utf-8")
completion_cap = int(os.environ.get("OPENCLAW_COMPLETION_MAX_TOKENS", "32768"))
require_patch_value = os.environ.get("OPENCLAW_REQUIRE_PATCH", "1").strip().lower()
require_patch = require_patch_value not in {"", "0", "false", "no"}
prompt_profile = os.environ.get("OPENCLAW_PROMPT_PROFILE", "edit_first").strip() or "edit_first"
candidate_models = [
    item.strip()
    for item in os.environ.get("OPENCLAW_SMOKE_MODEL_CANDIDATES", "").replace("\n", ",").split(",")
    if item.strip()
]
profile_lines = []
if prompt_profile == "edit_first":
    profile_lines = [
        "OpenClaw prompt profile: edit_first",
        "- Make the smallest plausible repository edit that fixes the issue.",
        "- Prefer editing a likely relevant file over extended exploration once you have enough evidence.",
        "- After applying the fix, stop and let AlphaDiana collect the diff.",
    ]
contract_lines = []
if require_patch:
    contract_lines = [
        "OpenClaw execution contract:",
        "- Modify files under /app (or the detected repo root) rather than only explaining the fix.",
        "- Leave a non-empty git diff in the repository before stopping.",
        "- Do not stop at diagnosis only; keep working until you have applied the fix or exhausted the run budget.",
    ]
profile_text = "\n".join(profile_lines).strip()
contract_text = "\n".join(contract_lines).strip()
final_prompt = prompt.rstrip()
if profile_text:
    final_prompt = f"{final_prompt}\n\n{profile_text}\n"
if contract_text:
    final_prompt = f"{final_prompt}\n\n{contract_text}\n"
(artifacts / "openclaw_candidate_models.txt").write_text(
    ("\n".join(candidate_models) + "\n") if candidate_models else "",
    encoding="utf-8",
)
(artifacts / "openclaw_prompt_profile.txt").write_text(
    "\n".join(
        [
            f"prompt_profile: {prompt_profile}",
            f"problem_statement_max_chars: {os.environ.get('OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS', '12000')}",
            f"resolved_model_alias: {os.environ['OPENAI_MODEL_NAME']}",
        ]
    )
    + "\n",
    encoding="utf-8",
)
(artifacts / "openclaw_prompt_contract.txt").write_text(
    (contract_text + "\n") if contract_text else "",
    encoding="utf-8",
)
payload = {
    "model": f"openclaw:{os.environ['OPENCLAW_AGENT_ID']}",
    "messages": [{"role": "user", "content": final_prompt}],
    "stream": True,
    "max_tokens": completion_cap,
}
user = os.environ.get("OPENCLAW_CHAT_USER", "").strip()
if user:
    payload["user"] = user

request_metadata = {
    "url": f"{os.environ['GATEWAY_URL']}/v1/chat/completions",
    "method": "POST",
    "resolved_model_alias": os.environ["OPENAI_MODEL_NAME"],
    "prompt_profile": prompt_profile,
    "problem_statement_max_chars": int(
        os.environ.get("OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS", "12000")
    ),
    "candidate_models": candidate_models,
    "openclaw_require_patch": require_patch,
    "openclaw_max_tool_calls_without_edit": int(
        os.environ.get("OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT", "20")
    ),
    "openclaw_max_no_edit_seconds": int(
        os.environ.get("OPENCLAW_MAX_NO_EDIT_SECONDS", "300")
    ),
    "prompt_contract_path": str(artifacts / "openclaw_prompt_contract.txt"),
    "headers": {
        "Content-Type": "application/json",
        "x-openclaw-agent-id": os.environ["OPENCLAW_AGENT_ID"],
    },
    "body": payload,
}
session_key = os.environ.get("OPENCLAW_SESSION_KEY", "").strip()
if session_key:
    request_metadata["headers"]["x-openclaw-session-key"] = session_key
Path(os.environ["REQUEST_BODY_FILE"]).write_text(
    json.dumps(payload),
    encoding="utf-8",
)
(Path(os.environ["ARTIFACTS_DIR"]) / "openclaw_request.json").write_text(
    json.dumps(request_metadata, indent=2),
    encoding="utf-8",
)
PY

export OPENCLAW_RUN_START_TS="$(date +%s)"
set +e
python3 - <<'PY'
import http.client
import os
import signal
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _timeout_handler(signum, frame):
    raise TimeoutError(f"request exceeded {os.environ['CHAT_TIMEOUT']}s")


artifacts_dir = Path(os.environ["ARTIFACTS_DIR"])
body = Path(os.environ["REQUEST_BODY_FILE"]).read_bytes()
chat_timeout = max(1, int(float(os.environ["CHAT_TIMEOUT"])))
stream_idle_timeout_raw = os.environ.get("OPENCLAW_STREAM_IDLE_TIMEOUT_SEC", "").strip()
if stream_idle_timeout_raw:
    stream_idle_timeout = max(1.0, min(chat_timeout, float(stream_idle_timeout_raw)))
else:
    stream_idle_timeout = max(1.0, min(chat_timeout, 180.0))
headers = {
    "Authorization": f"bearer {os.environ['OPENCLAW_GATEWAY_TOKEN']}",
    "Content-Type": "application/json",
    "x-openclaw-agent-id": os.environ["OPENCLAW_AGENT_ID"],
}
session_key = os.environ.get("OPENCLAW_SESSION_KEY", "").strip()
if session_key:
    headers["x-openclaw-session-key"] = session_key

request = urllib.request.Request(
    f"{os.environ['GATEWAY_URL']}/v1/chat/completions",
    data=body,
    headers=headers,
    method="POST",
)
output_path = artifacts_dir / "openclaw_sse_raw.jsonl"
stream_warning_path = artifacts_dir / "openclaw_stream_warning.txt"
truncated_reason = ""
signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(chat_timeout)
try:
    with urllib.request.urlopen(request, timeout=stream_idle_timeout) as response, output_path.open("wb") as out:
        while True:
            try:
                chunk = response.read(65536)
            except http.client.IncompleteRead as exc:
                partial = exc.partial or b""
                if partial:
                    out.write(partial)
                    out.flush()
                truncated_reason = f"IncompleteRead({len(partial)} bytes read)"
                break
            if not chunk:
                break
            out.write(chunk)
            out.flush()
except urllib.error.HTTPError as exc:
    output_path.write_bytes(exc.read())
    print(f"HTTPError: {exc.code}", file=sys.stderr)
    sys.exit(22)
except TimeoutError as exc:
    if output_path.exists() and output_path.stat().st_size > 0:
        truncated_reason = str(exc)
    else:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
except Exception as exc:
    if output_path.exists() and output_path.stat().st_size > 0:
        truncated_reason = str(exc)
    else:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
finally:
    signal.alarm(0)
if truncated_reason:
    stream_warning_path.write_text(truncated_reason + "\n", encoding="utf-8")
    print(f"stream warning: {truncated_reason}", file=sys.stderr)
PY
CURL_STATUS=$?
set -e
if [[ $CURL_STATUS -ne 0 ]]; then
  CURL_STATUS="$CURL_STATUS" python3 - <<'PY'
import json
import os
from pathlib import Path

artifacts = Path(os.environ["ARTIFACTS_DIR"])
verdict = {
    "classification": "provider_failure",
    "reason": f"curl exited with status {os.environ['CURL_STATUS']}",
    "curl_status": int(os.environ["CURL_STATUS"]),
}
(artifacts / "openclaw_tool_verdict.json").write_text(
    json.dumps(verdict, indent=2, sort_keys=True),
    encoding="utf-8",
)
(artifacts / "openclaw_edit_convergence.json").write_text(
    json.dumps(
        {
            "classification": "provider_failure",
            "reason": verdict["reason"],
            "tool_call_count": 0,
            "tool_result_count": 0,
            "tracked_repo_change_count": 0,
            "patch_size_bytes": 0,
            "max_tool_calls_without_edit": int(
                os.environ.get("OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT", "20")
            ),
            "max_no_edit_seconds": int(
                os.environ.get("OPENCLAW_MAX_NO_EDIT_SECONDS", "300")
            ),
            "no_edit_elapsed_seconds": 0,
            "prompt_contract_path": str(artifacts / "openclaw_prompt_contract.txt"),
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
(artifacts / "openclaw_trajectory_summary.json").write_text(
    json.dumps({"trajectory_event_count": 0, "non_config_event_count": 0}, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
  exit 1
fi

python3 - <<'PY'
import json
import os
from pathlib import Path

artifacts = Path(os.environ["ARTIFACTS_DIR"])
raw_path = artifacts / "openclaw_sse_raw.jsonl"
out_path = artifacts / "openclaw_output.jsonl"
response_path = artifacts / "openclaw_response.txt"

events = []
content_parts = []
for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line == "data: [DONE]":
        continue
    if line.startswith("data: "):
        line = line[6:]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        continue
    events.append(payload)
    for choice in payload.get("choices", []):
        delta = choice.get("delta", {})
        content = delta.get("content")
        if content:
            content_parts.append(content)

out_path.write_text(
    "\n".join(json.dumps(event, ensure_ascii=True) for event in events) + ("\n" if events else ""),
    encoding="utf-8",
)
response_path.write_text("".join(content_parts), encoding="utf-8")
print(len(events))
PY

EVENT_COUNT="$(python3 - <<'PY'
import os
from pathlib import Path
path = Path(os.environ["ARTIFACTS_DIR"]) / "openclaw_output.jsonl"
if not path.exists():
    print(0)
else:
    print(sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()))
PY
)"
if [[ "${EVENT_COUNT:-0}" -eq 0 ]]; then
  log "OpenClaw produced no SSE events"
  exit 1
fi

SESSION_TRACE_PATH=""
OPENCLAW_STATE_DIR="$OPENCLAW_HOME/.openclaw"
SESSION_DIR="$OPENCLAW_STATE_DIR/agents/$OPENCLAW_AGENT_ID/sessions"
if [[ -d "$SESSION_DIR" ]]; then
  SESSION_TRACE_PATH="$(
    find "$SESSION_DIR" -type f -name "*.jsonl" -printf '%T@ %p\n' 2>/dev/null \
      | sort -nr \
      | head -n1 \
      | cut -d' ' -f2- \
      || true
  )"
fi
if [[ -n "$SESSION_TRACE_PATH" ]]; then
  cp "$SESSION_TRACE_PATH" "$ARTIFACTS_DIR/trajectory.jsonl"
  cp "$SESSION_TRACE_PATH" "$ARTIFACTS_DIR/openclaw_session.jsonl"
elif [[ -d "$OPENCLAW_STATE_DIR" ]]; then
  find "$OPENCLAW_STATE_DIR" -name "*.jsonl" -exec cp {} "$ARTIFACTS_DIR/trajectory.jsonl" \; 2>/dev/null || true
fi

git -C "$REPO_ROOT" status --short >"$ARTIFACTS_DIR/git_status_after.txt" || true
git -C "$REPO_ROOT" diff --binary >"$ARTIFACTS_DIR/patch.diff"
export OPENCLAW_RUN_END_TS="$(date +%s)"
OPENCLAW_CONVERGENCE_CLASSIFICATION="$(
python3 - <<'PY'
import json
import os
from pathlib import Path

artifacts = Path(os.environ["ARTIFACTS_DIR"])
session_trace_path = artifacts / "openclaw_session.jsonl"
trajectory_path = session_trace_path if session_trace_path.exists() else artifacts / "trajectory.jsonl"
patch_path = artifacts / "patch.diff"
prompt_contract_path = artifacts / "openclaw_prompt_contract.txt"
response_text = (artifacts / "openclaw_response.txt").read_text(
    encoding="utf-8",
    errors="replace",
).strip() if (artifacts / "openclaw_response.txt").exists() else ""
git_status_after = (artifacts / "git_status_after.txt").read_text(
    encoding="utf-8",
    errors="replace",
).splitlines() if (artifacts / "git_status_after.txt").exists() else []
trajectory_event_count = 0
non_config_event_names = set()
tool_call_count = 0
tool_result_count = 0
tracked_repo_change_count = 0
max_tool_calls_without_edit = int(os.environ.get("OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT", "20"))
max_no_edit_seconds = int(os.environ.get("OPENCLAW_MAX_NO_EDIT_SECONDS", "300"))
run_start_ts = int(os.environ.get("OPENCLAW_RUN_START_TS", "0") or "0")
run_end_ts = int(os.environ.get("OPENCLAW_RUN_END_TS", "0") or "0")
no_edit_elapsed_seconds = max(run_end_ts - run_start_ts, 0)

for line in git_status_after:
    stripped = line.strip()
    if stripped and not stripped.startswith("?? "):
        tracked_repo_change_count += 1

if trajectory_path.exists():
    for line in trajectory_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        trajectory_event_count += 1
        event_name = str(payload.get("event") or payload.get("type") or "").strip()
        if event_name and event_name != "config.write":
            non_config_event_names.add(event_name)
        if payload.get("type") == "message":
            message = payload.get("message")
            if isinstance(message, dict):
                role = str(message.get("role") or "").strip()
                if role:
                    non_config_event_names.add(f"message:{role}")
                if role == "toolResult":
                    tool_result_count += 1
                if role == "assistant":
                    content = message.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "toolCall":
                                tool_call_count += 1

patch_size = patch_path.stat().st_size if patch_path.exists() else 0
non_config_event_names_sorted = sorted(non_config_event_names)
non_config_event_count = len(non_config_event_names_sorted)
tool_trace_present = tool_call_count > 0 or tool_result_count > 0
if patch_size > 0:
    classification = "toolful" if tool_trace_present else "repo_edit_without_tool_trace"
    reason = "patch.diff is non-empty"
    convergence_classification = "patch_created"
    convergence_reason = "patch.diff is non-empty"
elif tool_trace_present and tracked_repo_change_count == 0 and (
    tool_call_count >= max_tool_calls_without_edit
    or no_edit_elapsed_seconds >= max_no_edit_seconds
):
    classification = "toolful"
    reason = "OpenClaw session used tools but did not modify the repository"
    convergence_classification = "toolful_no_edit"
    convergence_reason = (
        "OpenClaw session exceeded the no-edit threshold while using tools without "
        "mutating tracked files"
    )
elif tool_trace_present:
    classification = "toolful"
    reason = "OpenClaw session used tools but did not modify the repository"
    convergence_classification = "toolful_without_patch_below_threshold"
    convergence_reason = "OpenClaw session used tools but remained below the no-edit threshold"
elif tracked_repo_change_count > 0:
    classification = "repo_edit_without_tool_trace"
    reason = "Repository state changed without captured OpenClaw tool traces"
    convergence_classification = "repo_edit_without_tool_trace"
    convergence_reason = "Repository state changed without captured OpenClaw tool traces"
elif response_text:
    classification = "text_only"
    reason = "OpenClaw streamed text output but did not modify the repository"
    convergence_classification = "text_only"
    convergence_reason = reason
else:
    classification = "provider_failure"
    reason = "OpenClaw did not produce tool traces, repo edits, or assistant text"
    convergence_classification = "provider_failure"
    convergence_reason = reason

summary = {
    "trace_source": trajectory_path.name if trajectory_path.exists() else "",
    "trajectory_event_count": trajectory_event_count,
    "non_config_event_count": non_config_event_count,
    "non_config_event_names": non_config_event_names_sorted,
    "tool_call_count": tool_call_count,
    "tool_result_count": tool_result_count,
    "tracked_repo_change_count": tracked_repo_change_count,
}
verdict = {
    "classification": classification,
    "reason": reason,
    "patch_size_bytes": patch_size,
    "trajectory_event_count": trajectory_event_count,
    "non_config_event_count": non_config_event_count,
    "tool_call_count": tool_call_count,
    "tool_result_count": tool_result_count,
    "tracked_repo_change_count": tracked_repo_change_count,
}
convergence = {
    "classification": convergence_classification,
    "reason": convergence_reason,
    "patch_size_bytes": patch_size,
    "tool_call_count": tool_call_count,
    "tool_result_count": tool_result_count,
    "tracked_repo_change_count": tracked_repo_change_count,
    "max_tool_calls_without_edit": max_tool_calls_without_edit,
    "max_no_edit_seconds": max_no_edit_seconds,
    "no_edit_elapsed_seconds": no_edit_elapsed_seconds,
    "prompt_contract_path": str(prompt_contract_path),
}
(artifacts / "openclaw_trajectory_summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True),
    encoding="utf-8",
)
(artifacts / "openclaw_tool_verdict.json").write_text(
    json.dumps(verdict, indent=2, sort_keys=True),
    encoding="utf-8",
)
(artifacts / "openclaw_edit_convergence.json").write_text(
    json.dumps(convergence, indent=2, sort_keys=True),
    encoding="utf-8",
)
print(convergence_classification)
PY
)"

if [[ "${EVENT_COUNT:-0}" -eq 0 ]]; then
  log "OpenClaw produced no SSE events"
  exit 1
fi
if [[ "$OPENCLAW_CONVERGENCE_CLASSIFICATION" == "text_only" ]]; then
  log "OpenClaw run completed as a text-only session"
  exit 1
fi
if [[ "$OPENCLAW_CONVERGENCE_CLASSIFICATION" == "toolful_no_edit" ]]; then
  log "OpenClaw run completed as a toolful-no-edit session"
  exit 1
fi
if [[ ! -s "$ARTIFACTS_DIR/patch.diff" ]]; then
  log "OpenClaw run completed but produced an empty patch.diff"
  exit 1
fi

printf '%s\n' "$REPO_ROOT" >"$ARTIFACTS_DIR/repo_root.txt"
log "OpenClaw run finished"
