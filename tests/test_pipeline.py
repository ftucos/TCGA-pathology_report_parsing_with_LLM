import base64
import json
from dataclasses import replace

import pytest
from conftest import TEXT

from blca.cli import export_results, main
from blca.pipeline import (
    configuration_key,
    process_report,
    render_page,
    report_lock,
    run_extraction,
    source_info,
)
from blca.storage import atomic_json, load_manifest, read_json, shard

MODELS = {
    "ocr": {"name": "glm-ocr:bf16", "digest": "ocr-1", "ollama_version": "test"},
    "extraction": {"name": "test-model", "digest": "llm-1", "ollama_version": "test"},
}


class FakeOllama:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.fail_page = None
        self.bad_json = False

    def request(self, endpoint, data):
        self.calls.append((endpoint, data))
        if endpoint == "/api/generate":
            assert base64.b64decode(data["images"][0]).startswith(b"\x89PNG")
            assert data["prompt"] == "Text Recognition:"
            if self.fail_page == len(self.calls):
                raise TimeoutError("Interrupted OCR")
            return {"done": True, "done_reason": "stop", "response": TEXT, "eval_count": 40}
        assert endpoint == "/api/chat"
        assert data["format"]["additionalProperties"] is False
        return {
            "done": True,
            "done_reason": "stop",
            "eval_count": 300,
            "message": {"content": "{broken" if self.bad_json else json.dumps(self.payload)},
        }


def test_render_and_manifest(settings, report):
    import io

    from PIL import Image

    png = render_page(report.path, 0, 250, 1000)
    with Image.open(io.BytesIO(png)) as image:
        assert max(image.size) <= 1001  # PDFium can round to next pixel
    reports = load_manifest(settings.manifest, settings.reports_dir)
    assert reports == [report]
    assert source_info(report)["case_id"] == "TCGA-AB-1234"


def test_complete_pipeline_resume_and_export(settings, report, payload):
    client = FakeOllama(payload)
    process_report(report, settings, client, MODELS, "run", False)
    assert len(client.calls) == 3
    process_report(report, settings, client, MODELS, "run", False)
    assert len(client.calls) == 3  # completed stages are resumed
    result_dir = settings.output_dir / "reports" / report.report_id
    result = read_json(result_dir / "result.json")
    assert result["normalized"]["specimens"][0]["pt"] == "pT3"
    assert result["configuration_key"] == configuration_key(settings)
    summary = export_results([report], settings)
    assert summary == {
        "manifest_reports": 1,
        "exported_reports": 1,
        "unavailable_reports": 0,
        "specimen_rows": 1,
    }
    assert "pT3" in (settings.output_dir / "exports/bladder_features.csv").read_text()
    # Configuration changes are visible; old results cannot masquerade as new ones.
    assert (
        export_results([report], replace(settings, num_ctx=settings.num_ctx + 1000))[
            "unavailable_reports"
        ]
        == 1
    )


def test_interrupted_ocr_resumes_completed_pages(settings, report, payload):
    client = FakeOllama(payload)
    client.fail_page = 2
    with pytest.raises(TimeoutError):
        process_report(report, settings, client, MODELS, "run", False)
    directory = settings.output_dir / "reports" / report.report_id
    assert not (directory / "result.json").exists()
    assert not list(directory.glob("ocr/*/transcript.json"))
    client.fail_page = None
    process_report(report, settings, client, MODELS, "run", False)
    assert len(client.calls) == 4  # second page + extraction, first page not OCRed twice
    assert not (directory / "error.json").exists()


def test_failed_force_does_not_export_old_success(settings, report, payload):
    client = FakeOllama(payload)
    process_report(report, settings, client, MODELS, "run", False)
    client.bad_json = True
    with pytest.raises(ValueError):
        process_report(report, settings, client, MODELS, "extract", True)
    assert export_results([report], settings)["unavailable_reports"] == 1
    assert list(
        (settings.output_dir / "reports" / report.report_id).glob("extractions/*/result.json")
    )


def test_changed_model_digest_recomputes_extraction_only(settings, report, payload):
    client = FakeOllama(payload)
    process_report(report, settings, client, MODELS, "run", False)
    changed = dict(MODELS, extraction=dict(MODELS["extraction"], digest="llm-2"))
    process_report(report, settings, client, changed, "run", False)
    assert len(client.calls) == 4


def test_oversized_report_fails_before_sending_or_truncating(settings, report, payload, tmp_path):
    transcript = {
        "fingerprint": "test",
        "text_sha256": "test",
        "pages": [{"page": 1, "text": "x" * 100000}],
    }
    client = FakeOllama(payload)
    with pytest.raises(ValueError, match="not truncated"):
        run_extraction(transcript, settings, client, MODELS["extraction"], tmp_path)
    assert not client.calls


def test_extract_requires_complete_current_ocr(settings, report, payload):
    with pytest.raises(ValueError, match="No complete OCR"):
        process_report(report, settings, FakeOllama(payload), MODELS, "extract", False)


