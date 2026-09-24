#!/usr/bin/env bash
#SBATCH --job-name=blca-local
#SBATCH --output=logs/blca-%j.out
#SBATCH --error=logs/blca-%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=6:00:00

# Submit from the project root. Account/partition/GPU type belong in sbatch options.
# Default: one job, one GPU, one Ollama server. Do not submit a multi-task array
# when limiting total GPU usage to one. Parallel report requests share this server.
# Preserve Slurm's CUDA_VISIBLE_DEVICES: never select an unallocated GPU.
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit this script from the project root}"
source scripts/hpc/common.sh
if [[ "${SLURM_ARRAY_TASK_STEP:-1}" != 1 ]] ||
   (( ${SLURM_ARRAY_TASK_COUNT:-1} != ${SLURM_ARRAY_TASK_MAX:-0} - ${SLURM_ARRAY_TASK_MIN:-0} + 1 )); then
    echo "Use a contiguous Slurm array with step 1" >&2
    exit 2
fi
start_ollama
stage=${BLCA_STAGE:-run}
case "$stage" in run|ocr|extract) ;; *) echo "Invalid BLCA_STAGE" >&2; exit 2 ;; esac
shard_count=${SLURM_ARRAY_TASK_COUNT:-1}
shard_index=$(( ${SLURM_ARRAY_TASK_ID:-0} - ${SLURM_ARRAY_TASK_MIN:-0} ))
echo "Stage $stage; shard $shard_index/$shard_count; node $(hostname)"
"$BLCA_PYTHON" -m blca --config "$TCGA_CONFIG_FILE" "$stage" \
    --num-shards "$shard_count" --shard-index "$shard_index" "$@"
