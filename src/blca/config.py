import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    manifest: Path
    reports_dir: Path
    output_dir: Path
    host: str
    timeout: float
    attempts: int
    ocr_model: str
    dpi: int
    max_image_side: int
    ocr_num_ctx: int
    ocr_max_tokens: int
    ocr_repeat_penalty: float
    ocr_revision: str
    ocr_model_dir: Path
    ocr_host: str
    ocr_prompt: str
    ocr_gpu_memory: float
    ocr_max_num_seqs: int
    extraction_model: str
    think: bool
    num_ctx: int
    max_tokens: int
    workers: int


def load_settings(path: Path) -> Settings:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Config not found: {path}; use the project's pyproject.toml")
    with path.open("rb") as f:
        document = tomllib.load(f)
    try:
        c = document["tool"]["blca"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"Missing [tool.blca] runtime settings in {path}") from e
    root = path.parent

    def setting(section, key, expected=str):
        try:
            value = c[section][key]
        except (KeyError, TypeError) as e:
            raise ValueError(f"Missing tool.blca.{section}.{key} in {path}") from e
        if (isinstance(value, bool) and expected is not bool) or not isinstance(value, expected):
            raise TypeError(f"Invalid type for tool.blca.{section}.{key} in {path}")
        return value

    def local(key):
        p = Path(setting("paths", key)).expanduser()
        return (root / p).resolve() if not p.is_absolute() else p.resolve()

    s = Settings(
        root=root,
        manifest=local("manifest"),
        reports_dir=local("reports_dir"),
        output_dir=local("output_dir"),
        host=os.getenv("OLLAMA_HOST") or setting("ollama", "host"),
        timeout=setting("ollama", "timeout_seconds", (int, float)),
        attempts=setting("ollama", "attempts", int),
        ocr_model=setting("ocr", "model"),
        dpi=setting("ocr", "dpi", int),
        max_image_side=setting("ocr", "max_image_side", int),
        ocr_num_ctx=setting("ocr", "num_ctx", int),
        ocr_max_tokens=setting("ocr", "max_tokens", int),
        ocr_repeat_penalty=setting("ocr", "repeat_penalty", (int, float)),
        ocr_revision=setting("ocr", "revision"),
        ocr_model_dir=(
            root
            / Path(os.getenv("BLCA_PADDLE_MODEL_DIR") or setting("ocr", "model_dir")).expanduser()
        ).resolve(),
        ocr_host=os.getenv("BLCA_PADDLE_HOST") or setting("ocr", "host"),
        ocr_prompt=setting("ocr", "prompt"),
        ocr_gpu_memory=setting("ocr", "gpu_memory_utilization", (int, float)),
        ocr_max_num_seqs=setting("ocr", "max_num_seqs", int),
        extraction_model=setting("extraction", "model"),
        think=setting("extraction", "think", bool),
        num_ctx=setting("extraction", "num_ctx", int),
        max_tokens=setting("extraction", "max_tokens", int),
        workers=setting("extraction", "workers", int),
    )
    if (
        min(
            s.timeout,
            s.attempts,
            s.dpi,
            s.max_image_side,
            s.workers,
            s.ocr_num_ctx,
            s.ocr_max_tokens,
            s.num_ctx,
            s.max_tokens,
        )
        <= 0
    ):
        raise ValueError(
            "Timeout, retry counts, workers and rendering/context limits must be positive"
        )
    if s.max_tokens >= s.num_ctx or s.ocr_max_tokens >= s.ocr_num_ctx:
        raise ValueError("Output token budgets must be smaller than context sizes")
    if not math.isfinite(s.ocr_repeat_penalty) or s.ocr_repeat_penalty <= 0:
        raise ValueError("OCR repeat_penalty must be finite and positive")
    if not 0 < s.ocr_gpu_memory < 1:
        raise ValueError("OCR gpu_memory_utilization must be between 0 and 1")
    if s.ocr_max_num_seqs <= 0:
        raise ValueError("OCR max_num_seqs must be positive")
    if not all(v.strip() for v in (s.ocr_model, s.ocr_revision, s.ocr_prompt)):
        raise ValueError("OCR model, revision and prompt must be nonempty")
    return s
