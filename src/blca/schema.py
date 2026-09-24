"""The LLM extracts observations; Python derives grade and stage separately."""

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")
NonnegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
PT = Literal[
    "pTX",
    "pT0",
    "pTa",
    "pTis",
    "pT1",
    "pT2",
    "pT2a",
    "pT2b",
    "pT3",
    "pT3a",
    "pT3b",
    "pT4",
    "pT4a",
    "pT4b",
]
Status = Literal["present", "not_identified", "indeterminate", "not_applicable"]
Extent = Literal[
    "no_residual_tumor",
    "noninvasive_papillary",
    "flat_cis",
    "lamina_propria",
    "muscularis_mucosae",
    "muscle_unspecified",
    "muscularis_propria",
    "muscularis_propria_inner_half",
    "muscularis_propria_outer_half",
    "adipose_unspecified",
    "perivesical_soft_tissue",
    "perivesical_microscopic",
    "perivesical_macroscopic_mass",
    "direct_transmural_prostatic_stroma",
    "direct_uterus_or_vagina",
    "direct_pelvic_or_abdominal_wall",
    "prostatic_urethra_or_ducts_only",
    "prostatic_stroma_route_uncertain",
    "other",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictModel):
    page: int = Field(ge=1, description="1-based PDF page, from the OCR page marker")
    quote: str = Field(min_length=1, description="Verbatim substring of that OCR page")


class Observation(StrictModel, Generic[T]):
    value: T | None
    evidence: list[Evidence]

    @model_validator(mode="after")
    def supported(self):
        if self.value is not None and not self.evidence:
            raise ValueError("Every non-null observation needs page-specific evidence")
        return self


class Component(StrictModel):
    histology: Observation[str]
    percent: Observation[Annotated[float, Field(ge=0, le=100)]]


class Grade(StrictModel):
    reported: Observation[Literal["high_grade", "low_grade", "mixed_grade"]]
    raw: Observation[str]
    differentiation: Observation[Literal["well", "moderate", "poor", "undifferentiated"]]
    legacy_who1973: Observation[Literal["G1", "G2", "G3"]]


class Stage(StrictModel):
    reported_pt: Observation[PT]
    raw: Observation[str]
    modifiers: Observation[list[Literal["y", "r", "m"]]]
    reported_pn: Observation[Literal["pNX", "pN0", "pN1", "pN2", "pN3"]]
    reported_pm: Observation[Literal["pM1", "pM1a", "pM1b"]]
    edition: Observation[str]


class Margin(StrictModel):
    site: Observation[str]
    invasive: Observation[Status]
    noninvasive: Observation[Status]


class Nodes(StrictModel):
    examined: Observation[NonnegativeInt]
    positive: Observation[NonnegativeInt]
    count_qualifier: Observation[Literal["exact", "at_least", "other"]]
    sites: Observation[list[str]]
    extranodal_extension: Observation[Status]

    @model_validator(mode="after")
    def counts(self):
        if (
            self.examined.value is not None
            and self.positive.value is not None
            and self.positive.value > self.examined.value
        ):
            raise ValueError("Positive nodes cannot exceed examined nodes")
        return self


class BladderSpecimen(StrictModel):
    specimen_label: str = Field(min_length=1)
    procedure: Observation[
        Literal[
            "biopsy",
            "TURBT",
            "partial_cystectomy",
            "radical_cystectomy",
            "cystoprostatectomy",
            "other",
        ]
    ]
    tumor_site: Observation[list[str]]
    histologic_type: Observation[str]
    histologic_family: Observation[
        Literal["urothelial", "squamous", "adenocarcinoma", "neuroendocrine", "other"]
    ]
    components: list[Component]
    grade: Grade
    tumor_size_cm: Observation[PositiveFloat]
    tumor_configuration: Observation[list[str]]
    deepest_extent: Observation[Extent]
    muscularis_propria: Observation[
        Literal["not_identified", "present_uninvolved", "present_involved", "indeterminate"]
    ]
    lymphovascular_invasion: Observation[Status]
    associated_cis: Observation[Status]
    margins: list[Margin]
    nodes: Nodes
    stage: Stage
    treatment_effect: Observation[str]
    associated_epithelial_lesions: Observation[list[str]]
    additional_findings: Observation[list[str]]
    uncertainties: list[str]


class BladderExtraction(StrictModel):
    schema_version: Literal["1.0"]
    bladder_specimens: list[BladderSpecimen]
    other_primary_present: Observation[bool]
    report_issues: list[str]


def validate_evidence(extraction: BladderExtraction, pages: dict[int, str]) -> None:
    """Reject fabricated quotes/page numbers; tolerate whitespace introduced by OCR."""
    normalized = {p: " ".join(t.split()) for p, t in pages.items()}

    def visit(value):
        if isinstance(value, dict):
            if set(value) == {"page", "quote"}:
                quote = " ".join(value["quote"].split())
                if not quote or quote not in normalized.get(value["page"], ""):
                    raise ValueError(f"Evidence not found on OCR page {value['page']}")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(extraction.model_dump())
