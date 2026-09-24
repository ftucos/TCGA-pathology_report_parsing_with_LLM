import hashlib
from dataclasses import replace
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from blca.config import load_settings
from blca.storage import Report

ROOT = Path(__file__).resolve().parents[1]
TEXT = (
    "Cystoprostatectomy. Bladder: poorly differentiated urothelial carcinoma, "
    "invades perivesical soft tissue. Prostate: adenocarcinoma Gleason 3+4, pT2."
)


@pytest.fixture
def payload():
    return {
        "pT": "T3",
        "pT_comment": "Inferred from: invades perivesical soft tissue; substage not determined.",
        "pN": "NX",
        "pN_comment": "Nodes not mentioned; assessment unknown.",
        "pM": "MX",
        "pM_comment": "Distant spread not assessed; project placeholder.",
        "grade": "High",
        "grade_comment": "Inferred project harmonization from poorly differentiated urothelial carcinoma.",
        "margins": "RX",
        "margins_comment": "Margins not mentioned.",
        "histology": "Urothelial carcinoma",
        "histology_comment": "Reported urothelial carcinoma; separate prostate carcinoma excluded.",
        "vascular_invasion": None,
        "vascular_invasion_comment": "Not mentioned.",
    }


@pytest.fixture
def settings(tmp_path):
    s = load_settings(ROOT / "pyproject.toml")
    return replace(
        s,
        root=tmp_path,
        reports_dir=tmp_path / "pdfs",
        output_dir=tmp_path / "out",
        manifest=tmp_path / "manifest.tsv",
        attempts=1,
    )


@pytest.fixture
def report(settings):
    rid = "file-uuid"
    filename = "TCGA-AB-1234.report.PDF"
    path = settings.reports_dir / rid / filename
    path.parent.mkdir(parents=True)
    with pdfium.PdfDocument.new() as doc:
        for _ in range(2):
            page = doc.new_page(612, 792)
            page.close()
        doc.save(path)
    data = path.read_bytes()
    r = Report(rid, "TCGA-AB-1234", path, filename, len(data), hashlib.md5(data).hexdigest())
    settings.manifest.write_text(
        f"id\tfilename\tmd5\tsize\tstate\n{rid}\t{filename}\t{r.expected_md5}\t{len(data)}\treleased\n"
    )
    return r
