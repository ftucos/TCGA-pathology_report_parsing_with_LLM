# TCGA-BLCA: local pathology report extraction

Rasterize the downloaded TCGA-BLCA PDFs, transcribe every page with **GLM-OCR on
Ollama**, and extract bladder carcinoma features with a second local Ollama model.
No Google Cloud or OpenAI API, API key, hosted inference, or runtime model download
is used. Models and Python packages must be staged before offline compute jobs.

The existing manifest and downloaded PDFs are inputs, unchanged by this rewrite.
They currently contain **413 reports for 412 cases**. Outputs are keyed by GDC file
UUID, not patient ID. Separate specimens and lesions remain separate; the pipeline
does not silently choose one report or stage per patient.

## Quick start

Use Python 3.11+ and a recent Ollama release with GLM-OCR support. Create the Python
environment on the HPC (do not copy a macOS virtual environment to Linux):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
blca inventory --verify-checksums
blca run --dry-run --limit 2
```

These last two commands need neither a GPU nor a running Ollama server. Runtime
settings live in the `[tool.blca.*]` sections of `pyproject.toml`; edit the models,
paths and generation settings there. No separate INI file is needed. Relative
paths resolve against the TOML file's directory. Run from the project root, or
select another TOML file with `--config /path/to/pyproject.toml` or
`TCGA_CONFIG_FILE`. Any alternative TOML must contain the full `[tool.blca.*]`
settings. `OLLAMA_HOST` still overrides the endpoint inside Slurm jobs.

In a GPU allocation, start your local Ollama service with `OLLAMA_NO_CLOUD=1` and
pre-downloaded weights. Then:

```bash
blca run --limit 2
# After inspection, process the full manifest. Completed matching work is reused.
blca run
blca export
```

`run` performs OCR and extraction. Alternatively, `ocr` and `extract` run the
stages separately; `extract` requires matching complete OCR checkpoints. All
three accept `--report-id UUID` (repeatable), `--num-shards N --shard-index I`,
`--limit N`, `--dry-run`, and `--force`. `--force` recomputes the requested stage;
`run --force` recomputes both. Errors give a nonzero exit status while other
reports continue. Retry the same command to resume.

## HPC / Slurm

The design follows the original `dossier_medical_reports_scraping` project: a local
Ollama server inside a Slurm allocation. The default is **one job, one GPU and one
Ollama server**, with two report workers sharing that server. Each worker sends
one model request at a time; GPU request concurrency does not request more GPUs.

1. Copy `scripts/hpc/environment.example.sh` to
   `scripts/hpc/environment.local.sh`. Set the absolute Python executable,
   `OLLAMA_MODELS` on shared storage and Ollama's binary path. Add your site's
   CUDA module if required. The reference project's paths are not assumed.
2. On a machine/allocation where internet downloads are permitted, run
   `bash scripts/hpc/prepare_models.sh`. It starts its own loopback Ollama server,
   pulls the two models from `[tool.blca]` in `pyproject.toml`, then stops that server. If compute nodes
   cannot download packages either, install the Linux environment from an institute
   mirror or staged wheels before submission. Keep the staged model directory
   available unchanged to compute jobs. Skip this step if the models are already cached.
3. Submit from the project root. Create `logs/` **before** submission, since Slurm
   opens log files before the script starts. Supply your institute's account,
   partition and GPU type options to `sbatch` where required.

```bash
mkdir -p logs
# First: one task and two reports; inspect OCR, JSON and GPU placement.
sbatch scripts/hpc/run.slurm --limit 2

# After the smoke test finishes: the full dataset on one GPU.
sbatch scripts/hpc/run.slurm
```

Do not pass a multi-task `--array` option when limiting total GPU usage to one.
Array support remains available for explicit multi-GPU runs: each array task
starts its own Ollama server and requests a separate GPU. On an older HPC copy
that still has `#SBATCH --array=0-3`, `sbatch --array=0-0 scripts/hpc/run.slurm`
overrides it to one task and processes the entire manifest.
Do not run overlapping submissions into the same output directory.
Per-report advisory locks prevent concurrent writes; verify that your shared
filesystem supports POSIX advisory locking.

For staged execution, export `BLCA_STAGE=ocr` or `BLCA_STAGE=extract` before
submitting (default: `run`). Submit extraction only after the OCR job completes.
The `run` workflow is simpler and resumes per report automatically.

After the processing job, export once on a CPU node. Replace `123456` with the
job ID printed by `sbatch`:

```bash
sbatch --dependency=afterany:123456 scripts/hpc/export.slurm
```

