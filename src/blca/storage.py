import csv
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def atomic_json(path: Path, data) -> None:
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Report:
    report_id: str
    case_id: str
    path: Path
    filename: str
    expected_size: int
    expected_md5: str


def load_manifest(manifest: Path, reports_dir: Path) -> list[Report]:
    reports = []
    seen = set()
    with manifest.open(encoding="utf-8-sig", newline="") as f:
        rows = csv.DictReader(f, delimiter="\t")
        if not {"id", "filename", "md5", "size"}.issubset(rows.fieldnames or []):
            raise ValueError("Expected a GDC TSV manifest with id, filename, md5, size")
        for row in rows:
            rid, name = row["id"], row["filename"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", rid) or Path(name).name != name or "\\" in name:
                raise ValueError("Unsafe manifest path")
            if not name.lower().endswith(".pdf"):
                raise ValueError(f"Manifest contains a non-PDF: {name}")
            if rid in seen:
                raise ValueError(f"Duplicate GDC file ID: {rid}")
            seen.add(rid)
            case = re.match(r"(TCGA-[A-Za-z0-9]{2}-[A-Za-z0-9]{4})(?:[.\-_]|$)", name)
            if not case:
                raise ValueError(f"Cannot identify TCGA case from {name}")
            reports.append(
                Report(
                    rid,
                    case[1],
                    reports_dir / rid / name,
                    name,
                    int(row["size"]),
                    row["md5"].lower(),
                )
            )
    if not reports:
        raise ValueError("Manifest is empty")
    return sorted(reports, key=lambda r: r.report_id)


def shard(reports: list[Report], count: int, index: int) -> list[Report]:
    if count < 1 or not 0 <= index < count:
        raise ValueError("Require num_shards >= 1 and 0 <= shard_index < num_shards")
    return [
        r
        for r in reports
        if int(hashlib.sha256(r.report_id.encode()).hexdigest(), 16) % count == index
    ]
