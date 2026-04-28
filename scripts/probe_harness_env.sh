#!/usr/bin/env bash
set -euo pipefail

MODEL_API_BASE="${MODEL_API_BASE:-http://localhost:8011/v1}"
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3.5-27B}"
HF_ENDPOINT_TO_PROBE="${HF_ENDPOINT:-https://hf-mirror.com}"
DOCKER_NETWORK="${DOCKER_NETWORK:-host}"
CURL_TIMEOUT="${CURL_TIMEOUT:-15}"
REQUIRE_DIRECT_HF="${REQUIRE_DIRECT_HF:-0}"
REQUIRE_WEB="${REQUIRE_WEB:-1}"
REQUIRE_WIKIPEDIA="${REQUIRE_WIKIPEDIA:-0}"
REQUIRE_SEARCH_HTTP="${REQUIRE_SEARCH_HTTP:-0}"
REQUIRE_GOOGLE_SEARCH="${REQUIRE_GOOGLE_SEARCH:-0}"
REQUIRE_NATIVE_SEARCH="${REQUIRE_NATIVE_SEARCH:-0}"

OPENCODE_CONTROLLER_IMAGE="${OPENCODE_CONTROLLER_IMAGE:-alphadiana/tb2-opencode-controller:latest}"
OPENCLAW_SANDBOX_IMAGE="${OPENCLAW_SANDBOX_IMAGE:-tmlrgroup/alphadiana:v1}"
ZEROCLAW_SANDBOX_IMAGE="${ZEROCLAW_SANDBOX_IMAGE:-zeroclaw-reasoning:0.6.9}"

run_probe() {
  local harness="$1"
  local image="$2"

  printf '## %s\n\n' "${harness}"
  printf -- '- image: `%s`\n' "${image}"
  printf -- '- docker_network: `%s`\n\n' "${DOCKER_NETWORK}"

  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    printf -- '- status: `missing_image`\n\n'
    return 1
  fi

  docker run --rm \
    --network "${DOCKER_NETWORK}" \
    -e MODEL_API_BASE="${MODEL_API_BASE}" \
    -e MODEL_NAME="${MODEL_NAME}" \
    -e HF_ENDPOINT_TO_PROBE="${HF_ENDPOINT_TO_PROBE}" \
    -e CURL_TIMEOUT="${CURL_TIMEOUT}" \
    -e REQUIRE_DIRECT_HF="${REQUIRE_DIRECT_HF}" \
    -e REQUIRE_WEB="${REQUIRE_WEB}" \
    -e REQUIRE_WIKIPEDIA="${REQUIRE_WIKIPEDIA}" \
    -e REQUIRE_SEARCH_HTTP="${REQUIRE_SEARCH_HTTP}" \
    -e REQUIRE_GOOGLE_SEARCH="${REQUIRE_GOOGLE_SEARCH}" \
    -e REQUIRE_NATIVE_SEARCH="${REQUIRE_NATIVE_SEARCH}" \
    -e BRAVE_API_KEY \
    -e GEMINI_API_KEY \
    -e XAI_API_KEY \
    -e KIMI_API_KEY \
    -e MOONSHOT_API_KEY \
    -e PERPLEXITY_API_KEY \
    -e OPENROUTER_API_KEY \
    "${image}" \
    sh -lc '
set -u
fail=0

probe_url() {
  label="$1"
  url="$2"
  required="$3"
  err_file="/tmp/probe-${label}.err"
  code_time=$(curl -L -sS -o /dev/null -w "%{http_code} %{time_total}" --max-time "${CURL_TIMEOUT}" "${url}" 2>"${err_file}" || true)
  err=$(cat "${err_file}" 2>/dev/null || true)
  printf -- "- url_%s: %s" "${label}" "${code_time}"
  if [ -n "${err}" ]; then
    printf " error=%s" "${err}"
  fi
  printf "\n"
  code=${code_time%% *}
  case "${code}" in
    2*|3*) ok=1 ;;
    *) ok=0 ;;
  esac
  if [ "${required}" = "1" ] && [ "${ok}" != "1" ]; then
    fail=1
  fi
}

probe_brave_search() {
  required="$1"
  err_file="/tmp/probe-brave_search_api.err"
  if [ -n "${BRAVE_API_KEY:-}" ]; then
    code_time=$(curl -L -sS -o /dev/null -w "%{http_code} %{time_total}" \
      -H "Accept: application/json" \
      -H "X-Subscription-Token: ${BRAVE_API_KEY}" \
      --max-time "${CURL_TIMEOUT}" \
      "https://api.search.brave.com/res/v1/web/search?q=alphadiana%20probe&count=1" \
      2>"${err_file}" || true)
  else
    code_time=$(curl -L -sS -o /dev/null -w "%{http_code} %{time_total}" \
      -H "Accept: application/json" \
      --max-time "${CURL_TIMEOUT}" \
      "https://api.search.brave.com/res/v1/web/search?q=alphadiana%20probe&count=1" \
      2>"${err_file}" || true)
  fi
  err=$(cat "${err_file}" 2>/dev/null || true)
  printf -- "- url_brave_search_api: %s" "${code_time}"
  if [ -n "${err}" ]; then
    printf " error=%s" "${err}"
  fi
  printf "\n"
  code=${code_time%% *}
  case "${code}" in
    2*|3*) ok=1 ;;
    *) ok=0 ;;
  esac
  if [ "${required}" = "1" ] && [ "${ok}" != "1" ]; then
    fail=1
  fi
}

credential_status() {
  key="$1"
  eval "value=\${${key}:-}"
  if [ -n "${value}" ]; then
    printf -- "- credential_%s: set\n" "${key}"
  else
    printf -- "- credential_%s: unset\n" "${key}"
  fi
}