`afterany` permits an accounting export even when processing failed. Export includes
all valid results and writes a status entry for every manifest report; it exits
nonzero if any report is missing, failed or stale. After retries, rerun export.
The processing job does not append to a shared CSV.

The scripts bind Ollama to a dynamically selected loopback port, check startup
with a bounded timeout and stop only their own server on exit. They preserve
Slurm's `CUDA_VISIBLE_DEVICES`. No model is pulled by a compute job. `OLLAMA_NO_CLOUD=1`,
loopback-only requests, disabled HTTP proxy/redirect handling, and model preflight
checks keep inference local. `/api/show` is checked for remote models, OCR vision
support and advertised context limits. Inspect `ollama ps`/`nvidia-smi` during the
smoke test to confirm GPU use; CPU fallback is possible if your installation or
allocation is wrong and cannot be tested on this laptop.

## Models and resources

Default OCR is `glm-ocr:bf16`, with each visible PDF page rendered at 250 DPI and a
maximum side of 3500 pixels. The embedded PDF text layer is never used. GLM-OCR's
native Ollama `/api/generate` receives a PNG and `Text Recognition:`. This is
page-image recognition, not the optional GLM-OCR SDK layout-detector pipeline.
OCR requests explicitly set `repeat_penalty = 1.1` and `repeat_last_n = 256`;
both are configurable under `[tool.blca.ocr]` and included in cache fingerprints.
See the [official GLM-OCR Ollama guide](https://github.com/zai-org/GLM-OCR/blob/main/examples/ollama-deploy/README.md)
and [model listing](https://ollama.com/library/glm-ocr).

The configured extraction model is the user's cached `qwen3.8:27b`, a configurable
local model, with 65,536 context tokens and 8,192 output tokens. It has not been
validated for this cohort. Choose another local instruction model in `pyproject.toml`
if desired and check its context capacity. Extraction uses Ollama's native
[schema-constrained JSON output](https://docs.ollama.com/capabilities/structured-outputs),
temperature 0, seed 0 and thinking enabled (`think = true` under
`[tool.blca.extraction]`). The output token budget covers thinking and the final
answer. Raw responses retain thinking; only the final answer is parsed as JSON.
Changing thinking settings invalidates extraction caches.

Start with `workers = 2` in `[tool.blca.extraction]` and `OLLAMA_NUM_PARALLEL=2` in
`scripts/hpc/environment.local.sh`. If you already copied that local file, update
it explicitly; changing the example does not override an existing local setting.
These are concurrent report workers and per-model request slots, respectively,
not additional servers or GPUs. `workers` applies to the entire report pipeline,
including OCR. PDF rendering is serialized because PDFium is not thread-safe.

On an H200, benchmark 4 workers/slots after the two-worker smoke test if memory
permits. Model quantization, architecture and context size determine actual VRAM
needs; parameter count alone is insufficient to choose the maximum concurrency.
At the current 65,536-token extraction context, two parallel slots allocate
context capacity for two requests; four slots increase it again. See
[Ollama's concurrency guidance](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).
Check GPU placement and memory on the allocated node before increasing concurrency.
For `ollama ps`, set `OLLAMA_HOST` to the loopback address/port printed in the job
log, and run it on that same compute node; the login node's Ollama is unrelated.
The Slurm `--mem` directive controls CPU RAM, not VRAM. `OLLAMA_MAX_LOADED_MODELS=2`
allows GLM-OCR and the extraction model to remain resident when memory permits;
it does not request a second GPU.
Model digests and Ollama/package versions are recorded. Preserve model weights
and environment versions for reproducible reruns; generation is not guaranteed
bitwise reproducible across hardware or runtime versions.

Before sending an extraction request, the pipeline checks a deliberately
conservative UTF-8 byte budget for prompt, schema, report and output reserve.
Oversized reports fail visibly rather than being sliced. Increase `num_ctx` only
within the selected model's supported capacity, or use a larger-context model.
There is currently no automatic long-report chunking. Empty OCR output, including
an unrecognized blank page, is treated as a failure requiring inspection. Partial
or output-token-limited responses never become successful checkpoints.

## Bladder features and interpretation

The supplied [CAP biopsy/TURBT template](docs/Bladder.Bx.TURBT_4.3.0.0.REL_CAPCP.pdf)
(v4.3.0.0, June 2025) guides the core fields. It explicitly excludes cystectomy,
so margins, nodes and advanced stages are supplemented from CAP's
[cystectomy protocol v4.2.0.0](https://documents.cap.org/protocols/Bladder_4.2.0.0.REL_CAPCP.pdf).
This is an original research extraction schema, not a replacement CAP reporting form.
The schema and field mapping are in [docs/schema.md](docs/schema.md).

- Specimen/procedure, tumor site, histology and variant components, differentiation,
  binary grade, size, configuration, invasion extent, detrusor status, LVI, CIS,
  margins, nodes, reported pTNM, treatment effect and associated findings.
- Every non-null observation has an OCR quote and page number. Missing information
  is `null`, never an invented negative. Quotes must exist on their cited page.
- **Perivesical soft tissue/fat invasion supports pT3, not pT4.** Unspecified fat
  is insufficient, especially in TURBT. Muscularis propria supports pT2; muscularis
  mucosae does not. Prostate involvement needs the correct origin and invasion route.
- Reported pT, extent-derived pT and the selected value/basis are stored separately.
  Conflicts preserve the reported value and require review. Inferred biopsy/TURBT
  extent is a minimum supported category. No inferred pT2 subdivisions or pT3/pT4
  from TURBT. Historical stages and y/r/m modifiers are retained, not silently restaged.
- Explicit high/low grade is preferred. Poorly differentiated/undifferentiated
  urothelial carcinoma and clearly identified legacy WHO G3 can map to inferred
  high grade; legacy WHO G1 can map to inferred low grade. These project mappings
  always require review. G2, moderate differentiation and well differentiation
  without an identifiable legacy grade stay unresolved. Pure non-urothelial tumors
  retain their own differentiation grading. Mixed grade is not forced into a binary label.
- Independent prostate cancer grade, Gleason score, Grade Group, TNM and margins
  are excluded. Bladder cancer invading prostate is handled separately from
  prostatic urethral spread and from a primary prostate carcinoma.

The [prompt](src/blca/prompts/bladder.md) specifies attribution and uncertainty;
[normalization rules](src/blca/normalize.py) derive stage and grade deterministically
from validated observations. Evidence checks establish that a quote exists, not
that the model interpreted it correctly. Human evaluation of OCR and extraction
is still required, especially mixed-organ specimens and inferred values.

## Output, provenance and recovery

Outputs default to `processed/blca_local/`:

```text
reports/<GDC-file-UUID>/
  .lock
  ocr/<fingerprint>/
    pages/0001.json                  # successful per-page checkpoints
    raw/page-0001-attempt-1.json      # full local OCR response
    transcript.json                 # only written when all pages succeed
    transcript.txt                  # [PAGE n] markers for inspection
  extractions/<fingerprint>/
    raw/attempt-1.json               # model response, including invalid attempts
    raw/attempt-1.error.txt          # validation failure, when present
    result.json                     # archived complete result
  result.json                       # current complete result
  error.json                        # last processing failure, when present
exports/
  bladder_features.jsonl             # full findings, evidence and provenance
  bladder_features.csv               # one row per bladder specimen/lesion
  report_status.json                 # all manifest reports, including missing/failed
  summary.json                      # counts
```

Source checksums, model digests, generation settings, renderer version, prompt,
schema and normalization version control cache validity. Writes are atomic. A
failed recomputation removes the current result; an archived older result is not
silently exported. Repeating the same command resumes matching successful pages.
`--force` may overwrite raw attempts for the same fingerprint; save a separate
output directory when retaining multiple identical-setting trials is important.

Export runs without Ollama and verifies the saved provenance against the current
configuration, source PDF and transcript. It cannot discover that a remote running
Ollama service now assigns a different digest to a tag; run processing preflight
again when changing locally installed weights. JSON is authoritative; CSV is a
convenience projection and does not contain every nested feature or evidence quote.
An empty bladder extraction remains in JSON/status with a review flag and has no
specimen CSV row. Do not treat CSV row counts as report or patient counts.

## Validation and migration

```bash
python -m pytest -q
blca schema --output docs/bladder.schema.json
```

The CPU test suite exercises PDF rendering, real manifest handling, local-only
HTTP preflight, model response validation, stage/grade rules, checkpoint recovery,
configuration/model invalidation, locks and export accounting. Model responses
are mocked. See [the synthetic example](docs/example.extraction.json), which is
not a result from a TCGA patient. GPU throughput, actual model compatibility on
your installed Ollama build, Slurm execution and clinical extraction accuracy
have not been tested on the laptop.

The previous cloud/breast scripts and redundant INI configuration have been
removed from this branch. The manifest, downloaded reports and CAP reference
remain unchanged. No cloud SDK is a dependency of the new package.
