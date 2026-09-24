import json
from pathlib import Path

import pytest
from conftest import TEXT, obs
from pydantic import ValidationError

from blca.normalize import normalize
from blca.schema import BladderExtraction, validate_evidence


def summary(payload):
    return normalize(BladderExtraction.model_validate(payload))["specimens"][0]


def test_cystoprostatectomy_keeps_inferred_bladder_pt3(payload):
    # Contract/normalization test on a curated model response, not a live model accuracy test.
    result = summary(payload)
    assert result["pt"] == "pT3"
    assert result["pt_basis"] == "inferred"
    assert result["grade"] == "high_grade"
    assert result["grade_basis"] == "inferred"
    assert result["review_reasons"]
    validate_evidence(BladderExtraction.model_validate(payload), {1: TEXT})


@pytest.mark.parametrize(
    ("extent", "expected"),
    [
        ("lamina_propria", "pT1"),
        ("muscularis_mucosae", "pT1"),
        ("muscularis_propria", "pT2"),
        ("muscularis_propria_inner_half", "pT2a"),
        ("muscularis_propria_outer_half", "pT2b"),
        ("perivesical_microscopic", "pT3a"),
        ("perivesical_macroscopic_mass", "pT3b"),
        ("direct_transmural_prostatic_stroma", "pT4a"),
        ("direct_uterus_or_vagina", "pT4a"),
        ("direct_pelvic_or_abdominal_wall", "pT4b"),
        ("adipose_unspecified", None),
        ("muscle_unspecified", None),
        ("prostatic_stroma_route_uncertain", None),
        ("prostatic_urethra_or_ducts_only", None),
        ("no_residual_tumor", None),
    ],
)
def test_anatomical_stage_rules(payload, extent, expected):
    payload["bladder_specimens"][0]["deepest_extent"] = obs(extent)
    assert summary(payload)["pt"] == expected


@pytest.mark.parametrize(
    ("extent", "expected"),
    [
        ("muscularis_propria_outer_half", "pT2"),
        ("perivesical_soft_tissue", None),
        ("direct_transmural_prostatic_stroma", None),
        ("lamina_propria", "pT1"),
    ],
)
def test_turbt_does_not_overstage(payload, extent, expected):
    s = payload["bladder_specimens"][0]
    s["procedure"] = obs("TURBT")
    s["deepest_extent"] = obs(extent)
    result = summary(payload)
    assert result["pt"] == expected
    assert result["pt_inferred_is_minimum"] is bool(expected)


def test_explicit_stage_preserved_and_conflict_flagged(payload):
    payload["bladder_specimens"][0]["stage"]["reported_pt"] = obs("pT4")
    result = summary(payload)
    assert result["pt"] == "pT4"
    assert result["pt_inferred_from_extent"] == "pT3"
    assert any("conflicts" in i for i in result["review_reasons"])


@pytest.mark.parametrize("family", ["squamous", "adenocarcinoma", "neuroendocrine", None])
def test_no_urothelial_grade_mapping_for_other_families(payload, family):
    payload["bladder_specimens"][0]["histologic_family"] = obs(family)
    assert summary(payload)["grade"] is None


@pytest.mark.parametrize("differentiation", ["well", "moderate", None])
def test_ambiguous_differentiation_not_forced_to_binary(payload, differentiation):
    payload["bladder_specimens"][0]["grade"]["differentiation"] = obs(differentiation)
    assert summary(payload)["grade"] is None


def test_legacy_g2_is_ambiguous(payload):
    s = payload["bladder_specimens"][0]
    s["grade"]["differentiation"] = obs()
    s["grade"]["legacy_who1973"] = obs("G2")
    assert summary(payload)["grade"] is None


def test_reported_grade_wins_and_conflict_is_visible(payload):
    payload["bladder_specimens"][0]["grade"]["reported"] = obs("low_grade")
    result = summary(payload)
    assert result["grade"] == "low_grade"
    assert result["grade_basis"] == "reported"
    assert any("grade conflicts" in i for i in result["review_reasons"])


def test_mixed_grade_not_collapsed(payload):
    payload["bladder_specimens"][0]["grade"]["reported"] = obs("mixed_grade")
    assert summary(payload)["grade"] is None


def test_schema_rejects_extra_prostate_fields_and_missing_fields(payload):
    payload["bladder_specimens"][0]["gleason_score"] = "3+4"
    with pytest.raises(ValidationError):
        BladderExtraction.model_validate(payload)
    payload["bladder_specimens"][0].pop("gleason_score")
    payload["bladder_specimens"][0].pop("grade")
    with pytest.raises(ValidationError):
        BladderExtraction.model_validate(payload)


def test_evidence_required_but_quote_and_page_mismatches_only_warn(payload):
    payload["bladder_specimens"][0]["deepest_extent"]["evidence"] = []
    with pytest.raises(ValidationError):
        BladderExtraction.model_validate(payload)
    payload["bladder_specimens"][0]["deepest_extent"] = obs(
        "perivesical_soft_tissue", "Invented quote"
    )
    warnings = validate_evidence(BladderExtraction.model_validate(payload), {1: TEXT})
    assert warnings == [
        "bladder_specimens[0].deepest_extent.evidence[0]: Evidence not found on OCR page 1"
    ]
    payload["bladder_specimens"][0]["deepest_extent"] = obs("perivesical_soft_tissue", TEXT, page=2)
    warnings = validate_evidence(BladderExtraction.model_validate(payload), {1: TEXT})
    assert len(warnings) == 1
    assert "page 2" in warnings[0]


def test_nodes_must_be_consistent_and_nonnegative(payload):
    nodes = payload["bladder_specimens"][0]["nodes"]
    nodes["examined"], nodes["positive"] = obs(2), obs(3)
    with pytest.raises(ValidationError, match="Positive nodes"):
        BladderExtraction.model_validate(payload)
    nodes["positive"] = obs(-1)
    with pytest.raises(ValidationError):
        BladderExtraction.model_validate(payload)


def test_no_bladder_requires_review(payload):
    payload["bladder_specimens"] = []
    result = normalize(BladderExtraction.model_validate(payload))
    assert result["review_required"] is True


def test_checked_in_schema_matches_code():
    saved = json.loads((Path(__file__).parents[1] / "docs/bladder.schema.json").read_text())
    assert saved == BladderExtraction.model_json_schema()


def test_matching_evidence_has_no_warnings_and_tolerates_whitespace(payload):
    parsed = BladderExtraction.model_validate(payload)
    assert validate_evidence(parsed, {1: TEXT.replace(" ", "\n  ")}) == []


def test_evidence_warnings_require_review_without_changing_findings(payload):
    parsed = BladderExtraction.model_validate(payload)
    before = parsed.model_dump()
    plain = normalize(parsed)
    warned = normalize(parsed, evidence_warnings=["Unmatched quote"])
    assert warned["review_required"] is True
    assert "Unmatched quote" in warned["review_reasons"]
    assert warned["specimens"] == plain["specimens"]
    assert parsed.model_dump() == before
