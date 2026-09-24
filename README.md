# TCGA-BLCA: local pathology report extraction

Rasterize the downloaded TCGA-BLCA PDFs, transcribe each page with
**PaddlePaddle/PaddleOCR-VL-1.6 on local vLLM**, then extract structured bladder
features with **Qwen3.8 on local Ollama, thinking enabled**. The OCR integration
uses the recognition model with the `OCR:` prompt on page images. It does not
install the separate PaddleOCR document-layout pipeline or PP-DocLayoutV3.
The deployment follows the [official vLLM recipe](https://recipes.vllm.ai/PaddlePaddle/PaddleOCR-VL-1.6).

Inference runs locally without API keys or runtime weight downloads. The manifest
contains 413 reports for 412 cases. Outputs are keyed by report UUID, with one row per report. Multiple bladder
findings are summarized in comparable value columns with separate review comments.

## Install on the HPC

This wheel setup targets Linux x86_64 (`uname -m`) with Python 3.11 or 3.12.
Keep your existing
`venv-hpc`; install the updated project into it. Create a **separate vLLM venv**
so its PyTorch/CUDA dependencies do not change the application environment.
Load your institute's Python/CUDA modules first, using the same modules for jobs.
Do not copy a macOS virtual environment to the HPC.

```bash
# From the project root; assumes your existing environment is called venv-hpc.
venv-hpc/bin/python -m pip install -e '.[models]'
python3 -m venv .venv-vllm-cu128
.venv-vllm-cu128/bin/python -m pip install -r scripts/hpc/requirements-vllm-cu128.txt
.venv-vllm-cu128/bin/python -m pip check
venv-hpc/bin/python -m blca inventory --verify-checksums
venv-hpc/bin/python -m blca run --dry-run --limit 2
```

The GPU requirements pin vLLM 0.11.1 and PyTorch 2.9.0 with CUDA **12.8** wheels,
using the [vLLM 0.11.1 installation guidance](https://docs.vllm.ai/en/v0.11.1/getting_started/installation/gpu/)
and its matching PyTorch dependencies. Do not use the former unbounded
`vllm>=0.11.1` command: it allowed vLLM 0.30.0 and a PyTorch runtime that failed
on this cluster's driver. A fresh environment avoids leftover GPU libraries;
keep the existing `venv-hpc`, downloaded model weights and old GPU environment.

The failing node reports CUDA driver API version `12080`, meaning **12.8**.
The cluster's `CUDA/12.6.0` module is a toolkit installation, not the driver's
compatibility version. Prebuilt PyTorch wheels install their CUDA runtime
dependencies, so this installation does not require a CUDA 12.8 toolkit module.
Loading CUDA/12.6.0 cannot change an incompatible PyTorch wheel's runtime.
This pin targets the reported H200 node; validate on each GPU node type used.

Package installation and weight staging need download access, or an institute
mirror/staged packages. Record the working versions with `pip freeze` in each
environment after the GPU smoke test.

Edit `scripts/hpc/environment.local.sh` using
[environment.example.sh](scripts/hpc/environment.example.sh) as a guide. If the
local file already exists, **add the new Paddle variables to it**; it is not
replaced automatically. Use absolute shared-storage paths:

```bash
# Put your actual module load commands here, before the environment variables.
export BLCA_PYTHON="/absolute/project/venv-hpc/bin/python"
export BLCA_PADDLE_PYTHON="/absolute/project/.venv-vllm-cu128/bin/python"
export BLCA_PADDLE_MODEL_DIR="/shared/models/PaddleOCR-VL-1.6"
export OLLAMA_MODELS="/shared/models/ollama"
export OLLAMA_NUM_PARALLEL=6
export OLLAMA_MAX_LOADED_MODELS=1
# Add Ollama to PATH if your installation requires it.
```

`sbatch` sources this file and invokes the specified Python executables directly.
You do not need `source venv-hpc/bin/activate` in the batch script. Module loading
only happens if you put the required `module load` commands in the local file.
All paths must be accessible on the allocated compute node.

If migrating from the previous installation, update `BLCA_PADDLE_PYTHON` in the
existing local file: its explicit value overrides the batch script's default.
Inside a GPU allocation, check the new environment before inference:

```bash
source scripts/hpc/environment.local.sh
nvidia-smi
"$BLCA_PADDLE_PYTHON" - <<'PY'
from importlib.metadata import version
import torch
print("vLLM:", version("vllm"), "PyTorch:", torch.__version__, "CUDA:", torch.version.cuda)
torch.cuda.init()
print("GPU:", torch.cuda.get_device_name(0))
x = torch.ones(1, device="cuda")
print("GPU check:", (x + x).item())
PY
```

Run this check on the allocated compute node, not a GPU-less login node. A
successful tensor operation checks basic CUDA execution; the two-report smoke
test is still needed to validate vLLM kernels and model output.

Stage weights explicitly on a machine/allocation allowed to download:

```bash
bash scripts/hpc/prepare_models.sh
```

This downloads Paddle from Hugging Face and pulls only the extraction model via
Ollama. Paddle's configured `revision` (default `main`) is resolved to an immutable
commit. Files are stored under `BLCA_PADDLE_MODEL_DIR/snapshots/<commit>`; a manifest
records SHA-256 hashes. Compute jobs verify this local snapshot and run with
Hugging Face offline mode. They do not contact Hugging Face or pull Ollama models.
The model's custom code is enabled through `--trust-remote-code`, using the staged
snapshot. Re-running preparation may resolve a newer `main`; set a commit hash
in `pyproject.toml` to pin the revision across future preparations. Do not prepare
models while processing jobs are using the same model directory.

## Submit Slurm jobs

Submit from the project root and supply institute-specific account, partition and
GPU type options as needed. Create `logs/` before submission:

```bash
mkdir -p logs
# Smoke test: two reports, one GPU.
sbatch scripts/hpc/run.slurm --limit 2
# After inspecting transcripts and extraction results:
sbatch scripts/hpc/run.slurm
```

The default `run` job starts **Paddle/vLLM, Ollama and the Python pipeline on the
same allocated node**, sharing one GPU. Each server gets its own dynamically
selected loopback port; those ports override the TOML defaults inside the job.
A login node or separate job cannot reach these loopback endpoints. Both servers
are stopped on job exit, including vLLM worker processes.

For separate stages, each job starts the service it needs:

```bash
BLCA_STAGE=ocr sbatch scripts/hpc/run.slurm --limit 2
# After OCR completes, with matching configuration and staged Paddle weights:
BLCA_STAGE=extract sbatch scripts/hpc/run.slurm --limit 2
# CPU-only export after processing (replace 123456 with the processing job ID):
sbatch --dependency=afterany:123456 scripts/hpc/export.slurm
```

Extraction-only verifies Paddle's local identity and reuses complete OCR
checkpoints; it does not start the Paddle server. Export needs neither server.
`afterany` permits an accounting export even after failures. Export returns
nonzero if any manifest report is missing, failed or stale; this is expected
after a two-report smoke test. Rerun export after completing the dataset.

Do not use a multi-task array if total GPU usage must stay at one. Each array task
requests its own GPU and starts its own servers. Avoid overlapping submissions
into the same output directory. Per-report POSIX advisory locks prevent concurrent
writes; the shared filesystem must support them.

## Settings, memory and troubleshooting

Runtime settings live in `[tool.blca.*]` in `pyproject.toml`. Relative paths resolve
against that file. `TCGA_CONFIG_FILE` selects an alternative complete TOML file.
`BLCA_PADDLE_HOST`, `BLCA_PADDLE_MODEL_DIR` and `OLLAMA_HOST` override their respective
locations. Changing the example shell file does not override an existing local file.

- OCR renders the visible page at 250 DPI, with a 3500-pixel maximum side; embedded
  PDF text is ignored. Paddle receives a PNG and the configurable `OCR:` prompt.
  It uses 16,384 context tokens, up to 8,192 output tokens, temperature 0 and
  `repeat_penalty = 1.1` (sent as vLLM's `repetition_penalty`). GLM-specific stop
  tokens and Ollama OCR options have been removed.
- Qwen uses Ollama's native schema-constrained `/api/chat`, `think = true`, 131,072
  context tokens and 98,304 output tokens. Its output budget covers thinking and
  the final answer. Raw responses retain thinking; only final content is parsed.
- `gpu_memory_utilization = 0.15` reserves about 21 GiB for Paddle on a 140 GiB H200.
  It is a starting setting for that GPU, not a universal setting. vLLM starts first;
  Qwen uses the remaining memory. `max_num_seqs = 2` limits Paddle concurrency.
  `workers = 6` controls report workers; `OLLAMA_NUM_PARALLEL=6` permits up to
  six concurrent Qwen requests. Slurm `--mem=64G` is CPU RAM, not VRAM.

The request timeout is 3,600 seconds to accommodate longer non-streaming thinking
responses. Six requests at 131,072 context tokens increase GPU context-cache
memory substantially; check that Qwen stays fully on GPU on the allocated node.
The model preflight checks the requested context against its advertised capacity.
If an existing `environment.local.sh` still sets another `OLLAMA_NUM_PARALLEL`,
change it to `6`; that explicit setting overrides the default in `common.sh`.
Changing context/output budgets invalidates extraction caches, so extraction is
recomputed with the new settings while matching Paddle OCR checkpoints are reused.

Inspect `logs/paddle-*.log`, `logs/ollama-*.log` and report `error.json` on failures.
HTTP errors retain the server's response body. If vLLM cannot allocate its context
cache, raise its GPU fraction or reduce OCR context/concurrency; if Qwen cannot
fit, reduce extraction context/concurrency or run OCR and extraction as separate
stages. Check `nvidia-smi` and `ollama ps` on the allocated node, using the job's
printed `OLLAMA_HOST`. Actual model quality, GPU fit and throughput need a smoke
test on the cluster; switching models does not guarantee repetition-free OCR.

`run`, `ocr` and `extract` accept `--report-id UUID` (repeatable), `--limit N`,
`--num-shards N --shard-index I`, `--dry-run` and `--force`. Retrying resumes matching
successful pages. `--force` recomputes the selected stage. New Paddle settings and
weight identities invalidate old GLM checkpoints automatically.

Empty or truncated OCR never becomes a successful checkpoint and fails immediately
without identical deterministic retries. Transport/server failures still use the
configured retry count. Extraction sends the complete prompt and report to Ollama,
which applies the model's tokenizer and chat template. There is no byte-count
estimate that rejects requests before inference. The output budget is a maximum,
not a measurement of the input or a requirement to generate that many tokens.
Requests set `truncate = false` and `shift = false` to disable automatic input
truncation and context shifting; use an Ollama version supporting those chat flags
(they are present in v0.13.0 and current releases). Actual context-limit errors
and incomplete generation still fail visibly. See the
[Ollama request definitions](https://github.com/ollama/ollama/blob/v0.13.0/api/types.go).

The job log records Ollama's `prompt_eval_count`, `eval_count` and `done_reason`
alongside the configured context and output limits; full responses remain in the
raw extraction files. Changing these request flags invalidates extraction caches;
matching Paddle OCR checkpoints remain reusable. There is no automatic long-report
chunking. Inspect transcripts before processing the full dataset.

## Comparison values and review comments

The LLM returns seven flat value/comment pairs, without nested CAP fields:

| Value column | Examples | Review column |
| --- | --- | --- |
| `pT` | `Ta`, `T1`, `T2b`, `T3` | `pT_comment` |
| `pN` | `NX`, `N0`, `N1`, `N2`, `N3` | `pN_comment` |
| `pM` | `MX`, `M1` | `pM_comment` |
| `grade` | `High`, `Low` | `grade_comment` |
| `margins` | `R0`, `R1`, `R2`, `RX` | `margins_comment` |
| `histology` | `Urothelial carcinoma, papillary`, `Urothelial carcinoma, nested` | `histology_comment` |
| `vascular_invasion` | `Present`, `Absent`, `Indeterminate` | `vascular_invasion_comment` |

Comments retain reported versus inferred basis, supporting wording, uncertainty,
original prefixes/grades, and specimen/site details for comparison with TCGA.
The prompt allows conservative stage inference from clear invasion descriptions.
Unknown nodes/distant spread become NX/MX, never an assumed N0/M0. MX is a project
unknown placeholder, not a formal modern pM category. Missing margins become RX.
R0/R1 summarize negative/involved margins; they do not establish overall residual
tumor status. Missing vascular invasion is null, not Absent.

Grade uses High/Low when supported; G2/moderate differentiation alone stays null
with its original wording in the comment. Permitted harmonizations from legacy
urothelial grading/differentiation are explicitly labeled as inference. Histology
uses standardized short labels while allowing rare types and mixed diagnoses.
Independent prostate-cancer findings are excluded.

Validation remains permissive: no mandatory evidence quotes, matching pages or
CAP completeness checks. Exact aliases are normalized; unexpected categorical
values move into comments for review instead of causing retries. Invalid JSON,
non-object responses and incomplete generation still trigger errors/retries.

See [the schema documentation](docs/schema.md),
[extraction prompt](src/blca/prompts/bladder.md), and
[synthetic example](docs/example.extraction.json) for definitions and inference
rules. Original OCR and raw responses remain available for review.

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
  bladder_features.jsonl             # comparison values, comments and provenance
  bladder_features.csv               # one row per report
  report_status.json                 # all manifest reports, including missing/failed
  summary.json                      # counts
```

The comparison schema is version 3.0 (recorded in result metadata). Updating to
this version regenerates extraction results because the prompt/schema changed;
matching OCR checkpoints remain reusable. After syncing the code to the HPC,
finish or stop the old job, then submit without `--force`:

```bash
BLCA_STAGE=run sbatch scripts/hpc/run.slurm
```

Previous CAP-style and four-field extraction results remain archived. They are
not silently converted or exported as the new comparison schema. Rerun export after processing.

Source checksums, model digests, generation settings, renderer version, prompt,
schema and extraction schema version control cache validity. Writes are atomic. A
failed recomputation removes the current result; an archived older result is not
silently exported. Repeating the same command resumes matching successful pages.
`--force` may overwrite raw attempts for the same fingerprint; save a separate
output directory when retaining multiple identical-setting trials is important.

Export runs without model servers and verifies the saved provenance against the current
configuration, source PDF and transcript. It does not rehash staged model weights or query running services; run processing
preflight again when changing installed weights. JSON is authoritative; CSV is a
projection of the seven value/comment pairs with report/case identifiers. Null
values become empty CSV cells. A report with no findings still has one CSV row. There are 413 reports
for 412 cases; report rows are not unique patient rows.

## Validation and migration

```bash
python -m pytest -q
blca schema --output docs/bladder.schema.json
```

The CPU test suite exercises PDF rendering, real manifest handling, local-only
HTTP preflight, permissive categorical parsing and review comments, checkpoint recovery,
configuration/model invalidation, locks and export accounting. Model responses
are mocked. See [the synthetic example](docs/example.extraction.json), which is
not a result from a TCGA patient. GPU throughput, actual model compatibility on
your installed vLLM/Ollama builds, Slurm execution and clinical extraction accuracy
have not been tested on the laptop.

The previous cloud/breast scripts and redundant INI configuration have been
removed from this branch. The manifest, downloaded reports and CAP reference
remain unchanged. No cloud SDK is a dependency of the new package.
