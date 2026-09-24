import httpx
import pytest

from blca.ollama import Ollama, completed_text, local_url


@pytest.mark.parametrize(
    "host",
    [
        "https://api.example.org",
        "http://192.168.1.1:11434",
        "http://localhost.evil.com",
        "http://user@localhost",
        "http://localhost/foo",
        "http://localhost?remote=true",
    ],
)
def test_no_external_inference_endpoints(host):
    with pytest.raises(ValueError):
        local_url(host)


def test_loopback_and_no_scheme():
    assert local_url("127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert local_url("http://[::1]:11434") == "http://[::1]:11434"


@pytest.mark.parametrize(
    "response",
    [
        {"done": True, "done_reason": "length", "response": "cut off"},
        {"done": False, "done_reason": "stop", "response": "incomplete"},
        {"done": True, "done_reason": "stop", "response": ""},
        {"done": True, "done_reason": "stop", "response": "text", "eval_count": 10},
    ],
)
def test_incomplete_ocr_never_succeeds(response):
    with pytest.raises(ValueError):
        completed_text(response, chat=False, max_tokens=10)


def test_model_preflight_requires_local_weights_and_context():
    client = Ollama("127.0.0.1:11434")
    client.close()
    seen = []

    def handler(request):
        seen.append(request.url.path)
        response = {
            "/api/tags": {"models": [{"name": "glm-ocr:bf16", "digest": "sha"}]},
            "/api/show": {"capabilities": ["vision"], "model_info": {"glm.context_length": 16384}},
            "/api/version": {"version": "test"},
        }[request.url.path]
        return httpx.Response(200, json=response)

    client.client = httpx.Client(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)
    )
    try:
        assert client.model_info("glm-ocr:bf16", 16384, vision=True)["digest"] == "sha"
        with pytest.raises(ValueError, match="exceeds"):
            client.model_info("glm-ocr:bf16", 32768)
        with pytest.raises(ValueError, match="not installed"):
            client.model_info("missing")
        with pytest.raises(ValueError, match="Cloud"):
            client.model_info("cloud-model")
        assert "/api/pull" not in seen
    finally:
        client.close()
