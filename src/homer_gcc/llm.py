from __future__ import annotations

import hashlib
import csv
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from . import db
from .evidence import build_ai_extraction_task
from .normalization import canonicalize_unit
from .validation import prepare_observation


LLM_EXTRACTION_VERSION = "llm_extraction_v0.5"


class LLMConfigurationError(ValueError):
    pass


class LLMResponseError(ValueError):
    pass


class LLMConnector(Protocol):
    provider_name: str
    model_name: str

    def complete(self, task: dict[str, Any], feedback: list[str] | None = None) -> str:
        """Return one raw JSON response for an immutable extraction task."""


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def task_sha256(task: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(task).encode("utf-8")).hexdigest()


class OpenAICompatibleConnector:
    """Minimal standard-library connector for OpenAI-compatible chat endpoints.

    Secrets are read from an environment variable and are never accepted in the
    workflow JSON. The default endpoint uses Chat Completions because it is also
    implemented by many local and hosted compatible providers.
    """

    provider_name = "openai_compatible"

    def __init__(self, config: dict[str, Any]) -> None:
        self.model_name = str(config.get("model") or "").strip()
        if not self.model_name:
            raise LLMConfigurationError("LLM configuration requires model")
        self.endpoint = str(
            config.get("endpoint") or "https://api.openai.com/v1/chat/completions"
        ).strip()
        key_env = str(config.get("api_key_env") or "OPENAI_API_KEY")
        self.api_key = os.environ.get(key_env)
        if not self.api_key:
            raise LLMConfigurationError(
                f"Environment variable {key_env!r} is required for provider openai_compatible"
            )
        self.timeout_seconds = float(config.get("timeout_seconds", 120))
        self.temperature = float(config.get("temperature", 0))

    def complete(self, task: dict[str, Any], feedback: list[str] | None = None) -> str:
        correction = ""
        if feedback:
            correction = (
                "\nThe previous response was rejected for these schema or provenance errors:\n- "
                + "\n- ".join(feedback)
                + "\nReturn a corrected response using the unchanged evidence task below."
            )
        body = {
            "model": self.model_name,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You extract explicitly reported quantitative evidence. "
                        "Return JSON only. Never infer a value or fabricate a quotation, "
                        "page, table, figure, section, paragraph or line locator."
                    ),
                },
                {
                    "role": "user",
                    "content": correction + "\n" + json.dumps(task, ensure_ascii=False),
                },
            ],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMResponseError(f"LLM request failed: {exc}") from exc
        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("LLM response does not contain choices[0].message.content") from exc


class FixtureConnector:
    """Offline connector used to reproduce retry and schema tests without an API key."""

    provider_name = "fixture"
    model_name = "scripted-fixture"

    def __init__(self, config: dict[str, Any], *, config_base: Path | None = None) -> None:
        response_file = Path(str(config.get("response_file") or ""))
        if not response_file.is_absolute() and config_base:
            response_file = (config_base / response_file).resolve()
        if not response_file.is_file():
            raise LLMConfigurationError(f"Fixture response file does not exist: {response_file}")
        payload = json.loads(response_file.read_text(encoding="utf-8"))
        self.responses = {
            str(item["document_id"]): list(item["responses"])
            for item in payload.get("documents", [])
        }
        self.positions: dict[str, int] = {}

    def complete(self, task: dict[str, Any], feedback: list[str] | None = None) -> str:
        key = str(task["document_id"])
        responses = self.responses.get(key, [])
        position = self.positions.get(key, 0)
        if not responses:
            raise LLMResponseError(f"No fixture responses configured for document {key}")
        selected = responses[min(position, len(responses) - 1)]
        self.positions[key] = position + 1
        return selected if isinstance(selected, str) else json.dumps(selected, ensure_ascii=False)


