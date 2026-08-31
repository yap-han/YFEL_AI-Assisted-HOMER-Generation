from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .evidence import build_ai_extraction_task
from .llm import LLMResponseError, validate_llm_response
from .ontology import load_json
from .providers import (
    MistralOCRConnector,
    OpenAIResponsesConnector,
    ProviderCall,
    ProviderConfigurationError,
    ProviderRequestError,
)
from .validation import prepare_observation


PIPELINE_VERSION = "0.6"
SCREENING_PROMPT_VERSION = "luna_relevance_v0.6"
EXTRACTION_PROMPT_VERSION = "terra_evidence_v0.6"


class PipelineStopped(RuntimeError):
    def __init__(self, reason: str, report: Path, *, status: str = "blocked") -> None:
        super().__init__(reason)
        self.report = report
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _resolve(base: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_advanced_config(path: str | Path) -> tuple[dict[str, Any], Path]:
    config_path = Path(path).resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "2.0":
        raise ValueError("Advanced pipeline requires schema_version '2.0'")
    if (payload.get("screening") or {}).get("model") != "gpt-5.6-luna":
        raise ValueError("screening.model must be gpt-5.6-luna for this study protocol")
    if (payload.get("extraction") or {}).get("model") != "gpt-5.6-terra":
        raise ValueError("extraction.model must be gpt-5.6-terra for this study protocol")
    return payload, config_path


def _stop(output: Path, status: str, blockers: list[dict[str, str]], completed: list[str]) -> None:
    report_json = _write_json(
        output / "PIPELINE_STATUS.json",
        {
            "pipeline_version": PIPELINE_VERSION,
            "status": status,
            "stopped_at": _now(),
            "completed_stages": completed,
            "blockers": blockers,
            "fixture_fallback_used": False,
        },
    )
    lines = ["# Pipeline stopped", "", f"Status: `{status}`", "", "No fixture or synthetic result was substituted.", "", "## Required actions", ""]
    for item in blockers:
        lines.append(f"- **{item['code']}**: {item['message']}")
    report_md = output / "PIPELINE_STOPPED.md"
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raise PipelineStopped(blockers[0]["message"], report_md if report_md.exists() else report_json, status=status)


def _load_corpus(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "2.0":
        raise ValueError("22-paper OCR corpus manifest requires schema_version '2.0'")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("Corpus manifest requires documents")
    ids = [str(item.get("source_id") or "") for item in documents]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("Every corpus record requires a unique source_id")
    return payload


def _source_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["source_id"],
        "title": item["title"],
        "authors": item.get("authors", []),
        "year": item.get("year"),
        "doi": item.get("doi"),
        "journal": item.get("journal"),
        "source_type": item.get("source_type", "journal_article"),
        "peer_reviewed": bool(item.get("peer_reviewed", True)),
        "open_access": item.get("open_access"),
        "region_scope": item.get("region_scope", "global"),
        "countries": item.get("countries", []),
        "system_types": item.get("system_types", []),
        "parameter_families": item.get("parameter_families", []),
        "url": item.get("url"),
        "evidence_level": "ocr_full_text",
        "relevance_note": item.get("relevance_note"),
        "verification_status": "manifest_registered",
        "quality_score": float(item.get("quality_score", 80)),
    }


def _parameter_definitions(database: Path, parameter_ids: list[str]) -> list[dict[str, Any]]:
    return [db.get_parameter_definition(database, value) for value in parameter_ids]


def _preflight(
    config: dict[str, Any],
    base: Path,
    output: Path,
    corpus: dict[str, Any],
) -> tuple[OpenAIResponsesConnector, OpenAIResponsesConnector, MistralOCRConnector, list[dict[str, Any]]]:
    blockers: list[dict[str, str]] = []
    for env_name, provider in (("OPENAI_API_KEY", "OpenAI"), ("MISTRAL_API_KEY", "Mistral")):
        if not os.environ.get(env_name):
            blockers.append(
                {
                    "code": f"missing_{env_name.lower()}",
                    "message": f"Set {env_name} to an active {provider} API key with billing and required model access.",
                }
            )
    expected = int((config.get("corpus") or {}).get("expected_documents", 22))
    if len(corpus["documents"]) != expected:
        blockers.append(
            {
                "code": "corpus_count_mismatch",
                "message": f"Corpus manifest contains {len(corpus['documents'])} documents; expected {expected}.",
            }
        )
    pdf_root = _resolve(base, (config.get("corpus") or {}).get("pdf_root"))
    pdf_rows: list[dict[str, Any]] = []
    for item in corpus["documents"]:
        pdf = _resolve(pdf_root or base, item.get("pdf_file"))
        row = {**item, "pdf_path": str(pdf)}
        pdf_rows.append(row)
        if item.get("allowed_to_process") is not True or not str(item.get("rights_note") or "").strip():
            blockers.append(
                {
                    "code": "document_rights_unconfirmed",
                    "message": f"Confirm allowed_to_process=true and add a rights_note for {item['source_id']}.",
                }
            )
        if pdf is None or not pdf.is_file():
            blockers.append(
                {
                    "code": "missing_original_pdf",
                    "message": f"Add the lawfully obtained original PDF for {item['source_id']} at {pdf}.",
                }
            )
    if blockers:
        _stop(output, "blocked_preflight", blockers, [])

    try:
        luna = OpenAIResponsesConnector(config["screening"])
        terra = OpenAIResponsesConnector(config["extraction"])
        ocr = MistralOCRConnector(config["ocr"])
    except ProviderConfigurationError as exc:
        _stop(output, "blocked_provider_configuration", [{"code": "provider_configuration", "message": str(exc)}], [])

    # Two tiny real calls verify account/model access before the 22 OCR requests.
    probe_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"ready": {"type": "boolean"}},
        "required": ["ready"],
    }
    calls: list[dict[str, Any]] = []
    try:
        for connector, role in ((luna, "screening"), (terra, "extraction")):
            result = connector.structured(
                operation=f"{role}_preflight",
                prompt_version="provider_preflight_v0.6",
                system_prompt="Return ready=true. This request verifies model access only.",
                evidence_payload={"probe": "model_access"},
                output_schema=probe_schema,
                schema_name=f"{role}_provider_probe",
            )
            if result.payload != {"ready": True}:
                raise ProviderRequestError(f"{role} model returned an unexpected preflight result")
            calls.append(result.call.to_dict())
    except ProviderRequestError as exc:
        _stop(
            output,
            "blocked_llm_unavailable",
            [{"code": "llm_connection_failed", "message": f"OpenAI model preflight failed: {exc}"}],
            [],
        )
    _write_json(output / "preflight_calls.json", calls)
    return luna, terra, ocr, pdf_rows


