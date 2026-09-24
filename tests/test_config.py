from pathlib import Path

import pytest

from blca.config import load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_toml_paths_resolve_from_config_and_slurm_host_overrides(tmp_path, monkeypatch):
    config_dir = tmp_path / "project"
    config_dir.mkdir()
    config = config_dir / "pyproject.toml"
    config.write_text((ROOT / "pyproject.toml").read_text())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:12345")
    settings = load_settings(config)
    assert settings.manifest == config_dir / "data/pathology_report-manifest.txt"
    assert settings.reports_dir == config_dir / "data/pathology_report"
    assert settings.host == "127.0.0.1:12345"
    assert settings.ocr_model == "glm-ocr:bf16"
    assert isinstance(settings.num_ctx, int)


def test_missing_runtime_section_explained(tmp_path):
    config = tmp_path / "pyproject.toml"
    config.write_text('[project]\nname = "example"\n')
    with pytest.raises(ValueError, match=r"Missing \[tool.blca\]"):
        load_settings(config)
