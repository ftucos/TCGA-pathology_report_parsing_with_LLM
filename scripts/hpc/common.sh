#!/usr/bin/env bash
# Sourced by the batch scripts; no institute-specific module or filesystem assumptions.
set -euo pipefail
HPC_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$HPC_DIR/../.." && pwd)
cd "$PROJECT_DIR"
if [[ -f "$HPC_DIR/environment.local.sh" ]]; then
    source "$HPC_DIR/environment.local.sh"
fi
export BLCA_PYTHON=${BLCA_PYTHON:-"$PROJECT_DIR/.venv/bin/python"}
export TCGA_CONFIG_FILE=${TCGA_CONFIG_FILE:-"$PROJECT_DIR/pyproject.toml"}
export OLLAMA_NO_CLOUD=1
export OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL:-2}
export OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAX_LOADED_MODELS:-2}
: "${OLLAMA_MODELS:?Set OLLAMA_MODELS to your pre-staged shared model directory}"
[[ -x "$BLCA_PYTHON" ]] || { echo "Python missing: $BLCA_PYTHON" >&2; exit 2; }
command -v ollama >/dev/null
mkdir -p logs
OLLAMA_PID=
cleanup() {
    if [[ -n "$OLLAMA_PID" ]]; then
        kill "$OLLAMA_PID" 2>/dev/null || true
        wait "$OLLAMA_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_ollama() {
    # OS-selected per-job port avoids deterministic collisions on shared nodes.
    # If the small bind race occurs, the process liveness check fails the job.
    local port
    port=$("$BLCA_PYTHON" - <<'PY'
import socket
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    print(s.getsockname()[1])
PY
)
    export OLLAMA_HOST="127.0.0.1:$port"
    local log="logs/ollama-${SLURM_JOB_ID:-local}-$$.log"
    ollama serve > "$log" 2>&1 &
    OLLAMA_PID=$!
    for ((i=0; i<60; i++)); do
        kill -0 "$OLLAMA_PID" 2>/dev/null || { echo "Ollama exited; see $log" >&2; exit 1; }
        if curl --noproxy '*' --fail --silent --max-time 2 "http://$OLLAMA_HOST/api/version" >/dev/null; then
            sleep 1
            kill -0 "$OLLAMA_PID" 2>/dev/null || { echo "Ollama port collision; retry job" >&2; exit 1; }
            echo "Local Ollama ready at $OLLAMA_HOST; log $log"
            return
        fi
        sleep 2
    done
    echo "Ollama startup timed out; see $log" >&2
    exit 1
}
