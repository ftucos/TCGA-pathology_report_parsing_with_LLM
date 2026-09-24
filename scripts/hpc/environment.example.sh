# Copy to scripts/hpc/environment.local.sh and adapt to your institute.
# This file is sourced by Bash. Use absolute paths on shared HPC storage.
# module load CUDA/12.6.0
# export PATH="/path/to/ollama/bin:$PATH"
export BLCA_PYTHON="/path/to/TCGA-pathology_report_parsing_with_LLM/.venv/bin/python"
export OLLAMA_MODELS="/path/to/shared/ollama/models"
# Optional; defaults to pyproject.toml in the project directory.
# export TCGA_CONFIG_FILE="/path/to/project/pyproject.toml"
# Keep these conservative until the smoke test succeeds; then tune with workers.
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=2
