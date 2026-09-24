import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from blca.paddle import (
    PaddleOCR,
    completed_ocr,
    local_model_info,
    model_path,
    ocr_payload,
    prepare_model,
    server_command,
    sha256,
)
from blca.storage import atomic_json


@pytest.fixture
def staged(settings, tmp_path):
    s = replace(settings, ocr_model_dir=tmp_path / "models")
    revision = "a" * 40
    path = s.ocr_model_dir / "snapshots" / revision
    path.mkdir(parents=True)
    (path / "model.safetensors").write_bytes(b"fake weights")
    atomic_json(
        path / "config.json",
        {
            "model_type": "paddleocr_vl",
            "max_position_embeddings": 131072,
        },
    )
    atomic_json(
        s.ocr_model_dir / "manifest.json",
        {
            "name": s.ocr_model,
            "revision": revision,
            "requested_revision": s.ocr_revision,
            "files": {p.name: sha256(p) for p in path.iterdir()},
        },
    )
    return s


def test_staging_pins_download_to_resolved_commit(staged, monkeypatch):
    huggingface_hub = SimpleNamespace(HfApi=None, snapshot_download=None)
    monkeypatch.setitem(sys.modules, "huggingface_hub", huggingface_hub)
    seen = []
    monkeypatch.setattr(
        huggingface_hub,
        "HfApi",
        lambda: SimpleNamespace(model_info=lambda *a, **kw: SimpleNamespace(sha="a" * 40)),
    )
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **kw: seen.append(kw))
    identity = prepare_model(staged)
    assert identity["revision"] == "a" * 40
    assert seen[0]["revision"] == identity["revision"]
    assert seen[0]["repo_id"] == staged.ocr_model
    assert seen[0]["local_dir"] == model_path(staged)


def test_local_model_verification_and_changed_weights(staged):
    identity = local_model_info(staged)
    assert identity["backend"] == "vllm"
    assert len(identity["digest"]) == 64
    with pytest.raises(ValueError, match="mismatch"):
        local_model_info(replace(staged, ocr_revision="other"))
    with pytest.raises(ValueError, match="capacity"):
        local_model_info(replace(staged, ocr_num_ctx=262144))
    (model_path(staged) / "model.safetensors").write_bytes(b"edited weights")
    with pytest.raises(ValueError, match="checksum"):
        local_model_info(staged)


def test_missing_weights_explains_staging(settings, tmp_path):
    with pytest.raises(ValueError, match="prepare_models.sh"):
        local_model_info(replace(settings, ocr_model_dir=tmp_path))


def test_ocr_request_has_vllm_image_format(settings):
    payload = ocr_payload(settings, b"PNG")
    assert payload["messages"][0]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,UE5H"}},
        {"type": "text", "text": "OCR:"},
    ]
    assert payload["repetition_penalty"] == settings.ocr_repeat_penalty
    assert payload["max_tokens"] == settings.ocr_max_tokens
    assert not {"options", "think", "images", "stop"} & payload.keys()


def response(content="recognized text", reason="stop", tokens=4):
    return {
        "choices": [{"finish_reason": reason, "message": {"content": content}}],
        "usage": {"completion_tokens": tokens},
    }


@pytest.mark.parametrize(
    "raw",
    [
        response(reason="length"),
        response(content=""),
        response(content=None),
        response(tokens=10),
        {"choices": []},
        {"choices": [{}, {}]},
    ],
)
def test_partial_or_empty_ocr_rejected(raw):
    with pytest.raises(ValueError):
        completed_ocr(raw, max_tokens=10)


def test_completed_ocr():
    assert completed_ocr(response(" text "), max_tokens=10) == "text"


def test_local_paddle_preflight_and_error_body(staged):
    client = PaddleOCR("http://127.0.0.1:1234")
    client.close()
    served = {
        "id": staged.ocr_model,
        "root": str(model_path(staged)),
        "max_model_len": staged.ocr_num_ctx,
    }

    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [served]})
        return httpx.Response(500, json={"error": "GPU allocation failed"})

    client.client = httpx.Client(
        base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(handler)
    )
    try:
        client.verify_model(staged)
        served["root"] = "/wrong/snapshot"
        with pytest.raises(ValueError, match="snapshot"):
            client.verify_model(staged)
        with pytest.raises(httpx.HTTPStatusError, match="Paddle/vLLM HTTP 500.*GPU allocation"):
            client.request("/v1/chat/completions", {})
    finally:
        client.close()
    with pytest.raises(ValueError, match="loopback"):
        PaddleOCR("http://example.com")


