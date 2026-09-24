import hashlib
from copy import deepcopy
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


def obs(value=None, quote=TEXT, page=1):
    return {
        "value": value,
        "evidence": [{"page": page, "quote": quote}] if value is not None else [],
    }


def specimen():
    return {
        "specimen_label": "A: bladder",
        "procedure": obs("cystoprostatectomy"),
        "tumor_site": obs(),
        "histologic_type": obs("Urothelial carcinoma"),
        "histologic_family": obs("urothelial"),
        "components": [],
        "grade": {
            "reported": obs(),
            "raw": obs("poorly differentiated"),
            "differentiation": obs("poor"),
            "legacy_who1973": obs(),
        },
        "tumor_size_cm": obs(),
        "tumor_configuration": obs(),
        "deepest_extent": obs("perivesical_soft_tissue"),
        "muscularis_propria": obs(),
        "lymphovascular_invasion": obs(),
        "associated_cis": obs(),
        "margins": [],
        "nodes": {
            "examined": obs(),
            "positive": obs(),
            "count_qualifier": obs(),
            "sites": obs(),
            "extranodal_extension": obs(),
        },
        "stage": {
            "reported_pt": obs(),
            "raw": obs(),
            "modifiers": obs(),
            "reported_pn": obs(),
            "reported_pm": obs(),
            "edition": obs(),
        },
        "treatment_effect": obs(),
        "associated_epithelial_lesions": obs(),
        "additional_findings": obs(),
        "uncertainties": [],
    }


@pytest.fixture
def payload():
    return {
        "schema_version": "1.0",
        "bladder_specimens": [deepcopy(specimen())],
        "other_primary_present": obs(True),
        "report_issues": [],
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