def build_connector(config: dict[str, Any], *, config_base: Path | None = None) -> LLMConnector:
    provider = str(config.get("provider") or "openai_compatible").lower()
    if provider == "openai_compatible":
        return OpenAICompatibleConnector(config)
    if provider == "fixture":
        return FixtureConnector(config, config_base=config_base)
    raise LLMConfigurationError(
        f"Unsupported LLM provider {provider!r}; choose openai_compatible or fixture"
    )


def _json_payload(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"response is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise LLMResponseError("response root must be an object")
    return payload


def _nonempty_string(item: dict[str, Any], field: str, errors: list[str], index: int) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"observations[{index}].{field} must be a non-empty string")
        return ""
    return value.strip()


def _number(item: dict[str, Any], field: str, errors: list[str], index: int) -> float | None:
    value = item.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        errors.append(f"observations[{index}].{field} must be a finite number")
        return None
    return float(value)


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def validate_llm_response(task: dict[str, Any], raw: str) -> list[dict[str, Any]]:
    """Validate values, ontology fields and verbatim evidence locations.

    A response is accepted only when every observation passes. This avoids
    silently retaining valid-looking rows from a malformed model response.
    """

    payload = _json_payload(raw)
    if set(payload) != {"observations"}:
        raise LLMResponseError("response root must contain only 'observations'")
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise LLMResponseError("observations must be an array")

    parameter_map = {item["parameter_id"]: item for item in task["parameters"]}
    page_map = {int(item["page_number"]): str(item["page_text"]) for item in task["pages"]}
    allowed_fields = {
        "parameter_id", "raw_value_min", "raw_value_central", "raw_value_max",
        "raw_unit", "raw_value_text", "evidence_quote", "page_number", "table_id",
        "figure_id", "locator", "technology", "system_boundary", "context_location",
        "scale", "operating_conditions", "currency", "source_cost_year",
        "extraction_confidence", "applicability_score",
    }
    errors: list[str] = []
    validated: list[dict[str, Any]] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            errors.append(f"observations[{index}] must be an object")
            continue
        extras = sorted(set(observation) - allowed_fields)
        if extras:
            errors.append(f"observations[{index}] contains unsupported fields: {', '.join(extras)}")
        parameter_id = _nonempty_string(observation, "parameter_id", errors, index)
        definition = parameter_map.get(parameter_id)
        if not definition:
            errors.append(f"observations[{index}].parameter_id is not requested: {parameter_id!r}")
        central = _number(observation, "raw_value_central", errors, index)
        low_value = observation.get("raw_value_min", central)
        high_value = observation.get("raw_value_max", central)
        if low_value is None:
            low_value = central
        if high_value is None:
            high_value = central
        low = _number({"value": low_value}, "value", errors, index)
        high = _number({"value": high_value}, "value", errors, index)
        raw_unit = _nonempty_string(observation, "raw_unit", errors, index)
        raw_value_text = _nonempty_string(observation, "raw_value_text", errors, index)
        evidence_quote = _nonempty_string(observation, "evidence_quote", errors, index)
        locator = _nonempty_string(observation, "locator", errors, index)
        confidence = _number(observation, "extraction_confidence", errors, index)
        applicability = observation.get("applicability_score", 0.5)
        applicability_number = _number({"value": applicability}, "value", errors, index)

        page_number = observation.get("page_number")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            errors.append(f"observations[{index}].page_number must be a positive integer")
            page_text = ""
        else:
            page_text = page_map.get(page_number, "")
            if not page_text:
                errors.append(f"observations[{index}].page_number is not present in the task")
        detailed_locator = any(
            token in locator.lower()
            for token in ("line", "paragraph", "section", "table", "figure")
        )
        if not detailed_locator:
            errors.append(
                f"observations[{index}].locator must identify a line, paragraph, section, table or figure"
            )
        if page_text and _normalized_text(evidence_quote) not in _normalized_text(page_text):
            errors.append(f"observations[{index}].evidence_quote is not verbatim text from page {page_number}")
        if raw_value_text and evidence_quote and _normalized_text(raw_value_text) not in _normalized_text(evidence_quote):
            errors.append(f"observations[{index}].raw_value_text is not contained in evidence_quote")
        if central is not None and low is not None and high is not None and not low <= central <= high:
            errors.append(f"observations[{index}] must satisfy raw_value_min <= central <= raw_value_max")
        if confidence is not None and not 0 <= confidence <= 1:
            errors.append(f"observations[{index}].extraction_confidence must be between 0 and 1")
        if applicability_number is not None and not 0 <= applicability_number <= 1:
            errors.append(f"observations[{index}].applicability_score must be between 0 and 1")
        if definition and raw_unit:
            allowed = {
                canonicalize_unit(unit)
                for unit in [definition.get("canonical_unit"), *definition.get("allowed_units", [])]
                if unit
            }
            if canonicalize_unit(raw_unit) not in allowed:
                errors.append(
                    f"observations[{index}].raw_unit {raw_unit!r} is outside ontology units {sorted(allowed)}"
                )

        validated.append(
            {
                **observation,
                "raw_value_min": low,
                "raw_value_central": central,
                "raw_value_max": high,
                "raw_unit": raw_unit,
                "raw_value_text": raw_value_text,
                "context_excerpt": evidence_quote,
                "locator": locator,
                "extraction_confidence": confidence,
                "applicability_score": applicability_number,
            }
        )
    if errors:
        raise LLMResponseError("; ".join(errors))
    return validated


