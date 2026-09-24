"""Native Ollama endpoints only; no proxy, redirects, cloud model or automatic pulls."""

import ipaddress
from urllib.parse import urlparse

import httpx


def local_url(host: str) -> str:
    url = host if "://" in host else "http://" + host
    p = urlparse(url)
    try:
        loopback = p.hostname == "localhost" or ipaddress.ip_address(p.hostname).is_loopback
    except ValueError:
        loopback = False
    if (
        p.scheme != "http"
        or not loopback
        or p.username
        or p.password
        or p.query
        or p.fragment
        or p.path not in {"", "/"}
    ):
        raise ValueError("Ollama must use a loopback HTTP endpoint (e.g. http://127.0.0.1:11434)")
    return url.rstrip("/")


class Ollama:
    def __init__(self, host: str, timeout: float = 600):
        self.client = httpx.Client(
            base_url=local_url(host), timeout=timeout, trust_env=False, follow_redirects=False
        )

    def close(self):
        self.client.close()

    def request(self, endpoint: str, payload=None):
        response = (
            self.client.get(endpoint)
            if payload is None
            else self.client.post(endpoint, json=payload)
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            # HTTPX's default message omits Ollama's explanation of runner failures.
            # Include the response body, never the request's report/image payload.
            detail = response.text.strip()
            if len(detail) > 4000:
                detail = detail[:4000] + " [truncated]"
            if not detail:
                detail = "Empty error response; inspect the job's logs/ollama-*.log"
            raise httpx.HTTPStatusError(
                f"Ollama HTTP {response.status_code} for {endpoint}: {detail}",
                request=e.request,
                response=e.response,
            ) from e
        data = response.json()
        if "error" in data:
            raise ValueError(f"Ollama error: {data['error']}")
        return data

    def model_info(self, model: str, num_ctx: int | None = None, vision: bool = False) -> dict:
        if "cloud" in model.lower():
            raise ValueError("Cloud models are disabled")
        models = self.request("/api/tags").get("models", [])
        canonical = model if ":" in model else model + ":latest"
        match = next((m for m in models if m.get("name") in {model, canonical}), None)
        if match is None:
            raise ValueError(f"Model {model} is not installed locally; pre-stage it before the job")
        details = self.request("/api/show", {"model": model})
        if (
            details.get("remote_model")
            or details.get("remote_host")
            or match.get("remote_model")
            or match.get("remote_host")
        ):
            raise ValueError(
                "Remote Ollama model detected; only downloaded local weights are allowed"
            )
        if not match.get("digest"):
            raise ValueError("Ollama did not return a model digest")
        lengths = [
            v
            for k, v in details.get("model_info", {}).items()
            if k.endswith(".context_length") and isinstance(v, int)
        ]
        if num_ctx and lengths and num_ctx > max(lengths):
            raise ValueError(f"Configured context {num_ctx} exceeds {model} context {max(lengths)}")
        if vision and "vision" not in details.get("capabilities", []):
            raise ValueError(
                f"OCR model {model} does not advertise vision support; update Ollama/model"
            )
        return {
            "name": model,
            "digest": match["digest"],
            "ollama_version": self.request("/api/version")["version"],
        }


def completed_text(response: dict, *, chat: bool, max_tokens: int) -> str:
    if response.get("done") is not True or response.get("done_reason") != "stop":
        raise ValueError("Incomplete or truncated model response; increase context/output budget")
    if response.get("eval_count", 0) >= max_tokens:
        raise ValueError("Model reached output token budget")
    text = response.get("message", {}).get("content") if chat else response.get("response")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty model output; inspect blank/unreadable page or model configuration")
    return text.strip()
