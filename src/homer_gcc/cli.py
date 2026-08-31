from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .advanced_pipeline import PipelineStopped, run_advanced_pipeline
from .evidence import build_ai_extraction_task, extract_numeric_candidates, read_document
from .ingestion import ingest_corpus
from .llm import run_llm_extraction_batch
from .ontology import load_json, ontology_summary, validate_ontology
from .quantitative import export_scenario, import_proposals, load_records
from .reporting import export_reports
from .retrieval import (
    DATABASE_NAMES,
    build_query_plan,
    deduplicate,
    fetch_openalex_citation_chain,
    safe_retrieve,
    score_candidate,
)
from .study import profile_summary, validate_study_profile
from .validation import prepare_observation, validate_proposal
from .workflow import run_workflow


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ONTOLOGY = PROJECT_ROOT / "config" / "ontology.json"
DEFAULT_PROFILE = PROJECT_ROOT / "config" / "study_profile.json"
DEFAULT_POLICY = PROJECT_ROOT / "config" / "source_policy.json"
DEFAULT_SOURCES = PROJECT_ROOT / "data" / "seed_sources.json"
DEFAULT_FIXTURE = PROJECT_ROOT / "data" / "offline_candidates.json"
DEFAULT_CITATION_FIXTURE = PROJECT_ROOT / "data" / "offline_citation_chain.json"
DEFAULT_PROPOSALS = PROJECT_ROOT / "data" / "proposed_parameters_demo.csv"
DEFAULT_OBSERVATIONS = PROJECT_ROOT / "data" / "quantitative_demo_observations.json"
DEFAULT_FULLTEXT = PROJECT_ROOT / "data" / "demo_fulltext.txt"
DEFAULT_MODEL_OUTPUTS = PROJECT_ROOT / "data" / "model_outputs_demo.json"


