import fcntl
import hashlib
import io
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import httpx
import pypdfium2 as pdfium

from . import __version__
from .config import Settings
from .ollama import Ollama, completed_text
from .paddle import InvalidOCRResponse, PaddleOCR, completed_ocr, local_model_info, ocr_payload
from .schema import SCHEMA_VERSION, BladderExtraction
from .storage import Report, atomic_json, atomic_text, fingerprint, read_json

LOG = logging.getLogger(__name__)
# PDFium is not thread-safe; only rendering holds this lock, not GPU inference.
PDF_LOCK = threading.Lock()


def prompt_text() -> str:
    return files("blca").joinpath("prompts/bladder.md").read_text(encoding="utf-8")


def ocr_settings(s: Settings) -> dict:
    return {
        "model": s.ocr_model,
        "revision": s.ocr_revision,
        "dpi": s.dpi,
        "max_image_side": s.max_image_side,
        "num_ctx": s.ocr_num_ctx,
        "max_tokens": s.ocr_max_tokens,
        "repeat_penalty": s.ocr_repeat_penalty,
        "backend": "vllm",
        "prompt": s.ocr_prompt,
        "temperature": 0,
        "seed": 0,
        "renderer": version("pypdfium2"),
        "pillow": version("Pillow"),
        "pipeline_version": __version__,
    }


def extraction_settings(s: Settings) -> dict:
    return {
        "model": s.extraction_model,
        "num_ctx": s.num_ctx,
        "max_tokens": s.max_tokens,
        "temperature": 0,
        "seed": 0,
        "think": s.think,
        "truncate": False,
        "shift": False,
        "prompt_sha256": fingerprint(prompt_text()),
        "schema_sha256": fingerprint(BladderExtraction.model_json_schema()),
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": __version__,
    }


def configuration_key(s: Settings) -> str:
    return fingerprint({"ocr": ocr_settings(s), "extraction": extraction_settings(s)})


def source_info(report: Report) -> dict:
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    size = 0
    with report.path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    if size != report.expected_size or md5.hexdigest() != report.expected_md5:
        raise ValueError(f"PDF does not match manifest size/MD5: {report.report_id}")
    return {
        "report_id": report.report_id,
        "case_id": report.case_id,
        "filename": report.filename,
        "sha256": sha.hexdigest(),
        "md5": md5.hexdigest(),
        "size": size,
    }


@contextmanager
def report_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise ValueError("Report is being processed by another worker/job") from e
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def page_count(path: Path) -> int:
    with PDF_LOCK, pdfium.PdfDocument(path) as doc:
        if not len(doc):
            raise ValueError("PDF has no pages")
        return len(doc)


def render_page(path: Path, index: int, dpi: int, max_side: int) -> bytes:
    """Rasterize the visible page, deliberately ignoring the embedded PDF text layer."""
    with PDF_LOCK, pdfium.PdfDocument(path) as doc:
        page = doc[index]
        try:
            width, height = page.get_size()
            scale = min(dpi / 72, max_side / max(width, height))
            bitmap = page.render(scale=scale)
            try:
                with bitmap.to_pil() as image, io.BytesIO() as stream:
                    image.convert("RGB").save(stream, format="PNG")
                    return stream.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()


def valid_transcript(path: Path, key: str, count: int) -> dict | None:
    if not path.exists():
        return None
    try:
        doc = read_json(path)
        if (
            doc["fingerprint"] != key
            or len(doc["pages"]) != count
            or [p["page"] for p in doc["pages"]] != list(range(1, count + 1))
            or not all(isinstance(p["text"], str) and p["text"].strip() for p in doc["pages"])
            or doc["text_sha256"] != fingerprint(doc["pages"])
        ):
            return None
        return doc
    except (ValueError, KeyError, TypeError):
        return None


