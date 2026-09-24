#!/usr/bin/env bash
# Run explicitly on a machine/allocation permitted to download model weights.
# This is the only pipeline helper that pulls models; compute jobs never pull.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
"$BLCA_PYTHON" -m blca.paddle prepare --config "$TCGA_CONFIG_FILE"
models=$("$BLCA_PYTHON" - <<'PY'
import os
from pathlib import Path
from blca.config import load_settings
s = load_settings(Path(os.environ['TCGA_CONFIG_FILE']))
print(s.extraction_model)
PY
)
start_ollama
while IFS= read -r model; do
    [[ "$model" != *cloud* ]] || { echo "Cloud models are disabled" >&2; exit 2; }
    ollama pull "$model"
done <<< "$models"
ollama list
