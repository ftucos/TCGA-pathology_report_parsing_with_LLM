# Bladder extraction schema and derivation rules

`blca schema` generates [bladder.schema.json](bladder.schema.json) directly from
`src/blca/schema.py`. Pydantic validates the same schema used for Ollama structured
output. Every object forbids extra fields and every field is required; unknown
observations use `{"value": null, "evidence": []}`. An explicit negative differs
from an absent mention. Evidence contains a 1-based PDF page and a verbatim OCR
substring, with whitespace normalized only for validation.

The schema is based on the supplied CAP biopsy/TURBT v4.3.0.0, pages 4–7 (fields)
and 11–15 (grading and invasion notes). CAP's cystectomy v4.2.0.0 supplies additional
resection concepts. Neither protocol is copied as a complete reporting form.

| JSON field within `bladder_specimens[]` | Meaning / source |
| --- | --- |
| `specimen_label`, `procedure` | Distinguish specimen, lesion and procedure; CAP specimen section |
| `tumor_site`, `histologic_type`, `histologic_family` | Bladder location, full diagnosis and family |
| `components[]` | Variant histologies/divergent differentiation and reported percentages |
| `grade` | Stated high/low/mixed grade, original wording, differentiation and explicitly identified legacy WHO 1973 grade |
| `tumor_size_cm`, `tumor_configuration` | Reported size (normalized to cm), papillary/solid/flat/other configuration |
| `deepest_extent` | Most deeply involved definite anatomic compartment of this lesion |
| `muscularis_propria` | Detrusor absent, present uninvolved, present involved or indeterminate |
| `lymphovascular_invasion`, `associated_cis` | Present, not identified, indeterminate, not applicable, or null if missing |
| `margins[]` | Cystectomy margin sites; invasive and noninvasive involvement kept separate |
| `nodes` | Bladder-primary regional node counts, qualifier, sites and extranodal extension |
| `stage` | Explicit bladder pT/pN/pM, verbatim statement, y/r/m descriptors and reported edition |
| `treatment_effect` | Stated effect, not a reason to invent post-treatment staging |
| `associated_epithelial_lesions`, `additional_findings` | Associated lesions, artifacts, therapy-related and other findings |
| `uncertainties` | Specimen-level contradictions and attribution/interpretation issues |

At report level, `other_primary_present` records supported presence/absence of
another primary without importing its grade or stage. `report_issues` captures
source problems and conflicts. Lists are empty when there are no supported entries.
The supplied synthetic example includes a prostate primary deliberately excluded
from the bladder grade/stage fields.

## Derived output

The LLM does not fill the final normalized fields. Python adds `normalized`:

- `specimens[].pt`, `pt_basis`, `pt_inferred_from_extent`, `pt_inferred_is_minimum`,
  and the evidence used. `pt_basis` is reported, inferred or unknown. A stated pTX
  is preserved. Non-assignment/unknown is null, not an invented pTX/pT0.
- `specimens[].grade`, `grade_basis`, and the evidence used. Binary grades are
  `high_grade`, `low_grade` or null. The full original grade always remains available.
- `review_required` and report/specimen `review_reasons`.

A normalized `pt="pT2"` plus `stage.modifiers.value=["y"]` represents ypT2;
the original ypT wording remains in `stage.raw`. Modifiers also remain visible in
CSV. Never discard modifiers when analyzing stage. pN/pM are extraction-only;
the software does not infer them from node counts or assume pM0.

Definite described extent supports a separate inferred category. Parent category
versus a compatible explicit subcategory is not a conflict. For example, stated
pT3b with perivesical soft tissue description remains pT3b; inference is only pT3.
An incompatible stated pT4 and perivesical-only extent is flagged while preserving
the report. Anatomically ambiguous terms do not trigger a stage guess.

For urothelial grade harmonization, poor/undifferentiated or legacy WHO G3 maps to
inferred high grade; legacy WHO G1 maps to inferred low grade. This is an explicit
project policy, not a claim that all historic grading systems are interchangeable.
G2, moderately differentiated, isolated well-differentiated, mixed-grade lesions
and unexplained numeric grades are not automatically converted. A conflicting
explicit binary grade is preserved with a review flag. Pure SCC/adenocarcinoma
keeps its differentiation-based grade instead of receiving a urothelial grade.

Inference and review flags do not establish accuracy. The test suite validates
software behavior on curated synthetic model responses, not the LLM's ability to
identify specimen boundaries or correctly read a real scan. Validate a sample on
the HPC, including TURBT, cystectomy, mixed bladder/prostate tumors, uncertain fat
or muscle invasion, historical grading, and negative/post-treatment specimens.

## Sources and maintenance

- Provided CAP file: `Bladder.Bx.TURBT_4.3.0.0.REL_CAPCP.pdf` (June 2025).
- [CAP cystectomy protocol v4.2.0.0](https://documents.cap.org/protocols/Bladder_4.2.0.0.REL_CAPCP.pdf)
  (September 2023; AJCC 8th edition).
- [GLM-OCR Ollama deployment](https://github.com/zai-org/GLM-OCR/blob/main/examples/ollama-deploy/README.md).
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

These references fix the intended interpretation; historical TCGA reports are not
silently converted to a newer edition. When changing derivation logic, bump
`RULES_VERSION` in `normalize.py`; when changing other pipeline behavior, bump the
package version in `pyproject.toml` and `src/blca/__init__.py`. Prompt/schema changes
are automatically fingerprinted. Regenerate the checked-in schema after editing
Pydantic models and rerun the tests.