def run_ocr(
    report: Report,
    source: dict,
    s: Settings,
    client: PaddleOCR | None,
    model: dict,
    directory: Path,
    *,
    force: bool = False,
    require_cached: bool = False,
) -> dict:
    settings = ocr_settings(s)
    key = fingerprint({"source": source, "settings": settings, "model": model})
    work = directory / "ocr" / key
    count = page_count(report.path)
    transcript_path = work / "transcript.json"
    cached = valid_transcript(transcript_path, key, count)
    if cached and not force:
        return cached
    if require_cached:
        raise ValueError(
            "No complete OCR checkpoint for this PDF/model/configuration; run blca ocr"
        )
    if force:
        # An interrupted forced rerun must not fall back to the previous complete
        # transcript or to pages that the forced rerun has not reached yet.
        transcript_path.unlink(missing_ok=True)
        (work / "transcript.txt").unlink(missing_ok=True)
        for checkpoint in (work / "pages").glob("*.json"):
            checkpoint.unlink()
    pages = []
    for number in range(1, count + 1):
        page_path = work / "pages" / f"{number:04d}.json"
        cached_page = None
        if page_path.exists() and not force:
            try:
                c = read_json(page_path)
                if (
                    c["fingerprint"] == key
                    and c["page"] == number
                    and isinstance(c["text"], str)
                    and c["text"].strip()
                    and c["text_sha256"] == fingerprint(c["text"])
                ):
                    cached_page = c
            except (ValueError, KeyError, TypeError):
                pass
        if cached_page:
            pages.append({"page": number, "text": cached_page["text"]})
            continue
        png = render_page(report.path, number - 1, s.dpi, s.max_image_side)
        if client is None:
            raise ValueError("Paddle OCR client is required for uncached pages")
        payload = ocr_payload(s, png)
        attempt = 1
        while True:
            try:
                raw = client.request("/v1/chat/completions", payload)
                atomic_json(work / "raw" / f"page-{number:04d}-attempt-{attempt}.json", raw)
                text = completed_ocr(raw, max_tokens=s.ocr_max_tokens)
                break
            except Exception as e:
                atomic_text(
                    work / "raw" / f"page-{number:04d}-attempt-{attempt}.error.txt",
                    f"{type(e).__name__}: {e}",
                )
                if isinstance(e, InvalidOCRResponse):
                    if payload["repetition_penalty"] >= 1.3:
                        raise
                    payload = {
                        **payload,
                        "repetition_penalty": min(
                            round(payload["repetition_penalty"] + 0.1, 1), 1.3
                        ),
                    }
                elif attempt >= s.attempts:
                    raise
                time.sleep(min(2 ** (attempt - 1), 8))
                attempt += 1
        atomic_json(
            page_path,
            {"fingerprint": key, "page": number, "text": text, "text_sha256": fingerprint(text)},
        )
        pages.append({"page": number, "text": text})
        LOG.info("%s OCR page %s/%s", report.report_id, number, count)
    transcript = {
        "fingerprint": key,
        "source": source,
        "model": model,
        "settings": settings,
        "pages": pages,
        "text_sha256": fingerprint(pages),
    }
    atomic_json(transcript_path, transcript)
    atomic_text(work / "transcript.txt", report_text(transcript))
    return transcript


def report_text(transcript: dict) -> str:
    return "\n\n".join(f"[PAGE {p['page']}]\n{p['text']}" for p in transcript["pages"])


