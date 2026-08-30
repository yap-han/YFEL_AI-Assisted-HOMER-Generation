from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .evidence import build_ai_extraction_task
from .ingestion import ingest_corpus
from .ontology import load_json, validate_ontology
from .quantitative import import_proposals, load_records
from .reporting import export_reports
from .study import validate_study_profile
from .validation import prepare_observation, validate_proposal


WORKFLOW_SCHEMA_VERSION = "1.0"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolved(base: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_workflow_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported workflow schema {payload.get('schema_version')!r}; "
            f"expected {WORKFLOW_SCHEMA_VERSION!r}"
        )
    if not isinstance(payload.get("ingestion"), dict) or not payload["ingestion"].get("manifest"):
        raise ValueError("Workflow config requires ingestion.manifest")
    return payload


def _safe_name(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return result[:70] or "document"


def _create_extraction_tasks(
    db_path: Path,
    document_ids: list[int],
    parameter_ids: list[str],
    output_dir: Path,
) -> list[str]:
    if not parameter_ids:
        return []
    parameters = [db.get_parameter_definition(db_path, parameter_id) for parameter_id in parameter_ids]
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for document_id in document_ids:
        document, pages = db.get_document(db_path, document_id)
        task = build_ai_extraction_task(document_id, document["title"], pages, parameters)
        output = output_dir / f"{document_id:04d}_{_safe_name(document['title'])}.json"
        output.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
        files.append(str(output))
    return files


def _observation_exists(db_path: Path, item: dict[str, Any]) -> int | None:
    source_id = str(item.get("source_id") or item.get("source_doi") or "unknown")
    with db.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT observation_id FROM evidence_observations
            WHERE parameter_id=? AND source_id=? AND locator=?
              AND COALESCE(raw_value_text, '')=COALESCE(?, '')
            ORDER BY observation_id LIMIT 1
            """,
            (item["parameter_id"], source_id, item.get("locator") or "unspecified locator", item.get("raw_value_text")),
        ).fetchone()
    return int(row[0]) if row else None


def _document_id_for_source(db_path: Path, source_id: str) -> int | None:
    with db.connect(db_path) as connection:
        row = connection.execute(
            "SELECT document_id FROM full_text_documents WHERE source_id=? ORDER BY document_id DESC LIMIT 1",
            (source_id,),
        ).fetchone()
    return int(row[0]) if row else None


def run_workflow(
    config_path: str | Path,
    *,
    database_override: str | Path | None = None,
    reset_database: bool = False,
) -> dict[str, Any]:
    """Run initialization -> ingestion -> extraction tasks -> validation -> reports."""

    config_file = Path(config_path).resolve()
    base = config_file.parent
    config = load_workflow_config(config_file)
    context = config.get("context") or {}
    ontology_path = _resolved(base, context.get("ontology", "ontology.json"))
    profile_path = _resolved(base, context.get("profile", "study_profile.json"))
    ontology = load_json(ontology_path)
    profile = load_json(profile_path)
    errors = [
        issue.__dict__
        for issue in [*validate_ontology(ontology), *validate_study_profile(profile)]
        if issue.level == "error"
    ]
    if errors:
        raise ValueError(f"Invalid ontology or profile: {errors}")

    db_path = Path(database_override).resolve() if database_override else _resolved(base, config.get("database", "../output/workflow.sqlite"))
    if reset_database and db_path.exists():
        db_path.unlink()
    db.initialize(db_path, ontology, profile)

    ingestion = config["ingestion"]
    ingestion_result = ingest_corpus(
        db_path,
        _resolved(base, ingestion["manifest"]),
        document_root=_resolved(base, ingestion.get("document_root")) if ingestion.get("document_root") else None,
        require_rights=bool(ingestion.get("require_rights", True)),
        fail_fast=bool(ingestion.get("fail_fast", False)),
    )
    document_ids = sorted(
        {
            int(row["document_id"])
            for row in ingestion_result["documents"]
            if row["status"] in {"ingested", "duplicate"}
        }
    )

    extraction = config.get("extraction") or {}
    extraction_output = _resolved(base, extraction.get("output_dir", "../output/workflow/extraction_tasks"))
    task_files = _create_extraction_tasks(
        db_path,
        document_ids,
        [str(value) for value in extraction.get("parameters", [])],
        extraction_output,
    )

    proposal_ids: list[int] = []
    proposal_file = _resolved(base, config.get("proposals"))
    if proposal_file:
        proposal_ids = import_proposals(db_path, load_records(proposal_file, "proposals"))

    observation_ids: list[int] = []
    skipped_observation_ids: list[int] = []
    observation_config = config.get("observations")
    if isinstance(observation_config, str):
        observation_config = {"file": observation_config}
    if observation_config:
        records = load_records(_resolved(base, observation_config["file"]), "observations")
        for record in records:
            item = dict(record)
            if not item.get("document_id") and item.get("document_source_id"):
                item["document_id"] = _document_id_for_source(db_path, str(item["document_source_id"]))
            existing = _observation_exists(db_path, item)
            if existing:
                skipped_observation_ids.append(existing)
                continue
            prepared = prepare_observation(
                db_path,
                item,
                fx_rate=observation_config.get("fx_rate"),
                target_cost_year=observation_config.get("target_cost_year"),
                annual_escalation_rate=observation_config.get("annual_escalation_rate"),
            )
            observation_ids.append(db.insert_evidence_observation(db_path, prepared))

    validation_config = config.get("validation") or {}
    validations: list[dict[str, Any]] = []
    if validation_config.get("enabled", bool(proposal_ids)):
        if not proposal_ids:
            with db.connect(db_path) as connection:
                proposal_ids = [int(row[0]) for row in connection.execute("SELECT proposal_id FROM proposed_parameters ORDER BY proposal_id")]
        validations = [
            validate_proposal(
                db_path,
                proposal_id,
                include_candidates=bool(validation_config.get("include_candidates", False)),
                min_independent_sources=int(validation_config.get("minimum_independent_sources", 2)),
            )
            for proposal_id in proposal_ids
        ]

    report_config = config.get("reports") or {}
    report_dir = _resolved(base, report_config.get("output_dir", "../output/workflow/reports"))
    report_files = export_reports(db_path, report_dir)
    result = {
        "status": "completed" if not ingestion_result["failed"] else "completed_with_ingestion_errors",
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "started_and_completed_at": _now(),
        "config": str(config_file),
        "database": str(db_path),
        "stages": {
            "initialization": {"status": "completed"},
            "ingestion": ingestion_result,
            "extraction_tasks": {"count": len(task_files), "files": task_files},
            "proposals": {"imported_or_updated": len(proposal_ids), "proposal_ids": proposal_ids},
            "observations": {
                "imported": len(observation_ids),
                "observation_ids": observation_ids,
                "skipped_existing_ids": skipped_observation_ids,
            },
            "validation": {
                "count": len(validations),
                "results": validations,
                "human_gate": "No validation is promoted automatically; reviewer approval remains required.",
            },
            "reports": {"count": len(report_files), "files": [str(path) for path in report_files]},
        },
    }
    summary_path = _resolved(base, config.get("run_summary", "../output/workflow/workflow_run.json"))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["run_summary"] = str(summary_path)
    return result
