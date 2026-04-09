#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${1:-alphadiana}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
LOCAL_OPENCLAW_ROOT="${LOCAL_OPENCLAW_ROOT:-${PROJECT_ROOT}/../openclaw}"
REPRO_ROOT="${PROJECT_ROOT}/repro/openclaw_reasoning"
GENERATED_DIR="${REPRO_ROOT}/generated"
PATCH_FILE="${REPRO_ROOT}/openclaw-stream-reasoning.patch"
OPENCLAW_REPO_URL="${OPENCLAW_REPO_URL:-https://github.com/openclaw/openclaw.git}"
OPENCLAW_BASE_COMMIT="${OPENCLAW_BASE_COMMIT:-f8eb23de1c4a8c5256be679c5cfd23ca1a031a06}"

ARK_BASE_URL="${ARK_BASE_URL:?Error: ARK_BASE_URL must be set}"
ARK_MODEL_NAME="${ARK_MODEL_NAME:-Kimi-K2.5}"
ARK_API_KEY="${ARK_API_KEY:-}"
OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-OPENCLAW}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-1200}"

# Fixed uncommon local ports to reduce collisions without introducing extra discovery logic.
export ROCK_RAY_PORT="${ROCK_RAY_PORT:-36380}"
export ROCK_RAY_DASHBOARD_PORT="${ROCK_RAY_DASHBOARD_PORT:-38265}"
export ROCK_RAY_CLIENT_SERVER_PORT="${ROCK_RAY_CLIENT_SERVER_PORT:-33051}"
export ROCK_REDIS_PORT="${ROCK_REDIS_PORT:-36379}"
export ROCK_REDIS_CONTAINER="${ROCK_REDIS_CONTAINER:-redis-stack-openclaw-repro}"
export ROCK_ADMIN_PORT="${ROCK_ADMIN_PORT:-39051}"
export ROCK_PROXY_PORT="${ROCK_PROXY_PORT:-39052}"
export ROCK_BASE_URL="http://127.0.0.1:${ROCK_ADMIN_PORT}"
export ROCK_PROXY_ROOT_URL="http://127.0.0.1:${ROCK_PROXY_PORT}"
export ROCK_PROXY_URL="${ROCK_PROXY_ROOT_URL}/apis/envs/sandbox/v1"
export ROCK_DYNAMIC_CONFIG="${GENERATED_DIR}/rock-local-proxy.repro.yml"
export ROCK_CONFIG="${ROCK_DYNAMIC_CONFIG}"
export TMPDIR="${PROJECT_ROOT}/.cache/tmp"
export RAY_TMPDIR="/tmp/${USER:-user}-ray-openclaw-repro"
export PYTHONPATH="${PROJECT_ROOT}/ref/ROCK:${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

mkdir -p "${GENERATED_DIR}" "${TMPDIR}" "${RAY_TMPDIR}"

if [[ -z "${ARK_API_KEY}" ]]; then
  echo "Missing ARK_API_KEY. Export it before running setup.sh." >&2
  exit 1
fi

if [[ ! -d "${LOCAL_OPENCLAW_ROOT}/.git" ]]; then
  mkdir -p "${LOCAL_OPENCLAW_ROOT}"
  git -C "${LOCAL_OPENCLAW_ROOT}" init
  if git -C "${LOCAL_OPENCLAW_ROOT}" remote get-url origin >/dev/null 2>&1; then
    git -C "${LOCAL_OPENCLAW_ROOT}" remote set-url origin "${OPENCLAW_REPO_URL}"
  else
    git -C "${LOCAL_OPENCLAW_ROOT}" remote add origin "${OPENCLAW_REPO_URL}"
  fi
  git -C "${LOCAL_OPENCLAW_ROOT}" fetch --depth 1 origin "${OPENCLAW_BASE_COMMIT}"
  git -C "${LOCAL_OPENCLAW_ROOT}" checkout -B repro-base FETCH_HEAD
