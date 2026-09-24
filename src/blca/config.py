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
    ocr_repeat_last_n: int
    ocr_stop: tuple[str, ...]
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
        ocr_repeat_last_n=setting("ocr", "repeat_last_n", int),
        ocr_stop=tuple(setting("ocr", "stop", list)),
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
    if s.ocr_repeat_last_n < -1:
        raise ValueError("OCR repeat_last_n must be -1, 0, or positive")
    if any(not isinstance(stop, str) or not stop.strip() for stop in s.ocr_stop):
        raise ValueError("OCR stop must contain only nonempty strings")
    return s
