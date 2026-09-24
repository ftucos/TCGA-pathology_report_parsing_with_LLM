"""PaddleOCR-VL via local vLLM. Downloads occur only in the explicit prepare command."""

import argparse
import base64
import hashlib
import os
import re
from pathlib import Path

from .config import Settings, load_settings
from .ollama import LocalHTTP
from .storage import atomic_json, fingerprint, read_json


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def model_path(s: Settings) -> Path:
    manifest = read_json(s.ocr_model_dir / "manifest.json")
    revision = manifest.get("revision", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid staged Paddle model revision; run prepare_models.sh")
    return s.ocr_model_dir / "snapshots" / revision


def local_model_info(s: Settings) -> dict:
    """Verify local weights once per invocation, including extraction-only jobs."""
    try:
        manifest = read_json(s.ocr_model_dir / "manifest.json")
        path = model_path(s)
        if manifest["name"] != s.ocr_model or manifest["requested_revision"] != s.ocr_revision:
            raise ValueError("Staged Paddle model/configuration mismatch; run prepare_models.sh")
        hashes = manifest["files"]
        actual = {
            str(p.relative_to(path))
            for p in path.rglob("*")
            if p.is_file() and ".cache" not in p.relative_to(path).parts
        }
        if (
            not hashes
            or set(hashes) != actual
            or not any(name.endswith(".safetensors") for name in hashes)
        ):
            raise ValueError("Staged Paddle files missing or changed; run prepare_models.sh")
        for name, digest in hashes.items():
            if sha256(path / name) != digest:
                raise ValueError(f"Staged Paddle file checksum mismatch: {name}")
        config = read_json(path / "config.json")
        if config.get("model_type") != "paddleocr_vl":
            raise ValueError("Expected a PaddleOCR-VL model")
        if s.ocr_num_ctx > config["max_position_embeddings"]:
            raise ValueError("OCR context exceeds model capacity")
        return {
            "name": s.ocr_model,
            "revision": manifest["revision"],
            "digest": fingerprint(hashes),
            "backend": "vllm",
        }
    except FileNotFoundError as e:
        raise ValueError("Paddle model is not staged; run scripts/hpc/prepare_models.sh") from e


def prepare_model(s: Settings) -> dict:
    from huggingface_hub import HfApi, snapshot_download

    revision = HfApi().model_info(s.ocr_model, revision=s.ocr_revision).sha
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Hugging Face did not return an immutable model revision")
    path = s.ocr_model_dir / "snapshots" / revision
    snapshot_download(repo_id=s.ocr_model, revision=revision, local_dir=path)
    hashes = {
        str(p.relative_to(path)): sha256(p)
        for p in sorted(path.rglob("*"))
        if p.is_file() and ".cache" not in p.relative_to(path).parts
    }
    atomic_json(
        s.ocr_model_dir / "manifest.json",
        {
            "name": s.ocr_model,
            "requested_revision": s.ocr_revision,
            "revision": revision,
            "files": hashes,
        },
    )
    return local_model_info(s)


class PaddleOCR(LocalHTTP):
    service = "Paddle/vLLM"

    def verify_model(self, s: Settings):
        models = self.request("/v1/models").get("data", [])
        model = next((m for m in models if m.get("id") == s.ocr_model), None)
        if model is None or model.get("root") != str(model_path(s)):
            raise ValueError("Paddle server is not serving the configured local snapshot")
        if model.get("max_model_len", 0) < s.ocr_num_ctx:
            raise ValueError("Paddle server context is below configured num_ctx")


def ocr_payload(s: Settings, png: bytes) -> dict:
    return {
        "model": s.ocr_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                        },
                    },
                    {"type": "text", "text": s.ocr_prompt},
                ],
            }
        ],
        "stream": False,
        "temperature": 0,
        "seed": 0,
        "max_tokens": s.ocr_max_tokens,
        "repetition_penalty": s.ocr_repeat_penalty,
    }


class InvalidOCRResponse(ValueError):
    """A completed but unusable response: identical deterministic retries cannot repair it."""


def completed_ocr(response: dict, *, max_tokens: int) -> str:
    choices = response.get("choices", [])
    if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        raise InvalidOCRResponse("Incomplete or truncated Paddle OCR response; inspect raw output")
    if response.get("usage", {}).get("completion_tokens", 0) >= max_tokens:
        raise InvalidOCRResponse("Paddle OCR reached output token budget")
    text = choices[0].get("message", {}).get("content")
    if not isinstance(text, str) or not text.strip():
        raise InvalidOCRResponse("Empty Paddle OCR output; inspect the page")
    return text.strip()


def server_command(s: Settings, python: str, port: int) -> list[str]:
    return [
        python,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(model_path(s)),
        "--served-model-name",
        s.ocr_model,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--trust-remote-code",
        "--max-model-len",
        str(s.ocr_num_ctx),
        "--max-num-batched-tokens",
        str(s.ocr_num_ctx),
        "--max-num-seqs",
        str(s.ocr_max_num_seqs),
        "--gpu-memory-utilization",
        str(s.ocr_gpu_memory),
        "--no-enable-prefix-caching",
        "--mm-processor-cache-gb",
        "0",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "serve"])
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--python", help="Python in the separate Linux vLLM environment")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    s = load_settings(args.config)
    if args.command == "prepare":
        print(prepare_model(s))
    else:
        print(local_model_info(s), flush=True)
        if args.command == "serve":
            if not args.python or not 0 < args.port < 65536:
                parser.error("serve requires --python and a valid --port")
            env = dict(
                os.environ,
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                VLLM_NO_USAGE_STATS="1",
                HF_HUB_DISABLE_TELEMETRY="1",
            )
            # Own process group lets Slurm cleanup terminate vLLM worker children too.
            os.setsid()
            command = server_command(s, args.python, args.port)
            os.execve(args.python, command, env)


if __name__ == "__main__":
    main()
