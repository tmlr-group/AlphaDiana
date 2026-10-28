#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[alphadiana-zeroclaw] %s\n' "$*"
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

require_env OPENAI_API_KEY
require_env OPENAI_BASE_URL
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
WORKDIR="$(mktemp -d /tmp/alphadiana-zeroclaw.XXXXXX)"
export ARTIFACTS_DIR
export WORKDIR
export HOME="$WORKDIR/home"
mkdir -p "$HOME/.zeroclaw"

ZEROCLAW_MODEL_NAME="${ZEROCLAW_SMOKE_MODEL_NAME:-${OPENAI_MODEL_NAME:-}}"
if [[ -z "$ZEROCLAW_MODEL_NAME" ]]; then
  log "Missing required environment variable: OPENAI_MODEL_NAME or ZEROCLAW_SMOKE_MODEL_NAME"
  exit 1
fi
export ZEROCLAW_MODEL_NAME
ZEROCLAW_TIMEOUT="${ZEROCLAW_TIMEOUT_SEC:-1200}"
ZEROCLAW_PROVIDER_TIMEOUT_SECS="${ZEROCLAW_PROVIDER_TIMEOUT_SECS:-${ZEROCLAW_TIMEOUT}}"
ZEROCLAW_PROVIDER_MAX_TOKENS="${ZEROCLAW_PROVIDER_MAX_TOKENS:-}"
ZEROCLAW_PROMPT_PROFILE="${ZEROCLAW_PROMPT_PROFILE:-edit_first}"
ZEROCLAW_REQUIRE_PATCH_RAW="${ZEROCLAW_REQUIRE_PATCH:-1}"
ZEROCLAW_WORKSPACE_ONLY_RAW="${ZEROCLAW_WORKSPACE_ONLY:-false}"
ZEROCLAW_MAX_TOOL_ITERATIONS="${ZEROCLAW_MAX_TOOL_ITERATIONS:-100}"
ZEROCLAW_MAX_ACTIONS_PER_HOUR="${ZEROCLAW_MAX_ACTIONS_PER_HOUR:-200}"
ZEROCLAW_RUNTIME_TRACE_MODE="${ZEROCLAW_RUNTIME_TRACE_MODE:-none}"
ZEROCLAW_REASONING_ENABLED="${ZEROCLAW_REASONING_ENABLED:-}"
ZEROCLAW_REASONING_EFFORT="${ZEROCLAW_REASONING_EFFORT:-}"
ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS="${ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS:-12000}"
ZEROCLAW_SMOKE_MODEL_CANDIDATES="${ZEROCLAW_SMOKE_MODEL_CANDIDATES:-${ZEROCLAW_MODEL_NAME}}"
ZEROCLAW_RUNTIME_TRACE_PATH="$ARTIFACTS_DIR/runtime_trace.jsonl"
ZEROCLAW_OUTPUT_PATH="$ARTIFACTS_DIR/zeroclaw_output.txt"
ZEROCLAW_STDERR_PATH="$ARTIFACTS_DIR/zeroclaw_stderr.log"
ZEROCLAW_NO_EDIT_REASON_PATH="$ARTIFACTS_DIR/zeroclaw_no_edit_reason.txt"
ZEROCLAW_PROMPT_CONTRACT_PATH="$ARTIFACTS_DIR/zeroclaw_prompt_contract.txt"
ZEROCLAW_PROMPT_PROFILE_PATH="$ARTIFACTS_DIR/zeroclaw_prompt_profile.txt"
ZEROCLAW_CANDIDATE_MODELS_PATH="$ARTIFACTS_DIR/zeroclaw_candidate_models.txt"
ZEROCLAW_ATTEMPT_MATRIX_PATH="$ARTIFACTS_DIR/zeroclaw_attempt_matrix.json"
ZEROCLAW_SELECTED_ATTEMPT_PATH="$ARTIFACTS_DIR/zeroclaw_selected_attempt.json"
ZEROCLAW_GIT_STATUS_BEFORE_PATH="$ARTIFACTS_DIR/git_status_before.txt"
ZEROCLAW_GIT_STATUS_AFTER_PATH="$ARTIFACTS_DIR/git_status_after.txt"
ZEROCLAW_PATCH_PATH="$ARTIFACTS_DIR/patch.diff"
ZEROCLAW_REPO_ROOT_PATH="$ARTIFACTS_DIR/repo_root.txt"
ZEROCLAW_RUN_STATUS=0
ZEROCLAW_MEMORY_DIR_EXISTED=0
ZEROCLAW_STATE_DIR_EXISTED=0

