import argparse
import csv
import io
import json
import logging
import os
from pathlib import Path

import httpx

from .config import load_settings
from .normalize import normalize
from .pipeline import configuration_key, run_reports, source_info, valid_transcript
from .schema import BladderExtraction, validate_evidence
from .storage import atomic_json, atomic_text, load_manifest, read_json, shard

LOG = logging.getLogger(__name__)


def export_results(reports, settings):
    """One serial exporter after all shards finish; account for every manifest report."""
    results, status, rows = [], [], []
    key = configuration_key(settings)
    for report in reports:
        directory = settings.output_dir / "reports" / report.report_id
        try:
            source = source_info(report)
            if (directory / "error.json").exists():
                raise ValueError("Processing failed; see report error.json")
            result = read_json(directory / "result.json")
            if result["source"] != source or result["configuration_key"] != key:
                raise ValueError("Stale result: PDF or pipeline configuration changed")
            ocr_path = directory / "ocr" / result["ocr"]["fingerprint"] / "transcript.json"
            saved = read_json(ocr_path)
            transcript = valid_transcript(
                ocr_path, result["ocr"]["fingerprint"], len(saved["pages"])
            )
            if transcript is None or transcript["text_sha256"] != result["ocr"]["text_sha256"]:
                raise ValueError("Missing, modified or incomplete OCR transcript")
            parsed = BladderExtraction.model_validate(result["extraction"])
            warnings = validate_evidence(
                parsed, {p["page"]: p["text"] for p in transcript["pages"]}
            )
            result["evidence_warnings"] = warnings
            result["normalized"] = normalize(parsed, evidence_warnings=warnings)
            results.append(result)
            status.append(
                {"report_id": report.report_id, "case_id": report.case_id, "status": "complete"}
            )
            for specimen, derived in zip(
                parsed.bladder_specimens, result["normalized"]["specimens"]
            ):
                rows.append(
                    {
                        "report_id": report.report_id,
                        "case_id": report.case_id,
                        "filename": report.filename,
                        "specimen_label": specimen.specimen_label,
                        "procedure": specimen.procedure.value,
                        "histology": specimen.histologic_type.value,
                        "grade": derived["grade"],
                        "grade_basis": derived["grade_basis"],
                        "grade_raw": specimen.grade.raw.value,
                        "differentiation": specimen.grade.differentiation.value,
                        "pt": derived["pt"],
                        "pt_basis": derived["pt_basis"],
                        "pt_reported": specimen.stage.reported_pt.value,
                        "pt_inferred": derived["pt_inferred_from_extent"],
                        "pt_inferred_is_minimum": derived["pt_inferred_is_minimum"],
                        "stage_modifiers": json.dumps(specimen.stage.modifiers.value),
                        "pn_reported": specimen.stage.reported_pn.value,
                        "pm_reported": specimen.stage.reported_pm.value,
                        "deepest_extent": specimen.deepest_extent.value,
                        "muscularis_propria": specimen.muscularis_propria.value,
                        "lvi": specimen.lymphovascular_invasion.value,
                        "cis": specimen.associated_cis.value,
                        "nodes_examined": specimen.nodes.examined.value,
                        "nodes_positive": specimen.nodes.positive.value,
                        "review_required": result["normalized"]["review_required"],
                        "review_reasons": json.dumps(
                            result["normalized"]["review_reasons"] + derived["review_reasons"],
                            ensure_ascii=False,
                        ),
                    }
                )
        except (OSError, ValueError, KeyError, TypeError) as e:
            status.append(
                {
                    "report_id": report.report_id,
                    "case_id": report.case_id,
                    "status": "unavailable",
                    "reason": str(e),
                }
            )
    target = settings.output_dir / "exports"
    atomic_text(
        target / "bladder_features.jsonl",
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
    )
    columns = [
        "report_id",
        "case_id",
        "filename",
        "specimen_label",
        "procedure",
        "histology",
        "grade",
        "grade_basis",
        "grade_raw",
        "differentiation",
        "pt",
        "pt_basis",
        "pt_reported",
        "pt_inferred",
        "pt_inferred_is_minimum",
        "stage_modifiers",
        "pn_reported",
        "pm_reported",
        "deepest_extent",
        "muscularis_propria",
        "lvi",
        "cis",
        "nodes_examined",
        "nodes_positive",
        "review_required",
        "review_reasons",
    ]
    with io.StringIO(newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        atomic_text(target / "bladder_features.csv", stream.getvalue())
    atomic_json(target / "report_status.json", status)
    summary = {
        "manifest_reports": len(reports),
        "exported_reports": len(results),
        "unavailable_reports": len(reports) - len(results),
        "specimen_rows": len(rows),
    }
    atomic_json(target / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="TCGA-BLCA local PaddleOCR-VL and bladder extraction"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.getenv("TCGA_CONFIG_FILE", "pyproject.toml")),
        help="TOML file containing [tool.blca] settings (default: pyproject.toml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="Check manifest and local PDFs without a model")
    inv.add_argument("--verify-checksums", action="store_true")
    for name in ("run", "ocr", "extract"):
        p = sub.add_parser(name)
        p.add_argument("--num-shards", type=int, default=1)
        p.add_argument("--shard-index", type=int, default=0)
        p.add_argument("--limit", type=int, help="Limit selected reports for an HPC smoke test")
        p.add_argument("--report-id", action="append", help="Select a GDC file UUID; repeatable")
        p.add_argument(
            "--force", action="store_true", help="Recompute this stage, preserving run archives"
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="List selection; no model calls or output writes",
        )
    sub.add_parser(
        "export", help="Export all complete reports; nonzero exit if any are unavailable"
    )
    schema = sub.add_parser("schema", help="Emit the exact JSON schema supplied to Ollama")
    schema.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "schema":
            data = BladderExtraction.model_json_schema()
            if args.output:
                atomic_json(args.output, data)
            else:
                print(json.dumps(data, indent=2))
            return 0
        settings = load_settings(args.config)
        reports = load_manifest(settings.manifest, settings.reports_dir)
        if args.command == "inventory":
            errors = []
            for report in reports:
                try:
                    if args.verify_checksums:
                        source_info(report)
                    elif report.path.stat().st_size != report.expected_size:
                        raise ValueError("PDF size does not match manifest")
                except (OSError, ValueError) as e:
                    errors.append({"report_id": report.report_id, "error": str(e)})
            print(
                json.dumps(
                    {
                        "reports": len(reports),
                        "cases": len({r.case_id for r in reports}),
                        "errors": errors,
                    },
                    indent=2,
                )
            )
            return int(bool(errors))
        if args.command == "export":
            summary = export_results(reports, settings)
            print(json.dumps(summary, indent=2))
            return int(summary["unavailable_reports"] > 0)
        if args.report_id:
            unknown = set(args.report_id) - {r.report_id for r in reports}
            if unknown:
                raise ValueError(f"Unknown report IDs: {sorted(unknown)}")
            reports = [r for r in reports if r.report_id in args.report_id]
        reports = shard(reports, args.num_shards, args.shard_index)
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError("--limit must be positive")
            reports = reports[: args.limit]
        if args.dry_run:
            print(
                json.dumps(
                    {"selected": len(reports), "report_ids": [r.report_id for r in reports]},
                    indent=2,
                )
            )
            return 0
        summary = run_reports(reports, settings, args.command, args.force)
        print(json.dumps(summary, indent=2))
        return int(summary["failed"] > 0)
    except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError) as e:
        LOG.error("%s", e)
        return 2
