#!/usr/bin/env bash
# Launch the AlphaDiana evaluation dashboard.
#
# Usage:
#   ./run.sh          # Development mode (frontend + backend separately)
#   ./run.sh --prod   # Production mode (serve built frontend via FastAPI)
#   ./run.sh -h        # Show help

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

export ALPHADIANA_RESULTS_DIR="${ALPHADIANA_RESULTS_DIR:-$PROJECT_ROOT/results}"
export ALPHADIANA_CONFIGS_DIR="${ALPHADIANA_CONFIGS_DIR:-$PROJECT_ROOT/configs}"

# --- Source ROCK port configuration if available ---
# This ensures the dashboard backend uses the correct ROCK admin/proxy ports.
ROCK_PORTS_ENV="${ROCK_PORTS_ENV:-$PROJECT_ROOT/dev/.rock_ports.env}"
if [ -f "$ROCK_PORTS_ENV" ] && [ -z "$ROCK_BASE_URL" ]; then
    echo "[INFO] Sourcing ROCK ports from $ROCK_PORTS_ENV"
    # shellcheck disable=SC1090
    source "$ROCK_PORTS_ENV"
fi

DEFAULT_BACKEND_PORT="${ALPHADIANA_BACKEND_PORT:-8000}"
DEFAULT_FRONTEND_PORT="${ALPHADIANA_FRONTEND_PORT:-5173}"
DEFAULT_RELOAD_DIRS="alphadiana/dashboard"

# --- Resolve Python environment ---
# Priority: ALPHADIANA_PYTHON env var > alphadiana conda env > current python3
resolve_python() {
    # 1. Explicit override
    if [ -n "$ALPHADIANA_PYTHON" ] && [ -x "$ALPHADIANA_PYTHON" ]; then
        echo "$ALPHADIANA_PYTHON"
        return 0
    fi

    # 2. If already in the alphadiana conda env, use current python
    if [ "$(basename "${CONDA_DEFAULT_ENV:-}")" = "alphadiana" ]; then
        echo "python3"
        return 0
    fi

    # 3. Try to find alphadiana conda env
    local conda_envs_output
    if command -v conda &>/dev/null; then
        conda_envs_output="$(conda info --envs 2>/dev/null || true)"
        local env_path
        env_path="$(echo "$conda_envs_output" | awk '/^alphadiana / {print $NF}')"
        if [ -n "$env_path" ] && [ -x "$env_path/bin/python" ]; then
            echo "$env_path/bin/python"
            return 0
        fi
    fi

    # 4. Fallback to current python3
    echo "python3"
    return 0
}

PYTHON="$(resolve_python)"