else
  CURRENT_HEAD="$(git -C "${LOCAL_OPENCLAW_ROOT}" rev-parse HEAD)"
  if [[ "${CURRENT_HEAD}" != "${OPENCLAW_BASE_COMMIT}" ]]; then
    if [[ -n "$(git -C "${LOCAL_OPENCLAW_ROOT}" status --short)" ]]; then
      cat <<EOF >&2
LOCAL_OPENCLAW_ROOT is dirty and not at the expected base commit.
Path: ${LOCAL_OPENCLAW_ROOT}
Current HEAD: ${CURRENT_HEAD}
Expected HEAD: ${OPENCLAW_BASE_COMMIT}
EOF
      exit 1
    fi
    if ! git -C "${LOCAL_OPENCLAW_ROOT}" remote get-url origin >/dev/null 2>&1; then
      git -C "${LOCAL_OPENCLAW_ROOT}" remote add origin "${OPENCLAW_REPO_URL}"
    fi
    git -C "${LOCAL_OPENCLAW_ROOT}" fetch --depth 1 origin "${OPENCLAW_BASE_COMMIT}"
    git -C "${LOCAL_OPENCLAW_ROOT}" checkout -B repro-base FETCH_HEAD
  fi
fi

cat > "${ROCK_DYNAMIC_CONFIG}" <<EOF
ray:
    runtime_env:
        working_dir: ./
    namespace: "rock-sandbox-openclaw-repro"

warmup:
    images:
      - "python:3.11"

redis:
    host: localhost
    port: ${ROCK_REDIS_PORT}
EOF

cat > "${GENERATED_DIR}/runtime.env" <<EOF
export ROCK_RAY_PORT=${ROCK_RAY_PORT}
export ROCK_RAY_DASHBOARD_PORT=${ROCK_RAY_DASHBOARD_PORT}
export ROCK_RAY_CLIENT_SERVER_PORT=${ROCK_RAY_CLIENT_SERVER_PORT}
export ROCK_REDIS_PORT=${ROCK_REDIS_PORT}
export ROCK_REDIS_CONTAINER=${ROCK_REDIS_CONTAINER}
export ROCK_ADMIN_PORT=${ROCK_ADMIN_PORT}
export ROCK_PROXY_PORT=${ROCK_PROXY_PORT}
export ROCK_BASE_URL=${ROCK_BASE_URL}
export ROCK_PROXY_ROOT_URL=${ROCK_PROXY_ROOT_URL}
export ROCK_PROXY_URL=${ROCK_PROXY_URL}
export ROCK_DYNAMIC_CONFIG=${ROCK_DYNAMIC_CONFIG}
export ROCK_CONFIG=${ROCK_CONFIG}
export TMPDIR=${TMPDIR}
export RAY_TMPDIR=${RAY_TMPDIR}
export LOCAL_OPENCLAW_ROOT=${LOCAL_OPENCLAW_ROOT}
export OPENCLAW_REPO_URL=${OPENCLAW_REPO_URL}
export OPENCLAW_BASE_COMMIT=${OPENCLAW_BASE_COMMIT}
export ARK_BASE_URL=${ARK_BASE_URL}
export ARK_MODEL_NAME=${ARK_MODEL_NAME}
export OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN}
EOF

cd "${PROJECT_ROOT}"
bash dev/setup_alphadiana_rock.sh "${ENV_NAME}"
eval "$(conda shell.bash hook)"
conda activate "${ENV_NAME}"

if ! python -c "import rock" >/dev/null 2>&1; then
  python -m pip install --no-build-isolation -e ref/ROCK
fi

docker start "${ROCK_REDIS_CONTAINER}" 2>/dev/null \
  || docker run -d --restart unless-stopped --name "${ROCK_REDIS_CONTAINER}" -p "${ROCK_REDIS_PORT}:6379" redis/redis-stack-server:latest

