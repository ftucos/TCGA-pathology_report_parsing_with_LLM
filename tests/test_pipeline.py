import base64
import json
from dataclasses import replace

import pytest
from conftest import TEXT

from blca.cli import export_results, main
from blca.pipeline import (
    configuration_key,
    render_page,
    report_lock,
    run_extraction,
    source_info,
)
from blca.pipeline import (
    process_report as _process_report,
)
from blca.schema import SCHEMA_VERSION, BladderExtraction
from blca.storage import atomic_json, load_manifest, read_json, shard

MODELS = {
    "ocr": {"name": "PaddlePaddle/PaddleOCR-VL-1.6", "digest": "ocr-1", "ollama_version": "test"},
    "extraction": {"name": "test-model", "digest": "llm-1", "ollama_version": "test"},
}


def process_report(report, settings, client, models, stage, force):
    return _process_report(report, settings, client, models, stage, force, ocr_client=client)


class FakeOllama:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.fail_page = None
        self.bad_json = False

    def request(self, endpoint, data):
        self.calls.append((endpoint, data))
        if endpoint == "/v1/chat/completions":
            content = data["messages"][0]["content"]
            assert base64.b64decode(content[0]["image_url"]["url"].split(",")[1]).startswith(
                b"\x89PNG"
            )
            assert content[1]["text"] == "OCR:"
            if self.fail_page == len(self.calls):
                raise TimeoutError("Interrupted OCR")
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": TEXT}}],
                "usage": {"completion_tokens": 40},
            }
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
    assert result["extraction"] == payload
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["configuration_key"] == configuration_key(settings)
    summary = export_results([report], settings)
    assert summary == {
        "manifest_reports": 1,
        "exported_reports": 1,
        "unavailable_reports": 0,
        "report_rows": 1,
    }
    assert (
        "perivesical soft tissue"
        in (settings.output_dir / "exports/bladder_features.csv").read_text()
    )
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


def test_input_bytes_do_not_block_model_tokenization(settings, payload, tmp_path, caplog):
    transcript = {
        "fingerprint": "test",
        "text_sha256": "test",
        "source": {"report_id": "test"},
        "model": MODELS["ocr"],
        "settings": {},
        "pages": [{"page": 1, "text": "x" * 100000}],
    }
    client = FakeOllama(payload)
    original = client.request

    def measured_response(endpoint, data):
        raw = original(endpoint, data)
        raw["prompt_eval_count"] = 5000
        return raw

    client.request = measured_response
    # Reproduce the large output reserve that exposed the bytes-as-tokens check.
    settings = replace(settings, num_ctx=131072, max_tokens=98304)
    with caplog.at_level("INFO", logger="blca.pipeline"):
        result = run_extraction(transcript, settings, client, MODELS["extraction"], tmp_path)
    assert result["status"] == "complete"
    assert len(client.calls) == 1
    request = client.calls[0][1]
    old_budget = len(json.dumps(request["messages"]).encode()) + 1024 + settings.max_tokens
    assert old_budget > settings.num_ctx
    assert request["messages"][1]["content"] == "Extract this report:\n\n[PAGE 1]\n" + "x" * 100000
    assert request["options"]["num_predict"] == 98304
    assert request["options"]["num_ctx"] == 131072
    assert request["truncate"] is False
    assert request["shift"] is False
    assert "prompt_eval_count=5000 eval_count=300" in caplog.text
    assert "num_ctx=131072 num_predict=98304" in caplog.text


def test_server_context_error_never_becomes_success(settings, report, payload):
    import httpx

    client = FakeOllama(payload)
    original = client.request

    def reject_context(endpoint, data):
        if endpoint == "/api/chat":
            assert data["truncate"] is False and data["shift"] is False
            response = httpx.Response(
                400,
                json={"error": "input exceeds context window"},
                request=httpx.Request("POST", "http://127.0.0.1/api/chat"),
            )
            response.raise_for_status()
        return original(endpoint, data)

    client.request = reject_context
    with pytest.raises(httpx.HTTPStatusError):
        process_report(report, settings, client, MODELS, "run", False)
    directory = settings.output_dir / "reports" / report.report_id
    assert not (directory / "result.json").exists()
    assert read_json(directory / "error.json")["error_type"] == "HTTPStatusError"
    assert list(directory.glob("extractions/*/raw/attempt-1.error.txt"))
    assert export_results([report], settings)["unavailable_reports"] == 1


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
    monkeypatch.setattr(pipeline, "PaddleOCR", lambda *a: client)
    monkeypatch.setattr(pipeline, "local_model_info", lambda s: MODELS["ocr"])
    client.verify_model = lambda s: None
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


