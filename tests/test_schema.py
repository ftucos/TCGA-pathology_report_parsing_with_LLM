import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from blca.schema import BladderExtraction

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"stage", "grade", "histology", "margins"}


def test_flat_findings_preserve_report_wording():
    findings = {
        "stage": "ypT2b pN0; original report states stage II",
        "grade": "G2, moderately differentiated",
        "histology": "Urothelial carcinoma with squamous differentiation",
        "margins": "Left ureter: involved; right ureter: negative; soft tissue: uncertain",
    }
    assert BladderExtraction.model_validate(findings).model_dump() == findings


def test_partial_report_needs_no_quotes_or_cap_fields():
    parsed = BladderExtraction.model_validate({"histology": "urothelial carcinoma"})
    assert parsed.model_dump() == {
        "stage": None,
        "grade": None,
        "histology": "urothelial carcinoma",
        "margins": None,
    }


def test_empty_report_is_valid():
    assert BladderExtraction.model_validate({}).model_dump() == dict.fromkeys(FIELDS)


def test_extra_keys_and_empty_strings_do_not_cause_retries():
    parsed = BladderExtraction.model_validate(
        {
            "stage": "  pT2  ",
            "grade": " \n",
            "comment": "not a CAP report",
        }
    )
    assert parsed.stage == "pT2"
    assert parsed.grade is None
    assert set(parsed.model_dump()) == FIELDS


def test_unexpected_json_values_are_preserved_as_text():
    margins = {"left": "positive", "right": "negative"}
    parsed = BladderExtraction.model_validate(
        {
            "stage": 2,
            "grade": ["G2", "G3"],
            "histology": {},
            "margins": margins,
        }
    )
    assert parsed.stage == "2"
    assert json.loads(parsed.grade) == ["G2", "G3"]
    assert parsed.histology is None
    assert json.loads(parsed.margins) == margins


@pytest.mark.parametrize("raw", ["{broken", "[]", "null", '"a report"'])
def test_response_must_still_be_a_json_object(raw):
    with pytest.raises(ValidationError):
        BladderExtraction.model_validate_json(raw)


def test_checked_in_schema_and_example_match_code():
    schema = BladderExtraction.model_json_schema()
    assert json.loads((ROOT / "docs/bladder.schema.json").read_text()) == schema
    assert set(schema["properties"]) == FIELDS
    assert "$defs" not in schema
    assert not schema.get("required")
    example = json.loads((ROOT / "docs/example.extraction.json").read_text())
    assert example["synthetic_example"] is True
    assert (
        BladderExtraction.model_validate(example["extraction"]).model_dump()
        == example["extraction"]
    )