def _csv_values(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_context(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return load_json(args.ontology), load_json(args.profile), load_json(args.policy)


def command_validate(args: argparse.Namespace) -> int:
    ontology = load_json(args.ontology)
    profile = load_json(args.profile)
    ontology_issues = validate_ontology(ontology)
    profile_issues = validate_study_profile(profile)
    payload = {
        "ontology": ontology_summary(ontology),
        "study_profile": profile_summary(profile),
        "ontology_issues": [issue.__dict__ for issue in ontology_issues],
        "profile_issues": [issue.__dict__ for issue in profile_issues],
    }
    print(json.dumps(payload, indent=2))
    return 1 if any(issue.level == "error" for issue in [*ontology_issues, *profile_issues]) else 0


def command_init(args: argparse.Namespace) -> int:
    ontology = load_json(args.ontology)
    profile = load_json(args.profile)
    errors = [
        issue
        for issue in [*validate_ontology(ontology), *validate_study_profile(profile)]
        if issue.level == "error"
    ]
    if errors:
        print(json.dumps({"status": "failed", "issues": [issue.__dict__ for issue in errors]}, indent=2))
        return 1
    db.initialize(args.db, ontology, profile)
    print(
        json.dumps(
            {
                "status": "initialized",
                "db": str(Path(args.db)),
                "ontology": ontology_summary(ontology),
                "study_profile": profile_summary(profile),
            },
            indent=2,
        )
    )
    return 0


def command_seed(args: argparse.Namespace) -> int:
    payload = load_json(args.sources)
    inserted, skipped = db.insert_sources(args.db, payload.get("sources", []))
    print(json.dumps({"status": "seeded", "inserted_or_updated": inserted, "skipped": skipped}, indent=2))
    return 0


def command_plan(args: argparse.Namespace) -> int:
    ontology = load_json(args.ontology)
    profile = load_json(args.profile)
    plan = build_query_plan(ontology, args.family, args.country, profile)
    print(json.dumps(plan.__dict__, indent=2, ensure_ascii=False))
    return 0


def _retrieve_scope(
    db_path: str | Path,
    ontology: dict[str, Any],
    profile: dict[str, Any],
    policy: dict[str, Any],
    providers: list[str],
    family_id: str,
    location: str | None,
    limit: int,
    fixture: str | Path,
) -> dict[str, Any]:
    plan = build_query_plan(ontology, family_id, location, profile)
    all_candidates: list[dict[str, Any]] = []
    query_errors = 0
    raw_occurrences = 0
    for provider in providers:
        for query in plan.queries:
            fixture_path = fixture if provider == "offline" else None
            raw, error = safe_retrieve(provider, query, limit, fixture_path)
            if error:
                query_errors += 1
            raw_occurrences += len(raw)
            for item in raw:
                item["query_text"] = query
                scored = score_candidate(
                    item,
                    policy,
                    plan.retrieval_terms,
                    location,
                    plan.domain_terms,
                    profile.get("locations", []),
                )
                all_candidates.append(scored)
            retrieved_at = raw[0].get("retrieved_at") if raw else _now()
            db.log_query(
                db_path,
                provider,
                family_id,
                location,
                query,
                len(raw),
                "error" if error else "ok",
                error,
                database=DATABASE_NAMES.get(provider, provider),
                retrieval_date=retrieved_at,
            )

    candidates = deduplicate(all_candidates)
    stored = db.insert_candidates(
        db_path,
        providers[0] if len(providers) == 1 else "multi_provider",
        family_id,
        location,
        candidates,
    )
    return {
        "family": family_id,
        "location": location or profile.get("region_search_term"),
        "providers": providers,
        "queries": len(plan.queries) * len(providers),
        "query_errors": query_errors,
        "raw_occurrences": raw_occurrences,
        "unique_candidates": stored,
        "shortlisted": sum(row.get("decision") == "shortlist" for row in candidates),
        "topical_exclusions": sum(row.get("decision") == "exclude" for row in candidates),
        "rejected": sum(row.get("decision") == "reject" for row in candidates),
        "candidates": candidates,
    }


def command_retrieve(args: argparse.Namespace) -> int:
    ontology, profile, policy = _load_context(args)
    db.initialize(args.db, ontology, profile)
    result = _retrieve_scope(
        args.db,
        ontology,
        profile,
        policy,
        [args.provider],
        args.family,
        args.country,
        args.limit,
        args.fixture,
    )
    top = [
        {
            "title": row["title"],
            "doi": row.get("doi"),
            "source_quality": row.get("source_quality_score"),
            "topical_relevance": row.get("topical_relevance_score"),
            "ranking_score": row.get("ranking_score"),
            "decision": row.get("decision"),
            "databases": row.get("database_names", []),
        }
        for row in result.pop("candidates")[:5]
    ]
    result["top_candidates"] = top
    result["status"] = "completed" if result["query_errors"] == 0 else "completed_with_errors"
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["query_errors"] == 0 or result["unique_candidates"] else 1


def command_batch_retrieve(args: argparse.Namespace) -> int:
    ontology, profile, policy = _load_context(args)
    db.initialize(args.db, ontology, profile)
    providers = _csv_values(args.providers)
    invalid = sorted(set(providers) - {"offline", "crossref", "openalex"})
    if not providers or invalid:
        raise ValueError(f"Invalid providers: {', '.join(invalid) if invalid else 'none'}")
    all_families = [family["id"] for family in ontology.get("families", [])]
    families = _csv_values(args.families) or all_families
    unknown_families = sorted(set(families) - set(all_families))
    if unknown_families:
        raise ValueError(f"Unknown families: {', '.join(unknown_families)}")
    locations: list[str | None] = _csv_values(args.locations) or list(profile.get("locations", []))
    if args.include_region:
        locations.append(None)

    scope_results = []
    for location in locations:
        for family_id in families:
            result = _retrieve_scope(
                args.db,
                ontology,
                profile,
                policy,
                providers,
                family_id,
                location,
                args.limit,
                args.fixture,
            )
            result.pop("candidates")
            scope_results.append(result)

    summary = {
        "status": "batch_complete",
        "profile": profile.get("id"),
        "providers": providers,
        "families": families,
        "locations": [location or profile.get("region_search_term") for location in locations],
        "scope_count": len(scope_results),
        "query_count": sum(row["queries"] for row in scope_results),
        "query_errors": sum(row["query_errors"] for row in scope_results),
        "raw_occurrences": sum(row["raw_occurrences"] for row in scope_results),
        "scope_unique_candidate_total": sum(row["unique_candidates"] for row in scope_results),
        "shortlisted_total": sum(row["shortlisted"] for row in scope_results),
        "topical_exclusion_total": sum(row["topical_exclusions"] for row in scope_results),
    }
    if not args.summary_only:
        summary["scopes"] = scope_results
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["query_errors"] == 0 or summary["scope_unique_candidate_total"] else 1


def command_screen(args: argparse.Namespace) -> int:
    reasons = _csv_values(args.reasons)
    db.record_screening(
        args.db,
        args.candidate_id,
        args.stage,
        args.decision,
        args.reviewer,
        reasons,
        args.notes,
    )
    print(
        json.dumps(
            {
                "status": "screening_recorded",
                "candidate_id": args.candidate_id,
                "stage": args.stage,
                "decision": args.decision,
                "reviewer": args.reviewer,
            },
            indent=2,
        )
    )
    return 0


def command_citation_chain(args: argparse.Namespace) -> int:
    ontology, profile, policy = _load_context(args)
    db.initialize(args.db, ontology, profile)
    plan = build_query_plan(ontology, args.family, args.country, profile)
    if args.provider == "offline":
        payload = load_json(args.fixture)
        seed = payload["seed"]
        direction = args.direction or payload.get("direction", "cited_by")
        raw = payload.get("candidates", [])[: args.limit]
    else:
        direction = args.direction or "cited_by"
        try:
            seed, raw = fetch_openalex_citation_chain(args.seed_doi, direction, args.limit)
        except Exception as exc:
            print(json.dumps({"status": "failed", "message": str(exc)}, indent=2))
            return 1

    retrieved_at = _now()
    database_name = "Offline citation-chain fixture" if args.provider == "offline" else "OpenAlex citation graph"
    scored = []
    for item in raw:
        item["provider"] = args.provider
        item["database_name"] = database_name
        item["retrieved_at"] = retrieved_at
        item["query_text"] = f"citation:{direction}:{seed.get('doi')}"
        scored.append(
            score_candidate(
                item,
                policy,
                plan.retrieval_terms,
                args.country,
                plan.domain_terms,
                profile.get("locations", []),
            )
        )
    candidates = deduplicate(scored)
    db.insert_candidates(args.db, args.provider, args.family, args.country, candidates)
    links = db.insert_citation_links(
        args.db,
        args.family,
        args.country,
        seed.get("doi") or args.seed_doi,
        seed.get("title"),
        direction,
        args.provider,
        database_name,
        candidates,
    )
    if not getattr(args, "quiet", False):
        print(
            json.dumps(
                {
                    "status": "citation_chain_complete",
                    "seed_doi": seed.get("doi") or args.seed_doi,
                    "direction": direction,
                    "records": links,
                    "shortlisted": sum(row.get("decision") == "shortlist" for row in candidates),
                },
                indent=2,
            )
        )
    return 0


def command_report(args: argparse.Namespace) -> int:
    files = export_reports(args.db, args.output)
    print(json.dumps({"status": "exported", "files": [str(path) for path in files]}, indent=2))
    return 0


def _initialize_quantitative(args: argparse.Namespace) -> None:
    ontology = load_json(args.ontology)
    profile = load_json(args.profile)
    db.initialize(args.db, ontology, profile)


def command_import_proposals(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    records = load_records(args.file, "proposals")
    ids = import_proposals(args.db, records)
    print(json.dumps({"status": "proposals_imported", "count": len(ids), "proposal_ids": ids}, indent=2))
    return 0


def command_propose_parameter(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    proposal_id = db.upsert_proposed_parameter(
        args.db,
        {
            "parameter_id": args.parameter_id,
            "scenario_id": args.scenario_id,
            "context_location": args.location,
            "technology": args.technology,
            "proposed_value": args.value,
            "proposed_unit": args.unit,
            "system_boundary": args.system_boundary,
            "source_basis": args.source_basis,
            "criticality": args.criticality,
            "entered_by": args.entered_by,
            "notes": args.notes,
        },
    )
    print(json.dumps({"status": "proposal_saved", "proposal_id": proposal_id}, indent=2))
    return 0


def command_ingest_fulltext(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    document = read_document(args.file)
    document_id = db.insert_full_text_document(
        args.db,
        document,
        source_id=args.source_id,
        title=args.title,
        doi=args.doi,
        metadata={"rights_note": args.rights_note} if args.rights_note else {},
    )
    print(
        json.dumps(
            {
                "status": "document_ingested",
                "document_id": document_id,
                "sha256": document["sha256"],
                "page_count": len(document["pages"]),
                "ingestion_method": document["ingestion_method"],
            },
            indent=2,
        )
    )
    return 0


def command_ingest_corpus(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    result = ingest_corpus(
        args.db,
        args.manifest,
        document_root=args.document_root,
        require_rights=args.require_rights,
        fail_fast=args.fail_fast,
    )
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        result["report"] = str(report)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if args.allow_partial or not result["failed"] else 1


def command_run_workflow(args: argparse.Namespace) -> int:
    result = run_workflow(
        args.config,
        database_override=args.db,
        reset_database=args.reset,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if args.allow_partial or result["status"] == "completed" else 1


def command_run_advanced_pipeline(args: argparse.Namespace) -> int:
    try:
        result = run_advanced_pipeline(args.config, reset=args.reset)
    except PipelineStopped as exc:
        print(
            json.dumps(
                {
                    "status": exc.status,
                    "message": str(exc),
                    "report": str(exc.report),
                    "fixture_fallback_used": False,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 3
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def command_make_extraction_task(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    document, pages = db.get_document(args.db, args.document_id)
    parameters = [
        db.get_parameter_definition(args.db, parameter_id)
        for parameter_id in _csv_values(args.parameters)
    ]
    if not parameters:
        raise ValueError("At least one parameter ID is required")
    task = build_ai_extraction_task(args.document_id, document["title"], pages, parameters)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": "extraction_task_created", "output": str(output)}, indent=2))
    return 0


def command_llm_extract_batch(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if isinstance(config.get("llm"), dict):
        config = config["llm"]
    if args.document_ids:
        document_ids = [int(value) for value in _csv_values(args.document_ids)]
    else:
        with db.connect(args.db) as connection:
            document_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT document_id FROM full_text_documents ORDER BY document_id"
                )
            ]
    if not document_ids:
        raise ValueError("No ingested documents are available for LLM extraction")
    parameters = _csv_values(args.parameters)
    if not parameters:
        raise ValueError("At least one parameter ID is required")
    result = run_llm_extraction_batch(
        args.db,
        document_ids,
        parameters,
        config,
        args.output,
        config_base=config_path.parent,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["failed_tasks"] == 0 or args.allow_partial else 1


def command_extract_observations(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    document, pages = db.get_document(args.db, args.document_id)
    definition = db.get_parameter_definition(args.db, args.parameter_id)
    candidates = extract_numeric_candidates([page["page_text"] for page in pages], definition)
    observation_ids = []
    for candidate in candidates:
        prepared = prepare_observation(
            args.db,
            {
                **candidate,
                "document_id": args.document_id,
                "source_id": args.source_id or document.get("source_id") or f"document:{args.document_id}",
                "source_title": document["title"],
                "source_doi": document.get("doi"),
                "technology": args.technology,
                "system_boundary": args.system_boundary,
                "context_location": args.location,
                "applicability_score": args.applicability,
                "verification_status": "candidate",
            },
            fx_rate=args.fx_rate,
            target_cost_year=args.target_cost_year,
            annual_escalation_rate=args.escalation_rate,
        )
        observation_ids.append(db.insert_evidence_observation(args.db, prepared))
    print(
        json.dumps(
            {
                "status": "candidate_observations_extracted",
                "count": len(observation_ids),
                "observation_ids": observation_ids,
                "warning": "Deterministic extraction is triage only; every candidate requires human review.",
            },
            indent=2,
        )
    )
    return 0


def command_import_observations(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    records = load_records(args.file, "observations")
    observation_ids = []
    for record in records:
        prepared = prepare_observation(
            args.db,
            record,
            fx_rate=args.fx_rate,
            target_cost_year=args.target_cost_year,
            annual_escalation_rate=args.escalation_rate,
        )
        observation_ids.append(db.insert_evidence_observation(args.db, prepared))
    print(
        json.dumps(
            {"status": "observations_imported", "count": len(observation_ids), "observation_ids": observation_ids},
            indent=2,
        )
    )
    return 0


def command_review_observation(args: argparse.Namespace) -> int:
    db.review_evidence_observation(args.db, args.observation_id, args.decision, args.reviewer, args.notes)
    print(
        json.dumps(
            {"status": "observation_reviewed", "observation_id": args.observation_id, "decision": args.decision},
            indent=2,
        )
    )
    return 0


def command_validate_parameter(args: argparse.Namespace) -> int:
    result = validate_proposal(
        args.db,
        args.proposal_id,
        include_candidates=args.include_candidates,
        min_independent_sources=args.min_sources,
    )
    print(json.dumps({"status": "validation_complete", **result}, indent=2))
    return 0


def command_review_validation(args: argparse.Namespace) -> int:
    scenario_parameter_id = db.review_parameter_validation(
        args.db,
        args.validation_id,
        args.decision,
        args.reviewer,
        low=args.low,
        base=args.base,
        high=args.high,
        notes=args.notes,
    )
    print(
        json.dumps(
            {
                "status": "validation_reviewed",
                "validation_id": args.validation_id,
                "decision": args.decision,
                "scenario_parameter_id": scenario_parameter_id,
            },
            indent=2,
        )
    )
    return 0


def command_export_scenario(args: argparse.Namespace) -> int:
    output = export_scenario(args.db, args.scenario_id, args.output, context_location=args.location)
    print(json.dumps({"status": "scenario_exported", "output": str(output)}, indent=2))
    return 0


def command_create_model_run(args: argparse.Namespace) -> int:
    result = db.create_model_run(
        args.db,
        args.scenario_id,
        args.model_name,
        context_location=args.location,
        model_version=args.model_version,
        notes=args.notes,
        run_id=args.run_id,
    )
    print(json.dumps(result, indent=2))
    return 0


def command_import_model_results(args: argparse.Namespace) -> int:
    outputs = load_records(args.file, "outputs")
    count = db.insert_model_outputs(args.db, args.run_id, outputs, complete=not args.partial)
    print(json.dumps({"status": "model_results_imported", "run_id": args.run_id, "count": count}, indent=2))
    return 0


def command_quant_demo(args: argparse.Namespace) -> int:
    _initialize_quantitative(args)
    proposal_ids = import_proposals(args.db, load_records(args.proposals, "proposals"))

    document = read_document(args.fulltext)
    document_id = db.insert_full_text_document(
        args.db,
        document,
        source_id="synthetic_demo_document",
        title="Synthetic quantitative extraction fixture",
        metadata={"warning": "Demonstration values only; not research evidence"},
    )
    definition = db.get_parameter_definition(args.db, "pv.capital_cost")
    regex_candidates = extract_numeric_candidates(document["pages"], definition)
    candidate_ids = []
    for candidate in regex_candidates:
        prepared = prepare_observation(
            args.db,
            {
                **candidate,
                "document_id": document_id,
                "source_id": "synthetic_demo_document",
                "source_title": "Synthetic quantitative extraction fixture",
                "context_location": args.location,
                "technology": "PV",
                "applicability_score": 0.5,
                "verification_status": "candidate",
            },
        )
        candidate_ids.append(db.insert_evidence_observation(args.db, prepared))

    observation_ids = []
    for record in load_records(args.observations, "observations"):
        prepared = prepare_observation(args.db, record)
        observation_ids.append(db.insert_evidence_observation(args.db, prepared))

    validation_results = []
    scenario_parameter_ids = []
    for proposal_id in proposal_ids:
        result = validate_proposal(args.db, proposal_id)
        validation_results.append(result)
        if result["decision"] == "supported":
            scenario_parameter_ids.append(
                db.review_parameter_validation(
                    args.db,
                    result["validation_id"],
                    "approve",
                    "synthetic_demo_reviewer",
                    notes="Scripted demonstration of the mandatory human approval transition",
                )
            )

    output_dir = Path(args.output)
    scenario_csv = export_scenario(args.db, args.scenario_id, output_dir / "scenario_parameters.csv", context_location=args.location)
    scenario_json = export_scenario(args.db, args.scenario_id, output_dir / "scenario_parameters.json", context_location=args.location)
    run = db.create_model_run(
        args.db,
        args.scenario_id,
        args.model_name,
        context_location=args.location,
        model_version=args.model_version,
        run_id=args.run_id,
        notes="Synthetic registry demonstration; HOMER was not executed by this command",
    )
    output_count = db.insert_model_outputs(
        args.db,
        run["run_id"],
        load_records(args.model_outputs, "outputs"),
    )
    reports = export_reports(args.db, output_dir)
    print(
        json.dumps(
            {
                "status": "quantitative_demo_complete",
                "warning": "All numerical evidence and model results in this demo are synthetic fixtures.",
                "proposals": len(proposal_ids),
                "full_text_documents": 1,
                "candidate_extractions": len(candidate_ids),
                "approved_evidence_observations": len(observation_ids),
                "validations": [
                    {"validation_id": row["validation_id"], "parameter_id": row["parameter_id"], "decision": row["decision"]}
                    for row in validation_results
                ],
                "approved_scenario_parameters": len(scenario_parameter_ids),
                "scenario_exports": [str(scenario_csv), str(scenario_json)],
                "model_run": run,
                "model_outputs": output_count,
                "reports": [str(path) for path in reports],
            },
            indent=2,
        )
    )
    return 0


def command_demo(args: argparse.Namespace) -> int:
    ontology, profile, policy = _load_context(args)
    errors = [
        issue
        for issue in [*validate_ontology(ontology), *validate_study_profile(profile)]
        if issue.level == "error"
    ]
    if errors:
        print(json.dumps({"status": "failed", "issues": [issue.__dict__ for issue in errors]}, indent=2))
        return 1
    db.initialize(args.db, ontology, profile)
    sources = load_json(args.sources).get("sources", [])
    db.insert_sources(args.db, sources)
    scope_results = []
    for family_id in ["aquaculture_load", "site_environment", "renewable_resources", "conventional_generation"]:
        result = _retrieve_scope(
            args.db,
            ontology,
            profile,
            policy,
            ["offline"],
            family_id,
            args.country,
            args.limit,
            args.fixture,
        )
        result.pop("candidates")
        scope_results.append(result)

    citation_args = argparse.Namespace(
        db=args.db,
        ontology=args.ontology,
        profile=args.profile,
        policy=args.policy,
        provider="offline",
        fixture=args.citation_fixture,
        family="renewable_resources",
        country=args.country,
        seed_doi=None,
        direction=None,
        limit=args.limit,
        quiet=True,
    )
    command_citation_chain(citation_args)
    files = export_reports(args.db, args.output)
    print(
        json.dumps(
            {
                "status": "demo_complete",
                "db": str(Path(args.db)),
                "ontology": ontology_summary(ontology),
                "study_profile": profile_summary(profile),
                "seed_sources": len(sources),
                "scopes": scope_results,
                "reports": [str(path) for path in files],
            },
            indent=2,
        )
    )
    return 0


def _add_context_arguments(command: argparse.ArgumentParser, include_policy: bool = True) -> None:
    command.add_argument("--ontology", default=DEFAULT_ONTOLOGY)
    command.add_argument("--profile", default=DEFAULT_PROFILE)
    if include_policy:
        command.add_argument("--policy", default=DEFAULT_POLICY)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Modular academic-evidence pipeline for HOMER energy-mix parameter studies"
    )
    sub = root.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate the parameter ontology and study profile")
    _add_context_arguments(validate, include_policy=False)
    validate.set_defaults(function=command_validate)

    init = sub.add_parser("init", help="Create or migrate the SQLite evidence registry")
    init.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(init, include_policy=False)
    init.set_defaults(function=command_init)

    seed = sub.add_parser("seed", help="Load the verified academic-first seed registry")
    seed.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    seed.add_argument("--sources", default=DEFAULT_SOURCES)
    seed.set_defaults(function=command_seed)

    plan = sub.add_parser("plan", help="Generate transparent scholarly search queries")
    _add_context_arguments(plan, include_policy=False)
    plan.add_argument("--family", required=True)
    plan.add_argument("--location", "--country", dest="country")
    plan.set_defaults(function=command_plan)

    retrieve = sub.add_parser("retrieve", help="Retrieve, gate and rank one evidence scope")
    retrieve.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(retrieve)
    retrieve.add_argument("--provider", choices=["offline", "crossref", "openalex"], default="offline")
    retrieve.add_argument("--family", required=True)
    retrieve.add_argument("--location", "--country", dest="country")
    retrieve.add_argument("--limit", type=int, default=20)
    retrieve.add_argument("--fixture", default=DEFAULT_FIXTURE)
    retrieve.set_defaults(function=command_retrieve)

    batch = sub.add_parser(
        "batch-retrieve",
        help="Search multiple databases across selected ontology families and locations",
    )
    batch.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(batch)
    batch.add_argument("--providers", default="crossref,openalex")
    batch.add_argument("--families", help="Comma-separated family IDs; default is every family")
    batch.add_argument("--locations", help="Comma-separated locations; default is the profile list")
    batch.add_argument("--include-region", action="store_true")
    batch.add_argument("--summary-only", action="store_true", help="Omit per-scope detail from stdout")
    batch.add_argument("--limit", type=int, default=20)
    batch.add_argument("--fixture", default=DEFAULT_FIXTURE)
    batch.set_defaults(function=command_batch_retrieve)

    screen = sub.add_parser("screen", help="Record a human title/abstract or full-text decision")
    screen.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    screen.add_argument("--candidate-id", type=int, required=True)
    screen.add_argument("--stage", choices=["title", "abstract", "title_abstract", "full_text"], default="title_abstract")
    screen.add_argument("--decision", choices=["include", "exclude", "uncertain"], required=True)
    screen.add_argument("--reviewer", required=True)
    screen.add_argument("--reasons", default="")
    screen.add_argument("--notes")
    screen.set_defaults(function=command_screen)

    chain = sub.add_parser("citation-chain", help="Add backward or forward citation-discovery records")
    chain.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(chain)
    chain.add_argument("--provider", choices=["offline", "openalex"], default="offline")
    chain.add_argument("--fixture", default=DEFAULT_CITATION_FIXTURE)
    chain.add_argument("--family", required=True)
    chain.add_argument("--location", "--country", dest="country")
    chain.add_argument("--seed-doi")
    chain.add_argument("--direction", choices=["references", "cited_by"])
    chain.add_argument("--limit", type=int, default=20)
    chain.set_defaults(function=command_citation_chain)

    report = sub.add_parser("report", help="Export auditable CSV reports")
    report.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    report.add_argument("--output", default=PROJECT_ROOT / "output")
    report.set_defaults(function=command_report)

    demo = sub.add_parser("demo", help="Run the reproducible offline demonstration")
    demo.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(demo)
    demo.add_argument("--sources", default=DEFAULT_SOURCES)
    demo.add_argument("--fixture", default=DEFAULT_FIXTURE)
    demo.add_argument("--citation-fixture", default=DEFAULT_CITATION_FIXTURE)
    demo.add_argument("--output", default=PROJECT_ROOT / "output")
    demo.add_argument("--location", "--country", dest="country", default="Oman")
    demo.add_argument("--limit", type=int, default=20)
    demo.set_defaults(function=command_demo)

    proposals = sub.add_parser("import-proposals", help="Import the researcher-defined parameter table")
    proposals.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(proposals, include_policy=False)
    proposals.add_argument("--file", required=True)
    proposals.set_defaults(function=command_import_proposals)

    propose = sub.add_parser("propose-parameter", help="Create or update one proposed model parameter")
    propose.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(propose, include_policy=False)
    propose.add_argument("--parameter-id", required=True)
    propose.add_argument("--scenario-id", default="base")
    propose.add_argument("--location", default="")
    propose.add_argument("--technology", default="")
    propose.add_argument("--value", type=float, required=True)
    propose.add_argument("--unit", required=True)
    propose.add_argument("--system-boundary")
    propose.add_argument("--source-basis")
    propose.add_argument("--criticality", choices=["low", "medium", "high", "safety_critical"])
    propose.add_argument("--entered-by", default="researcher")
    propose.add_argument("--notes")
    propose.set_defaults(function=command_propose_parameter)

    ingest = sub.add_parser("ingest-fulltext", help="Ingest a TXT, Markdown, JSON or PDF evidence document")
    ingest.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(ingest, include_policy=False)
    ingest.add_argument("--file", required=True)
    ingest.add_argument("--source-id")
    ingest.add_argument("--title")
    ingest.add_argument("--doi")
    ingest.add_argument("--rights-note")
    ingest.set_defaults(function=command_ingest_fulltext)

    ingest_corpus_parser = sub.add_parser(
        "ingest-corpus",
        help="Batch-ingest a versioned full-text corpus manifest with rights and quality gates",
    )
    ingest_corpus_parser.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(ingest_corpus_parser, include_policy=False)
    ingest_corpus_parser.add_argument("--manifest", required=True)
    ingest_corpus_parser.add_argument("--document-root")
    ingest_corpus_parser.add_argument(
        "--require-rights",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require a rights_note for every ingested document (default: true)",
    )
    ingest_corpus_parser.add_argument("--fail-fast", action="store_true")
    ingest_corpus_parser.add_argument("--allow-partial", action="store_true")
    ingest_corpus_parser.add_argument("--report")
    ingest_corpus_parser.set_defaults(function=command_ingest_corpus)

    workflow_parser = sub.add_parser(
        "run-workflow",
        help="Run the configured initialization, corpus ingestion, extraction-task and validation stages",
    )
    workflow_parser.add_argument("--config", required=True)
    workflow_parser.add_argument("--db", help="Override the database path in the workflow config")
    workflow_parser.add_argument(
        "--reset",
        action="store_true",
        help="Start from a new database; use only when the existing run is disposable",
    )
    workflow_parser.add_argument("--allow-partial", action="store_true")
    workflow_parser.set_defaults(function=command_run_workflow)

    advanced = sub.add_parser(
        "run-advanced-pipeline",
        help="Run strict OCR -> Luna screen -> Terra extraction -> review -> HOMER impact workflow",
    )
    advanced.add_argument("--config", required=True, help="Version 2.0 advanced-pipeline JSON config")
    advanced.add_argument(
        "--reset",
        action="store_true",
        help="Delete only the configured generated database before running; provider caches are retained",
    )
    advanced.set_defaults(function=command_run_advanced_pipeline)

    task = sub.add_parser("make-extraction-task", help="Create a strict JSON task for an external AI extractor")
    task.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(task, include_policy=False)
    task.add_argument("--document-id", type=int, required=True)
    task.add_argument("--parameters", required=True, help="Comma-separated ontology parameter IDs")
    task.add_argument("--output", required=True)
    task.set_defaults(function=command_make_extraction_task)

    llm_extract = sub.add_parser(
        "llm-extract-batch",
        help="Batch-submit ingested documents to a configured LLM and import schema-valid candidates",
    )
    llm_extract.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(llm_extract, include_policy=False)
    llm_extract.add_argument("--config", required=True, help="LLM provider JSON configuration")
    llm_extract.add_argument("--parameters", required=True, help="Comma-separated ontology parameter IDs")
    llm_extract.add_argument("--document-ids", help="Optional comma-separated IDs; default is every ingested document")
    llm_extract.add_argument("--output", default=PROJECT_ROOT / "output" / "llm_extraction")
    llm_extract.add_argument("--allow-partial", action="store_true")
    llm_extract.set_defaults(function=command_llm_extract_batch)

    extract = sub.add_parser("extract-observations", help="Run transparent regex pre-extraction on one parameter")
    extract.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(extract, include_policy=False)
    extract.add_argument("--document-id", type=int, required=True)
    extract.add_argument("--parameter-id", required=True)
    extract.add_argument("--source-id")
    extract.add_argument("--technology")
    extract.add_argument("--system-boundary")
    extract.add_argument("--location")
    extract.add_argument("--applicability", type=float, default=0.5)
    extract.add_argument("--fx-rate", type=float)
    extract.add_argument("--target-cost-year", type=int)
    extract.add_argument("--escalation-rate", type=float)
    extract.set_defaults(function=command_extract_observations)

    observations = sub.add_parser("import-observations", help="Import AI or manual evidence observations for review")
    observations.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    _add_context_arguments(observations, include_policy=False)
    observations.add_argument("--file", required=True)
    observations.add_argument("--fx-rate", type=float)
    observations.add_argument("--target-cost-year", type=int)
    observations.add_argument("--escalation-rate", type=float)
    observations.set_defaults(function=command_import_observations)

    review_observation = sub.add_parser("review-observation", help="Approve or reject one extracted value")
    review_observation.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    review_observation.add_argument("--observation-id", type=int, required=True)
    review_observation.add_argument("--decision", choices=["approved", "rejected", "needs_correction"], required=True)
    review_observation.add_argument("--reviewer", required=True)
    review_observation.add_argument("--notes")
    review_observation.set_defaults(function=command_review_observation)

    validate_parameter = sub.add_parser("validate-parameter", help="Compare a proposal with eligible evidence")
    validate_parameter.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    validate_parameter.add_argument("--proposal-id", type=int, required=True)
    validate_parameter.add_argument("--include-candidates", action="store_true")
    validate_parameter.add_argument("--min-sources", type=int, default=2)
    validate_parameter.set_defaults(function=command_validate_parameter)

    review_validation = sub.add_parser("review-validation", help="Human approval gate for a scenario input")
    review_validation.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    review_validation.add_argument("--validation-id", type=int, required=True)
    review_validation.add_argument("--decision", choices=["approve", "reject", "modify"], required=True)
    review_validation.add_argument("--reviewer", required=True)
    review_validation.add_argument("--low", type=float)
    review_validation.add_argument("--base", type=float)
    review_validation.add_argument("--high", type=float)
    review_validation.add_argument("--notes")
    review_validation.set_defaults(function=command_review_validation)

    scenario = sub.add_parser("export-scenario", help="Export approved low/base/high model inputs")
    scenario.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    scenario.add_argument("--scenario-id", required=True)
    scenario.add_argument("--location")
    scenario.add_argument("--output", required=True)
    scenario.set_defaults(function=command_export_scenario)

    model_run = sub.add_parser("create-model-run", help="Freeze an approved parameter snapshot for a model run")
    model_run.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    model_run.add_argument("--scenario-id", required=True)
    model_run.add_argument("--location")
    model_run.add_argument("--model-name", default="HOMER Pro")
    model_run.add_argument("--model-version")
    model_run.add_argument("--run-id")
    model_run.add_argument("--notes")
    model_run.set_defaults(function=command_create_model_run)

    model_results = sub.add_parser("import-model-results", help="Attach model outputs to a frozen input snapshot")
    model_results.add_argument("--db", default=PROJECT_ROOT / "output" / "prototype.sqlite")
    model_results.add_argument("--run-id", required=True)
    model_results.add_argument("--file", required=True)
    model_results.add_argument("--partial", action="store_true")
    model_results.set_defaults(function=command_import_model_results)

    quant_demo = sub.add_parser("quant-demo", help="Run the synthetic end-to-end quantitative validation demo")
    quant_demo.add_argument("--db", default=PROJECT_ROOT / "output" / "quantitative_demo.sqlite")
    _add_context_arguments(quant_demo, include_policy=False)
    quant_demo.add_argument("--proposals", default=DEFAULT_PROPOSALS)
    quant_demo.add_argument("--observations", default=DEFAULT_OBSERVATIONS)
    quant_demo.add_argument("--fulltext", default=DEFAULT_FULLTEXT)
    quant_demo.add_argument("--model-outputs", default=DEFAULT_MODEL_OUTPUTS)
    quant_demo.add_argument("--output", default=PROJECT_ROOT / "output" / "quantitative_demo")
    quant_demo.add_argument("--scenario-id", default="oman_demo")
    quant_demo.add_argument("--location", default="Oman")
    quant_demo.add_argument("--model-name", default="HOMER Pro")
    quant_demo.add_argument("--model-version", default="registry-demo")
    quant_demo.add_argument("--run-id", default="synthetic_demo_run")
    quant_demo.set_defaults(function=command_quant_demo)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.function(args)
    except (KeyError, ValueError) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, indent=2))
        return 1
