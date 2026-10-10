#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[alphadiana-opencode] %s\n' "$*"
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

require_env OPENAI_API_KEY
require_env OPENAI_BASE_URL
require_env ALPHADIANA_PROMPT_FILE
require_env ALPHADIANA_ARTIFACTS_DIR
require_env ALPHADIANA_CONFIG_TEMPLATE

REPO_ROOT="$(find_repo_root)"
if [[ -z "$REPO_ROOT" ]]; then
  die "Failed to detect the git repo root"
fi

ARTIFACTS_DIR="$ALPHADIANA_ARTIFACTS_DIR"
mkdir -p "$ARTIFACTS_DIR"
WORKDIR="$(mktemp -d /tmp/alphadiana-opencode.XXXXXX)"
export ARTIFACTS_DIR
export WORKDIR
export HOME="$WORKDIR/home"
mkdir -p "$HOME"

OPENCODE_MODEL_NAME="${OPENCODE_SMOKE_MODEL_NAME:-${OPENAI_MODEL_NAME:-}}"
if [[ -z "$OPENCODE_MODEL_NAME" ]]; then
  die "Missing required environment variable: OPENAI_MODEL_NAME or OPENCODE_SMOKE_MODEL_NAME"
fi
export OPENCODE_MODEL_NAME
OPENCODE_TIMEOUT="${OPENCODE_TIMEOUT_SEC:-1200}"
OPENCODE_PREFLIGHT_TIMEOUT="${OPENCODE_PREFLIGHT_TIMEOUT_SEC:-45}"
OPENCODE_STARTUP_TIMEOUT="${OPENCODE_STARTUP_TIMEOUT_SEC:-90}"
OPENCODE_IDLE_TIMEOUT="${OPENCODE_IDLE_TIMEOUT_SEC:-600}"
OPENCODE_IDLE_POLL="${OPENCODE_IDLE_POLL_SEC:-10}"
OPENCODE_MAX_ACTIVE_NO_EDIT_SEC="${OPENCODE_MAX_ACTIVE_NO_EDIT_SEC:-600}"
OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT="${OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT:-40}"
OPENCODE_ACTIVITY_HEARTBEAT_SEC="${OPENCODE_ACTIVITY_HEARTBEAT_SEC:-30}"
OPENCODE_REQUIRE_PATCH="${OPENCODE_REQUIRE_PATCH:-1}"
OPENCODE_PROMPT_PROFILE="${OPENCODE_PROMPT_PROFILE:-edit_first}"
OPENCODE_AUTO_TARGET_HINTS="${OPENCODE_AUTO_TARGET_HINTS:-1}"
OPENCODE_STRATEGY_SEQUENCE="${OPENCODE_STRATEGY_SEQUENCE:-bash_edit_first,guided_edit_first,edit_first}"
OPENCODE_STRATEGY_NAME="${OPENCODE_STRATEGY_NAME:-${OPENCODE_PROMPT_PROFILE}}"
OPENCODE_TARGET_FILE_HINTS="${OPENCODE_TARGET_FILE_HINTS:-}"
OPENCODE_PRIMARY_TARGET_FILE="${OPENCODE_PRIMARY_TARGET_FILE:-}"
OPENCODE_TARGET_FILE_HINTS_SOURCE="${OPENCODE_TARGET_FILE_HINTS_SOURCE:-none}"
OPENCODE_PROBLEM_STATEMENT_MAX_CHARS="${OPENCODE_PROBLEM_STATEMENT_MAX_CHARS:-12000}"
OPENCODE_SMOKE_MODEL_CANDIDATES="${OPENCODE_SMOKE_MODEL_CANDIDATES:-${OPENCODE_MODEL_NAME}}"
export OPENCODE_CONFIG="$WORKDIR/opencode.json"

OPENCODE_OUTPUT_PATH="$ARTIFACTS_DIR/opencode_output.jsonl"
OPENCODE_STDERR_PATH="$ARTIFACTS_DIR/opencode_stderr.log"
OPENCODE_STALL_REASON_PATH="$ARTIFACTS_DIR/opencode_stall_reason.txt"
OPENCODE_PROGRESS_SNAPSHOT_PATH="$ARTIFACTS_DIR/opencode_progress_snapshot.txt"
OPENCODE_PROVIDER_PREFLIGHT_PATH="$ARTIFACTS_DIR/opencode_provider_preflight.txt"
OPENCODE_STARTUP_DIAGNOSTICS_PATH="$ARTIFACTS_DIR/opencode_startup_diagnostics.txt"
OPENCODE_ACTIVITY_SUMMARY_PATH="$ARTIFACTS_DIR/opencode_activity_summary.json"
OPENCODE_NO_EDIT_REASON_PATH="$ARTIFACTS_DIR/opencode_no_edit_reason.txt"
OPENCODE_GIT_STATUS_BEFORE_PATH="$ARTIFACTS_DIR/git_status_before.txt"
OPENCODE_GIT_STATUS_AFTER_PATH="$ARTIFACTS_DIR/git_status_after.txt"
OPENCODE_VERSION="$(opencode --version 2>&1 | head -n 1 || true)"
OPENCODE_PROMPT_CONTRACT_PATH="$ARTIFACTS_DIR/opencode_prompt_contract.txt"
OPENCODE_PROMPT_PROFILE_PATH="$ARTIFACTS_DIR/opencode_prompt_profile.txt"
OPENCODE_CANDIDATE_MODELS_PATH="$ARTIFACTS_DIR/opencode_candidate_models.txt"
OPENCODE_TARGET_FILE_HINTS_PATH="$ARTIFACTS_DIR/opencode_target_file_hints.txt"
OPENCODE_EDIT_BOOTSTRAP_PATH="$ARTIFACTS_DIR/opencode_edit_bootstrap.txt"
OPENCODE_EDIT_CONTRACT_PATH="$ARTIFACTS_DIR/opencode_edit_contract.txt"
OPENCODE_ATTEMPT_MATRIX_PATH="$ARTIFACTS_DIR/opencode_attempt_matrix.json"
OPENCODE_SELECTED_ATTEMPT_PATH="$ARTIFACTS_DIR/opencode_selected_attempt.json"