ray stop --force >/dev/null 2>&1 || true
pkill -f "rock.admin.main --env local-proxy --role admin --port ${ROCK_ADMIN_PORT}" >/dev/null 2>&1 || true
pkill -f "rock.admin.main --env local-proxy --role proxy --port ${ROCK_PROXY_PORT}" >/dev/null 2>&1 || true
(
  cd ref/ROCK
  ray start --head \
    --port="${ROCK_RAY_PORT}" \
    --dashboard-port="${ROCK_RAY_DASHBOARD_PORT}" \
    --ray-client-server-port="${ROCK_RAY_CLIENT_SERVER_PORT}" \
    --temp-dir="${RAY_TMPDIR}" \
    --disable-usage-stats
)

(
  cd ref/ROCK
  setsid python -m rock.admin.main --env local-proxy --role admin --port "${ROCK_ADMIN_PORT}" \
    > "${GENERATED_DIR}/admin.log" 2>&1 < /dev/null &
  echo $! > "${GENERATED_DIR}/admin.pid"
)
(
  cd ref/ROCK
  setsid python -m rock.admin.main --env local-proxy --role proxy --port "${ROCK_PROXY_PORT}" \
    > "${GENERATED_DIR}/proxy.log" 2>&1 < /dev/null &
  echo $! > "${GENERATED_DIR}/proxy.pid"
)

for _ in $(seq 1 30); do
  if curl --noproxy '*' -sf "${ROCK_BASE_URL}/openapi.json" >/dev/null 2>&1 \
    && curl --noproxy '*' -sf "${ROCK_PROXY_ROOT_URL}/openapi.json" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! curl --noproxy '*' -sf "${ROCK_BASE_URL}/openapi.json" >/dev/null 2>&1; then
  echo "ROCK admin did not become healthy. See ${GENERATED_DIR}/admin.log" >&2
  exit 1
fi
if ! curl --noproxy '*' -sf "${ROCK_PROXY_ROOT_URL}/openapi.json" >/dev/null 2>&1; then
  echo "ROCK proxy did not become healthy. See ${GENERATED_DIR}/proxy.log" >&2
  exit 1
fi

if git -C "${LOCAL_OPENCLAW_ROOT}" apply --check "${PATCH_FILE}" >/dev/null 2>&1; then
  git -C "${LOCAL_OPENCLAW_ROOT}" apply "${PATCH_FILE}"
elif git -C "${LOCAL_OPENCLAW_ROOT}" apply --reverse --check "${PATCH_FILE}" >/dev/null 2>&1; then
  echo "OpenClaw patch already applied."
else
  echo "OpenClaw patch does not apply cleanly. Check ${PATCH_FILE} and local repo state." >&2
  exit 1
fi

if [[ ! -d "${LOCAL_OPENCLAW_ROOT}/node_modules" ]]; then
  corepack prepare pnpm@10.23.0 --activate
  (cd "${LOCAL_OPENCLAW_ROOT}" && pnpm install --frozen-lockfile)
fi

TARBALL="$(
  cd "${LOCAL_OPENCLAW_ROOT}" && npm pack --silent | tail -n 1
)"
TARBALL_PATH="${LOCAL_OPENCLAW_ROOT}/${TARBALL}"

unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy

conda run --no-capture-output -n "${ENV_NAME}" \
  python openclaw_deploy/deploy.py \
  --agent-config openclaw_deploy/rock_agent_config.yaml \
  --startup-timeout "${STARTUP_TIMEOUT}" \
  2>&1 | tee "${GENERATED_DIR}/deploy.log"

SID="$(grep -oE '[0-9a-f]{32}' "${GENERATED_DIR}/deploy.log" | tail -n 1)"
if [[ -z "${SID}" ]]; then
  echo "Failed to parse SANDBOX_ID from ${GENERATED_DIR}/deploy.log" >&2
  exit 1
fi

