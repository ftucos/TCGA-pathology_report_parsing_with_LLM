"""Comparable report-level values with permissive, loss-preserving comments."""

import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "3.0"
CATEGORIES = {
    "pT": [
        "TX",
        "T0",
        "Ta",
        "Tis",
        "T1",
        "T1a",
        "T1b",
        "T1c",
        "T1e",
        "T1m",
        "T2",
        "T2a",
        "T2b",
        "T3",
        "T3a",
        "T3b",
        "T4",
        "T4a",
        "T4b",
    ],
    "pN": ["NX", "N0", "N1", "N2", "N3"],
    "pM": ["MX", "M0", "M1", "M1a", "M1b", "M1c"],
    "grade": ["High", "Low"],
    "margins": ["R0", "R1", "R2", "RX"],
    "vascular_invasion": ["Present", "Absent", "Indeterminate"],
}


def status_field(name, description):
    return Field(
        default=None, description=description, json_schema_extra={"enum": CATEGORIES[name] + [None]}
    )


class BladderExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore", json_schema_extra={"additionalProperties": False})

    pT: str | None = status_field(
        "pT", "T category only; reported or conservatively inferred. Explain basis in pT_comment."
    )
    pT_comment: str | None = None
    pN: str | None = status_field(
        "pN", "N category only; NX if nodes absent or unassessed, never default N0."
    )
    pN_comment: str | None = None
    pM: str | None = status_field(
        "pM", "M category only; MX is a project placeholder for unassessed distant spread."
    )
    pM_comment: str | None = None
    grade: str | None = status_field(
        "grade",
        "High or Low; null if no defensible binary mapping. Explain original grade and any inference.",
    )
    grade_comment: str | None = None
    margins: str | None = status_field(
        "margins",
        "Project margin summary: R0 negative, R1 microscopic involvement, R2 stated gross residual disease, RX unknown.",
    )
    margins_comment: str | None = None
    histology: str | None = Field(
        default=None,
        description="Short standardized tumor type/subtype; e.g. Urothelial carcinoma, papillary / nested / sarcomatoid. Details in comment.",
    )
    histology_comment: str | None = None
    vascular_invasion: str | None = status_field(
        "vascular_invasion",
        "Lymphatic and/or vascular invasion: Present, Absent, Indeterminate, or null if not mentioned.",
    )
    vascular_invasion_comment: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def readable_value(cls, value):
        if value is None or value == [] or value == {}:
            return None
        if isinstance(value, str):
            return value.strip() or None
        return json.dumps(value, ensure_ascii=False)

    def add_comment(self, field, text):
        name = field + "_comment"
        old = getattr(self, name)
        if not old or text not in old:
            setattr(self, name, f"{old}; {text}" if old else text)

    @model_validator(mode="after")
    def comparable_values(self):
        # Only exact aliases are mapped; narrative/conflicting values go to comments.
        aliases = {
            "grade": {
                "high grade": "High",
                "high-grade": "High",
                "hg": "High",
                "low grade": "Low",
                "low-grade": "Low",
                "lg": "Low",
            },
            "margins": {"negative": "R0", "uninvolved": "R0", "positive": "R1", "involved": "R1"},
            "vascular_invasion": {
                "positive": "Present",
                "identified": "Present",
                "negative": "Absent",
                "not identified": "Absent",
                "equivocal": "Indeterminate",
                "suspicious": "Indeterminate",
            },
        }
        for field, allowed in CATEGORIES.items():
            raw = getattr(self, field)
            if raw is None:
                continue
            value = raw.casefold()
            if field in ("pT", "pN", "pM"):
                # Strip pathologic/treatment prefixes only, never silently turn cTNM into pTNM.
                value = re.sub(r"^(?:[yr]*p|[yr]+)(?=[tnm])", "", value)
            canonical = {v.casefold(): v for v in allowed}.get(value)
            canonical = canonical or aliases.get(field, {}).get(value)
            setattr(self, field, canonical)
            if canonical is None:
                self.add_comment(field, f"Unmapped model value (review): {raw}")
            elif raw.casefold() != canonical.casefold():
                self.add_comment(field, f"Normalized from: {raw}")
        if self.histology:
            original = self.histology
            value = re.sub(
                r"\b(?:transitional cell carcinoma|tcc)\b",
                "urothelial carcinoma",
                original,
                flags=re.IGNORECASE,
            )
            if value.casefold() == "urothelial carcinoma":
                value = "Urothelial carcinoma"
            for subtype in ("papillary", "nested", "sarcomatoid", "micropapillary", "plasmacytoid"):
                if value.casefold() in (
                    f"{subtype} urothelial carcinoma",
                    f"urothelial carcinoma, {subtype}",
                    f"urothelial carcinoma, {subtype} variant",
                    f"urothelial carcinoma {subtype}",
                    f"urothelial carcinoma {subtype} variant",
                    f"urothelial carcinoma with {subtype} differentiation",
                ):
                    value = f"Urothelial carcinoma, {subtype}"
                    break
            self.histology = value
            if value.casefold() != original.casefold():
                self.add_comment("histology", f"Original terminology: {original}")
        for field, fallback, explanation in (
            (
                "pN",
                "NX",
                "Nodal category unavailable; NX is an unknown/unassessed placeholder, not N0.",
            ),
            (
                "pM",
                "MX",
                "Distant spread unavailable; MX is a project unknown/unassessed placeholder, not a formal modern pM category or M0.",
            ),
            ("margins", "RX", "Margin status unavailable or unassessable."),
        ):
            if getattr(self, field) is None:
                setattr(self, field, fallback)
                self.add_comment(field, explanation)
            elif getattr(self, field) == fallback and not getattr(self, field + "_comment"):
                self.add_comment(field, explanation)
        for field in ("pT", "grade", "histology", "vascular_invasion"):
            if getattr(self, field) is None and not getattr(self, field + "_comment"):
                self.add_comment(field, "Not reported or not classifiable from the model response.")
        return self