def test_partial_findings_export_one_report_row_without_evidence(settings, report):
    import csv

    client = FakeOllama({"grade": "G2 / moderately differentiated", "unused": "ignored"})
    process_report(report, replace(settings, attempts=3), client, MODELS, "run", False)
    assert len(client.calls) == 3  # Two OCR pages and one extraction; no missing-field retries.
    directory = settings.output_dir / "reports" / report.report_id
    result = read_json(directory / "result.json")
    assert result["extraction"]["grade"] is None
    assert "G2 / moderately differentiated" in result["extraction"]["grade_comment"]
    assert result["extraction"]["pN"] == "NX"
    assert result["extraction"]["pM"] == "MX"
    assert "normalized" not in result
    assert "evidence_warnings" not in result
    assert export_results([report], settings)["report_rows"] == 1
    with (settings.output_dir / "exports/bladder_features.csv").open() as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        assert reader.fieldnames == [
            "report_id",
            "case_id",
            "filename",
            *BladderExtraction.model_fields,
        ]
    assert len(rows) == 1
    assert rows[0]["report_id"] == report.report_id
    assert rows[0]["grade"] == ""
    assert rows[0]["grade_comment"] == result["extraction"]["grade_comment"]
    assert rows[0]["pN"] == "NX"
    assert rows[0]["pM"] == "MX"
    exported = json.loads((settings.output_dir / "exports/bladder_features.jsonl").read_text())
    assert exported["extraction"] == result["extraction"]
    assert exported["ocr"] == result["ocr"]


def test_empty_findings_still_have_a_csv_row(settings, report):
    process_report(report, settings, FakeOllama({}), MODELS, "run", False)
    summary = export_results([report], settings)
    assert summary["exported_reports"] == 1
    assert summary["report_rows"] == 1


@pytest.mark.parametrize("old_version", ["1.0", "2.0"])
def test_legacy_result_reextracts_without_repeating_ocr(settings, report, payload, old_version):
    client = FakeOllama(payload)
    process_report(report, settings, client, MODELS, "run", False)
    directory = settings.output_dir / "reports" / report.report_id
    path = directory / "result.json"
    result = read_json(path)
    result["schema_version"] = old_version
    result["extraction"] = (
        {"bladder_specimens": []}
        if old_version == "1.0"
        else {"stage": "pT3 pNX", "grade": "poorly differentiated"}
    )
    atomic_json(path, result)
    assert export_results([report], settings)["unavailable_reports"] == 1
    process_report(report, settings, client, MODELS, "run", False)
    assert len(client.calls) == 4  # Only extraction is repeated.
    updated = read_json(path)
    assert updated["schema_version"] == SCHEMA_VERSION
    assert updated["extraction"] == payload
    assert export_results([report], settings)["exported_reports"] == 1


def test_old_prompt_settings_reextract_without_repeating_ocr(
    settings, report, payload, monkeypatch
):
    from blca import pipeline

    client = FakeOllama(payload)
    prompt = pipeline.prompt_text()
    monkeypatch.setattr(pipeline, "prompt_text", lambda: "Previous detailed extraction prompt")
    process_report(report, settings, client, MODELS, "run", False)
    monkeypatch.setattr(pipeline, "prompt_text", lambda: prompt)
    assert export_results([report], settings)["unavailable_reports"] == 1
    process_report(report, settings, client, MODELS, "run", False)
    assert len(client.calls) == 4
    assert export_results([report], settings)["exported_reports"] == 1
