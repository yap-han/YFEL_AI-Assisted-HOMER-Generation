from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from . import db
from .normalization import canonicalize_unit, normalize_range, physical_range_check


ALGORITHM_VERSION = "quant_validation_v0.3"


def _validation_rule(definition: dict[str, Any]) -> dict[str, Any]:
    rule = dict(definition.get("validation") or {})
    # Ratios expressed as percentages have a universal physical ceiling even
    # when a study ontology only states a non-negative rule.
    if canonicalize_unit(definition.get("canonical_unit", "")) == "%":
        rule.setdefault("min", 0)
        rule.setdefault("max", 100)
    return rule


def prepare_observation(
    db_path: str | Path,
    item: dict[str, Any],
    *,
    fx_rate: float | None = None,
    target_cost_year: int | None = None,
    annual_escalation_rate: float | None = None,
) -> dict[str, Any]:
    """Normalize one raw evidence observation without using an LLM.

    The caller is responsible for supplying evidence that was extracted from a
    source. This function only applies explicit unit, currency-year and physical
    range rules, preserving the original evidence in the returned record.
    """

    definition = db.get_parameter_definition(db_path, str(item["parameter_id"]))
    raw_unit = str(item.get("raw_unit") or "").strip()
    if not raw_unit:
        raise ValueError("raw_unit is required")
    allowed = {
        canonicalize_unit(unit)
        for unit in [definition["canonical_unit"], *definition.get("allowed_units", [])]
    }
    if canonicalize_unit(raw_unit) not in allowed:
        raise ValueError(
            f"Unit {raw_unit!r} is not permitted for {definition['id']}; "
            f"allowed units are {sorted(allowed)}"
        )

    central = float(item["raw_value_central"])
    source_cost_year = item.get("source_cost_year")
    selected_target_year = item.get("target_cost_year", target_cost_year)
    normalized = normalize_range(
        item.get("raw_value_min"),
        central,
        item.get("raw_value_max"),
        raw_unit,
        definition["canonical_unit"],
        fx_rate=item.get("fx_rate", fx_rate),
        source_cost_year=int(source_cost_year) if source_cost_year is not None else None,
        target_cost_year=int(selected_target_year) if selected_target_year is not None else None,
        annual_escalation_rate=item.get("annual_escalation_rate", annual_escalation_rate),
    )
    physical_passed, physical_reasons = physical_range_check(
        normalized.low,
        normalized.high,
        _validation_rule(definition),
    )
    if item.get("verification_status") == "approved" and not physical_passed:
        raise ValueError(
            "An observation that fails physical checks cannot be imported as approved: "
            + "; ".join(physical_reasons)
        )
    if item.get("verification_status") == "approved" and not item.get("reviewer"):
        raise ValueError("Approved observations require a named reviewer")

    prepared = dict(item)
    prepared.update(
        {
            "raw_value_min": float(item.get("raw_value_min", central)),
            "raw_value_central": central,
            "raw_value_max": float(item.get("raw_value_max", central)),
            "raw_unit": raw_unit,
            "normalized_value_min": normalized.low,
            "normalized_value_central": normalized.central,
            "normalized_value_max": normalized.high,
            "canonical_unit": normalized.canonical_unit,
            "target_cost_year": normalized.target_cost_year,
            "physical_check_passed": physical_passed,
            "normalization": {
                **normalized.as_dict(),
                "physical_reasons": physical_reasons,
                "method": "deterministic_unit_and_cost_year_v0.3",
            },
            "raw": dict(item),
        }
    )
    return prepared


