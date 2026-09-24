"""Exercise the real batch entry point with services replaced by shell stubs."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("stage", "services"),
    [("run", ["paddle", "ollama"]), ("ocr", ["paddle"]), ("extract", ["ollama"])],
)
def test_batch_starts_services_in_same_job(stage, services, tmp_path):
    hpc = tmp_path / "scripts/hpc"
    hpc.mkdir(parents=True)
    shutil.copy(ROOT / "scripts/hpc/run.slurm", hpc / "run.slurm")
    (hpc / "common.sh").write_text("""
set -euo pipefail
BLCA_PYTHON="$PWD/python-stub"
TCGA_CONFIG_FILE="$PWD/pyproject.toml"
start_paddle() { echo "paddle" >> "$PWD/calls"; export BLCA_PADDLE_HOST=127.0.0.1:1234; }
start_ollama() { echo "ollama" >> "$PWD/calls"; export OLLAMA_HOST=127.0.0.1:5678; }
""")
    python = tmp_path / "python-stub"
    python.write_text("""#!/bin/bash
printf '%s\\n' "${BLCA_PADDLE_HOST:-none}|${OLLAMA_HOST:-none}" >> "$PWD/calls"
printf '%s\\n' "$@" > "$PWD/args"
""")
    python.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("SLURM_") and k not in {"OLLAMA_HOST", "BLCA_PADDLE_HOST"}
    }
    env.update(SLURM_SUBMIT_DIR=str(tmp_path), BLCA_STAGE=stage)
    subprocess.run(
        ["bash", str(hpc / "run.slurm"), "--limit", "2"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls[:-1] == services
    assert calls[-1] == ("127.0.0.1:1234" if "paddle" in services else "none") + "|" + (
        "127.0.0.1:5678" if "ollama" in services else "none"
    )
    args = (tmp_path / "args").read_text().splitlines()
    assert args == [
        "-m",
        "blca",
        "--config",
        str(tmp_path / "pyproject.toml"),
        stage,
        "--num-shards",
        "1",
        "--shard-index",
        "0",
        "--limit",
        "2",
    ]
