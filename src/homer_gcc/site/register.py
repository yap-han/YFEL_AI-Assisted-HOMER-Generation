"""Site parameter register: cited values, derived rows, exports and coverage.

Two export shapes are produced from one register:

* ``proposed_parameters`` rows in the evidence pipeline's 11-column format,
  importable with ``import-proposals`` so the site values pass through the same
  validation and human-approval gate as literature values; and
* the team's 10-column master parameter register defined in the methodology
  note of 21 August 2026.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ..ontology import iter_parameters

PROPOSED_COLUMNS = [
    "parameter_id",
    "scenario_id",
    "context_location",
    "technology",
    "proposed_value",
    "proposed_unit",
    "system_boundary",
    "source_basis",
    "criticality",
    "entered_by",
    "notes",
]

MASTER_COLUMNS = [
    "parameter_name",
    "homer_input_field",
    "unit",
    "base_value",
    "low_high_values",
    "source",
    "country_year",
    "confidence",
    "responsible",
    "validation_status",
]

TECHNOLOGY_BY_PREFIX = {
    "grid": "Grid",
    "fuel": "Diesel generator",
    "load": "Electric load",
    "solar": "Solar resource",
    "site": "Site resource",
    "economics": "",
    "resilience": "",
    "pv": "PV",
    "generator": "Diesel generator",
    "battery": "Lithium-ion battery",
    "converter": "Converter",
}

REQUIRED_REGISTER_FIELDS = (
    "parameter_id",
    "homer_component",
    "homer_field",
    "unit",
    "base",
    "low",
    "high",
    "source_title",
    "source_url",
    "accessed",
    "confidence",
    "evidence_role",
    "validation_status",
)


class RegisterError(ValueError):
    pass


def ontology_index(ontology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {parameter["id"]: parameter for parameter in iter_parameters(ontology)}


def validate_register(register: dict[str, Any], ontology: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    index = ontology_index(ontology)
    seen: set[str] = set()
    for position, item in enumerate(register.get("parameters", [])):
        where = f"parameters[{position}]"
        missing = [field for field in REQUIRED_REGISTER_FIELDS if item.get(field) in (None, "")]
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
            continue
        parameter_id = item["parameter_id"]
        if parameter_id in seen:
            errors.append(f"{where}: duplicate parameter_id {parameter_id}")
        seen.add(parameter_id)
        definition = index.get(parameter_id)
        if definition is None:
            errors.append(f"{where}: {parameter_id} is not in the ontology")
            continue
        allowed = definition.get("allowed_units") or [definition.get("canonical_unit")]
        if item["unit"] not in allowed:
            errors.append(f"{where}: unit {item['unit']!r} not in allowed units {allowed}")
        try:
            low, base, high = float(item["low"]), float(item["base"]), float(item["high"])
        except (TypeError, ValueError):
            errors.append(f"{where}: low, base and high must be numeric")
            continue
        if not low <= base <= high:
            errors.append(f"{where}: expected low <= base <= high, got {low}, {base}, {high}")
        if item["validation_status"] not in {"candidate", "approved", "rejected"}:
            errors.append(f"{where}: validation_status must be candidate, approved or rejected")
    return errors


def derived_row(
    parameter_id: str,
    *,
    homer_component: str,
    homer_field: str,
    unit: str,
    base: float,
    low: float,
    high: float,
    basis: str,
    notes: str,
    evidence_role: str = "site_data_required",
    responsible: str = "site_layer",
    accessed: str = "",
) -> dict[str, Any]:
    return {
        "parameter_id": parameter_id,
        "homer_component": homer_component,
        "homer_field": homer_field,
        "unit": unit,
        "base": base,
        "low": low,
        "high": high,
        "source_title": "Derived by the site layer from cached public data and the archetype inventory",
        "source_url": "internal:site_layer",
        "source_type": "pipeline_derived",
        "country": "",
        "year": "",
        "accessed": accessed or "pipeline_run",
        "confidence": "medium",
        "evidence_role": evidence_role,
        "validation_status": "candidate",
        "responsible": responsible,
        "basis": basis,
        "notes": notes,
        "derived_from": "pipeline",
    }


def attach_derived(
    register: dict[str, Any],
    *,
    load_summary: dict[str, Any],
    weather_years: dict[str, Any],
    files: dict[str, dict[str, str]],
    accessed: str,
    country: str,
) -> dict[str, Any]:
    """Return a copy of the register with the pipeline-derived rows added or replaced."""
    result = {**register, "parameters": [dict(item) for item in register.get("parameters", [])]}
    existing = {item["parameter_id"]: index for index, item in enumerate(result["parameters"])}

    def file_note(key: str) -> str:
        entry = files.get(key)
        return f"file={entry['path']}; sha256={entry['sha256']}" if entry else ""

    annual = weather_years["annual_ghi_kwh_m2"]
    mean_temperature = weather_years["mean_temperature_c"]
    rows = [
        derived_row(
            "load.ac_primary_profile",
            homer_component="Electric Load",
            homer_field="Import time series (kW)",
            unit="kW",
            base=load_summary["mean_kw"],
            low=round(load_summary["mean_kw"] * (1.0 - load_summary.get("low_high_scale", 0.15)), 3),
            high=round(load_summary["mean_kw"] * (1.0 + load_summary.get("low_high_scale", 0.15)), 3),
            basis="Annual mean of the synthesised hourly profile; the HOMER input is the file, not the scalar",
            notes=file_note("load_total_base"),
            accessed=accessed,
        ),
        derived_row(
            "load.peak_demand",
            homer_component="Electric Load",
            homer_field="Peak load check (kW)",
            unit="kW",
            base=load_summary["peak_kw"],
            low=round(load_summary["peak_kw"] * (1.0 - load_summary.get("low_high_scale", 0.15)), 3),
            high=round(load_summary["peak_kw"] * (1.0 + load_summary.get("low_high_scale", 0.15)), 3),
            basis=f"Peak of the synthesised profile at {load_summary['peak_time']}",
            notes="Compare with the peak HOMER reports after import",
            accessed=accessed,
        ),
        derived_row(
            "solar.ghi",
            homer_component="Solar Resource",
            homer_field="Import time series (kW/m2)",
            unit="kWh/m2/hour",
            base=round(annual["base"] / 8760.0, 5),
            low=round(annual["low"] / 8760.0, 5),
            high=round(annual["high"] / 8760.0, 5),
            basis=(
                f"NASA POWER hourly GHI; base year {weather_years['years']['base']} "
                f"({annual['base']} kWh/m2/yr), low {weather_years['years']['low']} ({annual['low']}), "
                f"high {weather_years['years']['high']} ({annual['high']})"
            ),
            notes=file_note("ghi_base"),
            accessed=accessed,
        ),
        derived_row(
            "site.ambient_temperature",
            homer_component="Temperature Resource",
            homer_field="Import time series (degC)",
            unit="degC",
            base=mean_temperature["base"],
            low=min(mean_temperature.values()),
            high=max(mean_temperature.values()),
            basis=(
                "NASA POWER 2 m air temperature; base is the base GHI year, low and high are the coolest and "
                f"warmest annual means among the three reference years (low-GHI year {mean_temperature['low']} degC, "
                f"high-GHI year {mean_temperature['high']} degC)"
            ),
            notes=file_note("temperature_base"),
            accessed=accessed,
        ),
        derived_row(
            "resilience.critical_load_fraction",
            homer_component="Analysis",
            homer_field="Critical load fraction (%)",
            unit="%",
            base=round(100.0 * load_summary["critical_fraction"], 2),
            low=round(100.0 * load_summary.get("critical_fraction_low", load_summary["critical_fraction"]), 2),
            high=round(100.0 * load_summary.get("critical_fraction_high", load_summary["critical_fraction"]), 2),
            basis="Share of annual energy in the critical class of the archetype inventory",
            notes=file_note("load_critical_base"),
            evidence_role="supporting",
            accessed=accessed,
        ),
    ]
    for row in rows:
        row["country"] = country
        if row["parameter_id"] in existing:
            result["parameters"][existing[row["parameter_id"]]] = row
        else:
            result["parameters"].append(row)
    return result


def to_proposed_parameter_rows(
    register: dict[str, Any],
    ontology: dict[str, Any],
    scenario: dict[str, Any],
    *,
    scenario_id: str,
    context_location: str,
    entered_by: str,
    scenarios: tuple[str, ...] = ("base",),
) -> list[dict[str, Any]]:
    index = ontology_index(ontology)
    boundaries = {row["parameter_id"]: row["system_boundary"] for row in scenario.get("required_parameters", [])}
    rows = []
    for item in register.get("parameters", []):
        definition = index.get(item["parameter_id"], {})
        prefix = item["parameter_id"].split(".")[0]
        for label in scenarios:
            value = item[label]
            suffix = "" if label == "base" else f"_{label}"
            rows.append(
                {
                    "parameter_id": item["parameter_id"],
                    "scenario_id": f"{scenario_id}{suffix}",
                    "context_location": context_location,
                    "technology": TECHNOLOGY_BY_PREFIX.get(prefix, ""),
                    "proposed_value": value,
                    "proposed_unit": item["unit"],
                    "system_boundary": boundaries.get(item["parameter_id"], item.get("homer_component", "")),
                    "source_basis": f"{item.get('source_type', 'site_layer')}: {item['source_title']} (accessed {item['accessed']})",
                    "criticality": definition.get("criticality", "high"),
                    "entered_by": entered_by,
                    "notes": (
                        f"low={item['low']}; high={item['high']}; url={item['source_url']}; "
                        f"status={item.get('validation_status', 'candidate')}; "
                        f"evidence_role={item.get('evidence_role', '')}; {item.get('notes', '')}"
                    ).strip("; "),
                }
            )
    return rows


def to_master_register_rows(register: dict[str, Any], ontology: dict[str, Any]) -> list[dict[str, Any]]:
    index = ontology_index(ontology)
    rows = []
    for item in register.get("parameters", []):
        definition = index.get(item["parameter_id"], {})
        rows.append(
            {
                "parameter_name": f"{definition.get('label', item['parameter_id'])} [{item['parameter_id']}]",
                "homer_input_field": f"{item['homer_component']} > {item['homer_field']}",
                "unit": item["unit"],
                "base_value": item["base"],
                "low_high_values": f"{item['low']} / {item['high']}",
                "source": f"{item['source_title']} | {item['source_url']} | accessed {item['accessed']}",
                "country_year": f"{item.get('country', '')} {item.get('year', '')}".strip(),
                "confidence": item["confidence"],
                "responsible": item.get("responsible", ""),
                "validation_status": item.get("validation_status", "candidate"),
            }
        )
    return rows


def coverage_report(
    register: dict[str, Any],
    scenario: dict[str, Any],
    literature_ids: set[str] | None = None,
    technology: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Coverage of the scenario contract.

    ``literature_ids`` are parameters with provisional evidence from the evidence
    pipeline; ``technology`` maps parameter IDs to the evidence tier of a
    technology register (``baseline_generic`` or ``evidence_updated``). Pipeline
    evidence takes precedence over a technology register in the reported status.
    """
    literature = set(literature_ids or set())
    technology = dict(technology or {})
    site_ids = {item["parameter_id"] for item in register.get("parameters", [])}
    rows = []
    gaps = []
    for required in scenario.get("required_parameters", []):
        parameter_id = required["parameter_id"]
        role = required.get("evidence_role")
        if parameter_id in site_ids:
            status = "researcher_assumption_provided" if role == "researcher_assumption" else "site_layer_candidate"
        elif role == "literature_supported" and parameter_id in literature:
            status = "literature_provisional"
        elif role == "literature_supported" and parameter_id in technology:
            status = f"technology_{technology[parameter_id]}_candidate"
        else:
            status = "gap"
            gaps.append(parameter_id)
        rows.append({"parameter_id": parameter_id, "evidence_role": role, "status": status})
    required_count = len(rows)
    return {
        "required": required_count,
        "covered": required_count - len(gaps),
        "gaps": gaps,
        "site_layer_rows": sum(1 for row in rows if row["status"] in {"site_layer_candidate", "researcher_assumption_provided"}),
        "literature_rows": sum(1 for row in rows if row["status"] == "literature_provisional"),
        "technology_rows": sum(1 for row in rows if row["status"].startswith("technology_")),
        "rows": rows,
    }


def literature_ids_from_aggregates(payload: dict[str, Any]) -> set[str]:
    """Read parameter IDs with provisional evidence from an advanced-pipeline coverage export."""
    ids: set[str] = set()
    rows = payload.get("rows") or payload.get("parameters") or []
    for row in rows:
        covered = (
            row.get("coverage_status") == "provisional_evidence_available"
            or row.get("provisional_evidence_available")
            or row.get("status") in {"literature_provisional", "covered"}
        )
        if covered and row.get("parameter_id"):
            ids.add(row["parameter_id"])
    for key in ("literature_supported_ids", "covered_ids"):
        for value in payload.get(key, []) or []:
            ids.add(str(value))
    return ids


def write_csv(path: str | Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return output