def _existing_observation(db_path: Path, item: dict[str, Any]) -> int | None:
    with db.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT observation_id FROM evidence_observations
            WHERE parameter_id=? AND source_id=? AND locator=?
              AND COALESCE(raw_value_text, '')=COALESCE(?, '')
            ORDER BY observation_id LIMIT 1
            """,
            (item["parameter_id"], item["source_id"], item["locator"], item.get("raw_value_text")),
        ).fetchone()
    return int(row[0]) if row else None


def _range_summary(db_path: Path, observation_ids: list[int]) -> list[dict[str, Any]]:
    if not observation_ids:
        return []
    placeholders = ",".join("?" for _ in observation_ids)
    with db.connect(db_path) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM evidence_observations WHERE observation_id IN ({placeholders})",
                observation_ids,
            )
        ]
    summaries: list[dict[str, Any]] = []
    for parameter_id in sorted({row["parameter_id"] for row in rows}):
        selected = [row for row in rows if row["parameter_id"] == parameter_id and row["physical_check_passed"]]
        if not selected:
            continue
        lows = [float(row["normalized_value_min"]) for row in selected]
        centers = [float(row["normalized_value_central"]) for row in selected]
        highs = [float(row["normalized_value_max"]) for row in selected]
        chosen = float(statistics.median(centers))
        relative_spread = (max(centers) - min(centers)) / max(abs(chosen), 1e-12)
        conflict = len(centers) > 1 and relative_spread > 0.35
        justification = (
            "Provisional unweighted median of extracted source central estimates; the median limits "
            "the influence of extreme values while the reported envelope preserves observed uncertainty."
        )
        if conflict:
            justification += " The wide dispersion is conflict-flagged and requires boundary-specific human review."
        justification += " This is not a human-approved HOMER input."
        summaries.append(
            {
                "parameter_id": parameter_id,
                "observation_count": len(selected),
                "independent_source_count": len({row["independent_source_key"] for row in selected}),
                "evidence_low": min(lows),
                "provisional_selected_value": chosen,
                "evidence_high": max(highs),
                "canonical_unit": selected[0]["canonical_unit"],
                "conflict_flag": conflict,
                "selection_method": "unweighted_median_of_source_central_estimates",
                "selection_justification": justification,
            }
        )
    return summaries


def _write_performance_outputs(destination: Path, summary: dict[str, Any]) -> tuple[Path, Path]:
    ranges_path = destination / "PARAMETER_RANGES.csv"
    ranges = summary["parameter_ranges"]
    with ranges_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "parameter_id", "observation_count", "independent_source_count",
            "evidence_low", "provisional_selected_value", "evidence_high",
            "canonical_unit", "conflict_flag", "selection_method", "selection_justification",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(ranges)

    report_path = destination / "QUANTITATIVE_PERFORMANCE.md"
    lines = [
        "# LLM Extraction: Quantitative Performance",
        "",
        "## Measurement boundary",
        "",
        "This report measures operational evidence yield and extracted data ranges. It does **not** report extraction accuracy because no independently human-labelled gold-standard dataset is available. All extracted observations remain candidates pending source review.",
        "",
        "## Corpus and processing",
        "",
        f"- Reports submitted: {summary['documents_submitted']}",
        f"- Successful extraction tasks: {summary['successful_tasks']}",
        f"- Failed extraction tasks: {summary['failed_tasks']}",
        f"- Provider calls: {summary['api_calls']}",
        f"- Schema/provenance-invalid responses: {summary['invalid_responses']}",
        f"- Retries used: {summary['retries_used']}",
        f"- Candidate observations imported: {summary['observations_imported']}",
        f"- Reports contributing quantitative evidence: {summary['documents_with_quantitative_evidence']}",
        f"- Report-to-evidence yield: {summary['document_evidence_yield']:.1%}",
        f"- Parameters covered: {summary['parameters_covered']}",
        "",
        "## Extracted ranges and provisional selections",
        "",
        "| Parameter | Observations | Sources | Low | Provisional value | High | Unit | Conflict |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in ranges:
        lines.append(
            f"| `{row['parameter_id']}` | {row['observation_count']} | {row['independent_source_count']} | "
            f"{row['evidence_low']:.6g} | {row['provisional_selected_value']:.6g} | "
            f"{row['evidence_high']:.6g} | {row['canonical_unit']} | {str(row['conflict_flag']).lower()} |"
        )
    if not ranges:
        lines.append("| No valid candidate observations | 0 | 0 | — | — | — | — | — |")
    lines.extend(["", "## Selection justification", ""])
    for row in ranges:
        lines.extend(
            [
                f"### `{row['parameter_id']}`",
                "",
                row["selection_justification"],
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation",
            "",
            "A successful task means the response parsed as JSON, matched requested ontology parameters and units, cited a detailed location, and supplied a verbatim quotation found on the cited page. It does not prove that the source is applicable to the study or that the extracted value is correct. Human review remains necessary before model export.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path, ranges_path


def run_llm_extraction_batch(
    db_path: str | Path,
    document_ids: list[int],
    parameter_ids: list[str],
    config: dict[str, Any],
    output_dir: str | Path,
    *,
    connector: LLMConnector | None = None,
    config_base: Path | None = None,
) -> dict[str, Any]:
    """Submit every document once per task and import only fully valid responses."""

    database = Path(db_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    connector = connector or build_connector(config, config_base=config_base)
    parameters = [db.get_parameter_definition(database, value) for value in parameter_ids]
    max_retries = int(config.get("max_retries", 2))
    if max_retries < 0:
        raise LLMConfigurationError("max_retries must be non-negative")
    retry_delay = float(config.get("retry_delay_seconds", 0))
    normalization = dict(config.get("normalization") or {})
    results: list[dict[str, Any]] = []
    imported_ids: list[int] = []
    skipped_ids: list[int] = []
    total_calls = 0
    invalid_responses = 0

    for document_id in document_ids:
        document, pages = db.get_document(database, document_id)
        task = build_ai_extraction_task(document_id, document["title"], pages, parameters)
        immutable_hash = task_sha256(task)
        feedback: list[str] | None = None
        attempts: list[dict[str, Any]] = []
        validated: list[dict[str, Any]] | None = None
        for attempt in range(1, max_retries + 2):
            total_calls += 1
            try:
                raw = connector.complete(task, feedback)
                response_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                validated = validate_llm_response(task, raw)
                attempts.append(
                    {
                        "attempt": attempt,
                        "task_sha256": immutable_hash,
                        "response_sha256": response_hash,
                        "status": "valid",
                        "observation_count": len(validated),
                    }
                )
                break
            except (LLMResponseError, ValueError) as exc:
                invalid_responses += 1
                feedback = [str(exc)]
                attempts.append(
                    {
                        "attempt": attempt,
                        "task_sha256": immutable_hash,
                        "status": "invalid",
                        "errors": feedback,
                    }
                )
                if attempt <= max_retries and retry_delay:
                    time.sleep(retry_delay)

        record = {
            "document_id": document_id,
            "source_id": document.get("source_id"),
            "title": document["title"],
            "task_sha256": immutable_hash,
            "status": "valid" if validated is not None else "failed",
            "attempts": attempts,
            "observation_ids": [],
        }
        if validated is not None:
            for item in validated:
                candidate = {
                    **item,
                    "document_id": document_id,
                    "source_id": document.get("source_id") or f"document:{document_id}",
                    "source_title": document["title"],
                    "source_doi": document.get("doi"),
                    "independent_source_key": document.get("doi") or document.get("source_id") or f"document:{document_id}",
                    "extraction_method": f"{LLM_EXTRACTION_VERSION}:{connector.provider_name}:{connector.model_name}",
                    "verification_status": "candidate",
                    "authoritative_source": False,
                }
                existing = _existing_observation(database, candidate)
                if existing:
                    skipped_ids.append(existing)
                    record["observation_ids"].append(existing)
                    continue
                try:
                    prepared = prepare_observation(
                        database,
                        candidate,
                        fx_rate=normalization.get("fx_rate"),
                        target_cost_year=normalization.get("target_cost_year"),
                        annual_escalation_rate=normalization.get("annual_escalation_rate"),
                    )
                    observation_id = db.insert_evidence_observation(database, prepared)
                    imported_ids.append(observation_id)
                    record["observation_ids"].append(observation_id)
                except ValueError as exc:
                    record.setdefault("import_errors", []).append(str(exc))
        document_output = destination / f"{document_id:04d}_llm_result.json"
        document_output.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        record["result_file"] = str(document_output)
        results.append(record)

    successes = sum(item["status"] == "valid" for item in results)
    documents_with_values = sum(bool(item["observation_ids"]) for item in results)
    summary = {
        "version": LLM_EXTRACTION_VERSION,
        "provider": connector.provider_name,
        "model": connector.model_name,
        "documents_submitted": len(document_ids),
        "tasks_submitted": len(document_ids),
        "successful_tasks": successes,
        "failed_tasks": len(results) - successes,
        "api_calls": total_calls,
        "retries_used": max(total_calls - len(document_ids), 0),
        "invalid_responses": invalid_responses,
        "validated_observations": sum(len(item["observation_ids"]) for item in results),
        "observations_imported": len(imported_ids),
        "observations_skipped_as_duplicates": len(skipped_ids),
        "documents_with_quantitative_evidence": documents_with_values,
        "document_evidence_yield": documents_with_values / len(document_ids) if document_ids else 0,
        "parameters_covered": len({row["parameter_id"] for row in _range_summary(database, imported_ids)}),
        "parameter_ranges": _range_summary(database, imported_ids),
        "accuracy_status": "not_measured_no_human_gold_standard",
        "results": results,
    }
    summary_path = destination / "llm_extraction_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    summary["summary_file"] = str(summary_path)
    performance_report, ranges_file = _write_performance_outputs(destination, summary)
    summary["performance_report"] = str(performance_report)
    summary["parameter_ranges_file"] = str(ranges_file)
    return summary
