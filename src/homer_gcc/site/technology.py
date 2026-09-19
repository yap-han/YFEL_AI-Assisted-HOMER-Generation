"""Technology parameter register: the literature-supported inputs as a cited, bounded register.

The scenario contract marks 19 inputs as ``literature_supported``. Two registers
for those inputs can exist side by side, and comparing them is the experiment:

* ``baseline_generic``: values from public techno-economic datasets and software
  documentation (NREL ATB, IRENA, PNNL, HOMER Pro documentation), deliberately
  not specific to the GCC or to aquaculture. It makes the scenario runnable on
  day one and is the control arm.
* ``evidence_updated``: values from the evidence pipeline (GCC and aquaculture
  literature) after human approval, or from a manual extraction entered in the
  same schema. It is the treatment arm.

Both use the site register's row schema plus ``evidence_tier``, ``cost_year``,
``currency`` and ``locator``, export through the same 11-column and 10-column
writers, and enter the same human gate. Nothing here is approved evidence.
"""

from __future__ import annotations

from typing import Any

from .register import REQUIRED_REGISTER_FIELDS, ontology_index, validate_register

EVIDENCE_TIERS = ("baseline_generic", "evidence_updated")

REQUIRED_TECHNOLOGY_FIELDS = REQUIRED_REGISTER_FIELDS + ("source_type", "year", "basis")

COMPARISON_COLUMNS = [
    "parameter_id",
    "unit",
    "a_low",
    "a_base",
    "a_high",
    "b_low",
    "b_base",
    "b_high",
    "delta_base",
    "delta_base_pct",
    "b_base_within_a_bounds",
    "a_tier",
    "b_tier",
]

HOMER_INPUT_COLUMNS = [
    "order",
    "parameter_id",
    "label",
    "evidence_role",
    "origin",
    "homer_component",
    "homer_field",
    "unit",
    "low",
    "base",
    "high",
    "source_title",
    "source_url",
    "locator",
    "year",
    "accessed",
    "confidence",
    "validation_status",
    "basis",
]


class TechnologyRegisterError(ValueError):
    pass


def literature_parameter_ids(scenario: dict[str, Any]) -> list[str]:
    return [
        row["parameter_id"]
        for row in scenario.get("required_parameters", [])
        if row.get("evidence_role") == "literature_supported"
    ]


def validate_technology_register(
    register: dict[str, Any],
    ontology: dict[str, Any],
    scenario: dict[str, Any],
) -> list[str]:
    """Return every problem found; an empty list means the register is usable."""
    errors = validate_register(register, ontology)
    tier = register.get("evidence_tier")
    if tier not in EVIDENCE_TIERS:
        errors.append(f"evidence_tier must be one of {EVIDENCE_TIERS}, got {tier!r}")
    if not register.get("register_id"):
        errors.append("missing register_id")
    literature = set(literature_parameter_ids(scenario))
    for position, item in enumerate(register.get("parameters", [])):
        where = f"parameters[{position}]"
        missing = [field for field in REQUIRED_TECHNOLOGY_FIELDS if item.get(field) in (None, "")]
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
        parameter_id = item.get("parameter_id")
        if parameter_id and parameter_id not in literature:
            errors.append(f"{where}: {parameter_id} is not a literature_supported input of the scenario contract")
        if item.get("evidence_role") not in (None, "literature_supported"):
            errors.append(f"{where}: evidence_role must be literature_supported")
        row_tier = item.get("evidence_tier")
        if row_tier and row_tier != tier:
            errors.append(f"{where}: row evidence_tier {row_tier!r} differs from register tier {tier!r}")
        cost_year = item.get("cost_year")
        if cost_year not in (None, "") and not str(cost_year).isdigit():
            errors.append(f"{where}: cost_year must be a year")
    return errors


def technology_coverage(register: dict[str, Any], scenario: dict[str, Any]) -> dict[str, list[str]]:
    literature = literature_parameter_ids(scenario)
    present = {item["parameter_id"] for item in register.get("parameters", [])}
    return {
        "covered": [pid for pid in literature if pid in present],
        "missing": [pid for pid in literature if pid not in present],
    }


def tier_map(register: dict[str, Any] | None) -> dict[str, str]:
    if not register:
        return {}
    tier = str(register.get("evidence_tier", ""))
    return {item["parameter_id"]: item.get("evidence_tier") or tier for item in register.get("parameters", [])}


