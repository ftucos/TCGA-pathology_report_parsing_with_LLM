import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from blca.schema import SCHEMA_VERSION, BladderExtraction

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "pT",
    "pT_comment",
    "pN",
    "pN_comment",
    "pM",
    "pM_comment",
    "grade",
    "grade_comment",
    "margins",
    "margins_comment",
    "histology",
    "histology_comment",
    "vascular_invasion",
    "vascular_invasion_comment",
]


def test_comparison_values_keep_comments(payload):
    assert BladderExtraction.model_validate(payload).model_dump() == payload


def test_exact_aliases_normalize_without_losing_modifiers_or_wording():
    result = BladderExtraction.model_validate(
        {
            "pT": "ypT2b",
            "pT_comment": "Reported after treatment",
            "pN": "pN0",
            "pM": "pM1a",
            "grade": "high-grade",
            "margins": "positive",
            "histology": "transitional cell carcinoma",
            "vascular_invasion": "not identified",
        }
    )
    assert (result.pT, result.pN, result.pM) == ("T2b", "N0", "M1a")
    assert result.pT_comment == "Reported after treatment; Normalized from: ypT2b"
    assert result.grade == "High"
    assert result.margins == "R1"
    assert "positive" in result.margins_comment
    assert result.histology == "Urothelial carcinoma"
    assert "transitional cell carcinoma" in result.histology_comment
    assert result.vascular_invasion == "Absent"


def test_no_nodes_or_distant_assessment_are_unknown_not_negative():
    result = BladderExtraction.model_validate(
        {
            "pN": "NX",
            "pN_comment": "No nodes submitted",
            "pM": "MX",
            "pM_comment": "Distant metastases not assessed; project placeholder",
        }
    )
    assert result.pN == "NX"
    assert result.pN_comment == "No nodes submitted"
    assert result.pM == "MX"
    assert "Distant" in result.pM_comment
    assert result.margins == "RX"
    assert result.vascular_invasion is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("papillary transitional cell carcinoma", "Urothelial carcinoma, papillary"),
        ("urothelial carcinoma nested variant", "Urothelial carcinoma, nested"),
        ("Urothelial carcinoma, sarcomatoid", "Urothelial carcinoma, sarcomatoid"),
        ("Squamous cell carcinoma", "Squamous cell carcinoma"),
    ],
)
def test_histology_comparison_labels(raw, expected):
    result = BladderExtraction.model_validate({"histology": raw})
    assert result.histology == expected
    if raw != expected:
        assert raw in result.histology_comment


def test_partial_or_empty_report_needs_no_quotes_or_cap_fields():
    result = BladderExtraction.model_validate({"histology": "Adenocarcinoma"})
    assert result.histology == "Adenocarcinoma"
    assert result.grade is None
    assert (result.pN, result.pM, result.margins) == ("NX", "MX", "RX")
    empty = BladderExtraction.model_validate({})
    assert empty.pT is None
    assert "placeholder" in empty.pM_comment
    assert empty.grade is None


def test_unmapped_values_move_to_comments_instead_of_failing():
    result = BladderExtraction.model_validate(
        {
            "pT": "T2 or T3",
            "grade": "G2 / moderately differentiated",
            "grade_comment": "Legacy system",
            "margins": {"left": "positive", "right": "negative"},
            "vascular_invasion": ["present", "absent"],
            "unused": "ignored",
        }
    )
    assert result.pT is None
    assert "T2 or T3" in result.pT_comment
    assert result.grade is None
    assert "Legacy system" in result.grade_comment
    assert "G2 / moderately differentiated" in result.grade_comment
    assert result.margins == "RX"
    assert '"left": "positive"' in result.margins_comment
    assert result.vascular_invasion is None
    assert '["present", "absent"]' in result.vascular_invasion_comment
    assert list(result.model_dump()) == FIELDS
    # Revalidating saved results during cache reuse/export does not duplicate comments.
    assert BladderExtraction.model_validate(result.model_dump()).model_dump() == result.model_dump()


@pytest.mark.parametrize("field,value", [("pT", "cT2"), ("pN", "cN0"), ("pM", "cM0")])
def test_clinical_codes_are_not_silently_promoted_to_pathological(field, value):
    result = BladderExtraction.model_validate({field: value})
    assert getattr(result, field) in (None, "NX", "MX")
    assert value in getattr(result, field + "_comment")


def test_blank_values_and_extra_keys_are_tolerated():
    result = BladderExtraction.model_validate({"pT": "  pT2  ", "grade": " \n", "extra": 1})
    assert result.pT == "T2"
    assert result.grade is None
    assert list(result.model_dump()) == FIELDS


@pytest.mark.parametrize("raw", ["{broken", "[]", "null", '"a report"'])
def test_response_must_still_be_a_json_object(raw):
    with pytest.raises(ValidationError):
        BladderExtraction.model_validate_json(raw)


def test_checked_in_schema_and_example_match_code():
    schema = BladderExtraction.model_json_schema()
    assert json.loads((ROOT / "docs/bladder.schema.json").read_text()) == schema
    assert list(schema["properties"]) == FIELDS
    assert "$defs" not in schema
    assert not schema.get("required")
    assert schema["properties"]["grade"]["enum"] == ["High", "Low", None]
    example = json.loads((ROOT / "docs/example.extraction.json").read_text())
    assert example["synthetic_example"] is True
    assert example["schema_version"] == SCHEMA_VERSION
    assert (
        BladderExtraction.model_validate(example["extraction"]).model_dump()
        == example["extraction"]
    )