python_path=$(command -v python || true)
printf -- "- python_path: %s\n" "${python_path}"
if [ -n "${python_path}" ]; then
  printf -- "- python_version: %s\n" "$(python --version 2>&1 || true)"
else
  printf -- "- python_version: missing\n"
  fail=1
fi
printf -- "- python3_path: %s\n" "$(command -v python3 || true)"
printf -- "- python3_version: %s\n" "$(python3 --version 2>&1 || true)"
printf -- "- pip3_path: %s\n" "$(command -v pip3 || command -v pip || true)"
printf -- "- pip3_version: %s\n" "$( (pip3 --version || pip --version) 2>&1 || true )"
printf -- "- curl_path: %s\n" "$(command -v curl || true)"
printf -- "- wget_path: %s\n" "$(command -v wget || true)"
printf -- "- opencode_path: %s\n" "$(command -v opencode || true)"
printf -- "- openclaw_path: %s\n" "$(command -v openclaw || true)"
printf -- "- zeroclaw_path: %s\n" "$(command -v zeroclaw || true)"
node_path=$(command -v node || true)
printf -- "- node_path: %s\n" "${node_path}"
if [ -n "${node_path}" ]; then
  printf -- "- node_version: %s\n\n" "$(node --version 2>&1 || true)"
else
  printf -- "- node_version: missing\n\n"
fi

python3 - <<'"'"'PY'"'"'
import importlib.util
import sys

modules = [
    ("requests", "requests"),
    ("numpy", "numpy"),
    ("sympy", "sympy"),
    ("scipy", "scipy"),
    ("pandas", "pandas"),
    ("PIL", "Pillow"),
    ("matplotlib", "matplotlib"),
]

ok = True
for import_name, package_name in modules:
    found = importlib.util.find_spec(import_name) is not None
    print(f"- import_{package_name}: `{'"'"'ok'"'"' if found else '"'"'missing'"'"'}`")
    ok = ok and found
sys.exit(0 if ok else 1)
PY
if [ "$?" != "0" ]; then
  fail=1
fi
printf "\n"

models_body=$(curl -sS --max-time "${CURL_TIMEOUT}" "${MODEL_API_BASE%/}/models" 2>/tmp/probe-models.err || true)
model_err=$(cat /tmp/probe-models.err 2>/dev/null || true)
if printf "%s" "${models_body}" | grep -F "${MODEL_NAME}" >/dev/null 2>&1; then
  printf -- "- model_api: ok\n"
else
  printf -- "- model_api: missing_model"
  if [ -n "${model_err}" ]; then
    printf " error=%s" "${model_err}"
  fi
  printf "\n"
  fail=1
fi

probe_url "local_models" "${MODEL_API_BASE%/}/models" 1
probe_url "pypi" "https://pypi.org/simple/pip/" 1
probe_url "pythonhosted" "https://files.pythonhosted.org/" 1
probe_url "hf_endpoint" "${HF_ENDPOINT_TO_PROBE}" 1
probe_url "huggingface_direct" "https://huggingface.co/" "${REQUIRE_DIRECT_HF}"
probe_url "example" "https://example.com/" "${REQUIRE_WEB}"
probe_url "wikipedia" "https://www.wikipedia.org/" "${REQUIRE_WIKIPEDIA}"
probe_url "duckduckgo_html" "https://duckduckgo.com/html/?q=alphadiana+probe" "${REQUIRE_SEARCH_HTTP}"
probe_url "bing_search" "https://www.bing.com/search?q=alphadiana+probe" "${REQUIRE_SEARCH_HTTP}"
probe_url "google_search" "https://www.google.com/search?q=alphadiana+probe" "${REQUIRE_GOOGLE_SEARCH}"

credential_status BRAVE_API_KEY
credential_status GEMINI_API_KEY
credential_status XAI_API_KEY
credential_status KIMI_API_KEY
credential_status MOONSHOT_API_KEY
credential_status PERPLEXITY_API_KEY
credential_status OPENROUTER_API_KEY
if [ "${REQUIRE_NATIVE_SEARCH}" = "1" ] && [ -z "${BRAVE_API_KEY:-}${GEMINI_API_KEY:-}${XAI_API_KEY:-}${KIMI_API_KEY:-}${MOONSHOT_API_KEY:-}${PERPLEXITY_API_KEY:-}${OPENROUTER_API_KEY:-}" ]; then
  fail=1
fi
probe_brave_search "${REQUIRE_NATIVE_SEARCH}"

exit "${fail}"
  '
}

status=0

cat <<EOF
# Harness Environment Probe

- model_api_base: \`${MODEL_API_BASE}\`
- model_name: \`${MODEL_NAME}\`
- hf_endpoint: \`${HF_ENDPOINT_TO_PROBE}\`
- curl_timeout: \`${CURL_TIMEOUT}s\`
- require_web: \`${REQUIRE_WEB}\`
- require_wikipedia: \`${REQUIRE_WIKIPEDIA}\`
- require_search_http: \`${REQUIRE_SEARCH_HTTP}\`
- require_google_search: \`${REQUIRE_GOOGLE_SEARCH}\`
- require_native_search: \`${REQUIRE_NATIVE_SEARCH}\`

EOF

run_probe "opencode" "${OPENCODE_CONTROLLER_IMAGE}" || status=1
printf "\n"
run_probe "openclaw" "${OPENCLAW_SANDBOX_IMAGE}" || status=1
printf "\n"
run_probe "zeroclaw" "${ZEROCLAW_SANDBOX_IMAGE}" || status=1

exit "${status}"
