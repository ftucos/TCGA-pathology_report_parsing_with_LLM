#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
mkdir -p "$PROJECT_DIR/data/pathology_report"
cd "$PROJECT_DIR/data/pathology_report"
gdc-client download -m "$PROJECT_DIR/data/pathology_report-manifest.txt"
