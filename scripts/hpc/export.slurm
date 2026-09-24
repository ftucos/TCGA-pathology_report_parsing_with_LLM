#!/usr/bin/env bash
#SBATCH --job-name=blca-export
#SBATCH --output=logs/export-%j.out
#SBATCH --error=logs/export-%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=01:00:00
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit from the project root}"
if [[ -f scripts/hpc/environment.local.sh ]]; then
    source scripts/hpc/environment.local.sh
fi
"${BLCA_PYTHON:-$PWD/.venv/bin/python}" -m blca \
    --config "${TCGA_CONFIG_FILE:-$PWD/pyproject.toml}" export
