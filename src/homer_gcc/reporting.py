from __future__ import annotations

import csv
import json
from pathlib import Path

from .db import connect


def export_reports(db_path: str | Path, output_dir: str | Path) -> list[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    exports = {
        "ontology_parameters.csv": """
            SELECT parameter_id, family_id, label, data_type, value_kind,
                   canonical_unit, criticality, homer_mapping_json
            FROM parameter_definitions ORDER BY family_id, parameter_id
        """,
        "source_registry.csv": """
            SELECT source_id, title, year, doi, journal, source_type,
                   peer_reviewed, region_scope, countries_json,
                   parameter_families_json, verification_status, quality_score
            FROM source_registry ORDER BY quality_score DESC, year DESC
        """,
        "retrieval_candidates.csv": """
            SELECT candidate_id, family_id, context_location, title, abstract,
                   year, doi, venue, source_type, region_scope,
                   source_quality_score, topical_relevance_score, ranking_score,
                   relevance_gate_passed, automated_decision,
                   title_screen_status, abstract_screen_status,
                   human_screen_decision, matched_terms_json,
                   inclusion_reasons_json, exclusion_reasons_json,
                   providers_json, database_names_json, first_retrieved_at,
                   last_retrieved_at
            FROM evidence_candidates
            ORDER BY automated_decision='shortlist' DESC,
                     ranking_score DESC, topical_relevance_score DESC, year DESC
        """,
        "retrieval_provenance.csv": """
            SELECT p.provenance_id, p.candidate_id, c.family_id,
                   c.context_location, c.doi, c.title, p.provider,
                   p.database_name, p.query_text, p.retrieved_at, p.source_url
            FROM retrieval_provenance p
            JOIN evidence_candidates c ON c.candidate_id=p.candidate_id
            ORDER BY p.provenance_id
        """,
        "screening_decisions.csv": """
            SELECT s.screening_id, s.candidate_id, c.family_id,
                   c.context_location, c.doi, c.title, s.stage, s.decision,
                   s.reviewer, s.reasons_json, s.notes, s.screened_at
            FROM screening_decisions s
            JOIN evidence_candidates c ON c.candidate_id=s.candidate_id
            ORDER BY s.screening_id
        """,
        "citation_chains.csv": """
            SELECT chain_id, family_id, context_location, seed_doi, seed_title,
                   target_candidate_id, target_doi, target_title, direction,
                   provider, database_name, discovered_at
            FROM citation_chains ORDER BY chain_id
        """,
        "query_log.csv": """
            SELECT provider, database_name, family_id, country, query_text,
                   executed_at, retrieval_date, result_count, status, message
            FROM query_log ORDER BY query_id
        """,
        "proposed_parameters.csv": """
            SELECT proposal_id, parameter_id, scenario_id, context_location,
                   technology, proposed_value, proposed_unit, system_boundary,
                   source_basis, criticality, entered_by, status, notes,
                   created_at, updated_at
            FROM proposed_parameters ORDER BY scenario_id, parameter_id
        """,
        "full_text_documents.csv": """
            SELECT document_id, source_id, title, doi, file_name, mime_type,
                   sha256, ingestion_method, page_count, ingested_at,
                   metadata_json
            FROM full_text_documents ORDER BY document_id
        """,
        "evidence_observations.csv": """
            SELECT observation_id, parameter_id, document_id, source_id,
                   source_title, source_doi, independent_source_key,
                   raw_value_text, raw_value_min, raw_value_central,
                   raw_value_max, raw_unit, normalized_value_min,
                   normalized_value_central, normalized_value_max,
                   canonical_unit, currency, source_cost_year,
                   target_cost_year, technology, system_boundary,
                   context_location, scale, operating_conditions, page_number,
                   table_id, figure_id, locator, extraction_method,
                   extraction_confidence, applicability_score,
                   authoritative_source, physical_check_passed,
                   normalization_json, verification_status, reviewer,
                   reviewed_at, review_notes, created_at
            FROM evidence_observations ORDER BY observation_id
        """,
        "parameter_validations.csv": """
            SELECT v.validation_id, v.proposal_id, p.parameter_id,
                   p.scenario_id, p.context_location, p.technology,
                   v.decision, v.evidence_count, v.independent_source_count,
                   v.approved_evidence_count, v.evidence_ids_json,
                   v.proposed_normalized_value, v.canonical_unit,
                   v.aggregate_low, v.aggregate_base, v.aggregate_high,
                   v.range_position, v.conflict_flag,
                   v.average_applicability, v.physical_check_passed,
                   v.sensitivity_priority, v.reasons_json,
                   v.algorithm_version, v.human_status, v.reviewer,
                   v.reviewed_at, v.review_notes, v.created_at
            FROM parameter_validations v
            JOIN proposed_parameters p ON p.proposal_id=v.proposal_id
            ORDER BY v.validation_id
        """,
        "scenario_parameters.csv": """
            SELECT scenario_parameter_id, scenario_id, parameter_id,
                   context_location, technology, low_value, base_value,
                   high_value, selected_value, canonical_unit, validation_id,
                   evidence_ids_json, approval_status, approved_by,
                   approved_at, version, notes
            FROM scenario_parameters ORDER BY scenario_id, parameter_id
        """,
        "model_runs.csv": """
            SELECT run_id, scenario_id, context_location, model_name,
                   model_version, status, input_snapshot_sha256, started_at,
                   completed_at, notes
            FROM model_runs ORDER BY started_at
        """,
        "model_inputs.csv": """
            SELECT run_id, scenario_parameter_id, parameter_id, value, unit
            FROM model_inputs ORDER BY run_id, parameter_id
        """,
        "model_outputs.csv": """
            SELECT output_id, run_id, metric_id, metric_category, value, unit
            FROM model_outputs ORDER BY run_id, metric_id
        """,
    }

    with connect(db_path) as connection:
        for filename, query in exports.items():
            rows = connection.execute(query).fetchall()
            path = output / filename
            with path.open("w", newline="", encoding="utf-8") as handle:
                if rows:
                    writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(dict(row) for row in rows)
                else:
                    handle.write("")
            written.append(path)

        counts = {
            "parameter_count": connection.execute("SELECT COUNT(*) FROM parameter_definitions").fetchone()[0],
            "source_count": connection.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0],
            "peer_reviewed_source_count": connection.execute(
                "SELECT COUNT(*) FROM source_registry WHERE peer_reviewed=1"
            ).fetchone()[0],
            "candidate_count": connection.execute("SELECT COUNT(*) FROM evidence_candidates").fetchone()[0],
            "retrieval_occurrence_count": connection.execute(
                "SELECT COUNT(*) FROM retrieval_provenance"
            ).fetchone()[0],
            "shortlisted_candidate_count": connection.execute(
                "SELECT COUNT(*) FROM evidence_candidates WHERE automated_decision='shortlist'"
            ).fetchone()[0],
            "topical_gate_failure_count": connection.execute(
                "SELECT COUNT(*) FROM evidence_candidates WHERE relevance_gate_passed=0"
            ).fetchone()[0],
            "human_included_count": connection.execute(
                "SELECT COUNT(*) FROM evidence_candidates WHERE human_screen_decision='include'"
            ).fetchone()[0],
            "human_excluded_count": connection.execute(
                "SELECT COUNT(*) FROM evidence_candidates WHERE human_screen_decision='exclude'"
            ).fetchone()[0],
            "citation_chain_count": connection.execute(
                "SELECT COUNT(*) FROM citation_chains"
            ).fetchone()[0],
            "proposed_parameter_count": connection.execute(
                "SELECT COUNT(*) FROM proposed_parameters"
            ).fetchone()[0],
            "full_text_document_count": connection.execute(
                "SELECT COUNT(*) FROM full_text_documents"
            ).fetchone()[0],
            "evidence_observation_count": connection.execute(
                "SELECT COUNT(*) FROM evidence_observations"
            ).fetchone()[0],
            "approved_evidence_observation_count": connection.execute(
                "SELECT COUNT(*) FROM evidence_observations WHERE verification_status='approved'"
            ).fetchone()[0],
            "candidate_evidence_observation_count": connection.execute(
                "SELECT COUNT(*) FROM evidence_observations WHERE verification_status='candidate'"
            ).fetchone()[0],
            "parameter_validation_count": connection.execute(
                "SELECT COUNT(*) FROM parameter_validations"
            ).fetchone()[0],
            "pending_human_validation_count": connection.execute(
                "SELECT COUNT(*) FROM parameter_validations WHERE human_status='pending'"
            ).fetchone()[0],
            "approved_scenario_parameter_count": connection.execute(
                "SELECT COUNT(*) FROM scenario_parameters WHERE approval_status IN ('approved','modified')"
            ).fetchone()[0],
            "model_run_count": connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0],
            "model_output_count": connection.execute("SELECT COUNT(*) FROM model_outputs").fetchone()[0],
        }
    summary_path = output / "prototype_summary.json"
    summary_path.write_text(json.dumps(counts, indent=2), encoding="utf-8")
    written.append(summary_path)
    return written