docker cp "${TARBALL_PATH}" "${SID}:/tmp/${TARBALL}"
docker exec "${SID}" bash -lc "
  set -euo pipefail
  NPM_BIN=\$(find /tmp/rock-runtime-envs/node -name npm -type f ! -path '*/nodewin/*' ! -path '*/shims/*' 2>/dev/null | head -1)
  NODE_BIN_DIR=\$(find /tmp/rock-runtime-envs/node -type d -path '*/runtime-env/bin' 2>/dev/null | head -1)
  export PATH=\${NODE_BIN_DIR}:\$(dirname \"\${NPM_BIN}\"):\$PATH
  npm install -g /tmp/${TARBALL} --registry https://registry.npmmirror.com >/tmp/local_openclaw_install.log 2>&1
  OPENCLAW_ROOT=\$(npm root -g)/openclaw
  mkdir -p /tmp/empty-bundled /tmp/oc_home/.openclaw/workspace/.openclaw /tmp/oc_home/docs
  ln -sfn \${OPENCLAW_ROOT}/docs /tmp/oc_home/docs
  pkill -f openclaw-gateway || true
  pkill -x openclaw || true
  cat > /tmp/oc_home/openclaw_local_min.json <<'EOF'
{
  \"models\": {
    \"mode\": \"merge\",
    \"providers\": {
      \"custom_openai\": {
        \"baseUrl\": \"\${ARK_BASE_URL}\",
        \"apiKey\": \"\${ARK_API_KEY}\",
        \"api\": \"openai-completions\",
        \"models\": [
          {
            \"id\": \"\${ARK_MODEL_NAME}\",
            \"name\": \"\${ARK_MODEL_NAME}\",
            \"reasoning\": true
          }
        ]
      }
    }
  },
  \"agents\": {
    \"defaults\": {
      \"model\": {
        \"primary\": \"custom_openai/\${ARK_MODEL_NAME}\"
      },
      \"thinkingDefault\": \"high\",
      \"workspace\": \"/tmp/oc_home/.openclaw/workspace\",
      \"compaction\": { \"mode\": \"safeguard\" },
      \"timeoutSeconds\": 3600,
      \"maxConcurrent\": 4,
      \"subagents\": { \"maxConcurrent\": 8 }
    }
  },
  \"messages\": { \"ackReactionScope\": \"group-mentions\" },
  \"commands\": {
    \"native\": \"auto\",
    \"nativeSkills\": \"auto\",
    \"restart\": true,
    \"ownerDisplay\": \"raw\"
  },
  \"gateway\": {
    \"port\": ${OPENCLAW_GATEWAY_PORT:-8080},
    \"mode\": \"local\",
    \"bind\": \"lan\",
    \"controlUi\": {
      \"enabled\": true,
      \"allowedOrigins\": [\"http://localhost:${OPENCLAW_GATEWAY_PORT:-8080}\", \"http://127.0.0.1:${OPENCLAW_GATEWAY_PORT:-8080}\"],
      \"allowInsecureAuth\": true
    },
    \"auth\": { \"mode\": \"token\", \"token\": \"\${OPENCLAW_GATEWAY_TOKEN}\" },
    \"trustedProxies\": [\"127.0.0.1/32\", \"127.0.0.1/16\"],
    \"http\": { \"endpoints\": { \"chatCompletions\": { \"enabled\": true } } }
  },
  \"plugins\": { \"entries\": {} }
}
EOF
  export HOME=/tmp/oc_home
  export OPENCLAW_HOME=/tmp/oc_home
  export OPENCLAW_CONFIG_PATH=/tmp/oc_home/openclaw_local_min.json
  export ARK_BASE_URL='${ARK_BASE_URL}'
  export ARK_API_KEY='${ARK_API_KEY}'
  export ARK_MODEL_NAME='${ARK_MODEL_NAME}'
  export OPENCLAW_GATEWAY_TOKEN='${OPENCLAW_GATEWAY_TOKEN}'
  export OPENCLAW_BUNDLED_PLUGINS_DIR=/tmp/empty-bundled
  nohup openclaw gateway > /tmp/oc_home/local_openclaw_kimi.log 2>&1 &
  sleep 8
  tail -n 120 /tmp/oc_home/local_openclaw_kimi.log
" | tee "${GENERATED_DIR}/restart.log"

{
  echo "export SID=${SID}"
  echo "export OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN}"
} >> "${GENERATED_DIR}/runtime.env"

cat <<EOF

Setup finished.
Source this file before verification:
  source ${GENERATED_DIR}/runtime.env

Then run:
  bash ${REPRO_ROOT}/verify.sh
EOF