def _table_text(table: Any, index: int) -> str:
    if isinstance(table, str):
        return table
    if not isinstance(table, dict):
        return str(table)
    content = table.get("content") or table.get("markdown") or table.get("html") or ""
    table_id = table.get("id") or f"table-{index + 1}"
    return f"\n[TABLE {table_id}]\n{content}\n[/TABLE {table_id}]"


def _ocr_pages(response: dict[str, Any]) -> list[str]:
    pages: list[str] = []
    for page in response.get("pages") or []:
        text = str(page.get("markdown") or "")
        tables = page.get("tables") or []
        if tables:
            text += "\n" + "\n".join(_table_text(table, index) for index, table in enumerate(tables))
        blocks = page.get("blocks") or []
        if blocks:
            labels = [str(block.get("type") or block.get("label") or "unknown") for block in blocks if isinstance(block, dict)]
            text += "\n[OCR BLOCK LABELS: " + ", ".join(labels) + "]"
        pages.append(text)
    return pages


def chunk_pages(document_id: int, pages: list[dict[str, Any]], *, max_characters: int, overlap_characters: int) -> list[dict[str, Any]]:
    if max_characters < 500 or overlap_characters < 0 or overlap_characters >= max_characters:
        raise ValueError("chunking requires max_characters >= 500 and 0 <= overlap < max")
    chunks: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page["page_number"])
        text = str(page["page_text"])
        start = 0
        part = 1
        while start < len(text):
            tentative_end = min(start + max_characters, len(text))
            end = tentative_end
            if tentative_end < len(text):
                boundary = text.rfind("\n", start + max_characters // 2, tentative_end)
                if boundary > start:
                    end = boundary
            excerpt = text[start:end]
            chunk_id = f"d{document_id:04d}-p{page_number:04d}-c{part:03d}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "page_number": page_number,
                    "character_start": start,
                    "character_end": end,
                    "text": excerpt,
                    "sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                }
            )
            if end >= len(text):
                break
            start = max(end - overlap_characters, start + 1)
            part += 1
    return chunks


