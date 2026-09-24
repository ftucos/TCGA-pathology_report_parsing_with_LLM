# Comparison columns and review comments

Schema version **3.0** returns one flat object with seven value/comment pairs.
Every value has a separate `<field>_comment` containing the original wording,
reported/inferred basis, useful source phrase, uncertainty, and specimen/site
context. Comments are not checked for verbatim OCR matches. No CAP completeness
requirement or nested specimen/evidence structure is imposed.

| Value column | Comparable values | Comment column |
| --- | --- | --- |
| `pT` | `Ta`, `Tis`, `T0`–`T4` and supported substages, `TX`, or null | `pT_comment` |
| `pN` | `N0`, `N1`, `N2`, `N3`, `NX` | `pN_comment` |
| `pM` | `M0`, `M1`, reported M1 subcategories, `MX` | `pM_comment` |
| `grade` | `High`, `Low`, or null | `grade_comment` |
| `margins` | `R0`, `R1`, `R2`, `RX` | `margins_comment` |
| `histology` | Short type/subtype, e.g. `Urothelial carcinoma, papillary`, `Urothelial carcinoma, nested`, `Urothelial carcinoma, sarcomatoid`; other types or `Mixed` allowed | `histology_comment` |
| `vascular_invasion` | `Present`, `Absent`, `Indeterminate`, or null | `vascular_invasion_comment` |

The `pT`/`pN`/`pM` columns contain category-only values for comparison. Original
prefixes (such as `ypT2b`), stage group, edition and clinical staging belong in
comments. Explicit pathological staging takes precedence; conservative inference
from unambiguous invasion wording is allowed, labeled **Inferred** with the source
phrase. For example, perivesical soft-tissue invasion can yield `T3` without
inventing T3a/T3b. Biopsy-based inference is described as minimum supported extent.
Reported historical staging is preserved rather than silently updated.

`NX` represents unavailable/unassessed nodal status. Comments distinguish explicit
absence of nodes from no nodal information. Examined negative nodes or explicit
pathological N0 are needed for `N0`; omission is not negative. `MX` is a **project
placeholder for unknown/unassessed distant spread**, not a formal modern pM
category. Missing nodes do not imply anything about distant metastases. `M0` is
retained only if explicitly reported as historical pathological staging; clinical
cM0 is kept in the comment with `pM=MX`.

Grade prefers reported High/Low. The prompt permits clearly labeled project
harmonization of urothelial G3/poor differentiation to High and G1/well
differentiation to Low, retaining original wording; these are not exact
cross-system equivalences. G2/moderate differentiation alone remains null with a
comment, as do non-urothelial grades without explicit High/Low. Grade is not
inferred merely from invasion or variant. Mixed-grade details belong in comments.

Margins use a **project margin-status summary**: R0 for explicitly negative
assessed margins, R1 for microscopic involvement, R2 only for stated gross
residual disease, and RX for unavailable/unassessable margins. This does not
establish whole-patient residual-tumor status. Comments retain site, invasive
versus CIS/noninvasive involvement, and differing statuses. Missing margin text
or a negative biopsy never implies R0.

Histology uses Urothelial carcinoma as the preferred synonym for transitional
cell carcinoma. The main/dominant subtype is the comparison label; other
components, percentages and associated CIS remain in comments. If there is no
dominant subtype, use Mixed and explain. An independent prostate cancer is
excluded. Vascular invasion includes lymphatic and/or blood-vessel invasion;
comments distinguish the type when stated. Perineural invasion and node
metastases alone do not establish vascular invasion. Missing text is null,
explicit absence is Absent, and equivocal language is Indeterminate.

## Permissive parsing

The generation schema guides categorical values, but the parser never rejects a
report for missing fields, extra keys, unmatched comments, or an unfamiliar
category. Exact aliases/case/prefix differences are normalized. Unrecognized or
conflicting categorical values, including unexpected arrays/objects, move into
the corresponding comment for review, leaving a null/unknown value. No narrative
text is silently parsed as a definitive category. Histology remains open text to
accommodate rare types. Revalidating a result does not duplicate comments.

Missing pN, pM and margins become NX, MX and RX with explanatory comments; other
missing values remain null. Malformed JSON, a non-object response or incomplete
model generation still causes retry/error. This validates data handling, not the
clinical correctness of an LLM inference; review comments and source reports for
discrepancies with TCGA metadata.

## Export and migration

CSV has one row per report: `report_id, case_id, filename`, followed by the seven
value/comment pairs in the order above. Nulls become empty cells. Reports with
no findings still have a row. JSONL contains the same fields in `extraction`,
plus source/model metadata and the OCR checkpoint reference. Original OCR and raw
responses remain available. See [synthetic example](example.extraction.json).

Version 3.0 changes the extraction fingerprint. Rerun `run` without `--force` to
reuse matching OCR and regenerate extraction; no model download or environment
rebuild is needed. Old CAP-style and four-field results remain archived and are
excluded from the new export until reprocessed. Regenerate exports afterward.
There are 413 reports for 412 cases; case ID is not unique.

Interpretive references (not completeness/validation requirements):
[EAU staging and grading](https://uroweb.org/guidelines/non-muscle-invasive-bladder-cancer/chapter/pathological-staging-and-classification-systems)
and [CAP bladder resection protocol](https://www.cap.org/wp-content/uploads/protocols/cp-urinary-bladder-resection-20-4020.pdf?download=true).
