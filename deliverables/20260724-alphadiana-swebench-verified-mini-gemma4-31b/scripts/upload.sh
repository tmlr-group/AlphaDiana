#!/usr/bin/env bash
set -euo pipefail

AGENT="${1:-}"
RUN_VERSION="${2:-${RUN_VERSION:-v01}}"
HF_REPO="${HF_REPO:-T-MARS/alphadiana-benchmark-results}"
RUN_DATE="${RUN_DATE:-$(date +%Y%m%d)}"

usage() {
  printf 'Usage: %s {directllm|openclaw|opencode|zeroclaw} [vNN]\n' "$0" >&2
  exit 2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "$AGENT" =~ ^(directllm|openclaw|opencode|zeroclaw)$ ]] || usage
[[ "$RUN_VERSION" =~ ^v[0-9][0-9]$ ]] || fail "version must match vNN"
[[ "$RUN_DATE" =~ ^[0-9]{8}$ ]] || fail "RUN_DATE must match YYYYMMDD"
command -v hf >/dev/null 2>&1 || fail "hf CLI is required"

RUN_ID="full_swe_bench_verified_mini_${AGENT}_gemma4_31b_${RUN_VERSION}"
HF_FOLDER="${RUN_DATE}-swe-bench-verified-mini-${AGENT}-gemma-4-31b-it-${RUN_VERSION}"
STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/alphadiana-hf-upload.XXXXXX")"
PAYLOAD="$STAGE_ROOT/$HF_FOLDER"
mkdir -p "$PAYLOAD"

cleanup() {
  rm -rf "$STAGE_ROOT"
}
trap cleanup EXIT

if [[ "$AGENT" == "directllm" ]]; then
  [[ -n "${DIRECTLLM_SWE_VERIFIED_ROOT:-}" ]] || fail "DIRECTLLM_SWE_VERIFIED_ROOT is required"
  SOURCE="$DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/$RUN_ID"
  [[ -d "$SOURCE" ]] || fail "missing run directory: $SOURCE"
  cp -a "$SOURCE/." "$PAYLOAD/"
  EVAL_LOGS="$DIRECTLLM_SWE_VERIFIED_ROOT/logs/run_evaluation/$RUN_ID"
  if [[ -d "$EVAL_LOGS" ]]; then
    mkdir -p "$PAYLOAD/official_evaluation"
    cp -a "$EVAL_LOGS/." "$PAYLOAD/official_evaluation/"
  fi
else
  [[ -n "${ALPHADIANA_ROOT:-}" ]] || fail "ALPHADIANA_ROOT is required"
  SOURCE="$ALPHADIANA_ROOT/results/$RUN_ID"
  AGGREGATE="$ALPHADIANA_ROOT/results/$RUN_ID.jsonl"
  [[ -d "$SOURCE" ]] || fail "missing run directory: $SOURCE"
  [[ -f "$AGGREGATE" ]] || fail "missing aggregate result: $AGGREGATE"
  cp -a "$SOURCE/." "$PAYLOAD/"
  cp "$AGGREGATE" "$PAYLOAD/results.jsonl"
fi

python - "$HF_REPO" "$HF_FOLDER" <<'PY'
import os
import sys
from huggingface_hub import HfApi

repo_id, prefix = sys.argv[1], sys.argv[2].rstrip("/") + "/"
api = HfApi()
try:
    files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
except Exception as exc:
    raise SystemExit(f"ERROR: cannot inspect HF dataset repo {repo_id}: {exc}")
collisions = [path for path in files if path == prefix.rstrip("/") or path.startswith(prefix)]
if collisions and os.environ.get("ALLOW_EXISTING_HF_FOLDER") != "1":
    sample = ", ".join(collisions[:3])
    raise SystemExit(
        f"ERROR: HF destination {prefix.rstrip('/')} already contains files "
        f"({sample}). Bump RUN_VERSION instead of overwriting it."
    )
PY

python - "$PAYLOAD" "$RUN_ID" "$AGENT" "$HF_FOLDER" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

payload, run_id, agent, hf_folder = sys.argv[1:]
metadata = {
    "run_id": run_id,
    "dataset": "MariusHobbhahn/swe-bench-verified-mini",
    "agent": agent,
    "model": "google/gemma-4-31B-it",
    "temperature": 0.0,
    "top_p": 0.95,
    "presence_penalty": 1.5,
    "max_model_length": 262144,
    "max_tokens": 131072,
    "thinking": True,
    "streaming": True,
    "sample_k": 1,
    "max_concurrent": 4,
    "hf_folder": hf_folder,
    "staged_at": datetime.now(timezone.utc).isoformat(),
}
Path(payload, "UPLOAD_METADATA.json").write_text(
    json.dumps(metadata, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

hf upload "$HF_REPO" "$PAYLOAD" "$HF_FOLDER" \
  --repo-type dataset \
  --commit-message "Upload $RUN_ID"

printf 'Uploaded to %s/%s\n' "$HF_REPO" "$HF_FOLDER"