def test_changed_pdf_rejected_against_manifest(settings, report, payload):
    with report.path.open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ValueError, match="manifest"):
        process_report(report, settings, FakeOllama(payload), MODELS, "run", False)


def test_tampered_transcript_not_exported(settings, report, payload):
    process_report(report, settings, FakeOllama(payload), MODELS, "run", False)
    path = next((settings.output_dir / "reports" / report.report_id).glob("ocr/*/transcript.json"))
    doc = read_json(path)
    doc["pages"][0]["text"] += " altered"
    atomic_json(path, doc)
    assert export_results([report], settings)["unavailable_reports"] == 1


def test_exclusive_report_lock(tmp_path):
    with (
        report_lock(tmp_path),
        pytest.raises(ValueError, match="another worker"),
        report_lock(tmp_path),
    ):
        pass


def test_shards_cover_every_report_once_and_do_not_shift(report):
    reports = [replace(report, report_id=f"id-{n}") for n in range(50)]
    groups = [shard(reports, 4, n) for n in range(4)]
    assert sorted(r.report_id for g in groups for r in g) == sorted(r.report_id for r in reports)
    for index in range(4):
        assert (
            shard(reports + [replace(report, report_id="extra")], 4, index)[: len(groups[index])]
            == groups[index]
        )
    with pytest.raises(ValueError):
        shard(reports, 4, 4)


def test_missing_results_are_accounted_for(settings, report):
    summary = export_results([report], settings)
    assert summary["unavailable_reports"] == 1
    status = read_json(settings.output_dir / "exports/report_status.json")
    assert len(status) == 1
    assert status[0]["report_id"] == report.report_id


def test_dry_run_no_model_or_writes(capsys):
    from conftest import ROOT

    assert main(["--config", str(ROOT / "pyproject.toml"), "run", "--dry-run", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["selected"] == 1


def test_validation_retry_repairs_json_without_repeating_ocr(
    settings, report, payload, monkeypatch
):
    from blca import pipeline

    monkeypatch.setattr(pipeline.time, "sleep", lambda _: None)
    client = FakeOllama(payload)
    original = client.request
    chats = 0

    def repairing(endpoint, data):
        nonlocal chats
        if endpoint == "/api/chat":
            chats += 1
            client.bad_json = chats == 1
        return original(endpoint, data)

    client.request = repairing
    process_report(report, replace(settings, attempts=2), client, MODELS, "run", False)
    assert len(client.calls) == 4
    assert "Previous output failed validation" in client.calls[-1][1]["messages"][-1]["content"]
    assert export_results([report], settings)["exported_reports"] == 1


def test_changed_ocr_parameters_invalidate_both_stages(settings, report, payload):
    client = FakeOllama(payload)
    process_report(report, settings, client, MODELS, "run", False)
    process_report(report, replace(settings, dpi=200), client, MODELS, "run", False)
    assert len(client.calls) == 6


def test_corrupt_page_checkpoint_is_repaired(settings, report, payload):
    client = FakeOllama(payload)
    process_report(report, settings, client, MODELS, "ocr", False)
    directory = settings.output_dir / "reports" / report.report_id
    next(directory.glob("ocr/*/transcript.json")).unlink()
    page = next(directory.glob("ocr/*/pages/0001.json"))
    page.write_text("{corrupt")
    process_report(report, settings, client, MODELS, "run", False)
    assert len(client.calls) == 4  # only corrupt page plus extraction


def test_parallel_report_failure_does_not_stop_other_reports(
    settings, report, payload, monkeypatch
):
    from blca import pipeline

    client = FakeOllama(payload)
    client.model_info = lambda model, *a, **kw: MODELS["ocr" if kw.get("vision") else "extraction"]
    client.close = lambda: None
    monkeypatch.setattr(pipeline, "Ollama", lambda *a: client)
    missing = replace(report, report_id="missing", path=settings.root / "missing.pdf")
    summary = pipeline.run_reports([report, missing], replace(settings, workers=2), "run")
    assert summary == {"selected": 2, "complete": 1, "failed": 1}
    exported = export_results([report, missing], settings)
    assert exported["exported_reports"] == 1
    assert exported["unavailable_reports"] == 1


def test_interrupted_forced_ocr_cannot_fall_back_to_old_transcript(settings, report, payload):
    client = FakeOllama(payload)
    process_report(report, settings, client, MODELS, "run", False)
    client.fail_page = 5  # initial three calls, new first page, failure on new second page
    with pytest.raises(TimeoutError):
        process_report(report, settings, client, MODELS, "run", True)
    directory = settings.output_dir / "reports" / report.report_id
    assert not list(directory.glob("ocr/*/transcript.json"))
    assert not list(directory.glob("ocr/*/pages/0002.json"))
    client.fail_page = None
    process_report(report, settings, client, MODELS, "run", False)
    assert len(client.calls) == 7
    assert export_results([report], settings)["exported_reports"] == 1