def _proposal_row(db_path: str | Path, proposal_id: int) -> dict[str, Any]:
    with db.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT p.*, d.definition_json
            FROM proposed_parameters p
            JOIN parameter_definitions d ON d.parameter_id=p.parameter_id
            WHERE p.proposal_id=?
            """,
            (proposal_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"Unknown proposal_id: {proposal_id}")
    result = dict(row)
    result["definition"] = json.loads(result.pop("definition_json"))
    return result


def validate_proposal(
    db_path: str | Path,
    proposal_id: int,
    *,
    include_candidates: bool = False,
    min_independent_sources: int = 2,
) -> dict[str, Any]:
    """Compare a proposed model input with traceable quantitative evidence.

    Candidate AI extractions are excluded by default. Enabling them cannot make
    a result fully ``supported``; it is intended only for exploratory triage.
    """

    proposal = _proposal_row(db_path, proposal_id)
    definition = proposal["definition"]
    normalized_proposal = normalize_range(
        None,
        float(proposal["proposed_value"]),
        None,
        proposal["proposed_unit"],
        definition["canonical_unit"],
    )
    proposal_physical, physical_reasons = physical_range_check(
        normalized_proposal.low,
        normalized_proposal.high,
        _validation_rule(definition),
    )

    statuses = ("approved", "candidate") if include_candidates else ("approved",)
    placeholders = ",".join("?" for _ in statuses)
    with db.connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM evidence_observations
            WHERE parameter_id=? AND verification_status IN ({placeholders})
            ORDER BY observation_id
            """,
            (proposal["parameter_id"], *statuses),
        ).fetchall()
    evidence = [dict(row) for row in rows]
    usable = [row for row in evidence if row["physical_check_passed"]]
    approved_count = sum(row["verification_status"] == "approved" for row in usable)
    independent_count = len({row["independent_source_key"] for row in usable})
    authoritative = any(row["authoritative_source"] for row in usable)
    reasons: list[str] = []

    aggregate_low: float | None = None
    aggregate_base: float | None = None
    aggregate_high: float | None = None
    range_position = "no_evidence"
    conflict = False
    average_applicability = 0.0

    if usable:
        lows = [float(row["normalized_value_min"]) for row in usable]
        centers = [float(row["normalized_value_central"]) for row in usable]
        highs = [float(row["normalized_value_max"]) for row in usable]
        aggregate_low = min(lows)
        aggregate_base = float(statistics.median(centers))
        aggregate_high = max(highs)
        proposed_value = normalized_proposal.central
        if proposed_value < aggregate_low:
            range_position = "below"
        elif proposed_value > aggregate_high:
            range_position = "above"
        else:
            range_position = "within"
        average_applicability = statistics.fmean(
            float(row["applicability_score"]) for row in usable
        )
        center_scale = max(abs(aggregate_base), 1e-12)
        relative_spread = (max(centers) - min(centers)) / center_scale
        ranges_do_not_overlap = max(lows) > min(highs)
        conflict = len(usable) > 1 and ranges_do_not_overlap and relative_spread > 0.35

    sufficient_sources = independent_count >= min_independent_sources or authoritative
    if not proposal_physical:
        decision = "not_supported"
        reasons.extend(physical_reasons)
    elif not usable:
        decision = "insufficient_evidence"
        reasons.append("no physically valid, eligible quantitative evidence")
    elif range_position != "within":
        decision = "not_supported"
        reasons.append(f"proposed value is {range_position} the evidence envelope")
    elif (
        sufficient_sources
        and average_applicability >= 0.5
        and not conflict
        and not include_candidates
    ):
        decision = "supported"
        reasons.append("proposal is within a sufficiently applicable, independently supported evidence envelope")
    else:
        decision = "conditionally_supported"
        if not sufficient_sources:
            reasons.append(f"fewer than {min_independent_sources} independent sources")
        if average_applicability < 0.5:
            reasons.append("average applicability is below 0.5")
        if conflict:
            reasons.append("eligible sources contain materially conflicting ranges")
        if include_candidates:
            reasons.append("candidate AI extractions were included; human evidence review is incomplete")

    criticality = proposal.get("criticality") or definition.get("criticality", "medium")
    if criticality in {"high", "safety_critical"} or decision != "supported" or conflict:
        priority = "high"
    elif independent_count < 3 or average_applicability < 0.75:
        priority = "medium"
    else:
        priority = "low"

    result = {
        "proposal_id": proposal_id,
        "parameter_id": proposal["parameter_id"],
        "decision": decision,
        "evidence_count": len(evidence),
        "independent_source_count": independent_count,
        "approved_evidence_count": approved_count,
        "evidence_ids": [int(row["observation_id"]) for row in usable],
        "proposed_normalized_value": normalized_proposal.central,
        "canonical_unit": normalized_proposal.canonical_unit,
        "aggregate_low": aggregate_low,
        "aggregate_base": aggregate_base,
        "aggregate_high": aggregate_high,
        "range_position": range_position,
        "conflict_flag": conflict,
        "average_applicability": average_applicability,
        "physical_check_passed": proposal_physical,
        "sensitivity_priority": priority,
        "reasons": reasons,
        "algorithm_version": ALGORITHM_VERSION,
        "human_status": "pending",
    }
    result["validation_id"] = db.insert_parameter_validation(db_path, result)
    return result