write_opencode_attempt_artifacts() {
  python3 - <<'PY'
import json
import os
from pathlib import Path

artifacts = Path(os.environ["ARTIFACTS_DIR"])
artifacts.mkdir(parents=True, exist_ok=True)

candidate_models_path = artifacts / "opencode_candidate_models.txt"
if not candidate_models_path.exists():
    candidate_models = [
        item.strip()
        for item in os.environ.get("OPENCODE_SMOKE_MODEL_CANDIDATES", "").replace("\n", ",").split(",")
        if item.strip()
    ]
    candidate_models_path.write_text(
        ("\n".join(candidate_models) + "\n") if candidate_models else "",
        encoding="utf-8",
    )

prompt_profile_path = artifacts / "opencode_prompt_profile.txt"
if not prompt_profile_path.exists():
    prompt_profile_path.write_text(
        "\n".join(
            [
                f"prompt_profile: {os.environ.get('OPENCODE_PROMPT_PROFILE', 'edit_first')}",
                f"strategy_name: {os.environ.get('OPENCODE_STRATEGY_NAME', os.environ.get('OPENCODE_PROMPT_PROFILE', 'edit_first'))}",
                f"problem_statement_max_chars: {os.environ.get('OPENCODE_PROBLEM_STATEMENT_MAX_CHARS', '12000')}",
                f"target_file_hints_source: {os.environ.get('OPENCODE_TARGET_FILE_HINTS_SOURCE', 'none')}",
                f"resolved_model_alias: {os.environ.get('OPENCODE_MODEL_NAME', '')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

target_file_hints = [
    item.strip()
    for item in os.environ.get("OPENCODE_TARGET_FILE_HINTS", "").replace("\n", ",").split(",")
    if item.strip()
]
primary_target_file = os.environ.get("OPENCODE_PRIMARY_TARGET_FILE", "").strip()
if not primary_target_file and target_file_hints:
    primary_target_file = target_file_hints[0]
target_hints_path = artifacts / "opencode_target_file_hints.txt"
if not target_hints_path.exists():
    target_hint_lines = [
        f"strategy_name: {os.environ.get('OPENCODE_STRATEGY_NAME', os.environ.get('OPENCODE_PROMPT_PROFILE', 'edit_first'))}",
        f"target_file_hints_source: {os.environ.get('OPENCODE_TARGET_FILE_HINTS_SOURCE', 'none')}",
        f"primary_target_file: {primary_target_file or '<none>'}",
        "target_file_hints:",
    ]
    if target_file_hints:
        target_hint_lines.extend(f"- {item}" for item in target_file_hints)
    else:
        target_hint_lines.append("<none>")
    target_hints_path.write_text("\n".join(target_hint_lines) + "\n", encoding="utf-8")

bootstrap_path = artifacts / "opencode_edit_bootstrap.txt"
if not bootstrap_path.exists():
bootstrap_lines = [
    "OpenCode edit bootstrap",
    f"strategy_name: {os.environ.get('OPENCODE_STRATEGY_NAME', os.environ.get('OPENCODE_PROMPT_PROFILE', 'edit_first'))}",
    f"target_file_hints_source: {os.environ.get('OPENCODE_TARGET_FILE_HINTS_SOURCE', 'none')}",
    f"primary_target_file: {primary_target_file or '<none>'}",
]
    if target_file_hints:
        bootstrap_lines.append("target_file_hints:")
        bootstrap_lines.extend(f"- {item}" for item in target_file_hints)
    else:
        bootstrap_lines.append("target_file_hints: <none>")
    bootstrap_path.write_text("\n".join(bootstrap_lines) + "\n", encoding="utf-8")

def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}

def read_status(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("status: "):
            return line.split(": ", 1)[1].strip()
    return ""

def read_error(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("error: "):
            return line.split(": ", 1)[1].strip()
    return ""

activity_summary = read_json(artifacts / "opencode_activity_summary.json")
patch_path = artifacts / "patch.diff"
patch_size = patch_path.stat().st_size if patch_path.exists() else 0
preflight_status = read_status(artifacts / "opencode_provider_preflight.txt") or "unknown"
startup_status = read_status(artifacts / "opencode_startup_diagnostics.txt") or "unknown"
classification = str(activity_summary.get("classification") or "").strip()
stall_reason_path = artifacts / "opencode_stall_reason.txt"
stall_reason = stall_reason_path.read_text(encoding="utf-8", errors="replace").strip() if stall_reason_path.exists() else ""
no_edit_reason_path = artifacts / "opencode_no_edit_reason.txt"
no_edit_reason = no_edit_reason_path.read_text(encoding="utf-8", errors="replace").strip() if no_edit_reason_path.exists() else ""
if not classification and patch_size > 0:
    classification = "patch_created"
if not classification and preflight_status == "failed":
    classification = "provider_preflight_failed"
if not classification and stall_reason:
    classification = "stalled_no_progress"
if not classification and startup_status != "unknown":
    classification = startup_status
reason = no_edit_reason or read_error(artifacts / "opencode_provider_preflight.txt") or stall_reason
if not reason:
    stderr_path = artifacts / "opencode_stderr.log"
    reason = stderr_path.read_text(encoding="utf-8", errors="replace").strip() if stderr_path.exists() else ""
reason = reason or "unknown OpenCode outcome"

record = {
    "attempt_index": 1,
    "resolved_model_alias": os.environ.get("OPENCODE_MODEL_NAME", "").strip(),
    "strategy_name": (
        os.environ.get("OPENCODE_STRATEGY_NAME", os.environ.get("OPENCODE_PROMPT_PROFILE", "edit_first")).strip()
        or "edit_first"
    ),
    "prompt_profile": os.environ.get("OPENCODE_PROMPT_PROFILE", "edit_first").strip() or "edit_first",
    "target_file_hints": target_file_hints,
    "primary_target_file": primary_target_file,
    "target_file_hints_source": os.environ.get("OPENCODE_TARGET_FILE_HINTS_SOURCE", "none").strip() or "none",
    "preflight_status": preflight_status,
    "startup_status": startup_status,
    "classification": classification or "provider_failure",
    "patch_size_bytes": patch_size,
    "tool_use_count": int(activity_summary.get("tool_use_count") or 0),
    "tracked_repo_change_count": int(activity_summary.get("tracked_repo_change_count") or 0),
    "tracked_repo_changed_paths": list(activity_summary.get("tracked_repo_changed_paths") or []),
    "primary_target_file_changed": bool(activity_summary.get("primary_target_file_changed")),
    "reason": reason,
    "artifacts_dir": str(artifacts),
}
(artifacts / "opencode_attempt_matrix.json").write_text(
    json.dumps(
        {
            "attempts": [record],
            "selected_attempt_index": 1,
            "tried_aliases": [
                item.strip()
                for item in candidate_models_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if item.strip()
            ],
            "tried_strategy_names": [
                item.strip()
                for item in os.environ.get("OPENCODE_STRATEGY_SEQUENCE", "").replace("\n", ",").split(",")
                if item.strip()
            ],
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
(artifacts / "opencode_selected_attempt.json").write_text(
    json.dumps(record, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
}

cleanup() {
  write_opencode_attempt_artifacts || true
}
trap cleanup EXIT

timestamp_now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

count_session_jsonl() {
  if [[ ! -d "$HOME/.opencode" ]]; then
    printf '0'
    return 0
  fi
  find "$HOME/.opencode" -name "*.jsonl" 2>/dev/null | wc -l | tr -d ' '
}

count_tracked_repo_changes() {
  git -C "$REPO_ROOT" status --short 2>/dev/null | awk 'BEGIN{count=0} NF && $0 !~ /^\?\? / {count++} END{print count}'
}

tracked_repo_changed_paths_json() {
  REPO_ROOT_ENV="$REPO_ROOT" python3 - <<'PY'
import json
import subprocess
import os

repo_root = os.environ["REPO_ROOT_ENV"]
result = subprocess.run(
    ["git", "-C", repo_root, "diff", "--name-only", "--relative"],
    capture_output=True,
    text=True,
    check=False,
)
paths = []
if result.returncode == 0:
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped and stripped not in paths:
            paths.append(stripped)
print(json.dumps(paths))
PY
}

count_tool_use_events() {
  OPENCODE_OUTPUT_PATH_ENV="$OPENCODE_OUTPUT_PATH" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["OPENCODE_OUTPUT_PATH_ENV"])
count = 0
if path.exists():
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "tool_use":
            count += 1
print(count)
PY
}

write_startup_diagnostics() {
  local status="$1"
  local stdout_bytes="0"
  local stderr_bytes="0"
  local session_count="0"

  if [[ -f "$OPENCODE_OUTPUT_PATH" ]]; then
    stdout_bytes="$(wc -c <"$OPENCODE_OUTPUT_PATH" | tr -d ' ')"
  fi
  if [[ -f "$OPENCODE_STDERR_PATH" ]]; then
    stderr_bytes="$(wc -c <"$OPENCODE_STDERR_PATH" | tr -d ' ')"
  fi
  session_count="$(count_session_jsonl)"

  cat >"$OPENCODE_STARTUP_DIAGNOSTICS_PATH" <<EOF
OpenCode startup diagnostics
status: $status
model: $OPENCODE_MODEL_NAME
repo_root: $REPO_ROOT
workdir: $WORKDIR
opencode_version: $OPENCODE_VERSION
startup_timeout_sec: $OPENCODE_STARTUP_TIMEOUT
stdout_bytes: $stdout_bytes
stderr_bytes: $stderr_bytes
session_file_count: $session_count
first_stdout_ts: ${FIRST_STDOUT_TS:-none}
first_stderr_ts: ${FIRST_STDERR_TS:-none}
first_session_ts: ${FIRST_SESSION_TS:-none}
first_stdout_or_session_ts: ${FIRST_STDOUT_OR_SESSION_TS:-none}
EOF
}

write_activity_summary() {
  local status="$1"
  local active_elapsed_sec="$2"
  local tool_use_count="$3"
  local tracked_repo_change_count="$4"
  local tracked_repo_changed_paths_json="$5"
  local stdout_bytes="0"
  local stderr_bytes="0"
  local session_count="0"
  local primary_target_file="${OPENCODE_PRIMARY_TARGET_FILE:-}"

  if [[ -z "$primary_target_file" && -n "$OPENCODE_TARGET_FILE_HINTS" ]]; then
    primary_target_file="${OPENCODE_TARGET_FILE_HINTS%%,*}"
  fi

  if [[ -f "$OPENCODE_OUTPUT_PATH" ]]; then
    stdout_bytes="$(wc -c <"$OPENCODE_OUTPUT_PATH" | tr -d ' ')"
  fi
  if [[ -f "$OPENCODE_STDERR_PATH" ]]; then
    stderr_bytes="$(wc -c <"$OPENCODE_STDERR_PATH" | tr -d ' ')"
  fi
  session_count="$(count_session_jsonl)"

  cat >"$OPENCODE_ACTIVITY_SUMMARY_PATH" <<EOF
{
  "classification": "${status}",
  "model": "${OPENCODE_MODEL_NAME}",
  "repo_root": "${REPO_ROOT}",
  "workdir": "${WORKDIR}",
  "opencode_version": "${OPENCODE_VERSION}",
  "activity_heartbeat_sec": ${OPENCODE_ACTIVITY_HEARTBEAT_SEC},
  "max_active_no_edit_sec": ${OPENCODE_MAX_ACTIVE_NO_EDIT_SEC},
  "max_tool_calls_without_edit": ${OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT},
  "active_elapsed_sec": ${active_elapsed_sec},
  "tool_use_count": ${tool_use_count},
  "tracked_repo_change_count": ${tracked_repo_change_count},
  "tracked_repo_changed_paths": ${tracked_repo_changed_paths_json},
  "primary_target_file": "$(printf '%s' "$primary_target_file" | sed 's/"/\\"/g')",
  "primary_target_file_changed": $(PRIMARY_TARGET_FILE="$primary_target_file" TRACKED_REPO_CHANGED_PATHS_JSON="$tracked_repo_changed_paths_json" python3 - <<'PY'
import json
import os

primary = os.environ.get("PRIMARY_TARGET_FILE", "").strip()
paths = json.loads(os.environ.get("TRACKED_REPO_CHANGED_PATHS_JSON", "[]"))
print("true" if primary and primary in paths else "false")
PY
),
  "stdout_bytes": ${stdout_bytes},
  "stderr_bytes": ${stderr_bytes},
  "session_file_count": ${session_count},
  "startup_status": "$(if [[ -n "${FIRST_STDOUT_OR_SESSION_TS:-}" ]]; then printf 'activity_observed'; else printf 'no_activity'; fi)",
  "first_stdout_or_session_ts": "${FIRST_STDOUT_OR_SESSION_TS:-none}"
}
EOF
}

write_no_edit_reason() {
  local reason="$1"
  printf '%s\n' "$reason" >"$OPENCODE_NO_EDIT_REASON_PATH"
}

record_active_no_edit_artifacts() {
  local classification="$1"
  local reason="$2"
  local active_elapsed_sec="$3"
  local tool_use_count="$4"
  local tracked_repo_change_count="$5"
  local tracked_repo_changed_paths_json
  tracked_repo_changed_paths_json="$(tracked_repo_changed_paths_json)"

  write_startup_diagnostics "activity_observed"
  write_activity_summary "$classification" "$active_elapsed_sec" "$tool_use_count" "$tracked_repo_change_count" "$tracked_repo_changed_paths_json"
  write_no_edit_reason "$reason"
  write_progress_snapshot
}

collect_progress_signature() {
  OPENCODE_OUTPUT_PATH_ENV="$OPENCODE_OUTPUT_PATH" OPENCODE_HOME_DIR="$HOME/.opencode" python3 - <<'PY'
import os
from pathlib import Path

paths = []
output_path = Path(os.environ["OPENCODE_OUTPUT_PATH_ENV"])
if output_path.exists():
    paths.append(output_path)

session_root = Path(os.environ["OPENCODE_HOME_DIR"])
if session_root.exists():
    paths.extend(sorted(session_root.rglob("*.jsonl")))

parts = []
for path in paths:
    stat = path.stat()
    parts.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")

print("|".join(parts))
PY
}

write_progress_snapshot() {
  OPENCODE_OUTPUT_PATH_ENV="$OPENCODE_OUTPUT_PATH" \
  OPENCODE_STDERR_PATH_ENV="$OPENCODE_STDERR_PATH" \
  OPENCODE_HOME_DIR="$HOME/.opencode" \
  OPENCODE_PROGRESS_SNAPSHOT_PATH_ENV="$OPENCODE_PROGRESS_SNAPSHOT_PATH" \
  python3 - <<'PY'
import os
from pathlib import Path


def tail_lines(path: Path, count: int = 20) -> str:
    if not path.exists():
        return "<missing>"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "<empty>"
    return "\n".join(lines[-count:])


def describe(path: Path) -> str:
    if not path.exists():
        return f"{path} | missing"
    stat = path.stat()
    line_count = path.read_text(encoding="utf-8", errors="replace").count("\n")
    return f"{path} | bytes={stat.st_size} lines={line_count}"


output_path = Path(os.environ["OPENCODE_OUTPUT_PATH_ENV"])
stderr_path = Path(os.environ["OPENCODE_STDERR_PATH_ENV"])
session_root = Path(os.environ["OPENCODE_HOME_DIR"])
snapshot_path = Path(os.environ["OPENCODE_PROGRESS_SNAPSHOT_PATH_ENV"])

sections = [
    "OpenCode progress snapshot",
    describe(output_path),
    describe(stderr_path),
    "",
    "===== tail: opencode_output.jsonl =====",
    tail_lines(output_path),
    "",
    "===== tail: opencode_stderr.log =====",
    tail_lines(stderr_path),
]

session_files = sorted(session_root.rglob("*.jsonl")) if session_root.exists() else []
if not session_files:
    sections.extend(["", "===== session jsonl =====", "<none>"])
else:
    sections.extend(["", "===== session jsonl ====="])
    for path in session_files[-5:]:
        sections.append(describe(path))
        sections.append(tail_lines(path))
        sections.append("")

snapshot_path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
PY
}

terminate_process_group() {
  local leader_pid="$1"
  if ! kill -0 "$leader_pid" 2>/dev/null; then
    return 0
  fi

  log "Terminating stalled OpenCode process group $leader_pid"
  kill -TERM -- "-$leader_pid" 2>/dev/null || true
  for _ in $(seq 1 10); do
    if ! kill -0 "$leader_pid" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done

  kill -KILL -- "-$leader_pid" 2>/dev/null || true
}

write_opencode_config() {
  python3 - <<'PY'
import json
import os
from pathlib import Path

template = Path(os.environ["ALPHADIANA_CONFIG_TEMPLATE"])
target = Path(os.environ["OPENCODE_CONFIG"])
config = json.loads(template.read_text(encoding="utf-8"))

model_name = os.environ["OPENCODE_MODEL_NAME"]
options = config["provider"]["custom"]["options"]
options["apiKey"] = os.environ["OPENAI_API_KEY"]
options["baseURL"] = os.environ["OPENAI_BASE_URL"]

def present(name: str) -> bool:
    return os.environ.get(name, "").strip() != ""

def truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

if present("OPENCODE_PROVIDER_TEMPERATURE"):
    options["temperature"] = float(os.environ["OPENCODE_PROVIDER_TEMPERATURE"])
if present("OPENCODE_PROVIDER_TOP_P"):
    options["top_p"] = float(os.environ["OPENCODE_PROVIDER_TOP_P"])
if present("OPENCODE_PROVIDER_MAX_TOKENS"):
    options["max_tokens"] = int(os.environ["OPENCODE_PROVIDER_MAX_TOKENS"])
if present("OPENCODE_PROVIDER_TIMEOUT_MS"):
    options["timeout"] = int(os.environ["OPENCODE_PROVIDER_TIMEOUT_MS"])
if present("OPENCODE_PROVIDER_STREAMING"):
    options["streaming"] = truthy("OPENCODE_PROVIDER_STREAMING")
if truthy("OPENCODE_PROVIDER_LOGPROBS"):
    options["logprobs"] = True
    options["top_logprobs"] = int(os.environ.get("OPENCODE_PROVIDER_TOP_LOGPROBS", "20"))
config["provider"]["custom"]["models"] = {
    model_name: {
        "name": model_name,
        "tool_call": True,
    }
}
config["model"] = f"custom/{model_name}"

target.write_text(json.dumps(config, indent=2), encoding="utf-8")
PY
}

write_opencode_config

PROMPT="$(python3 - <<'PY'
import os
from pathlib import Path

artifacts = Path(os.environ["ARTIFACTS_DIR"])
prompt = Path(os.environ["ALPHADIANA_PROMPT_FILE"]).read_text(encoding="utf-8")
prompt_profile = os.environ.get("OPENCODE_PROMPT_PROFILE", "edit_first").strip() or "edit_first"
strategy_name = os.environ.get("OPENCODE_STRATEGY_NAME", prompt_profile).strip() or prompt_profile
require_patch = os.environ.get("OPENCODE_REQUIRE_PATCH", "1").strip().lower() not in {"", "0", "false", "no"}
target_file_hints = [
    item.strip()
    for item in os.environ.get("OPENCODE_TARGET_FILE_HINTS", "").replace("\n", ",").split(",")
    if item.strip()
]
primary_target_file = os.environ.get("OPENCODE_PRIMARY_TARGET_FILE", "").strip()
if not primary_target_file and target_file_hints:
    primary_target_file = target_file_hints[0]
target_file_hints_source = os.environ.get("OPENCODE_TARGET_FILE_HINTS_SOURCE", "none").strip() or "none"
candidate_models = [
    item.strip()
    for item in os.environ.get("OPENCODE_SMOKE_MODEL_CANDIDATES", "").replace("\n", ",").split(",")
    if item.strip()
]

profile_lines = []
if prompt_profile == "edit_first":
    profile_lines = [
        "OpenCode prompt profile: edit_first",
        f"OpenCode strategy: {strategy_name}",
        "- Make the smallest plausible tracked repository edit that fixes the issue.",
        "- Prefer editing a likely relevant file over prolonged exploration once you have enough evidence.",
        "- After applying the fix, stop and let AlphaDiana collect the diff.",
    ]

contract_lines = []
if require_patch:
    contract_lines = [
        "OpenCode execution contract:",
        "- Modify tracked repository files instead of only describing the fix.",
        "- Leave a non-empty git diff before stopping.",
        "- Do not stop at diagnosis only; keep working until you have applied a minimal fix or exhausted the run budget.",
    ]
if target_file_hints:
    contract_lines.append(
        "- Prefer editing one of these likely target files first: "
        + ", ".join(target_file_hints)
    )
if primary_target_file:
    contract_lines.extend(
        [
            f"- Your first tracked repository edit should be in `{primary_target_file}` unless it is clearly irrelevant.",
            "- Do not broaden beyond the primary target file before either making the first tracked edit or ruling that file out explicitly.",
        ]
    )

bootstrap_lines = [
    "OpenCode edit bootstrap:",
    f"- strategy_name: {strategy_name}",
    f"- target_file_hints_source: {target_file_hints_source}",
    f"- primary_target_file: {primary_target_file or '<none>'}",
]
bootstrap_prompt_lines = []
if strategy_name == "bash_edit_first" and target_file_hints:
    bootstrap_lines.extend(
        [
            "- Use bash to create the first tracked repository edit before prolonged diagnosis.",
            "- Do not spend your first turns proving the named tests; if the issue statement mentions missing test strings, treat that as noise and still edit a hinted file first.",
            "- If no dedicated edit tool appears, use a bash here-doc, `python - <<'\"'\"'PY'\"'\"'`, or `perl -0pi` style command to modify the file directly.",
            "- The first tracked edit may be a small temporary marker near the relevant logic, but it must touch a tracked repo file before more exploration.",
            "- Keep the first edit inside one of the hinted files unless they are clearly irrelevant.",
        ]
    )
    bootstrap_prompt_lines = [
        "OpenCode bash-first smoke bootstrap:",
        f"- Primary target file: {primary_target_file or '<none>'}",
        "- Your first substantive action must create a tracked repository edit in one hinted file.",
        "- Use `bash` to modify the file directly if no dedicated edit tool appears.",
        "- Do not spend your first turns proving the named tests or searching for missing test strings.",
        "- Prefer this hinted shortlist for the first edit:",
        *[f"- {item}" for item in target_file_hints],
        "- After the first tracked edit exists, continue diagnosis and refine it if needed.",
        "- Stop only after leaving a non-empty git diff.",
    ]
elif strategy_name == "guided_edit_first" and target_file_hints:
    bootstrap_lines.extend(
        [
            "- Inspect only these hinted files before broader repo exploration:",
            *[f"  - {item}" for item in target_file_hints],
            f"- Treat `{primary_target_file}` as the primary file to edit first." if primary_target_file else "- No primary target file was resolved.",
            "- Make a minimal tracked repository edit in one of them before broadening the search.",
            "- Only expand beyond the hinted files after inspecting them and ruling them out.",
        ]
    )
    bootstrap_prompt_lines = [
        "OpenCode guided edit bootstrap:",
        f"- Primary target file: {primary_target_file or '<none>'}",
        "- Inspect only these hinted files before broader repo exploration:",
        *[f"- {item}" for item in target_file_hints],
        "- Make a minimal tracked repository edit in one of them before broadening the search.",
        "- Only expand beyond the hinted files after inspecting them and ruling them out.",
    ]
elif target_file_hints:
    bootstrap_lines.extend(
        [
            "- Guided target-file bootstrap is disabled for this strategy.",
            *[f"  - hinted file: {item}" for item in target_file_hints],
        ]
    )
else:
    bootstrap_lines.append("- No target-file hints were resolved for this attempt.")

profile_text = "\n".join(profile_lines).strip()
contract_text = "\n".join(contract_lines).strip()
bootstrap_text = "\n".join(bootstrap_lines).strip()
bootstrap_prompt_text = "\n".join(bootstrap_prompt_lines).strip()
final_prompt = prompt.rstrip()
if strategy_name == "bash_edit_first":
    prompt_prefix = [item for item in [profile_text, contract_text, bootstrap_prompt_text] if item]
    if prompt_prefix:
        final_prompt = "\n\n".join([*prompt_prefix, final_prompt]).rstrip() + "\n"
else:
    if profile_text:
        final_prompt = f"{final_prompt}\n\n{profile_text}\n"
    if contract_text:
        final_prompt = f"{final_prompt}\n\n{contract_text}\n"
    if bootstrap_prompt_text:
        final_prompt = f"{final_prompt}\n\n{bootstrap_prompt_text}\n"

(artifacts / "opencode_candidate_models.txt").write_text(
    ("\n".join(candidate_models) + "\n") if candidate_models else "",
    encoding="utf-8",
)
(artifacts / "opencode_prompt_profile.txt").write_text(
    "\n".join(
        [
            f"prompt_profile: {prompt_profile}",
            f"strategy_name: {strategy_name}",
            f"problem_statement_max_chars: {os.environ.get('OPENCODE_PROBLEM_STATEMENT_MAX_CHARS', '12000')}",
            f"target_file_hints_source: {target_file_hints_source}",
            f"target_file_hints: {', '.join(target_file_hints)}",
            f"primary_target_file: {primary_target_file or '<none>'}",
            f"prompt_prefix_mode: {'prepend' if strategy_name == 'bash_edit_first' else 'append'}",
            f"resolved_model_alias: {os.environ.get('OPENCODE_MODEL_NAME', '')}",
        ]
    )
    + "\n",
    encoding="utf-8",
)
(artifacts / "opencode_target_file_hints.txt").write_text(
    "\n".join(
        [
            f"strategy_name: {strategy_name}",
            f"target_file_hints_source: {target_file_hints_source}",
            f"primary_target_file: {primary_target_file or '<none>'}",
            "target_file_hints:",
            *([f"- {item}" for item in target_file_hints] if target_file_hints else ["<none>"]),
        ]
    )
    + "\n",
    encoding="utf-8",
)
(artifacts / "opencode_prompt_contract.txt").write_text(
    (contract_text + "\n") if contract_text else "",
    encoding="utf-8",
)
(artifacts / "opencode_edit_bootstrap.txt").write_text(
    (bootstrap_text + "\n") if bootstrap_text else "",
    encoding="utf-8",
)
(artifacts / "opencode_edit_contract.txt").write_text(
    "\n".join(
        [
            f"prompt_profile: {prompt_profile}",
            f"strategy_name: {strategy_name}",
            f"require_patch: {str(require_patch).lower()}",
            f"target_file_hints_source: {target_file_hints_source}",
            f"primary_target_file: {primary_target_file or '<none>'}",
            "contract_lines:",
            *([f"- {line}" for line in contract_lines] if contract_lines else ["<none>"]),
        ]
    )
    + "\n",
    encoding="utf-8",
)

print(final_prompt, end="")
PY
)"

PREFLIGHT_PROMPT='Reply with a single JSON object {"status":"ok"} and no extra commentary.'
PREFLIGHT_DIR="$WORKDIR/preflight"
PREFLIGHT_STDOUT="$WORKDIR/opencode_preflight.stdout"
PREFLIGHT_STDERR="$WORKDIR/opencode_preflight.stderr"
mkdir -p "$PREFLIGHT_DIR"
set +e
(
  cd "$PREFLIGHT_DIR"
  exec timeout "$OPENCODE_PREFLIGHT_TIMEOUT" opencode run --format json "$PREFLIGHT_PROMPT"
) >"$PREFLIGHT_STDOUT" 2>"$PREFLIGHT_STDERR"
OPENCODE_PREFLIGHT_STATUS=$?
set -e

OPENCODE_PREFLIGHT_RESULT="$(
PREFLIGHT_STDOUT="$PREFLIGHT_STDOUT" \
PREFLIGHT_STDERR="$PREFLIGHT_STDERR" \
OPENCODE_PROVIDER_PREFLIGHT_PATH="$OPENCODE_PROVIDER_PREFLIGHT_PATH" \
OPENCODE_PREFLIGHT_STATUS="$OPENCODE_PREFLIGHT_STATUS" \
OPENCODE_PREFLIGHT_TIMEOUT="$OPENCODE_PREFLIGHT_TIMEOUT" \
OPENCODE_MODEL_NAME="$OPENCODE_MODEL_NAME" \
python3 - <<'PY'
import json
import os
from pathlib import Path

stdout_path = Path(os.environ["PREFLIGHT_STDOUT"])
stderr_path = Path(os.environ["PREFLIGHT_STDERR"])
artifact_path = Path(os.environ["OPENCODE_PROVIDER_PREFLIGHT_PATH"])
stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
exit_code = int(os.environ["OPENCODE_PREFLIGHT_STATUS"])
error_message = ""

records = []
for index, line in enumerate(stdout_text.splitlines(), start=1):
    stripped = line.strip()
    if not stripped:
        continue
    try:
        records.append(json.loads(stripped))
    except json.JSONDecodeError:
        error_message = f"invalid JSON record on line {index}"
        break

if not error_message:
    if exit_code != 0:
        error_message = f"opencode preflight exited with status {exit_code}"
    elif not records:
        error_message = "opencode preflight produced no JSON records"
    else:
        for record in records:
            if record.get("type") == "error":
                error = record.get("error") or {}
                data = error.get("data") if isinstance(error, dict) else {}
                message = ""
                for candidate in (
                    data.get("message") if isinstance(data, dict) else "",
                    error.get("message") if isinstance(error, dict) else "",
                    error.get("name") if isinstance(error, dict) else "",
                    record.get("message"),
                ):
                    if isinstance(candidate, str) and candidate.strip():
                        message = candidate.strip()
                        break
                error_message = message or "opencode preflight returned an error record"
                break

status = "passed" if not error_message else "failed"
lines = [
    "OpenCode provider preflight",
    f"status: {status}",
    f"model: {os.environ['OPENCODE_MODEL_NAME']}",
    f"timeout_sec: {os.environ['OPENCODE_PREFLIGHT_TIMEOUT']}",
    f"exit_code: {exit_code}",
    f"stdout_bytes: {len(stdout_text.encode('utf-8'))}",
    f"stderr_bytes: {len(stderr_text.encode('utf-8'))}",
]
if error_message:
    lines.append(f"error: {error_message}")
lines.extend([
    "",
    "===== stdout =====",
    stdout_text.rstrip() or "<empty>",
    "",
    "===== stderr =====",
    stderr_text.rstrip() or "<empty>",
])
artifact_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
print(status)
PY
)"

rm -rf "$HOME/.opencode"
mkdir -p "$HOME"

if [[ "$OPENCODE_PREFLIGHT_RESULT" != "passed" ]]; then
  exit 1
fi

if [[ -n "${ALPHADIANA_OPENCODE_PROXY_BASE_URL:-}" ]]; then
  export OPENAI_BASE_URL="$ALPHADIANA_OPENCODE_PROXY_BASE_URL"
  if [[ -n "${ALPHADIANA_OPENCODE_PROXY_API_KEY:-}" ]]; then
    export OPENAI_API_KEY="$ALPHADIANA_OPENCODE_PROXY_API_KEY"
  fi
  write_opencode_config
fi

git -C "$REPO_ROOT" status --short >"$OPENCODE_GIT_STATUS_BEFORE_PATH" || true

log "OpenCode config: model=$OPENCODE_MODEL_NAME baseURL=$OPENAI_BASE_URL repo_root=$REPO_ROOT timeout=${OPENCODE_TIMEOUT}s preflight_timeout=${OPENCODE_PREFLIGHT_TIMEOUT}s startup_timeout=${OPENCODE_STARTUP_TIMEOUT}s idle_timeout=${OPENCODE_IDLE_TIMEOUT}s"

set +e
(
  cd "$REPO_ROOT"
  exec setsid bash -lc 'exec timeout "$1" opencode run --format json "$2"' _ "$OPENCODE_TIMEOUT" "$PROMPT"
) >"$OPENCODE_OUTPUT_PATH" 2>"$OPENCODE_STDERR_PATH" &
OPENCODE_PID=$!
set -e

LAST_PROGRESS_SIGNATURE=""
LAST_PROGRESS_TS="$(date +%s)"
STALL_DETECTED=0
STARTUP_TIMEOUT_DETECTED=0
ACTIVE_NO_EDIT_DETECTED=0
FIRST_STDOUT_TS=""
FIRST_STDERR_TS=""
FIRST_SESSION_TS=""
FIRST_STDOUT_OR_SESSION_TS=""
PROCESS_START_TS="$(date +%s)"
FIRST_ACTIVITY_TS_EPOCH=""
LAST_ACTIVITY_HEARTBEAT_TS="$PROCESS_START_TS"

while kill -0 "$OPENCODE_PID" 2>/dev/null; do
  CURRENT_SIGNATURE="$(collect_progress_signature)"
  if [[ "$CURRENT_SIGNATURE" != "$LAST_PROGRESS_SIGNATURE" ]]; then
    LAST_PROGRESS_SIGNATURE="$CURRENT_SIGNATURE"
    LAST_PROGRESS_TS="$(date +%s)"
  fi

  CURRENT_STDOUT_BYTES="0"
  CURRENT_STDERR_BYTES="0"
  if [[ -f "$OPENCODE_OUTPUT_PATH" ]]; then
    CURRENT_STDOUT_BYTES="$(wc -c <"$OPENCODE_OUTPUT_PATH" | tr -d ' ')"
  fi
  if [[ -f "$OPENCODE_STDERR_PATH" ]]; then
    CURRENT_STDERR_BYTES="$(wc -c <"$OPENCODE_STDERR_PATH" | tr -d ' ')"
  fi
  CURRENT_SESSION_COUNT="$(count_session_jsonl)"
  NOW_ISO="$(timestamp_now)"
  if [[ -z "$FIRST_STDOUT_TS" && "$CURRENT_STDOUT_BYTES" != "0" ]]; then
    FIRST_STDOUT_TS="$NOW_ISO"
  fi
  if [[ -z "$FIRST_STDERR_TS" && "$CURRENT_STDERR_BYTES" != "0" ]]; then
    FIRST_STDERR_TS="$NOW_ISO"
  fi
  if [[ -z "$FIRST_SESSION_TS" && "$CURRENT_SESSION_COUNT" != "0" ]]; then
    FIRST_SESSION_TS="$NOW_ISO"
  fi
  if [[ -z "$FIRST_STDOUT_OR_SESSION_TS" && ( "$CURRENT_STDOUT_BYTES" != "0" || "$CURRENT_SESSION_COUNT" != "0" ) ]]; then
    FIRST_STDOUT_OR_SESSION_TS="$NOW_ISO"
  fi

  NOW_TS="$(date +%s)"
  if [[ -z "$FIRST_STDOUT_OR_SESSION_TS" ]] && (( NOW_TS - PROCESS_START_TS >= OPENCODE_STARTUP_TIMEOUT )); then
    write_startup_diagnostics "no_activity_within_timeout"
    terminate_process_group "$OPENCODE_PID"
    STARTUP_TIMEOUT_DETECTED=1
    break
  fi
  if [[ -n "$FIRST_STDOUT_OR_SESSION_TS" ]]; then
    if [[ -z "$FIRST_ACTIVITY_TS_EPOCH" ]]; then
      FIRST_ACTIVITY_TS_EPOCH="$NOW_TS"
      LAST_ACTIVITY_HEARTBEAT_TS="$NOW_TS"
    fi
    ACTIVE_ELAPSED_SEC=$(( NOW_TS - FIRST_ACTIVITY_TS_EPOCH ))
    CURRENT_TRACKED_REPO_CHANGES="$(count_tracked_repo_changes)"
    CURRENT_TOOL_USE_COUNT="$(count_tool_use_events)"

    if (( NOW_TS - LAST_ACTIVITY_HEARTBEAT_TS >= OPENCODE_ACTIVITY_HEARTBEAT_SEC )); then
      CURRENT_TRACKED_REPO_CHANGED_PATHS_JSON="$(tracked_repo_changed_paths_json)"
      write_activity_summary "activity_observed" \
        "$ACTIVE_ELAPSED_SEC" \
        "$CURRENT_TOOL_USE_COUNT" \
        "$CURRENT_TRACKED_REPO_CHANGES" \
        "$CURRENT_TRACKED_REPO_CHANGED_PATHS_JSON"
      LAST_ACTIVITY_HEARTBEAT_TS="$NOW_TS"
    fi

    if [[ "$CURRENT_TRACKED_REPO_CHANGES" == "0" ]] && \
      (( ACTIVE_ELAPSED_SEC >= OPENCODE_MAX_ACTIVE_NO_EDIT_SEC || CURRENT_TOOL_USE_COUNT >= OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT )); then
      ACTIVE_NO_EDIT_REASON="OpenCode active session remained live for ${ACTIVE_ELAPSED_SEC}s and ${CURRENT_TOOL_USE_COUNT} tool_use events without tracked repository edits"
      if (( CURRENT_TOOL_USE_COUNT >= OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT )); then
        ACTIVE_NO_EDIT_REASON="${ACTIVE_NO_EDIT_REASON}; tool threshold ${OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT} reached"
      fi
      if (( ACTIVE_ELAPSED_SEC >= OPENCODE_MAX_ACTIVE_NO_EDIT_SEC )); then
        ACTIVE_NO_EDIT_REASON="${ACTIVE_NO_EDIT_REASON}; no-edit timeout ${OPENCODE_MAX_ACTIVE_NO_EDIT_SEC}s reached"
      fi
      record_active_no_edit_artifacts \
        "active_session_no_patch" \
        "$ACTIVE_NO_EDIT_REASON" \
        "$ACTIVE_ELAPSED_SEC" \
        "$CURRENT_TOOL_USE_COUNT" \
        "$CURRENT_TRACKED_REPO_CHANGES"
      terminate_process_group "$OPENCODE_PID"
      ACTIVE_NO_EDIT_DETECTED=1
      break
    fi
  fi
  if (( NOW_TS - LAST_PROGRESS_TS >= OPENCODE_IDLE_TIMEOUT )); then
    if [[ -n "${FIRST_STDOUT_OR_SESSION_TS:-}" ]]; then
      write_startup_diagnostics "activity_observed"
    else
      write_startup_diagnostics "no_activity_within_timeout"
    fi
    printf 'OpenCode stalled after %ss without output progress\n' "$OPENCODE_IDLE_TIMEOUT" \
      >"$OPENCODE_STALL_REASON_PATH"
    write_progress_snapshot
    terminate_process_group "$OPENCODE_PID"
    STALL_DETECTED=1
    break
  fi
  sleep "$OPENCODE_IDLE_POLL"
done

set +e
wait "$OPENCODE_PID"
OPENCODE_STATUS=$?
set -e

git -C "$REPO_ROOT" status --short >"$OPENCODE_GIT_STATUS_AFTER_PATH" || true

FINAL_TRACKED_REPO_CHANGES="$(count_tracked_repo_changes)"
FINAL_TOOL_USE_COUNT="$(count_tool_use_events)"
FINAL_ACTIVE_ELAPSED_SEC=0
if [[ -n "$FIRST_ACTIVITY_TS_EPOCH" ]]; then
  FINAL_ACTIVE_ELAPSED_SEC=$(( $(date +%s) - FIRST_ACTIVITY_TS_EPOCH ))
fi

if [[ ! -f "$OPENCODE_STARTUP_DIAGNOSTICS_PATH" ]]; then
  if [[ -n "$FIRST_STDOUT_OR_SESSION_TS" ]]; then
    write_startup_diagnostics "activity_observed"
  else
    write_startup_diagnostics "process_exited_before_activity"
  fi
fi

if [[ -d "$HOME/.opencode" ]]; then
  OPENCODE_HOME_DIR="$HOME/.opencode" python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["OPENCODE_HOME_DIR"])
target = Path(os.environ["ARTIFACTS_DIR"]) / "opencode_session.jsonl"
parts = []
for path in sorted(root.rglob("*.jsonl")):
    text = path.read_text(encoding="utf-8", errors="replace")
    parts.append(f"# file: {path}\n{text.rstrip()}\n")
if parts:
    target.write_text("\n".join(parts), encoding="utf-8")
PY
fi

if [[ $ACTIVE_NO_EDIT_DETECTED -eq 1 ]]; then
  exit 1
fi
if [[ $STARTUP_TIMEOUT_DETECTED -eq 1 ]]; then
  exit 1
fi
if [[ $STALL_DETECTED -eq 1 || -f "$OPENCODE_STALL_REASON_PATH" ]]; then
  exit 1
fi

if [[ ! -f "$OPENCODE_OUTPUT_PATH" ]]; then
  die "OpenCode did not produce opencode_output.jsonl"
fi
if [[ ! -s "$OPENCODE_OUTPUT_PATH" ]]; then
  die "OpenCode produced an empty opencode_output.jsonl"
fi

OPENCODE_ERROR_CHECK_STATUS=0
OPENCODE_ERROR_MSG=""
set +e
OPENCODE_ERROR_MSG="$(ARTIFACTS_DIR_ENV="$ARTIFACTS_DIR" python3 - <<'PY' 2>&1
import json
import os
from pathlib import Path

path = Path(os.environ["ARTIFACTS_DIR_ENV"]) / "opencode_output.jsonl"
records = []
for index, line in enumerate(
    path.read_text(encoding="utf-8", errors="replace").splitlines(),
    start=1,
):
    if not line.strip():
        continue
    try:
        records.append(json.loads(line))
    except json.JSONDecodeError as exc:
        print(f"invalid JSONL record on line {index}: {exc}")
        raise SystemExit(1)

if not records:
    print("OpenCode produced no JSON records")
    raise SystemExit(1)

for record in records:
    if record.get("type") == "error":
        error = record.get("error", {})
        data = error.get("data", {}) if isinstance(error, dict) else {}
        message = ""
        for candidate in (
            data.get("message") if isinstance(data, dict) else "",
            error.get("message") if isinstance(error, dict) else "",
            error.get("name") if isinstance(error, dict) else "",
            record.get("message"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                message = candidate.strip()
                break
        print(message or "unknown error")
        raise SystemExit(1)

print("")
PY
)"
OPENCODE_ERROR_CHECK_STATUS=$?
set -e

if [[ $OPENCODE_ERROR_CHECK_STATUS -ne 0 ]]; then
  die "OpenCode agent error: ${OPENCODE_ERROR_MSG:-unknown error}"
fi

if [[ $OPENCODE_STATUS -ne 0 && -n "$FIRST_STDOUT_OR_SESSION_TS" && "$FINAL_TRACKED_REPO_CHANGES" == "0" ]]; then
  ACTIVE_NO_EDIT_REASON="OpenCode active session timed out without repository edits (status=${OPENCODE_STATUS}, active_elapsed_sec=${FINAL_ACTIVE_ELAPSED_SEC}, tool_use_count=${FINAL_TOOL_USE_COUNT})"
  record_active_no_edit_artifacts \
    "active_session_no_patch" \
    "$ACTIVE_NO_EDIT_REASON" \
    "$FINAL_ACTIVE_ELAPSED_SEC" \
    "$FINAL_TOOL_USE_COUNT" \
    "$FINAL_TRACKED_REPO_CHANGES"
  die "$ACTIVE_NO_EDIT_REASON"
fi

if [[ $OPENCODE_STATUS -ne 0 ]]; then
  die "OpenCode exited with status $OPENCODE_STATUS"
fi

git -C "$REPO_ROOT" diff --binary >"$ARTIFACTS_DIR/patch.diff"
if [[ ! -s "$ARTIFACTS_DIR/patch.diff" ]]; then
  if [[ -n "$FIRST_STDOUT_OR_SESSION_TS" && "$FINAL_TRACKED_REPO_CHANGES" == "0" ]]; then
    ACTIVE_NO_EDIT_REASON="OpenCode active session completed without repository edits (active_elapsed_sec=${FINAL_ACTIVE_ELAPSED_SEC}, tool_use_count=${FINAL_TOOL_USE_COUNT})"
    record_active_no_edit_artifacts \
      "active_session_no_patch" \
      "$ACTIVE_NO_EDIT_REASON" \
      "$FINAL_ACTIVE_ELAPSED_SEC" \
      "$FINAL_TOOL_USE_COUNT" \
      "$FINAL_TRACKED_REPO_CHANGES"
    die "$ACTIVE_NO_EDIT_REASON"
  fi
  die "OpenCode run completed but produced an empty patch.diff"
fi

if [[ -n "$FIRST_STDOUT_OR_SESSION_TS" ]]; then
  FINAL_TRACKED_REPO_CHANGED_PATHS_JSON="$(tracked_repo_changed_paths_json)"
  write_activity_summary \
    "patch_created" \
    "$FINAL_ACTIVE_ELAPSED_SEC" \
    "$FINAL_TOOL_USE_COUNT" \
    "$FINAL_TRACKED_REPO_CHANGES" \
    "$FINAL_TRACKED_REPO_CHANGED_PATHS_JSON"
fi

printf '%s\n' "$REPO_ROOT" >"$ARTIFACTS_DIR/repo_root.txt"
log "OpenCode run finished"