def test_server_launch_uses_staged_snapshot_and_bounded_memory(staged):
    command = server_command(staged, "/venv-vllm/bin/python", 41234)
    assert command[0] == "/venv-vllm/bin/python"
    assert command[command.index("--model") + 1] == str(model_path(staged))
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--gpu-memory-utilization") + 1] == "0.15"
    assert "--no-enable-prefix-caching" in command


@pytest.mark.parametrize("stage", ["run", "ocr", "extract"])
def test_pipeline_routes_stages_to_separate_services(stage, settings, report, payload, monkeypatch):
    from test_pipeline import MODELS, FakeOllama

    from blca import pipeline

    calls = []
    closed = []
    fake = FakeOllama(payload)

    class OCR:
        def __init__(self, *args):
            calls.append("start-ocr")

        def verify_model(self, s):
            pass

        def request(self, endpoint, data):
            assert endpoint == "/v1/chat/completions"
            return fake.request(endpoint, data)

        def close(self):
            closed.append("ocr")

    class Extraction:
        def __init__(self, *args):
            calls.append("start-extraction")

        def model_info(self, *args):
            return MODELS["extraction"]

        def request(self, endpoint, data):
            assert endpoint == "/api/chat"
            assert data["think"] is True
            return fake.request(endpoint, data)

        def close(self):
            closed.append("extraction")

    monkeypatch.setattr(pipeline, "PaddleOCR", OCR)
    monkeypatch.setattr(pipeline, "Ollama", Extraction)
    monkeypatch.setattr(pipeline, "local_model_info", lambda s: MODELS["ocr"])
    if stage == "extract":
        pipeline.process_report(report, settings, None, MODELS, "ocr", False, ocr_client=OCR())
        calls.clear()
    assert pipeline.run_reports([report], settings, stage)["complete"] == 1
    expected = (
        {"ocr", "extraction"} if stage == "run" else {"ocr" if stage == "ocr" else "extraction"}
    )
    assert set(calls) == {"start-" + item for item in expected}
    assert set(closed) == expected
    if stage != "ocr":
        result = json.loads(
            (settings.output_dir / "reports" / report.report_id / "result.json").read_text()
        )
        assert result["extraction_settings"]["think"] is True


def test_truncated_ocr_is_saved_without_identical_retries(settings, report):
    from blca.pipeline import process_report

    calls = []

    class Truncating:
        def request(self, *args):
            calls.append(args)
            return response(reason="length")

    with pytest.raises(ValueError, match="truncated"):
        process_report(
            report,
            replace(settings, attempts=3),
            None,
            {"ocr": {"name": settings.ocr_model, "digest": "test"}},
            "ocr",
            False,
            ocr_client=Truncating(),
        )
    assert len(calls) == 1
    directory = settings.output_dir / "reports" / report.report_id
    assert list(directory.glob("ocr/*/raw/page-0001-attempt-1.json"))
    assert not list(directory.glob("ocr/*/transcript.json"))
    assert not list(directory.glob("ocr/*/pages/*.json"))


def test_serve_executes_vllm_offline(staged, monkeypatch):
    from blca import paddle

    seen = {}
    monkeypatch.setattr(paddle, "load_settings", lambda path: staged)
    monkeypatch.setattr("sys.argv", ["blca.paddle", "serve", "--python", "/vllm/bin/python"])
    monkeypatch.setattr(paddle.os, "setsid", lambda: seen.update(session=True))

    def execute(python, args, env):
        seen.update(python=python, args=args, env=env)

    monkeypatch.setattr(paddle.os, "execve", execute)
    paddle.main()
    assert seen["session"] is True
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert seen["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert seen["python"] == "/vllm/bin/python"
    assert seen["args"][seen["args"].index("--model") + 1] == str(model_path(staged))