def _screening_schema(chunks: list[dict[str, Any]], parameter_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "relevant_chunks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "chunk_id": {"type": "string", "enum": [item["chunk_id"] for item in chunks]},
                        "parameter_ids": {"type": "array", "items": {"type": "string", "enum": parameter_ids}, "minItems": 1},
                        "relevance_score": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["chunk_id", "parameter_ids", "relevance_score", "reason"],
                },
            }
        },
        "required": ["relevant_chunks"],
    }


def _validate_screening(payload: dict[str, Any], chunks: list[dict[str, Any]], parameter_ids: list[str], threshold: float) -> list[dict[str, Any]]:
    if set(payload) != {"relevant_chunks"} or not isinstance(payload["relevant_chunks"], list):
        raise ValueError("screening root must contain only relevant_chunks array")
    chunk_ids = {item["chunk_id"] for item in chunks}
    parameters = set(parameter_ids)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(payload["relevant_chunks"]):
        if not isinstance(item, dict):
            raise ValueError(f"relevant_chunks[{index}] is not an object")
        chunk_id = item.get("chunk_id")
        score = item.get("relevance_score")
        ids = item.get("parameter_ids")
        if chunk_id not in chunk_ids or chunk_id in seen:
            raise ValueError(f"invalid or duplicate chunk_id {chunk_id!r}")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
            raise ValueError(f"invalid relevance score for {chunk_id}")
        if not isinstance(ids, list) or not ids or not set(ids).issubset(parameters):
            raise ValueError(f"invalid parameter IDs for {chunk_id}")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError(f"missing screening reason for {chunk_id}")
        seen.add(str(chunk_id))
        if float(score) >= threshold:
            selected.append(item)
    return selected