write_zeroclaw_attempt_artifacts() {
  python3 - <<'PY'
import json
import os
from pathlib import Path


def _is_truthy(value: str) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


artifacts = Path(os.environ["ARTIFACTS_DIR"])
artifacts.mkdir(parents=True, exist_ok=True)
prompt_profile = os.environ.get("ZEROCLAW_PROMPT_PROFILE", "edit_first").strip() or "edit_first"
problem_statement_max_chars = os.environ.get("ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS", "12000")
candidate_models = [
    item.strip()
    for item in os.environ.get("ZEROCLAW_SMOKE_MODEL_CANDIDATES", "").replace("\n", ",").split(",")
    if item.strip()
]
candidate_models_path = artifacts / "zeroclaw_candidate_models.txt"
candidate_models_path.write_text(
    ("\n".join(candidate_models) + "\n") if candidate_models else "",
    encoding="utf-8",
)
prompt_profile_path = artifacts / "zeroclaw_prompt_profile.txt"
prompt_profile_path.write_text(
    "\n".join(
        [
            f"prompt_profile: {prompt_profile}",
            f"problem_statement_max_chars: {problem_statement_max_chars}",
            f"require_patch: {str(_is_truthy(os.environ.get('ZEROCLAW_REQUIRE_PATCH', '1'))).lower()}",
            f"workspace_only: {str(_is_truthy(os.environ.get('ZEROCLAW_WORKSPACE_ONLY', 'false'))).lower()}",
            f"resolved_model_alias: {os.environ.get('ZEROCLAW_MODEL_NAME', '').strip()}",
        ]
    )
    + "\n",
    encoding="utf-8",
)
prompt_contract_path = artifacts / "zeroclaw_prompt_contract.txt"
prompt_contract_path.write_text(
    "\n".join(
        [
            "ZeroClaw execution contract:",
            "- Work directly in the checked-out repository.",
            "- Do not print a patch manually.",
            "- Leave a non-empty git diff in the repository before stopping.",
        ]
    )
    + "\n",
    encoding="utf-8",
)

git_status_after_lines = [
    line.rstrip()
    for line in _read_text(artifacts / "git_status_after.txt").splitlines()
    if line.strip()
]
tracked_repo_change_count = 0
untracked_repo_change_count = 0
tracked_repo_changed_paths: list[str] = []
for line in git_status_after_lines:
    stripped = line.strip()
    if stripped.startswith("?? "):
        untracked_repo_change_count += 1
        continue
    tracked_repo_change_count += 1
    path = stripped[3:].strip() if len(stripped) > 3 else ""
    if path and path not in tracked_repo_changed_paths:
        tracked_repo_changed_paths.append(path)

patch_path = artifacts / "patch.diff"
patch_size = patch_path.stat().st_size if patch_path.exists() else 0
run_status = int(os.environ.get("ZEROCLAW_RUN_STATUS", "0"))
stderr_text = _read_text(artifacts / "zeroclaw_stderr.log").strip()
stdout_text = _read_text(artifacts / "zeroclaw_output.txt").strip()
no_edit_reason_path = artifacts / "zeroclaw_no_edit_reason.txt"
existing_no_edit_reason = _read_text(no_edit_reason_path).strip()

if patch_size > 0:
    classification = "patch_created"
elif run_status != 0:
    classification = "cli_error"
elif untracked_repo_change_count > 0 and tracked_repo_change_count == 0:
    classification = "untracked_only_changes"
else:
    classification = "active_session_no_patch"

reason = existing_no_edit_reason
if not reason and classification == "cli_error":
    if run_status == 124:
        reason = f"ZeroClaw agent timed out after {os.environ.get('ZEROCLAW_TIMEOUT_SEC', '1200')}s"
    else:
        reason = stderr_text.splitlines()[-1] if stderr_text else ""
        if not reason and stdout_text:
            reason = stdout_text.splitlines()[-1]
        if not reason:
            reason = f"ZeroClaw agent exited with status {run_status}"
elif not reason and classification == "untracked_only_changes":
    reason = "Only untracked files changed; git diff --binary remained empty"
elif not reason and classification == "active_session_no_patch":
    reason = "ZeroClaw finished without tracked repository edits"

if patch_size == 0:
    no_edit_reason_path.write_text((reason or "no patch produced") + "\n", encoding="utf-8")

record = {
    "attempt_index": 1,
    "resolved_model_alias": os.environ.get("ZEROCLAW_MODEL_NAME", "").strip(),
    "prompt_profile": prompt_profile,
    "classification": classification,
    "patch_size_bytes": patch_size,
    "tracked_repo_change_count": tracked_repo_change_count,
    "tracked_repo_changed_paths": tracked_repo_changed_paths,
    "untracked_repo_change_count": untracked_repo_change_count,
    "reason": reason or "unknown ZeroClaw outcome",
    "returncode": run_status,
    "artifacts_dir": str(artifacts),
}
(artifacts / "zeroclaw_attempt_matrix.json").write_text(
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
(artifacts / "zeroclaw_selected_attempt.json").write_text(
    json.dumps(record, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
}

cleanup() {
  export ZEROCLAW_RUN_STATUS
  write_zeroclaw_attempt_artifacts || true
}
trap cleanup EXIT

zeroclaw_api_base="${OPENAI_BASE_URL%/}"
zeroclaw_provider="openai"
if [[ "${zeroclaw_api_base}" == *"openrouter"* ]]; then
  zeroclaw_provider="openrouter"
elif [[ -n "${zeroclaw_api_base}" && "${zeroclaw_api_base}" != "https://api.openai.com" && "${zeroclaw_api_base}" != "https://api.openai.com/v1" ]]; then
  zeroclaw_provider="custom:${zeroclaw_api_base}"
fi

export ZEROCLAW_API_KEY="${OPENAI_API_KEY}"
export OPENROUTER_API_KEY="${OPENAI_API_KEY}"
export ZEROCLAW_PROVIDER="${zeroclaw_provider}"
export ZEROCLAW_RUNTIME_TRACE_PATH

if [[ -e "$REPO_ROOT/memory" ]]; then
  ZEROCLAW_MEMORY_DIR_EXISTED=1
fi
if [[ -e "$REPO_ROOT/state" ]]; then
  ZEROCLAW_STATE_DIR_EXISTED=1
fi

ln -sfn "$REPO_ROOT" "$HOME/.zeroclaw/workspace"
printf '%s\n' "$REPO_ROOT" >"$ZEROCLAW_REPO_ROOT_PATH"

python3 - <<'PY'
import json
import os
from pathlib import Path


def _is_truthy(value: str) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


template = Path(os.environ["ALPHADIANA_CONFIG_TEMPLATE"]).read_text(encoding="utf-8")
replacements = {
    "__ZEROCLAW_DEFAULT_PROVIDER__": json.dumps(os.environ["ZEROCLAW_PROVIDER"]),
    "__ZEROCLAW_DEFAULT_MODEL__": json.dumps(os.environ["ZEROCLAW_MODEL_NAME"]),
    "__ZEROCLAW_DEFAULT_TEMPERATURE__": str(float(os.environ.get("ZEROCLAW_TEMPERATURE", "0.2"))),
    "__ZEROCLAW_PROVIDER_TIMEOUT_SECS__": str(int(os.environ.get("ZEROCLAW_PROVIDER_TIMEOUT_SECS", "1200"))),
    "__ZEROCLAW_RUNTIME_TRACE_MODE__": json.dumps(os.environ.get("ZEROCLAW_RUNTIME_TRACE_MODE", "none")),
    "__ZEROCLAW_RUNTIME_TRACE_PATH__": json.dumps(os.environ["ZEROCLAW_RUNTIME_TRACE_PATH"]),
    "__ZEROCLAW_WORKSPACE_ONLY__": "true" if _is_truthy(os.environ.get("ZEROCLAW_WORKSPACE_ONLY", "false")) else "false",
    "__ZEROCLAW_MAX_ACTIONS_PER_HOUR__": str(int(os.environ.get("ZEROCLAW_MAX_ACTIONS_PER_HOUR", "200"))),
    "__ZEROCLAW_MAX_TOOL_ITERATIONS__": str(int(os.environ.get("ZEROCLAW_MAX_TOOL_ITERATIONS", "100"))),
}
provider_max_tokens = os.environ.get("ZEROCLAW_PROVIDER_MAX_TOKENS", "").strip()
replacements["__ZEROCLAW_PROVIDER_MAX_TOKENS_LINE__"] = (
    f"provider_max_tokens = {int(provider_max_tokens)}\n"
    if provider_max_tokens
    else ""
)
runtime_lines = []
reasoning_enabled = os.environ.get("ZEROCLAW_REASONING_ENABLED", "").strip()
if reasoning_enabled:
    runtime_lines.append(
        f"reasoning_enabled = {str(_is_truthy(reasoning_enabled)).lower()}"
    )
reasoning_effort = os.environ.get("ZEROCLAW_REASONING_EFFORT", "").strip()
if reasoning_effort:
    runtime_lines.append(f"reasoning_effort = {json.dumps(reasoning_effort)}")
replacements["__ZEROCLAW_RUNTIME_SECTION__"] = (
    "[runtime]\n" + "\n".join(runtime_lines) + "\n\n"
    if runtime_lines
    else ""
)
for key, value in replacements.items():
    template = template.replace(key, value)
config_path = Path(os.environ["HOME"]) / ".zeroclaw" / "config.toml"
config_path.write_text(template, encoding="utf-8")
PY
chmod 600 "$HOME/.zeroclaw/config.toml"

git -C "$REPO_ROOT" status --short >"$ZEROCLAW_GIT_STATUS_BEFORE_PATH" || true

log "ZeroClaw config: model=$ZEROCLAW_MODEL_NAME baseURL=$OPENAI_BASE_URL timeout=${ZEROCLAW_TIMEOUT}s repo_root=$REPO_ROOT"
set +e
cd "$REPO_ROOT"
prompt="$(cat "$ALPHADIANA_PROMPT_FILE")"
timeout "$ZEROCLAW_TIMEOUT" zeroclaw agent -m "$prompt" >"$ZEROCLAW_OUTPUT_PATH" 2>"$ZEROCLAW_STDERR_PATH"
ZEROCLAW_RUN_STATUS=$?
set -e

if [[ "$ZEROCLAW_MEMORY_DIR_EXISTED" -eq 0 && -e "$REPO_ROOT/memory" ]]; then
  rm -rf "$REPO_ROOT/memory"
fi
if [[ "$ZEROCLAW_STATE_DIR_EXISTED" -eq 0 && -e "$REPO_ROOT/state" ]]; then
  rm -rf "$REPO_ROOT/state"
fi

git -C "$REPO_ROOT" status --short >"$ZEROCLAW_GIT_STATUS_AFTER_PATH" || true
git -C "$REPO_ROOT" diff --binary >"$ZEROCLAW_PATCH_PATH"

if [[ ! -s "$ZEROCLAW_OUTPUT_PATH" && "$ZEROCLAW_RUN_STATUS" -eq 0 ]]; then
  printf 'ZeroClaw agent produced no output\n' >"$ZEROCLAW_NO_EDIT_REASON_PATH"
  ZEROCLAW_RUN_STATUS=1
fi

if [[ ! -s "$ZEROCLAW_PATCH_PATH" && "${ZEROCLAW_REQUIRE_PATCH_RAW,,}" != "0" && "${ZEROCLAW_REQUIRE_PATCH_RAW,,}" != "false" && "${ZEROCLAW_REQUIRE_PATCH_RAW,,}" != "no" && "${ZEROCLAW_REQUIRE_PATCH_RAW,,}" != "off" ]]; then
  if [[ "$ZEROCLAW_RUN_STATUS" -eq 0 ]]; then
    ZEROCLAW_RUN_STATUS=1
  fi
fi

exit "$ZEROCLAW_RUN_STATUS"