# --- Check required dependencies ---
check_dependencies() {
    echo "[INFO] Using Python: $PYTHON ($(${PYTHON} --version 2>&1))"
    local missing=()
    for pkg in alphadiana fastapi uvicorn; do
        if ! "${PYTHON}" -c "import ${pkg}" &>/dev/null; then
            missing+=("$pkg")
        fi
    done
    if [ ${#missing[@]} -gt 0 ]; then
        echo "[ERROR] Missing Python packages: ${missing[*]}" >&2
        echo "" >&2
        echo "Please install AlphaDiana in the correct environment:" >&2
        echo "  conda activate alphadiana" >&2
        echo "  pip install -e '.[all]'" >&2
        echo "" >&2
        echo "Or set ALPHADIANA_PYTHON to point to the right interpreter:" >&2
        echo "  ALPHADIANA_PYTHON=/path/to/python ./run.sh" >&2
        exit 1
    fi
    # Optional: check rock SDK (needed for sandbox features)
    if ! "${PYTHON}" -c "import rock" &>/dev/null; then
        echo "[WARNING] rock SDK not found — sandbox deploy features will not work." >&2
        echo "  Install with: pip install 'rl-rock[admin,sandbox-actor]>=1.3.0'" >&2
    fi
    echo "[INFO] All dependencies OK."
}

print_usage() {
        cat <<'EOF'
Usage: ./run.sh [--prod] [-h|--help]

Options:
    --prod      Run production mode (build frontend + serve via FastAPI)
    -h, --help  Show this help message

Environment variables:
    ALPHADIANA_BACKEND_PORT   Preferred backend start port (default: 8000)
    ALPHADIANA_FRONTEND_PORT  Preferred frontend start port (default: 5173)
    ALPHADIANA_RELOAD_DIRS    Colon-separated dev reload dirs
                              (default: alphadiana/dashboard)

Behavior:
    If a preferred port is occupied, the script automatically finds the next free port.
EOF
}

is_port_available() {
    local port="$1"
    "${PYTHON}" - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

find_available_port() {
    local start_port="$1"
    local max_try="${2:-100}"
    local port="$start_port"
    local i=0

    while [ "$i" -lt "$max_try" ]; do
        if is_port_available "$port"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
        i=$((i + 1))
    done

    return 1
}

pick_port_with_notice() {
    local preferred="$1"
    local label="$2"
    local picked=""
    if ! picked="$(find_available_port "$preferred")"; then
        echo "[ERROR] Could not find available ${label} port near ${preferred}" >&2
        exit 1
    fi
    if [ "$picked" != "$preferred" ]; then
        echo "[INFO] ${label} port ${preferred} is occupied, switched to ${picked}." >&2
    fi
    echo "$picked"
}

build_reload_args() {
    local reload_dirs_raw="${ALPHADIANA_RELOAD_DIRS:-$DEFAULT_RELOAD_DIRS}"
    local -a reload_dirs=()
    local dir=""

    IFS=':' read -r -a reload_dirs <<< "$reload_dirs_raw"
    RELOAD_ARGS=()
    RESOLVED_RELOAD_DIRS=()

    for dir in "${reload_dirs[@]}"; do
        [ -n "$dir" ] || continue
        case "$dir" in
            /*)
                RELOAD_ARGS+=(--reload-dir "$dir")
                RESOLVED_RELOAD_DIRS+=("$dir")
                ;;
            *)
                RELOAD_ARGS+=(--reload-dir "$PROJECT_ROOT/$dir")
                RESOLVED_RELOAD_DIRS+=("$PROJECT_ROOT/$dir")
                ;;
        esac
    done
}

MODE="dev"
if [ "$#" -gt 1 ]; then
    echo "[ERROR] Too many arguments." >&2
    print_usage
    exit 1
fi

if [ "$#" -eq 1 ]; then
    case "$1" in
        --prod)
            MODE="prod"
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown argument: $1" >&2
            print_usage
            exit 1
            ;;
    esac
fi

check_dependencies

if [ "$MODE" = "prod" ]; then
    BACKEND_PORT="$(pick_port_with_notice "$DEFAULT_BACKEND_PORT" "Backend")"

    echo "==> Building frontend..."
    cd "$SCRIPT_DIR/frontend"
    npm run build

    echo "==> Starting production server at http://localhost:${BACKEND_PORT}"
    cd "$PROJECT_ROOT"
    "${PYTHON}" -m uvicorn alphadiana.dashboard.backend.main:app --host 0.0.0.0 --port "$BACKEND_PORT"
else
    BACKEND_PORT="$(pick_port_with_notice "$DEFAULT_BACKEND_PORT" "Backend")"
    FRONTEND_PORT="$(pick_port_with_notice "$DEFAULT_FRONTEND_PORT" "Frontend")"
    build_reload_args

    echo "==> Starting backend at http://localhost:${BACKEND_PORT}"
    echo "==> Starting frontend at http://localhost:${FRONTEND_PORT}"
    echo "==> Backend reload dirs: ${RESOLVED_RELOAD_DIRS[*]}"
    echo ""

    # Start backend in background
    cd "$PROJECT_ROOT"
    "${PYTHON}" -m uvicorn alphadiana.dashboard.backend.main:app --reload "${RELOAD_ARGS[@]}" --port "$BACKEND_PORT" &
    BACKEND_PID=$!

    # Start frontend
    cd "$SCRIPT_DIR/frontend"
    VITE_BACKEND_PORT="$BACKEND_PORT" npm run dev -- --port "$FRONTEND_PORT" --strictPort &
    FRONTEND_PID=$!

    trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
    wait
fi
