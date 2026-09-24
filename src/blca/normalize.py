"""Conservative, explicit project rules, not a replacement for pathologic review."""

from .schema import BladderExtraction, BladderSpecimen

RULES_VERSION = "1.0"
CYSTECTOMY = {"partial_cystectomy", "radical_cystectomy", "cystoprostatectomy"}
EXTENT_PT = {
    "noninvasive_papillary": "pTa",
    "flat_cis": "pTis",
    "lamina_propria": "pT1",
    "muscularis_mucosae": "pT1",
    "muscularis_propria": "pT2",
    "muscularis_propria_inner_half": "pT2a",
    "muscularis_propria_outer_half": "pT2b",
    "perivesical_soft_tissue": "pT3",
    "perivesical_microscopic": "pT3a",
    "perivesical_macroscopic_mass": "pT3b",
    "direct_transmural_prostatic_stroma": "pT4a",
    "direct_uterus_or_vagina": "pT4a",
    "direct_pelvic_or_abdominal_wall": "pT4b",
}


def normalize_specimen(s: BladderSpecimen) -> dict:
    issues = list(s.uncertainties)
    extent = s.deepest_extent.value
    is_cystectomy = s.procedure.value in CYSTECTOMY
    inferred = EXTENT_PT.get(extent)
    if inferred and not is_cystectomy:
        if inferred.startswith(("pT3", "pT4")):
            inferred = None
            issues.append(
                "Advanced stage inference requires a cystectomy specimen and clear anatomy."
            )
        elif inferred in {"pT2a", "pT2b"}:
            inferred = "pT2"
    if extent in {
        "adipose_unspecified",
        "muscle_unspecified",
        "prostatic_stroma_route_uncertain",
        "prostatic_urethra_or_ducts_only",
        "other",
    }:
        issues.append("Extent does not establish a bladder pT category; review anatomy and route.")
    # A tumor-free fragment alone does not establish pT0 for the primary tumor.
    stated = s.stage.reported_pt.value
    pt = stated or inferred
    pt_basis = "reported" if stated else "inferred" if inferred else "unknown"
    if inferred and not stated:
        issues.append("pT inferred from described extent; verify against the source report.")
    if stated and inferred and stated != inferred:
        # Parent categories and their own subcategories can coexist without contradiction.
        compatible = (stated in {"pT2", "pT3", "pT4"} and inferred.startswith(stated)) or (
            inferred in {"pT2", "pT3", "pT4"} and stated.startswith(inferred)
        )
        if not compatible:
            issues.append(f"Reported pT {stated} conflicts with extent-derived {inferred}.")
    if not is_cystectomy and stated and stated.startswith(("pT3", "pT4", "pT2a", "pT2b")):
        issues.append(
            "Reported advanced/subdivided pT in non-cystectomy: verify specimen and staging."
        )
    if s.muscularis_propria.value in {"present_uninvolved", "not_identified"} and (
        extent or ""
    ).startswith("muscularis_propria"):
        issues.append("Muscularis propria status conflicts with extent.")

    reported_grade = s.grade.reported.value
    grade = reported_grade if reported_grade in {"high_grade", "low_grade"} else None
    grade_basis = "reported" if grade else "unknown"
    urothelial = s.histologic_family.value == "urothelial"
    # Project harmonization, explicitly marked inferred. Never apply to a separate prostate tumor
    # or to pure SCC/adenocarcinoma (which retain their differentiation-based grade).
    inferred_grade = None
    if urothelial:
        if (
            s.grade.differentiation.value in {"poor", "undifferentiated"}
            or s.grade.legacy_who1973.value == "G3"
        ):
            inferred_grade = "high_grade"
        elif s.grade.legacy_who1973.value == "G1":
            inferred_grade = "low_grade"
    if grade is None and reported_grade != "mixed_grade" and inferred_grade:
        grade = inferred_grade
        grade_basis = "inferred"
        issues.append(
            "Grade harmonized from urothelial differentiation/legacy grade; review mapping."
        )
    if reported_grade == "mixed_grade":
        issues.append("Mixed grade retained; no single binary grade assigned.")
    if grade_basis == "reported" and inferred_grade and grade != inferred_grade:
        issues.append("Reported grade conflicts with differentiation/legacy grade.")
    if pt is None:
        issues.append("Bladder pT unavailable or insufficiently supported.")
    if grade is None:
        issues.append("Binary bladder grade unavailable, ambiguous, or not applicable.")
    return {
        "specimen_label": s.specimen_label,
        "pt": pt,
        "pt_basis": pt_basis,
        "pt_inferred_from_extent": inferred,
        "pt_inferred_is_minimum": bool(inferred and not is_cystectomy),
        "pt_evidence": (s.stage.reported_pt if stated else s.deepest_extent).model_dump()[
            "evidence"
        ],
        "grade": grade,
        "grade_basis": grade_basis,
        "grade_evidence": (
            s.grade.reported.evidence
            if grade_basis == "reported"
            else s.grade.differentiation.evidence + s.grade.legacy_who1973.evidence
        )
        if grade
        else [],
        "review_reasons": list(dict.fromkeys(issues)),
    }


def normalize(extraction: BladderExtraction) -> dict:
    specimens = [normalize_specimen(s) for s in extraction.bladder_specimens]
    # Evidence instances in inferred summaries must remain JSON serializable.
    for s in specimens:
        s["grade_evidence"] = [e.model_dump() for e in s["grade_evidence"]]
    reasons = list(extraction.report_issues)
    if not specimens:
        reasons.append("No bladder specimen extracted; verify report relevance/OCR completeness.")
    if len(specimens) > 1:
        reasons.append(
            "Multiple bladder specimens/lesions retained separately; no patient-level collapse."
        )
    return {
        "rules_version": RULES_VERSION,
        "specimens": specimens,
        "review_required": bool(reasons or any(s["review_reasons"] for s in specimens)),
        "review_reasons": reasons,
    }
