#!/usr/bin/env bash
# Stage and upload one finished AIME 2026 pass@64 run to the private HF dataset
# repo. Refuses to reuse a non-empty destination folder.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT="${1:-}"
RUN_VERSION="${2:-${RUN_VERSION:-v01}}"
HF_REPO="${HF_REPO:-T-MARS/alphadiana-benchmark-results}"
RUN_DATE="${RUN_DATE:-$(date +%Y%m%d)}"

usage() {
  printf 'Usage: %s {openclaw|opencode|zeroclaw} [vNN]\n' "$0" >&2
  exit 2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "$AGENT" =~ ^(openclaw|opencode|zeroclaw)$ ]] || usage
[[ "$RUN_VERSION" =~ ^v[0-9][0-9]$ ]] || fail "version must match vNN"
[[ "$RUN_DATE" =~ ^[0-9]{8}$ ]] || fail "RUN_DATE must match YYYYMMDD"
command -v hf >/dev/null 2>&1 || fail "hf CLI is required"

if [[ -z "${ALPHADIANA_ROOT:-}" && -d "$BUNDLE_ROOT/../../alphadiana" ]]; then
  ALPHADIANA_ROOT="$(cd "$BUNDLE_ROOT/../.." && pwd)"
fi
[[ -n "${ALPHADIANA_ROOT:-}" ]] || fail "ALPHADIANA_ROOT is required"

RUN_ID="full_aime2026_pass64_${AGENT}_qwen35_27b_${RUN_VERSION}"
HF_FOLDER="${RUN_DATE}-aime-2026-${AGENT}-qwen35-27b-${RUN_VERSION}"
SOURCE="$ALPHADIANA_ROOT/results/$RUN_ID"
AGGREGATE="$ALPHADIANA_ROOT/results/$RUN_ID.jsonl"
[[ -d "$SOURCE" ]] || fail "missing run directory: $SOURCE"
[[ -f "$AGGREGATE" ]] || fail "missing aggregate result: $AGGREGATE"

# Refuse to stage a run that fails structural verification.
python "$SCRIPT_DIR/verify_outputs.py" \
  --agent "$AGENT" --root "$ALPHADIANA_ROOT" --version "$RUN_VERSION" \
  || fail "verify_outputs failed; not uploading an incomplete run"

STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/alphadiana-hf-upload.XXXXXX")"
PAYLOAD="$STAGE_ROOT/$HF_FOLDER"
mkdir -p "$PAYLOAD"

cleanup() {
  rm -rf "$STAGE_ROOT"
}
trap cleanup EXIT

cp -a "$SOURCE/." "$PAYLOAD/"
cp "$AGGREGATE" "$PAYLOAD/results.jsonl"

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
    "dataset": "MathArena/aime_2026",
    "benchmark_display_name": "AIME 2026",
    "agent": agent,
    "model": "Qwen/Qwen3.5-27B",
    "temperature": 0.6,
    "top_p": 0.95,
    "max_model_length": 262144,
    "max_tokens": 131072,
    "thinking": True,
    "sample_k": 64,
    "sampling_mode": "pass@64",
    "max_concurrent_per_harness": 3,
    "capture_logprobs": True,
    "top_logprobs": 20,
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