def merge_registers(*registers: dict[str, Any] | None) -> dict[str, Any]:
    """Concatenate registers; a later register replaces an earlier row with the same parameter_id."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for register in registers:
        if not register:
            continue
        tier = register.get("evidence_tier")
        for item in register.get("parameters", []):
            row = dict(item)
            if tier and not row.get("evidence_tier"):
                row["evidence_tier"] = tier
            if row["parameter_id"] not in merged:
                order.append(row["parameter_id"])
            merged[row["parameter_id"]] = row
    return {"parameters": [merged[pid] for pid in order]}


def compare_registers(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """Parameter-by-parameter comparison of two registers (control versus treatment)."""
    a_rows = {item["parameter_id"]: item for item in a.get("parameters", [])}
    b_rows = {item["parameter_id"]: item for item in b.get("parameters", [])}
    a_tier = a.get("evidence_tier", "")
    b_tier = b.get("evidence_tier", "")
    rows = []
    for parameter_id in sorted(set(a_rows) | set(b_rows)):
        left = a_rows.get(parameter_id)
        right = b_rows.get(parameter_id)
        row: dict[str, Any] = {
            "parameter_id": parameter_id,
            "unit": (left or right or {}).get("unit", ""),
            "a_low": left["low"] if left else None,
            "a_base": left["base"] if left else None,
            "a_high": left["high"] if left else None,
            "b_low": right["low"] if right else None,
            "b_base": right["base"] if right else None,
            "b_high": right["high"] if right else None,
            "delta_base": None,
            "delta_base_pct": None,
            "b_base_within_a_bounds": None,
            "a_tier": (left or {}).get("evidence_tier", a_tier),
            "b_tier": (right or {}).get("evidence_tier", b_tier),
        }
        if left and right:
            if left.get("unit") != right.get("unit"):
                raise TechnologyRegisterError(
                    f"{parameter_id}: units differ ({left.get('unit')} versus {right.get('unit')}); normalise before comparing"
                )
            a_base = float(left["base"])
            b_base = float(right["base"])
            row["delta_base"] = round(b_base - a_base, 6)
            row["delta_base_pct"] = round(100.0 * (b_base - a_base) / a_base, 2) if a_base else None
            row["b_base_within_a_bounds"] = float(left["low"]) <= b_base <= float(left["high"])
        rows.append(row)
    return rows


def _origin(item: dict[str, Any], role: str) -> str:
    if item.get("evidence_tier"):
        return str(item["evidence_tier"])
    if item.get("derived_from") == "pipeline":
        return "site_layer_derived"
    if role == "researcher_assumption":
        return "researcher_assumption"
    return "site_register"


def homer_input_sheet(
    scenario: dict[str, Any],
    ontology: dict[str, Any],
    site_register: dict[str, Any],
    technology_register: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """One row per required input, in contract order, then any supporting rows the site register carries."""
    index = ontology_index(ontology)
    merged = merge_registers(site_register, technology_register)
    by_id = {item["parameter_id"]: item for item in merged["parameters"]}
    roles = {row["parameter_id"]: row.get("evidence_role", "") for row in scenario.get("required_parameters", [])}
    ordered = [row["parameter_id"] for row in scenario.get("required_parameters", [])]
    ordered += [pid for pid in by_id if pid not in roles]
    rows = []
    for position, parameter_id in enumerate(ordered, start=1):
        item = by_id.get(parameter_id)
        definition = index.get(parameter_id, {})
        role = roles.get(parameter_id, "supporting")
        if item is None:
            rows.append(
                {
                    "order": position,
                    "parameter_id": parameter_id,
                    "label": definition.get("label", parameter_id),
                    "evidence_role": role,
                    "origin": "gap",
                    "homer_component": (definition.get("homer_mapping") or {}).get("component", ""),
                    "homer_field": (definition.get("homer_mapping") or {}).get("field", ""),
                    "unit": definition.get("canonical_unit", ""),
                    "low": "",
                    "base": "",
                    "high": "",
                    "source_title": "",
                    "source_url": "",
                    "locator": "",
                    "year": "",
                    "accessed": "",
                    "confidence": "",
                    "validation_status": "gap",
                    "basis": "no candidate value; supply through the evidence pipeline or a technology register",
                }
            )
            continue
        rows.append(
            {
                "order": position,
                "parameter_id": parameter_id,
                "label": definition.get("label", parameter_id),
                "evidence_role": role,
                "origin": _origin(item, role),
                "homer_component": item.get("homer_component", ""),
                "homer_field": item.get("homer_field", ""),
                "unit": item.get("unit", ""),
                "low": item.get("low", ""),
                "base": item.get("base", ""),
                "high": item.get("high", ""),
                "source_title": item.get("source_title", ""),
                "source_url": item.get("source_url", ""),
                "locator": item.get("locator", ""),
                "year": item.get("year", ""),
                "accessed": item.get("accessed", ""),
                "confidence": item.get("confidence", ""),
                "validation_status": item.get("validation_status", "candidate"),
                "basis": item.get("basis", ""),
            }
        )
    return rows