def run_extraction(
    transcript: dict,
    s: Settings,
    client: Ollama,
    model: dict,
    directory: Path,
    *,
    force: bool = False,
) -> dict:
    settings = extraction_settings(s)
    key = fingerprint(
        {
            "ocr_fingerprint": transcript["fingerprint"],
            "text_sha256": transcript["text_sha256"],
            "settings": settings,
            "model": model,
        }
    )
    work = directory / "extractions" / key
    current = directory / "result.json"
    if current.exists() and not force:
        try:
            cached = read_json(current)
            if cached["fingerprint"] == key and cached.get("schema_version") == SCHEMA_VERSION:
                parsed = BladderExtraction.model_validate(cached["extraction"])
                cached["extraction"] = parsed.model_dump()
                return cached
        except (ValueError, KeyError, TypeError):
            pass
    # Historical result remains in extractions/<fingerprint>; failed retries cannot export stale success.
    current.unlink(missing_ok=True)
    schema = BladderExtraction.model_json_schema()
    system = prompt_text() + "\n\nJSON SCHEMA:\n" + json.dumps(schema)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Extract this report:\n\n" + report_text(transcript)},
    ]
    for attempt in range(1, s.attempts + 1):
        # UTF-8 bytes are not tokens. Let Ollama apply the model's tokenizer and
        # template, explicitly requesting errors instead of dropping input/history.
        payload = {
            "model": s.extraction_model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "think": s.think,
            "truncate": False,
            "shift": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0,
                "seed": 0,
                "num_ctx": s.num_ctx,
                "num_predict": s.max_tokens,
            },
        }
        try:
            raw = client.request("/api/chat", payload)
            atomic_json(work / "raw" / f"attempt-{attempt}.json", raw)
            LOG.info(
                "%s extraction attempt %d: prompt_eval_count=%s eval_count=%s "
                "done_reason=%s num_ctx=%d num_predict=%d",
                directory.name,
                attempt,
                raw.get("prompt_eval_count", "unavailable"),
                raw.get("eval_count", "unavailable"),
                raw.get("done_reason", "unavailable"),
                s.num_ctx,
                s.max_tokens,
            )
            text = completed_text(raw, chat=True, max_tokens=s.max_tokens)
            parsed = BladderExtraction.model_validate_json(text)
            break
        except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
            # Keep diagnostics local. Retry with the original report and bounded correction feedback.
            error = f"{type(e).__name__}: {e}"
            atomic_text(work / "raw" / f"attempt-{attempt}.error.txt", error)
            if attempt == s.attempts:
                raise
            correction = {
                "role": "user",
                "content": "Previous output failed validation. Return the "
                "complete corrected JSON for the original report. Error: " + error[:2000],
            }
            messages = messages[:2] + [correction]
            time.sleep(min(2 ** (attempt - 1), 8))
    result = {
        "status": "complete",
        "fingerprint": key,
        "source": transcript["source"],
        "configuration_key": configuration_key(s),
        "created_at": datetime.now(UTC).isoformat(),
        "ocr": {
            "fingerprint": transcript["fingerprint"],
            "model": transcript["model"],
            "text_sha256": transcript["text_sha256"],
            "settings": transcript["settings"],
        },
        "extraction_model": model,
        "extraction_settings": settings,
        "schema_version": SCHEMA_VERSION,
        "extraction": parsed.model_dump(),
    }
    atomic_json(work / "result.json", result)
    atomic_json(current, result)
    return result


def process_report(
    report: Report,
    s: Settings,
    client: Ollama | None,
    models: dict,
    stage: str,
    force: bool,
    *,
    ocr_client: PaddleOCR | None = None,
) -> str:
    directory = s.output_dir / "reports" / report.report_id
    with report_lock(directory):
        try:
            source = source_info(report)
            if stage == "ocr":
                # An explicit OCR rerun invalidates the current downstream result.
                (directory / "result.json").unlink(missing_ok=True)
            transcript = run_ocr(
                report,
                source,
                s,
                ocr_client,
                models["ocr"],
                directory,
                force=force and stage != "extract",
                require_cached=stage == "extract",
            )
            if stage != "ocr":
                if client is None:
                    raise ValueError("Ollama client is required for extraction")
                run_extraction(transcript, s, client, models["extraction"], directory, force=force)
            (directory / "error.json").unlink(missing_ok=True)
            return "complete"
        except Exception as e:
            (directory / "result.json").unlink(missing_ok=True)
            atomic_json(
                directory / "error.json",
                {
                    "report_id": report.report_id,
                    "stage": stage,
                    "error_type": type(e).__name__,
                    "message": str(e),
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            raise


def run_reports(reports: list[Report], s: Settings, stage: str, force: bool = False) -> dict:
    if not reports:
        return {"selected": 0, "complete": 0, "failed": 0}
    if stage not in {"run", "ocr", "extract"}:
        raise ValueError(f"Invalid stage: {stage}")
    client = None
    ocr_client = None
    try:
        models = {"ocr": local_model_info(s)}
        if stage != "extract":
            ocr_client = PaddleOCR(s.ocr_host, s.timeout)
            ocr_client.verify_model(s)
        if stage != "ocr":
            client = Ollama(s.host, s.timeout)
            models["extraction"] = client.model_info(s.extraction_model, s.num_ctx)
        summary = {"selected": len(reports), "complete": 0, "failed": 0}
        with ThreadPoolExecutor(max_workers=s.workers) as pool:
            futures = {
                pool.submit(
                    process_report, r, s, client, models, stage, force, ocr_client=ocr_client
                ): r
                for r in reports
            }
            for future in as_completed(futures):
                report = futures[future]
                try:
                    future.result()
                    summary["complete"] += 1
                    LOG.info("%s complete", report.report_id)
                except Exception as e:  # noqa: BLE001 - isolate failures at the report boundary
                    summary["failed"] += 1
                    LOG.error(
                        "%s failed (%s); inspect report error.json",
                        report.report_id,
                        type(e).__name__,
                    )
        return summary
    finally:
        if client is not None:
            client.close()
        if ocr_client is not None:
            ocr_client.close()