def _selected_pages(chunks: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_ids = {item["chunk_id"] for item in decisions}
    grouped: dict[int, list[str]] = defaultdict(list)
    for chunk in chunks:
        if chunk["chunk_id"] in selected_ids:
            grouped[int(chunk["page_number"])].append(
                f"[CHUNK {chunk['chunk_id']}]\n{chunk['text']}\n[/CHUNK {chunk['chunk_id']}]"
            )
    return [
        {"page_number": page, "page_text": "\n".join(grouped[page])}
        for page in sorted(grouped)
    ]


def _retry_structured(
    connector: OpenAIResponsesConnector,
    *,
    operation: str,
    prompt_version: str,
    system_prompt: str,
    evidence_payload: dict[str, Any],
    output_schema: dict[str, Any],
    schema_name: str,
    validator: Any,
    max_retries: int,
) -> tuple[Any, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    feedback: list[str] | None = None
    evidence_hash = _hash(evidence_payload)
    for attempt in range(1, max_retries + 2):
        result = connector.structured(
            operation=operation,
            prompt_version=prompt_version,
            system_prompt=system_prompt,
            evidence_payload=evidence_payload,
            output_schema=output_schema,
            schema_name=schema_name,
            attempt=attempt,
            correction_feedback=feedback,
        )
        call = result.call.to_dict()
        call["immutable_evidence_sha256"] = evidence_hash
        try:
            validated = validator(result.payload, result.raw_text)
            call["validation_status"] = "valid"
            calls.append(call)
            return validated, calls
        except (ValueError, LLMResponseError) as exc:
            feedback = [str(exc)]
            call["validation_status"] = "invalid"
            call["validation_errors"] = feedback
            calls.append(call)
    raise ProviderRequestError(f"{operation} remained invalid after {max_retries + 1} attempts: {feedback}")


def _existing_observation(database: Path, candidate: dict[str, Any]) -> int | None:
    with db.connect(database) as connection:
        row = connection.execute(
            """
            SELECT observation_id FROM evidence_observations
            WHERE parameter_id=? AND source_id=? AND locator=?
              AND COALESCE(raw_value_text, '')=COALESCE(?, '')
            LIMIT 1
            """,
            (candidate["parameter_id"], candidate["source_id"], candidate["locator"], candidate.get("raw_value_text")),
        ).fetchone()
    return int(row[0]) if row else None


def _boundary_class(parameter_id: str, text: str | None, taxonomy: dict[str, Any]) -> str:
    lowered = (text or "").lower()
    rules = (taxonomy.get("parameters") or {}).get(parameter_id) or taxonomy.get("default") or {}
    for category, terms in rules.items():
        if any(str(term).lower() in lowered for term in terms):
            return category
    return "unclear"


def aggregate_by_boundary(database: Path, observation_ids: list[int], taxonomy: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    if not observation_ids:
        return []
    placeholders = ",".join("?" for _ in observation_ids)
    with db.connect(database) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM evidence_observations WHERE observation_id IN ({placeholders})",
                observation_ids,
            )
        ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        boundary = _boundary_class(row["parameter_id"], row.get("system_boundary"), taxonomy)
        grouped[(row["parameter_id"], boundary)].append(row)
    targets = {item["parameter_id"]: item for item in scenario.get("required_parameters", [])}
    output: list[dict[str, Any]] = []
    for (parameter_id, boundary), values in sorted(grouped.items()):
        target = targets.get(parameter_id) or {}
        centers = [float(row["normalized_value_central"]) for row in values if row["physical_check_passed"]]
        if not centers:
            continue
        independent = len({row["independent_source_key"] for row in values})
        required_boundary = target.get("system_boundary")
        compatible = bool(required_boundary and boundary == required_boundary)
        status = "provisional_candidate" if compatible and independent >= 2 else "not_selected"
        reasons = []
        if not compatible:
            reasons.append(f"boundary '{boundary}' does not match required '{required_boundary}'")
        if independent < 2:
            reasons.append("fewer than two independent sources")
        if status == "provisional_candidate":
            reasons.append("median selected within a matching boundary; human approval still required")
        output.append(
            {
                "parameter_id": parameter_id,
                "boundary_class": boundary,
                "required_boundary": required_boundary,
                "observation_count": len(values),
                "independent_source_count": independent,
                "evidence_low": min(float(row["normalized_value_min"]) for row in values),
                "provisional_value": statistics.median(centers),
                "evidence_high": max(float(row["normalized_value_max"]) for row in values),
                "canonical_unit": values[0]["canonical_unit"],
                "selection_status": status,
                "selection_justification": "; ".join(reasons),
                "observation_ids": [int(row["observation_id"]) for row in values],
            }
        )
    return output


REVIEW_FIELDS = [
    "observation_id", "parameter_id", "source_id", "raw_value_text", "raw_value_central",
    "raw_unit", "page_number", "locator", "context_excerpt", "system_boundary",
    "numerical_correct", "semantic_correct", "quotation_correct", "boundary_correct",
    "reviewer", "review_notes",
]


def export_review_sample(database: Path, observation_ids: list[int], path: Path, sample_size: int) -> Path:
    if observation_ids:
        placeholders = ",".join("?" for _ in observation_ids)
        with db.connect(database) as connection:
            rows = [dict(row) for row in connection.execute(
                f"SELECT * FROM evidence_observations WHERE observation_id IN ({placeholders}) ORDER BY parameter_id, source_id, observation_id",
                observation_ids,
            )]
    else:
        rows = []
    # Deterministic spread: one row per parameter first, then fill in stable order.
    selected: list[dict[str, Any]] = []
    used: set[int] = set()
    for parameter in sorted({row["parameter_id"] for row in rows}):
        row = next(item for item in rows if item["parameter_id"] == parameter)
        selected.append(row)
        used.add(int(row["observation_id"]))
    for row in rows:
        if len(selected) >= sample_size:
            break
        if int(row["observation_id"]) not in used:
            selected.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for row in selected[:sample_size]:
            writer.writerow({field: row.get(field, "") for field in REVIEW_FIELDS})
    return path


def evaluate_review(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {"status": "incomplete", "reviewed": 0, "reason": "review file has no rows"}
    dimensions = ["numerical_correct", "semantic_correct", "quotation_correct", "boundary_correct"]
    allowed = {"yes", "no"}
    complete = [row for row in rows if row.get("reviewer", "").strip() and all(row.get(key, "").strip().lower() in allowed for key in dimensions)]
    if len(complete) != len(rows):
        return {
            "status": "incomplete",
            "reviewed": len(complete),
            "required": len(rows),
            "reason": "every row requires a named reviewer and yes/no for all four correctness fields",
        }
    metrics = {key: sum(row[key].strip().lower() == "yes" for row in complete) / len(complete) for key in dimensions}
    exact = sum(all(row[key].strip().lower() == "yes" for key in dimensions) for row in complete) / len(complete)
    return {"status": "completed", "reviewed": len(complete), **metrics, "all_dimensions_correct": exact}


def scenario_coverage(aggregates: list[dict[str, Any]], scenario: dict[str, Any]) -> dict[str, Any]:
    selected = {row["parameter_id"] for row in aggregates if row["selection_status"] == "provisional_candidate"}
    rows = []
    for item in scenario.get("required_parameters", []):
        parameter_id = item["parameter_id"]
        evidence_role = item.get("evidence_role", "literature_supported")
        if evidence_role == "site_data_required":
            status = "site_data_required"
        elif evidence_role == "researcher_assumption":
            status = "researcher_input_required"
        else:
            status = "provisional_evidence_available" if parameter_id in selected else "evidence_gap"
        rows.append({**item, "coverage_status": status})
    complete = all(
        row["coverage_status"] == "provisional_evidence_available"
        for row in rows
        if row.get("evidence_role") == "literature_supported"
    )
    return {
        "required_parameter_count": len(rows),
        "literature_parameter_count": sum(row.get("evidence_role") == "literature_supported" for row in rows),
        "site_data_parameter_count": sum(row.get("evidence_role") == "site_data_required" for row in rows),
        "researcher_assumption_count": sum(row.get("evidence_role") == "researcher_assumption" for row in rows),
        "literature_coverage_complete": complete,
        "parameters": rows,
    }


def compare_mix_results(baseline_path: Path, provisional_path: Path) -> dict[str, Any]:
    def load(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("architectures")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{path} requires a non-empty architectures array")
        for row in rows:
            if not isinstance(row.get("npc_usd"), (int, float)) or not row.get("architecture_id"):
                raise ValueError(f"Every architecture in {path} requires architecture_id and npc_usd")
        preferred = min(rows, key=lambda row: float(row["npc_usd"]))
        return payload, preferred

    baseline, base_best = load(baseline_path)
    provisional, provisional_best = load(provisional_path)
    return {
        "baseline_scenario": baseline.get("scenario_id"),
        "provisional_scenario": provisional.get("scenario_id"),
        "baseline_preferred_architecture": base_best["architecture_id"],
        "provisional_preferred_architecture": provisional_best["architecture_id"],
        "preferred_mix_changed": base_best["architecture_id"] != provisional_best["architecture_id"],
        "baseline_npc_usd": float(base_best["npc_usd"]),
        "provisional_npc_usd": float(provisional_best["npc_usd"]),
        "baseline_renewable_fraction": base_best.get("renewable_fraction"),
        "provisional_renewable_fraction": provisional_best.get("renewable_fraction"),
        "method": "comparison of independently exported HOMER architecture rankings; no surrogate optimization",
    }


def _telemetry_summary(calls: list[dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    for call in calls:
        key = str(call["model"])
        row = by_model.setdefault(key, {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "latency_seconds": 0.0, "estimated_cost_usd": 0.0})
        row["calls"] += 1
        for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
            row[field] += int(call.get(field) or 0)
        row["latency_seconds"] += float(call.get("latency_seconds") or 0)
        row["estimated_cost_usd"] += float(call.get("estimated_cost_usd") or 0)
    return {
        "calls": len(calls),
        "models": by_model,
        "total_input_tokens": sum(int(call.get("input_tokens") or 0) for call in calls),
        "total_output_tokens": sum(int(call.get("output_tokens") or 0) for call in calls),
        "total_latency_seconds": sum(float(call.get("latency_seconds") or 0) for call in calls),
        "total_estimated_cost_usd": sum(float(call.get("estimated_cost_usd") or 0) for call in calls),
    }


def run_advanced_pipeline(config_path: str | Path, *, reset: bool = False) -> dict[str, Any]:
    config, config_file = load_advanced_config(config_path)
    base = config_file.parent
    output = _resolve(base, config.get("output_dir") or "../output/advanced_pipeline")
    output.mkdir(parents=True, exist_ok=True)
    corpus_path = _resolve(base, (config.get("corpus") or {}).get("manifest"))
    corpus = _load_corpus(corpus_path)
    completed: list[str] = []
    luna, terra, ocr, pdf_rows = _preflight(config, base, output, corpus)
    completed.append("provider_and_corpus_preflight")

    ontology_path = _resolve(base, (config.get("context") or {}).get("ontology", "ontology.json"))
    profile_path = _resolve(base, (config.get("context") or {}).get("profile", "study_profile.json"))
    scenario_path = _resolve(base, config.get("scenario_requirements"))
    boundary_path = _resolve(base, config.get("boundary_taxonomy"))
    ontology = load_json(ontology_path)
    profile = load_json(profile_path)
    scenario = load_json(scenario_path)
    taxonomy = load_json(boundary_path)
    parameter_ids = [str(item["parameter_id"]) for item in scenario.get("required_parameters", [])]
    database = _resolve(base, config.get("database") or "../output/advanced_pipeline/evidence.sqlite")
    if reset and database.exists():
        database.unlink()
    db.initialize(database, ontology, profile)
    parameters = _parameter_definitions(database, parameter_ids)
    db.insert_sources(database, [_source_record(item) for item in corpus["documents"]])

    telemetry: list[dict[str, Any]] = json.loads((output / "preflight_calls.json").read_text(encoding="utf-8"))
    document_rows: list[dict[str, Any]] = []
    ocr_dir = output / "ocr"
    for item in pdf_rows:
        pdf = Path(item["pdf_path"])
        pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        cache = ocr_dir / f"{item['source_id']}.json"
        cached: dict[str, Any] | None = None
        if cache.is_file():
            candidate = json.loads(cache.read_text(encoding="utf-8"))
            if candidate.get("pdf_sha256") == pdf_sha:
                cached = candidate
        if cached:
            response = cached["ocr_response"]
        else:
            try:
                response, call = ocr.process_pdf(pdf)
            except (ProviderConfigurationError, ProviderRequestError) as exc:
                _stop(output, "blocked_ocr_request", [{"code": "ocr_request_failed", "message": str(exc)}], completed)
            telemetry.append(call.to_dict())
            _write_json(cache, {"pdf_sha256": pdf_sha, "ocr_response": response, "telemetry": call.to_dict()})
        pages = _ocr_pages(response)
        document = {
            "pages": pages,
            "file_name": pdf.name,
            "file_path": str(pdf),
            "mime_type": "application/pdf",
            "sha256": pdf_sha,
            "ingestion_method": f"mistral_ocr:{response.get('model') or ocr.model_name}:tables=markdown:blocks=true",
        }
        document_id = db.insert_full_text_document(
            database,
            document,
            source_id=item["source_id"],
            title=item["title"],
            doi=item.get("doi"),
            metadata={"ocr_cache": str(cache), "table_format": "markdown", "include_blocks": True},
        )
        _, db_pages = db.get_document(database, document_id)
        document_rows.append({**item, "document_id": document_id, "pages": db_pages})
    completed.append("ocr_and_structured_table_extraction")

    chunk_config = config.get("chunking") or {}
    max_chars = int(chunk_config.get("max_characters", 6000))
    overlap = int(chunk_config.get("overlap_characters", 600))
    threshold = float((config.get("screening") or {}).get("relevance_threshold", 0.65))
    max_retries = int(config.get("max_retries", 2))
    imported_ids: list[int] = []
    per_document: list[dict[str, Any]] = []
    llm_dir = output / "llm"
    for row in document_rows:
        chunks = chunk_pages(row["document_id"], row["pages"], max_characters=max_chars, overlap_characters=overlap)
        screening_task = {
            "document_id": row["document_id"],
            "document_title": row["title"],
            "instructions": "Return only chunks containing explicit quantitative evidence for at least one requested parameter. Bibliography-only matches are irrelevant.",
            "parameters": [
                {"parameter_id": p["id"], "label": p["label"], "description": p.get("description"), "extraction_terms": p.get("extraction_terms", [])}
                for p in parameters
            ],
            "chunks": chunks,
        }
        screen_cache = llm_dir / f"{row['source_id']}_screening.json"
        screen_hash = _hash(screening_task)
        if screen_cache.is_file() and json.loads(screen_cache.read_text(encoding="utf-8")).get("task_sha256") == screen_hash:
            cached = json.loads(screen_cache.read_text(encoding="utf-8"))
            decisions = _validate_screening(cached["payload"], chunks, parameter_ids, threshold)
        else:
            def screen_validator(payload: dict[str, Any], _raw: str) -> list[dict[str, Any]]:
                return _validate_screening(payload, chunks, parameter_ids, threshold)

            try:
                decisions, calls = _retry_structured(
                    luna,
                    operation="parameter_relevance_screening",
                    prompt_version=SCREENING_PROMPT_VERSION,
                    system_prompt="Screen evidence chunks conservatively. Select a chunk only when its own text contains quantitative evidence applicable to a requested ontology parameter. Do not extract numbers.",
                    evidence_payload=screening_task,
                    output_schema=_screening_schema(chunks, parameter_ids),
                    schema_name="parameter_relevance_screening",
                    validator=screen_validator,
                    max_retries=max_retries,
                )
            except ProviderRequestError as exc:
                _stop(output, "blocked_luna_screening", [{"code": "luna_screening_failed", "message": str(exc)}], completed)
            telemetry.extend(calls)
            payload = {"relevant_chunks": decisions}
            _write_json(screen_cache, {"task_sha256": screen_hash, "payload": payload, "calls": calls})

        selected_pages = _selected_pages(chunks, decisions)
        selected_parameter_ids = sorted({pid for item in decisions for pid in item["parameter_ids"]})
        observations: list[dict[str, Any]] = []
        extraction_calls: list[dict[str, Any]] = []
        if selected_pages and selected_parameter_ids:
            selected_parameters = [p for p in parameters if p["id"] in selected_parameter_ids]
            extraction_task = build_ai_extraction_task(row["document_id"], row["title"], selected_pages, selected_parameters)
            extraction_task["screening_provenance"] = {
                "model": luna.model_name,
                "prompt_version": SCREENING_PROMPT_VERSION,
                "selected_chunks": decisions,
            }
            extract_cache = llm_dir / f"{row['source_id']}_extraction.json"
            extract_hash = _hash(extraction_task)
            if extract_cache.is_file() and json.loads(extract_cache.read_text(encoding="utf-8")).get("task_sha256") == extract_hash:
                cached = json.loads(extract_cache.read_text(encoding="utf-8"))
                observations = validate_llm_response(extraction_task, json.dumps(cached["payload"], ensure_ascii=False))
            else:
                def extraction_validator(payload: dict[str, Any], _raw: str) -> list[dict[str, Any]]:
                    return validate_llm_response(extraction_task, json.dumps(payload, ensure_ascii=False))

                try:
                    observations, extraction_calls = _retry_structured(
                        terra,
                        operation="quantitative_evidence_extraction",
                        prompt_version=EXTRACTION_PROMPT_VERSION,
                        system_prompt="Extract only explicitly reported quantitative evidence. Every number needs a verbatim quotation and page plus table/figure/section/paragraph/line locator. Preserve the stated system boundary. Never infer or calculate missing values.",
                        evidence_payload=extraction_task,
                        output_schema=extraction_task["output_schema"],
                        schema_name="quantitative_evidence_observations",
                        validator=extraction_validator,
                        max_retries=max_retries,
                    )
                except ProviderRequestError as exc:
                    _stop(output, "blocked_terra_extraction", [{"code": "terra_extraction_failed", "message": str(exc)}], completed)
                telemetry.extend(extraction_calls)
                _write_json(extract_cache, {"task_sha256": extract_hash, "payload": {"observations": observations}, "calls": extraction_calls})

        document_observation_ids: list[int] = []
        for observation in observations:
            candidate = {
                **observation,
                "document_id": row["document_id"],
                "source_id": row["source_id"],
                "source_title": row["title"],
                "source_doi": row.get("doi"),
                "independent_source_key": row.get("doi") or row["source_id"],
                "extraction_method": f"{EXTRACTION_PROMPT_VERSION}:{terra.model_name};screen={luna.model_name}",
                "verification_status": "candidate",
                "authoritative_source": False,
            }
            existing = _existing_observation(database, candidate)
            if existing:
                document_observation_ids.append(existing)
                imported_ids.append(existing)
                continue
            try:
                prepared = prepare_observation(
                    database,
                    candidate,
                    target_cost_year=(config.get("normalization") or {}).get("target_cost_year"),
                    annual_escalation_rate=(config.get("normalization") or {}).get("annual_escalation_rate"),
                )
                observation_id = db.insert_evidence_observation(database, prepared)
                document_observation_ids.append(observation_id)
                imported_ids.append(observation_id)
            except ValueError as exc:
                _write_json(llm_dir / f"{row['source_id']}_normalization_error.json", {"error": str(exc), "observation": candidate})
        per_document.append(
            {
                "source_id": row["source_id"],
                "document_id": row["document_id"],
                "chunks_total": len(chunks),
                "chunks_selected": len(decisions),
                "selected_parameter_count": len(selected_parameter_ids),
                "observations": len(document_observation_ids),
                "observation_ids": document_observation_ids,
            }
        )
    completed.extend(["luna_chunk_screening", "terra_schema_validated_extraction"])
    _write_json(output / "telemetry.json", {"calls": telemetry, "summary": _telemetry_summary(telemetry)})
    _write_json(output / "document_processing.json", per_document)

    review_config = config.get("human_review") or {}
    review_file = _resolve(base, review_config.get("file") or "../output/advanced_pipeline/HUMAN_REVIEW.csv")
    if not review_file.is_file():
        export_review_sample(database, sorted(set(imported_ids)), review_file, int(review_config.get("sample_size", 30)))
        _stop(
            output,
            "awaiting_human_review",
            [{"code": "human_review_required", "message": f"Manually complete {review_file} and rerun the same command."}],
            completed,
        )
    review_metrics = evaluate_review(review_file)
    _write_json(output / "human_review_metrics.json", review_metrics)
    if review_metrics["status"] != "completed":
        _stop(output, "awaiting_human_review", [{"code": "human_review_incomplete", "message": review_metrics["reason"]}], completed)
    completed.append("manual_observation_review")

    aggregates = aggregate_by_boundary(database, sorted(set(imported_ids)), taxonomy, scenario)
    _write_json(output / "boundary_aggregates.json", aggregates)
    coverage = scenario_coverage(aggregates, scenario)
    _write_json(output / "scenario_coverage.json", coverage)
    completed.extend(["boundary_aware_aggregation", "complete_homer_scenario_coverage"])

    mix = config.get("mix_impact") or {}
    baseline = _resolve(base, mix.get("baseline_results"))
    provisional = _resolve(base, mix.get("provisional_results"))
    missing = [str(path) for path in (baseline, provisional) if path is None or not path.is_file()]
    if missing:
        _stop(
            output,
            "awaiting_homer_results",
            [{"code": "homer_results_required", "message": "Export baseline and evidence-updated HOMER architecture results to: " + ", ".join(missing)}],
            completed,
        )
    mix_impact = compare_mix_results(baseline, provisional)
    _write_json(output / "energy_mix_impact.json", mix_impact)
    completed.append("renewable_conventional_mix_impact")

    result = {
        "pipeline_version": PIPELINE_VERSION,
        "status": "completed",
        "completed_at": _now(),
        "documents_ingested": len(document_rows),
        "documents_with_observations": sum(bool(row["observations"]) for row in per_document),
        "chunks_total": sum(row["chunks_total"] for row in per_document),
        "chunks_selected": sum(row["chunks_selected"] for row in per_document),
        "observations": len(set(imported_ids)),
        "human_review_metrics": review_metrics,
        "scenario_coverage": coverage,
        "energy_mix_impact": mix_impact,
        "telemetry": _telemetry_summary(telemetry),
        "completed_stages": completed,
        "database": str(database),
        "output_dir": str(output),
    }
    _write_json(output / "PIPELINE_STATUS.json", result)
    return result
