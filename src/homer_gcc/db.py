from __future__ import annotations

import json
import re
import sqlite3
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ontology import iter_parameters


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parameter_definitions (
    parameter_id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    label TEXT NOT NULL,
    data_type TEXT NOT NULL,
    value_kind TEXT NOT NULL,
    canonical_unit TEXT NOT NULL,
    allowed_units_json TEXT NOT NULL,
    criticality TEXT NOT NULL,
    homer_mapping_json TEXT NOT NULL,
    evidence_preference_json TEXT NOT NULL,
    extraction_terms_json TEXT NOT NULL,
    definition_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_registry (
    source_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors_json TEXT NOT NULL,
    year INTEGER,
    doi TEXT UNIQUE,
    journal TEXT,
    source_type TEXT NOT NULL,
    peer_reviewed INTEGER NOT NULL DEFAULT 0,
    open_access INTEGER,
    region_scope TEXT NOT NULL,
    countries_json TEXT NOT NULL,
    system_types_json TEXT NOT NULL,
    parameter_families_json TEXT NOT NULL,
    url TEXT,
    evidence_level TEXT,
    relevance_note TEXT,
    verification_status TEXT NOT NULL,
    quality_score REAL NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS query_log (
    query_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    database_name TEXT,
    family_id TEXT NOT NULL,
    country TEXT,
    query_text TEXT NOT NULL,
    executed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retrieval_date TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    message TEXT
);

CREATE TABLE IF NOT EXISTS evidence_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL,
    family_id TEXT NOT NULL,
    context_location TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    authors_json TEXT NOT NULL,
    year INTEGER,
    doi TEXT,
    venue TEXT,
    url TEXT,
    peer_reviewed INTEGER NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL,
    region_scope TEXT NOT NULL,
    source_quality_score REAL NOT NULL,
    topical_relevance_score REAL NOT NULL,
    ranking_score REAL NOT NULL,
    relevance_gate_passed INTEGER NOT NULL DEFAULT 0,
    automated_decision TEXT NOT NULL,
    title_screen_status TEXT NOT NULL DEFAULT 'pending',
    abstract_screen_status TEXT NOT NULL DEFAULT 'pending',
    human_screen_decision TEXT NOT NULL DEFAULT 'pending',
    matched_terms_json TEXT NOT NULL,
    system_matches_json TEXT NOT NULL,
    inclusion_reasons_json TEXT NOT NULL,
    exclusion_reasons_json TEXT NOT NULL,
    decision_reasons_json TEXT NOT NULL,
    providers_json TEXT NOT NULL,
    database_names_json TEXT NOT NULL,
    first_retrieved_at TEXT NOT NULL,
    last_retrieved_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(family_id, context_location, canonical_key)
);

CREATE TABLE IF NOT EXISTS retrieval_provenance (
    provenance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES evidence_candidates(candidate_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    database_name TEXT NOT NULL,
    query_text TEXT,
    retrieved_at TEXT NOT NULL,
    source_url TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(candidate_id, provider, query_text, retrieved_at, source_url)
);

CREATE TABLE IF NOT EXISTS screening_decisions (
    screening_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES evidence_candidates(candidate_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    notes TEXT,
    screened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citation_chains (
    chain_id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id TEXT NOT NULL,
    context_location TEXT NOT NULL DEFAULT '',
    seed_doi TEXT NOT NULL,
    seed_title TEXT,
    target_candidate_id INTEGER REFERENCES evidence_candidates(candidate_id) ON DELETE SET NULL,
    target_doi TEXT,
    target_title TEXT NOT NULL,
    direction TEXT NOT NULL,
    provider TEXT NOT NULL,
    database_name TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(family_id, context_location, seed_doi, target_title, direction, provider)
);

CREATE TABLE IF NOT EXISTS proposed_parameters (
    proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    parameter_id TEXT NOT NULL REFERENCES parameter_definitions(parameter_id),
    scenario_id TEXT NOT NULL DEFAULT 'base',
    context_location TEXT NOT NULL DEFAULT '',
    technology TEXT NOT NULL DEFAULT '',
    proposed_value REAL NOT NULL,
    proposed_unit TEXT NOT NULL,
    system_boundary TEXT,
    source_basis TEXT,
    criticality TEXT NOT NULL,
    entered_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_validation',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(parameter_id, scenario_id, context_location, technology)
);

CREATE TABLE IF NOT EXISTS full_text_documents (
    document_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    title TEXT NOT NULL,
    doi TEXT,
    file_name TEXT NOT NULL,
    file_path TEXT,
    mime_type TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    ingestion_method TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_pages (
    document_id INTEGER NOT NULL REFERENCES full_text_documents(document_id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    page_text TEXT NOT NULL,
    PRIMARY KEY(document_id, page_number)
);

CREATE TABLE IF NOT EXISTS evidence_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    parameter_id TEXT NOT NULL REFERENCES parameter_definitions(parameter_id),
    document_id INTEGER REFERENCES full_text_documents(document_id) ON DELETE SET NULL,
    source_id TEXT NOT NULL,
    source_title TEXT,
    source_doi TEXT,
    independent_source_key TEXT NOT NULL,
    raw_value_text TEXT,
    raw_value_min REAL NOT NULL,
    raw_value_central REAL NOT NULL,
    raw_value_max REAL NOT NULL,
    raw_unit TEXT NOT NULL,
    normalized_value_min REAL NOT NULL,
    normalized_value_central REAL NOT NULL,
    normalized_value_max REAL NOT NULL,
    canonical_unit TEXT NOT NULL,
    currency TEXT,
    source_cost_year INTEGER,
    target_cost_year INTEGER,
    technology TEXT,
    system_boundary TEXT,
    context_location TEXT,
    scale TEXT,
    operating_conditions TEXT,
    page_number INTEGER,
    table_id TEXT,
    figure_id TEXT,
    locator TEXT NOT NULL,
    context_excerpt TEXT,
    extraction_method TEXT NOT NULL,
    extraction_confidence REAL NOT NULL,
    applicability_score REAL NOT NULL DEFAULT 0,
    authoritative_source INTEGER NOT NULL DEFAULT 0,
    physical_check_passed INTEGER NOT NULL DEFAULT 0,
    normalization_json TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'candidate',
    reviewer TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parameter_validations (
    validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL REFERENCES proposed_parameters(proposal_id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    independent_source_count INTEGER NOT NULL,
    approved_evidence_count INTEGER NOT NULL,
    proposed_normalized_value REAL NOT NULL,
    canonical_unit TEXT NOT NULL,
    aggregate_low REAL,
    aggregate_base REAL,
    aggregate_high REAL,
    range_position TEXT NOT NULL,
    conflict_flag INTEGER NOT NULL DEFAULT 0,
    average_applicability REAL NOT NULL DEFAULT 0,
    physical_check_passed INTEGER NOT NULL DEFAULT 0,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    sensitivity_priority TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    human_status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_parameters (
    scenario_parameter_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL,
    parameter_id TEXT NOT NULL REFERENCES parameter_definitions(parameter_id),
    context_location TEXT NOT NULL DEFAULT '',
    technology TEXT NOT NULL DEFAULT '',
    low_value REAL NOT NULL,
    base_value REAL NOT NULL,
    high_value REAL NOT NULL,
    selected_value REAL NOT NULL,
    canonical_unit TEXT NOT NULL,
    validation_id INTEGER NOT NULL REFERENCES parameter_validations(validation_id),
    evidence_ids_json TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    UNIQUE(scenario_id, parameter_id, context_location, technology)
);

CREATE TABLE IF NOT EXISTS model_runs (
    run_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    context_location TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL,
    model_version TEXT,
    status TEXT NOT NULL,
    input_snapshot_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS model_inputs (
    run_id TEXT NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    scenario_parameter_id INTEGER REFERENCES scenario_parameters(scenario_parameter_id),
    parameter_id TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    PRIMARY KEY(run_id, parameter_id)
);

CREATE TABLE IF NOT EXISTS model_outputs (
    output_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    metric_id TEXT NOT NULL,
    metric_category TEXT,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(run_id, metric_id)
);

CREATE INDEX IF NOT EXISTS idx_sources_family ON source_registry(parameter_families_json);
CREATE INDEX IF NOT EXISTS idx_evidence_candidates_rank ON evidence_candidates(ranking_score DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_candidates_doi ON evidence_candidates(doi);
CREATE INDEX IF NOT EXISTS idx_evidence_candidates_screen ON evidence_candidates(human_screen_decision);
CREATE INDEX IF NOT EXISTS idx_provenance_candidate ON retrieval_provenance(candidate_id);
CREATE INDEX IF NOT EXISTS idx_chains_seed ON citation_chains(seed_doi);
CREATE INDEX IF NOT EXISTS idx_proposals_parameter ON proposed_parameters(parameter_id, scenario_id);
CREATE INDEX IF NOT EXISTS idx_observations_parameter ON evidence_observations(parameter_id, verification_status);
CREATE INDEX IF NOT EXISTS idx_validations_proposal ON parameter_validations(proposal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_scenario_parameters ON scenario_parameters(scenario_id, approval_status);
CREATE INDEX IF NOT EXISTS idx_model_outputs_run ON model_outputs(run_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_column(connection: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def initialize(
    path: str | Path,
    ontology: dict[str, Any],
    profile: dict[str, Any] | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        # These two migrations keep query logging compatible with v0.1 databases.
        _ensure_column(connection, "query_log", "database_name TEXT")
        _ensure_column(connection, "query_log", "retrieval_date TEXT")
        _ensure_column(connection, "parameter_validations", "evidence_ids_json TEXT NOT NULL DEFAULT '[]'")
        metadata = {
            "schema_version": "0.3.0",
            "ontology_name": str(ontology.get("name", "")),
            "ontology_version": str(ontology.get("version", "")),
        }
        if profile:
            metadata["domain_profile_id"] = str(profile.get("id", "custom"))
            metadata["domain_profile_name"] = str(profile.get("name", ""))
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            metadata.items(),
        )
        for parameter in iter_parameters(ontology):
            connection.execute(
                """
                INSERT OR REPLACE INTO parameter_definitions(
                    parameter_id, family_id, label, data_type, value_kind,
                    canonical_unit, allowed_units_json, criticality,
                    homer_mapping_json, evidence_preference_json,
                    extraction_terms_json, definition_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parameter["id"],
                    parameter["family_id"],
                    parameter["label"],
                    parameter["data_type"],
                    parameter["value_kind"],
                    parameter["canonical_unit"],
                    json.dumps(parameter.get("allowed_units", []), ensure_ascii=False),
                    parameter["criticality"],
                    json.dumps(parameter.get("homer_mapping", {}), ensure_ascii=False),
                    json.dumps(parameter.get("evidence_preference", []), ensure_ascii=False),
                    json.dumps(parameter.get("extraction_terms", []), ensure_ascii=False),
                    json.dumps(parameter, ensure_ascii=False),
                ),
            )


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized or None


def _canonical_key(item: dict[str, Any]) -> str:
    doi = normalize_doi(item.get("doi"))
    if doi:
        return f"doi:{doi}"
    title = re.sub(r"[^a-z0-9]+", " ", str(item.get("title", "")).lower()).strip()
    return f"title:{title}|year:{item.get('year') or ''}"


def insert_sources(path: str | Path, sources: Iterable[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    with connect(path) as connection:
        for source in sources:
            doi = normalize_doi(source.get("doi"))
            try:
                connection.execute(
                    """
                    INSERT INTO source_registry(
                        source_id, title, authors_json, year, doi, journal, source_type,
                        peer_reviewed, open_access, region_scope, countries_json,
                        system_types_json, parameter_families_json, url, evidence_level,
                        relevance_note, verification_status, quality_score, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        title=excluded.title, authors_json=excluded.authors_json,
                        year=excluded.year, doi=excluded.doi, journal=excluded.journal,
                        source_type=excluded.source_type, peer_reviewed=excluded.peer_reviewed,
                        open_access=excluded.open_access, region_scope=excluded.region_scope,
                        countries_json=excluded.countries_json,
                        system_types_json=excluded.system_types_json,
                        parameter_families_json=excluded.parameter_families_json,
                        url=excluded.url, evidence_level=excluded.evidence_level,
                        relevance_note=excluded.relevance_note,
                        verification_status=excluded.verification_status,
                        quality_score=excluded.quality_score, raw_json=excluded.raw_json
                    """,
                    (
                        source["id"],
                        source["title"],
                        json.dumps(source.get("authors", []), ensure_ascii=False),
                        source.get("year"),
                        doi,
                        source.get("journal"),
                        source["source_type"],
                        int(bool(source.get("peer_reviewed"))),
                        None if source.get("open_access") is None else int(bool(source.get("open_access"))),
                        source.get("region_scope", "global"),
                        json.dumps(source.get("countries", []), ensure_ascii=False),
                        json.dumps(source.get("system_types", []), ensure_ascii=False),
                        json.dumps(source.get("parameter_families", []), ensure_ascii=False),
                        source.get("url"),
                        source.get("evidence_level"),
                        source.get("relevance_note"),
                        source.get("verification_status", "candidate"),
                        float(source.get("quality_score", 0)),
                        json.dumps(source, ensure_ascii=False),
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
    return inserted, skipped


def _merge_json_list(existing: str, incoming: Iterable[Any]) -> str:
    values = json.loads(existing or "[]")
    for item in incoming:
        if item not in values:
            values.append(item)
    return json.dumps(values, ensure_ascii=False)


def insert_candidates(
    path: str | Path,
    provider: str,
    family_id: str,
    country: str | None,
    candidates: Iterable[dict[str, Any]],
) -> int:
    """Cross-provider upsert of candidates and their retrieval provenance."""

    processed: set[str] = set()
    location = country or ""
    with connect(path) as connection:
        for item in candidates:
            key = item.get("canonical_key") or _canonical_key(item)
            processed.add(key)
            retrieved_at = str(item.get("retrieved_at") or _now())
            provenance = item.get("provenance") or [
                {
                    "provider": provider,
                    "database_name": item.get("database_name") or provider,
                    "query_text": item.get("query_text"),
                    "retrieved_at": retrieved_at,
                    "url": item.get("url"),
                }
            ]
            providers = sorted(
                {str(row.get("provider")) for row in provenance if row.get("provider")}
                | {str(value) for value in item.get("providers", []) if value}
            )
            databases = sorted(
                {str(row.get("database_name")) for row in provenance if row.get("database_name")}
                | {str(value) for value in item.get("database_names", []) if value}
            )
            existing = connection.execute(
                """
                SELECT * FROM evidence_candidates
                WHERE family_id=? AND context_location=? AND canonical_key=?
                """,
                (family_id, location, key),
            ).fetchone()
            if existing:
                providers_json = _merge_json_list(existing["providers_json"], providers)
                databases_json = _merge_json_list(existing["database_names_json"], databases)
                connection.execute(
                    """
                    UPDATE evidence_candidates SET
                        title=?, abstract=?, authors_json=?, year=?, doi=?, venue=?, url=?,
                        peer_reviewed=?, source_type=?, region_scope=?, source_quality_score=?,
                        topical_relevance_score=?, ranking_score=?, relevance_gate_passed=?,
                        automated_decision=?, matched_terms_json=?, system_matches_json=?,
                        inclusion_reasons_json=?, exclusion_reasons_json=?,
                        decision_reasons_json=?, providers_json=?, database_names_json=?,
                        last_retrieved_at=?, raw_json=?
                    WHERE candidate_id=?
                    """,
                    (
                        item["title"], str(item.get("abstract", "")),
                        json.dumps(item.get("authors", []), ensure_ascii=False), item.get("year"),
                        normalize_doi(item.get("doi")), item.get("venue"), item.get("url"),
                        int(bool(item.get("peer_reviewed"))), item.get("source_type", "unknown"),
                        item.get("region_scope", "global"), float(item.get("source_quality_score", 0)),
                        float(item.get("topical_relevance_score", 0)),
                        float(item.get("ranking_score", item.get("quality_score", 0))),
                        int(bool(item.get("relevance_gate_passed"))), item.get("decision", "review"),
                        json.dumps(item.get("matched_terms", []), ensure_ascii=False),
                        json.dumps(item.get("system_matches", []), ensure_ascii=False),
                        json.dumps(item.get("inclusion_reasons", []), ensure_ascii=False),
                        json.dumps(item.get("exclusion_reasons", []), ensure_ascii=False),
                        json.dumps(item.get("decision_reasons", []), ensure_ascii=False),
                        providers_json, databases_json, retrieved_at,
                        json.dumps(item.get("raw", item), ensure_ascii=False), existing["candidate_id"],
                    ),
                )
                candidate_id = int(existing["candidate_id"])
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO evidence_candidates(
                        canonical_key, family_id, context_location, title, abstract,
                        authors_json, year, doi, venue, url, peer_reviewed, source_type,
                        region_scope, source_quality_score, topical_relevance_score,
                        ranking_score, relevance_gate_passed, automated_decision,
                        title_screen_status, abstract_screen_status, human_screen_decision,
                        matched_terms_json, system_matches_json, inclusion_reasons_json,
                        exclusion_reasons_json, decision_reasons_json, providers_json,
                        database_names_json, first_retrieved_at, last_retrieved_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key, family_id, location, item["title"], str(item.get("abstract", "")),
                        json.dumps(item.get("authors", []), ensure_ascii=False), item.get("year"),
                        normalize_doi(item.get("doi")), item.get("venue"), item.get("url"),
                        int(bool(item.get("peer_reviewed"))), item.get("source_type", "unknown"),
                        item.get("region_scope", "global"), float(item.get("source_quality_score", 0)),
                        float(item.get("topical_relevance_score", 0)),
                        float(item.get("ranking_score", item.get("quality_score", 0))),
                        int(bool(item.get("relevance_gate_passed"))), item.get("decision", "review"),
                        item.get("title_screen_status", "pending"),
                        item.get("abstract_screen_status", "pending"),
                        item.get("human_screen_decision", "pending"),
                        json.dumps(item.get("matched_terms", []), ensure_ascii=False),
                        json.dumps(item.get("system_matches", []), ensure_ascii=False),
                        json.dumps(item.get("inclusion_reasons", []), ensure_ascii=False),
                        json.dumps(item.get("exclusion_reasons", []), ensure_ascii=False),
                        json.dumps(item.get("decision_reasons", []), ensure_ascii=False),
                        json.dumps(providers, ensure_ascii=False), json.dumps(databases, ensure_ascii=False),
                        retrieved_at, retrieved_at, json.dumps(item.get("raw", item), ensure_ascii=False),
                    ),
                )
                candidate_id = int(cursor.lastrowid)

            for record in provenance:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO retrieval_provenance(
                        candidate_id, provider, database_name, query_text,
                        retrieved_at, source_url, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        record.get("provider") or provider,
                        record.get("database_name") or provider,
                        record.get("query_text"),
                        record.get("retrieved_at") or retrieved_at,
                        record.get("url") or item.get("url"),
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
    return len(processed)


def log_query(
    path: str | Path,
    provider: str,
    family_id: str,
    country: str | None,
    query_text: str,
    result_count: int,
    status: str,
    message: str | None = None,
    database: str | None = None,
    retrieval_date: str | None = None,
) -> None:
    timestamp = retrieval_date or _now()
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO query_log(
                provider, database_name, family_id, country, query_text,
                executed_at, retrieval_date, result_count, status, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider, database or provider, family_id, country, query_text,
                timestamp, timestamp[:10], result_count, status, message,
            ),
        )


def record_screening(
    path: str | Path,
    candidate_id: int,
    stage: str,
    decision: str,
    reviewer: str,
    reasons: Iterable[str],
    notes: str | None = None,
    screened_at: str | None = None,
) -> None:
    if stage not in {"title", "abstract", "title_abstract", "full_text"}:
        raise ValueError("Invalid screening stage")
    if decision not in {"include", "exclude", "uncertain"}:
        raise ValueError("Invalid screening decision")
    timestamp = screened_at or _now()
    with connect(path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM evidence_candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if not exists:
            raise KeyError(f"Unknown candidate_id: {candidate_id}")
        connection.execute(
            """
            INSERT INTO screening_decisions(
                candidate_id, stage, decision, reviewer, reasons_json, notes, screened_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (candidate_id, stage, decision, reviewer, json.dumps(list(reasons)), notes, timestamp),
        )
        if stage == "title":
            connection.execute(
                "UPDATE evidence_candidates SET title_screen_status=?, human_screen_decision=? WHERE candidate_id=?",
                (decision, decision, candidate_id),
            )
        elif stage == "abstract":
            connection.execute(
                "UPDATE evidence_candidates SET abstract_screen_status=?, human_screen_decision=? WHERE candidate_id=?",
                (decision, decision, candidate_id),
            )
        elif stage == "title_abstract":
            connection.execute(
                """
                UPDATE evidence_candidates SET title_screen_status=?, abstract_screen_status=?,
                    human_screen_decision=? WHERE candidate_id=?
                """,
                (decision, decision, decision, candidate_id),
            )
        else:
            connection.execute(
                "UPDATE evidence_candidates SET human_screen_decision=? WHERE candidate_id=?",
                (decision, candidate_id),
            )


def candidate_id_for(
    path: str | Path,
    family_id: str,
    country: str | None,
    item: dict[str, Any],
) -> int | None:
    with connect(path) as connection:
        row = connection.execute(
            """
            SELECT candidate_id FROM evidence_candidates
            WHERE family_id=? AND context_location=? AND canonical_key=?
            """,
            (family_id, country or "", item.get("canonical_key") or _canonical_key(item)),
        ).fetchone()
    return int(row[0]) if row else None


def insert_citation_links(
    path: str | Path,
    family_id: str,
    country: str | None,
    seed_doi: str,
    seed_title: str | None,
    direction: str,
    provider: str,
    database: str,
    candidates: Iterable[dict[str, Any]],
) -> int:
    count = 0
    discovered_at = _now()
    with connect(path) as connection:
        for item in candidates:
            candidate_id = connection.execute(
                """
                SELECT candidate_id FROM evidence_candidates
                WHERE family_id=? AND context_location=? AND canonical_key=?
                """,
                (family_id, country or "", item.get("canonical_key") or _canonical_key(item)),
            ).fetchone()
            connection.execute(
                """
                INSERT OR IGNORE INTO citation_chains(
                    family_id, context_location, seed_doi, seed_title,
                    target_candidate_id, target_doi, target_title, direction,
                    provider, database_name, discovered_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    family_id, country or "", normalize_doi(seed_doi), seed_title,
                    int(candidate_id[0]) if candidate_id else None,
                    normalize_doi(item.get("doi")), item["title"], direction,
                    provider, database, discovered_at, json.dumps(item.get("raw", item), ensure_ascii=False),
                ),
            )
            count += 1
    return count


def get_parameter_definition(path: str | Path, parameter_id: str) -> dict[str, Any]:
    with connect(path) as connection:
        row = connection.execute(
            "SELECT definition_json FROM parameter_definitions WHERE parameter_id=?",
            (parameter_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"Unknown parameter_id: {parameter_id}")
    return json.loads(row["definition_json"])


def upsert_proposed_parameter(path: str | Path, item: dict[str, Any]) -> int:
    parameter_id = str(item["parameter_id"])
    definition = get_parameter_definition(path, parameter_id)
    timestamp = _now()
    scenario_id = str(item.get("scenario_id") or "base")
    location = str(item.get("context_location") or "")
    technology = str(item.get("technology") or "")
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO proposed_parameters(
                parameter_id, scenario_id, context_location, technology,
                proposed_value, proposed_unit, system_boundary, source_basis,
                criticality, entered_by, status, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(parameter_id, scenario_id, context_location, technology)
            DO UPDATE SET
                proposed_value=excluded.proposed_value,
                proposed_unit=excluded.proposed_unit,
                system_boundary=excluded.system_boundary,
                source_basis=excluded.source_basis,
                criticality=excluded.criticality,
                entered_by=excluded.entered_by,
                status='pending_validation',
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            (
                parameter_id,
                scenario_id,
                location,
                technology,
                float(item["proposed_value"]),
                str(item.get("proposed_unit") or definition["canonical_unit"]),
                item.get("system_boundary"),
                item.get("source_basis"),
                str(item.get("criticality") or definition.get("criticality", "medium")),
                str(item.get("entered_by") or "researcher"),
                str(item.get("status") or "pending_validation"),
                item.get("notes"),
                timestamp,
                timestamp,
            ),
        )
        row = connection.execute(
            """
            SELECT proposal_id FROM proposed_parameters
            WHERE parameter_id=? AND scenario_id=? AND context_location=? AND technology=?
            """,
            (parameter_id, scenario_id, location, technology),
        ).fetchone()
    return int(row["proposal_id"])


def insert_full_text_document(
    path: str | Path,
    document: dict[str, Any],
    *,
    source_id: str | None = None,
    title: str | None = None,
    doi: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    pages = [str(page) for page in document.get("pages", [])]
    if not pages:
        raise ValueError("Document contains no pages")
    with connect(path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO full_text_documents(
                source_id, title, doi, file_name, file_path, mime_type, sha256,
                ingestion_method, page_count, ingested_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                title or document["file_name"],
                normalize_doi(doi),
                document["file_name"],
                document.get("file_path"),
                document["mime_type"],
                document["sha256"],
                document["ingestion_method"],
                len(pages),
                _now(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        row = connection.execute(
            "SELECT document_id FROM full_text_documents WHERE sha256=?",
            (document["sha256"],),
        ).fetchone()
        document_id = int(row["document_id"])
        connection.executemany(
            """
            INSERT OR REPLACE INTO document_pages(document_id, page_number, page_text)
            VALUES (?, ?, ?)
            """,
            [(document_id, index, page) for index, page in enumerate(pages, start=1)],
        )
    return document_id


def get_document(path: str | Path, document_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with connect(path) as connection:
        document = connection.execute(
            "SELECT * FROM full_text_documents WHERE document_id=?",
            (document_id,),
        ).fetchone()
        pages = connection.execute(
            "SELECT page_number, page_text FROM document_pages WHERE document_id=? ORDER BY page_number",
            (document_id,),
        ).fetchall()
    if not document:
        raise KeyError(f"Unknown document_id: {document_id}")
    return dict(document), [dict(row) for row in pages]


def insert_evidence_observation(path: str | Path, item: dict[str, Any]) -> int:
    get_parameter_definition(path, str(item["parameter_id"]))
    confidence = float(item.get("extraction_confidence", 0))
    applicability = float(item.get("applicability_score", 0))
    if not 0 <= confidence <= 1:
        raise ValueError("extraction_confidence must be between 0 and 1")
    if not 0 <= applicability <= 1:
        raise ValueError("applicability_score must be between 0 and 1")
    source_id = str(item.get("source_id") or item.get("source_doi") or "unknown")
    source_key = str(
        item.get("independent_source_key")
        or normalize_doi(item.get("source_doi"))
        or source_id
    ).lower()
    with connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO evidence_observations(
                parameter_id, document_id, source_id, source_title, source_doi,
                independent_source_key, raw_value_text, raw_value_min,
                raw_value_central, raw_value_max, raw_unit,
                normalized_value_min, normalized_value_central,
                normalized_value_max, canonical_unit, currency,
                source_cost_year, target_cost_year, technology, system_boundary,
                context_location, scale, operating_conditions, page_number,
                table_id, figure_id, locator, context_excerpt, extraction_method,
                extraction_confidence, applicability_score, authoritative_source,
                physical_check_passed, normalization_json, verification_status,
                reviewer, reviewed_at, review_notes, created_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["parameter_id"],
                item.get("document_id"),
                source_id,
                item.get("source_title"),
                normalize_doi(item.get("source_doi")),
                source_key,
                item.get("raw_value_text"),
                float(item["raw_value_min"]),
                float(item["raw_value_central"]),
                float(item["raw_value_max"]),
                item["raw_unit"],
                float(item["normalized_value_min"]),
                float(item["normalized_value_central"]),
                float(item["normalized_value_max"]),
                item["canonical_unit"],
                item.get("currency"),
                item.get("source_cost_year"),
                item.get("target_cost_year"),
                item.get("technology"),
                item.get("system_boundary"),
                item.get("context_location"),
                item.get("scale"),
                item.get("operating_conditions"),
                item.get("page_number"),
                item.get("table_id"),
                item.get("figure_id"),
                item.get("locator") or "unspecified locator",
                item.get("context_excerpt"),
                item.get("extraction_method", "llm_json_contract"),
                confidence,
                applicability,
                int(bool(item.get("authoritative_source"))),
                int(bool(item.get("physical_check_passed"))),
                json.dumps(item.get("normalization", {}), ensure_ascii=False),
                item.get("verification_status", "candidate"),
                item.get("reviewer"),
                item.get("reviewed_at"),
                item.get("review_notes"),
                _now(),
                json.dumps(item.get("raw", item), ensure_ascii=False),
            ),
        )
    return int(cursor.lastrowid)


def review_evidence_observation(
    path: str | Path,
    observation_id: int,
    decision: str,
    reviewer: str,
    notes: str | None = None,
) -> None:
    if decision not in {"approved", "rejected", "needs_correction"}:
        raise ValueError("Observation decision must be approved, rejected or needs_correction")
    with connect(path) as connection:
        row = connection.execute(
            "SELECT physical_check_passed FROM evidence_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown observation_id: {observation_id}")
        if decision == "approved" and not row["physical_check_passed"]:
            raise ValueError("An observation that fails physical checks cannot be approved")
        result = connection.execute(
            """
            UPDATE evidence_observations
            SET verification_status=?, reviewer=?, reviewed_at=?, review_notes=?
            WHERE observation_id=?
            """,
            (decision, reviewer, _now(), notes, observation_id),
        )
        if result.rowcount != 1:
            raise KeyError(f"Unknown observation_id: {observation_id}")


def insert_parameter_validation(path: str | Path, result: dict[str, Any]) -> int:
    with connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO parameter_validations(
                proposal_id, decision, evidence_count, independent_source_count,
                approved_evidence_count, proposed_normalized_value, canonical_unit,
                aggregate_low, aggregate_base, aggregate_high, range_position,
                conflict_flag, average_applicability, physical_check_passed,
                evidence_ids_json, sensitivity_priority, reasons_json,
                algorithm_version, human_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["proposal_id"],
                result["decision"],
                result["evidence_count"],
                result["independent_source_count"],
                result["approved_evidence_count"],
                result["proposed_normalized_value"],
                result["canonical_unit"],
                result.get("aggregate_low"),
                result.get("aggregate_base"),
                result.get("aggregate_high"),
                result["range_position"],
                int(bool(result.get("conflict_flag"))),
                float(result.get("average_applicability", 0)),
                int(bool(result.get("physical_check_passed"))),
                json.dumps(result.get("evidence_ids", []), ensure_ascii=False),
                result["sensitivity_priority"],
                json.dumps(result.get("reasons", []), ensure_ascii=False),
                result.get("algorithm_version", "quant_validation_v0.3"),
                "pending",
                _now(),
            ),
        )
        connection.execute(
            "UPDATE proposed_parameters SET status='validated', updated_at=? WHERE proposal_id=?",
            (_now(), result["proposal_id"]),
        )
    return int(cursor.lastrowid)


def review_parameter_validation(
    path: str | Path,
    validation_id: int,
    decision: str,
    reviewer: str,
    *,
    low: float | None = None,
    base: float | None = None,
    high: float | None = None,
    notes: str | None = None,
) -> int | None:
    if decision not in {"approve", "reject", "modify"}:
        raise ValueError("Validation decision must be approve, reject or modify")
    timestamp = _now()
    with connect(path) as connection:
        row = connection.execute(
            """
            SELECT v.*, p.parameter_id, p.scenario_id, p.context_location,
                   p.technology, p.proposed_value
            FROM parameter_validations v
            JOIN proposed_parameters p ON p.proposal_id=v.proposal_id
            WHERE v.validation_id=?
            """,
            (validation_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown validation_id: {validation_id}")
        if decision == "approve" and row["decision"] != "supported":
            raise ValueError(
                "Only an algorithmically supported result can be approved unchanged; "
                "use modify with documented values or reject"
            )
        if decision == "modify" and not notes:
            raise ValueError("Modified validation decisions require reviewer notes")
        connection.execute(
            """
            UPDATE parameter_validations
            SET human_status=?, reviewer=?, reviewed_at=?, review_notes=?
            WHERE validation_id=?
            """,
            (decision, reviewer, timestamp, notes, validation_id),
        )
        if decision == "reject":
            connection.execute(
                "UPDATE proposed_parameters SET status='rejected', updated_at=? WHERE proposal_id=?",
                (timestamp, row["proposal_id"]),
            )
            return None

        if row["aggregate_low"] is None or row["aggregate_high"] is None:
            if low is None or base is None or high is None:
                raise ValueError("Approving a validation without an evidence range requires low, base and high")
        selected_low = float(low if low is not None else row["aggregate_low"])
        default_base = (
            row["proposed_normalized_value"] if decision == "approve" else row["aggregate_base"]
        )
        selected_base = float(base if base is not None else default_base)
        selected_high = float(high if high is not None else row["aggregate_high"])
        if not selected_low <= selected_base <= selected_high:
            raise ValueError("Expected low <= base <= high")
        evidence_ids = json.loads(row["evidence_ids_json"] or "[]")
        existing = connection.execute(
            """
            SELECT version FROM scenario_parameters
            WHERE scenario_id=? AND parameter_id=? AND context_location=? AND technology=?
            """,
            (row["scenario_id"], row["parameter_id"], row["context_location"], row["technology"]),
        ).fetchone()
        version = int(existing["version"]) + 1 if existing else 1
        connection.execute(
            """
            INSERT INTO scenario_parameters(
                scenario_id, parameter_id, context_location, technology,
                low_value, base_value, high_value, selected_value,
                canonical_unit, validation_id, evidence_ids_json,
                approval_status, approved_by, approved_at, version, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scenario_id, parameter_id, context_location, technology)
            DO UPDATE SET
                low_value=excluded.low_value, base_value=excluded.base_value,
                high_value=excluded.high_value, selected_value=excluded.selected_value,
                canonical_unit=excluded.canonical_unit,
                validation_id=excluded.validation_id,
                evidence_ids_json=excluded.evidence_ids_json,
                approval_status=excluded.approval_status,
                approved_by=excluded.approved_by, approved_at=excluded.approved_at,
                version=excluded.version, notes=excluded.notes
            """,
            (
                row["scenario_id"],
                row["parameter_id"],
                row["context_location"],
                row["technology"],
                selected_low,
                selected_base,
                selected_high,
                selected_base,
                row["canonical_unit"],
                validation_id,
                json.dumps([int(item) for item in evidence_ids]),
                "approved" if decision == "approve" else "modified",
                reviewer,
                timestamp,
                version,
                notes,
            ),
        )
        scenario_row = connection.execute(
            """
            SELECT scenario_parameter_id FROM scenario_parameters
            WHERE scenario_id=? AND parameter_id=? AND context_location=? AND technology=?
            """,
            (row["scenario_id"], row["parameter_id"], row["context_location"], row["technology"]),
        ).fetchone()
        connection.execute(
            "UPDATE proposed_parameters SET status='approved', updated_at=? WHERE proposal_id=?",
            (timestamp, row["proposal_id"]),
        )
    return int(scenario_row["scenario_parameter_id"])


def scenario_parameter_rows(
    path: str | Path,
    scenario_id: str,
    context_location: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["scenario_id=?", "approval_status IN ('approved', 'modified')"]
    values: list[Any] = [scenario_id]
    if context_location is not None:
        clauses.append("context_location=?")
        values.append(context_location)
    with connect(path) as connection:
        rows = connection.execute(
            f"""
            SELECT scenario_parameter_id, scenario_id, parameter_id,
                   context_location, technology, low_value, base_value,
                   high_value, selected_value, canonical_unit, validation_id,
                   evidence_ids_json, approval_status, approved_by,
                   approved_at, version, notes
            FROM scenario_parameters
            WHERE {" AND ".join(clauses)}
            ORDER BY parameter_id
            """,
            values,
        ).fetchall()
    return [dict(row) for row in rows]


def create_model_run(
    path: str | Path,
    scenario_id: str,
    model_name: str,
    *,
    context_location: str | None = None,
    model_version: str | None = None,
    notes: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    inputs = scenario_parameter_rows(path, scenario_id, context_location)
    if not inputs:
        raise ValueError(f"No approved scenario parameters for scenario_id={scenario_id}")
    snapshot = [
        {
            "scenario_parameter_id": row["scenario_parameter_id"],
            "parameter_id": row["parameter_id"],
            "value": row["selected_value"],
            "unit": row["canonical_unit"],
            "version": row["version"],
        }
        for row in inputs
    ]
    checksum = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    identifier = run_id or f"run_{uuid.uuid4().hex[:12]}"
    timestamp = _now()
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO model_runs(
                run_id, scenario_id, context_location, model_name, model_version,
                status, input_snapshot_sha256, started_at, notes
            ) VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?)
            """,
            (
                identifier,
                scenario_id,
                context_location or "",
                model_name,
                model_version,
                checksum,
                timestamp,
                notes,
            ),
        )
        connection.executemany(
            """
            INSERT INTO model_inputs(
                run_id, scenario_parameter_id, parameter_id, value, unit
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    identifier,
                    row["scenario_parameter_id"],
                    row["parameter_id"],
                    row["selected_value"],
                    row["canonical_unit"],
                )
                for row in inputs
            ],
        )
    return {
        "run_id": identifier,
        "scenario_id": scenario_id,
        "input_count": len(inputs),
        "input_snapshot_sha256": checksum,
        "status": "created",
    }


def insert_model_outputs(
    path: str | Path,
    run_id: str,
    outputs: Iterable[dict[str, Any]],
    *,
    complete: bool = True,
) -> int:
    rows = list(outputs)
    with connect(path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM model_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if not exists:
            raise KeyError(f"Unknown run_id: {run_id}")
        for item in rows:
            connection.execute(
                """
                INSERT INTO model_outputs(
                    run_id, metric_id, metric_category, value, unit, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, metric_id)
                DO UPDATE SET
                    metric_category=excluded.metric_category,
                    value=excluded.value, unit=excluded.unit,
                    raw_json=excluded.raw_json
                """,
                (
                    run_id,
                    item["metric_id"],
                    item.get("metric_category"),
                    float(item["value"]),
                    item["unit"],
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        connection.execute(
            """
            UPDATE model_runs
            SET status=?, completed_at=?
            WHERE run_id=?
            """,
            ("completed" if complete else "results_partial", _now() if complete else None, run_id),
        )
    return len(rows)
