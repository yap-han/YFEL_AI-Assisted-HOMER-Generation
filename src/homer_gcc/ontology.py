from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


VALID_CRITICALITIES = {"low", "medium", "high", "safety_critical"}
VALID_VALUE_KINDS = {"scalar", "range", "timeseries", "categorical", "distribution"}
VALID_DATA_TYPES = {"number", "string", "boolean", "integer"}


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    location: str
    message: str


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_parameters(ontology: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for family in ontology.get("families", []):
        family_id = family.get("id", "")
        defaults = family.get("parameter_defaults", {})
        for parameter in family.get("parameters", []):
            item = {**defaults, **parameter}
            item["family_id"] = family_id
            yield item


def validate_ontology(ontology: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not ontology.get("name"):
        issues.append(ValidationIssue("error", "root", "Missing ontology name"))
    if not ontology.get("version"):
        issues.append(ValidationIssue("error", "root", "Missing ontology version"))

    family_ids: set[str] = set()
    parameter_ids: set[str] = set()
    for f_index, family in enumerate(ontology.get("families", [])):
        location = f"families[{f_index}]"
        family_id = family.get("id")
        if not family_id:
            issues.append(ValidationIssue("error", location, "Missing family id"))
            continue
        if family_id in family_ids:
            issues.append(ValidationIssue("error", location, f"Duplicate family id: {family_id}"))
        family_ids.add(family_id)
        if not family.get("retrieval_terms"):
            issues.append(ValidationIssue("warning", location, "No retrieval terms"))

        for p_index, parameter in enumerate(family.get("parameters", [])):
            parameter = {**family.get("parameter_defaults", {}), **parameter}
            p_location = f"{location}.parameters[{p_index}]"
            parameter_id = parameter.get("id")
            if not parameter_id:
                issues.append(ValidationIssue("error", p_location, "Missing parameter id"))
                continue
            if parameter_id in parameter_ids:
                issues.append(ValidationIssue("error", p_location, f"Duplicate parameter id: {parameter_id}"))
            parameter_ids.add(parameter_id)

            required = {
                "label",
                "data_type",
                "value_kind",
                "canonical_unit",
                "criticality",
                "description",
                "evidence_preference",
                "extraction_terms",
                "validation",
            }
            missing = sorted(field for field in required if field not in parameter)
            if missing:
                issues.append(
                    ValidationIssue("error", p_location, f"Missing fields: {', '.join(missing)}")
                )
            if parameter.get("criticality") not in VALID_CRITICALITIES:
                issues.append(ValidationIssue("error", p_location, "Invalid criticality"))
            if parameter.get("value_kind") not in VALID_VALUE_KINDS:
                issues.append(ValidationIssue("error", p_location, "Invalid value_kind"))
            if parameter.get("data_type") not in VALID_DATA_TYPES:
                issues.append(ValidationIssue("error", p_location, "Invalid data_type"))
            if parameter.get("data_type") == "number" and not parameter.get("allowed_units"):
                issues.append(ValidationIssue("error", p_location, "Numeric parameter has no allowed_units"))
            if not parameter.get("homer_mapping"):
                issues.append(ValidationIssue("warning", p_location, "No HOMER mapping or external constraint"))

    if not parameter_ids:
        issues.append(ValidationIssue("error", "root", "Ontology contains no parameters"))
    return issues


def ontology_summary(ontology: dict[str, Any]) -> dict[str, Any]:
    parameters = list(iter_parameters(ontology))
    return {
        "name": ontology.get("name"),
        "version": ontology.get("version"),
        "family_count": len(ontology.get("families", [])),
        "parameter_count": len(parameters),
        "criticality_counts": {
            level: sum(p.get("criticality") == level for p in parameters)
            for level in sorted(VALID_CRITICALITIES)
        },
        "timeseries_count": sum(p.get("value_kind") == "timeseries" for p in parameters),
        "scope_locations": ontology.get("scope", {}).get("countries", []),
    }
