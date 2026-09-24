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
    assert settings.ocr_model == "PaddlePaddle/PaddleOCR-VL-1.6"
    assert isinstance(settings.num_ctx, int)


def test_missing_runtime_section_explained(tmp_path):
    config = tmp_path / "pyproject.toml"
    config.write_text('[project]\nname = "example"\n')
    with pytest.raises(ValueError, match=r"Missing \[tool.blca\]"):
        load_settings(config)


def test_paddle_overrides_and_thinking(tmp_path, monkeypatch):
    monkeypatch.setenv("BLCA_PADDLE_HOST", "http://127.0.0.1:41235")
    monkeypatch.setenv("BLCA_PADDLE_MODEL_DIR", str(tmp_path / "models"))
    s = load_settings(ROOT / "pyproject.toml")
    assert s.ocr_host == "http://127.0.0.1:41235"
    assert s.ocr_model_dir == tmp_path / "models"
    assert s.think is True


def test_revision_invalidates_export_configuration():
    from dataclasses import replace

    from blca.pipeline import configuration_key

    s = load_settings(ROOT / "pyproject.toml")
    assert configuration_key(s) != configuration_key(replace(s, ocr_revision="a" * 40))
