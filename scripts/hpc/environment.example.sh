# Copy to scripts/hpc/environment.local.sh and adapt to your institute.
# This file is sourced by Bash. Use absolute paths on shared HPC storage.
# module load Python/<your-version> CUDA/<your-version>
# export PATH="/path/to/ollama/bin:$PATH"
export BLCA_PYTHON="/path/to/TCGA-pathology_report_parsing_with_LLM/venv-hpc/bin/python"
# CUDA 12.8 wheel runtime; works with the reported 12.8-capable driver even
# when the cluster's latest toolkit module is CUDA/12.6.0.
export BLCA_PADDLE_PYTHON="/path/to/TCGA-pathology_report_parsing_with_LLM/.venv-vllm-cu128/bin/python"
export BLCA_PADDLE_MODEL_DIR="/path/to/shared/paddle/PaddleOCR-VL-1.6"
export OLLAMA_MODELS="/path/to/shared/ollama/models"
# Optional; defaults to pyproject.toml in the project directory.
# export TCGA_CONFIG_FILE="/path/to/project/pyproject.toml"
# Keep these conservative until the smoke test succeeds; then tune with workers.
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
