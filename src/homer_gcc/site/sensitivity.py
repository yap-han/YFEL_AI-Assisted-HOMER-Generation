"""Sensitivity table and seeded Monte Carlo cases for the site parameters.

HOMER Pro accepts several values per sensitivity variable and runs every
combination. The table lists the low, base and high values to enter and the
basis for each bound. The Monte Carlo cases are for the paper's uncertainty
discussion; HOMER does not read them directly.
"""

from __future__ import annotations

import random
import statistics
from typing import Any

SENSITIVITY_COLUMNS = [
    "homer_field",
    "parameter_id",
    "unit",
    "low",
    "base",
    "high",
    "low_basis",
    "high_basis",
    "note",
]


def build_sensitivity_table(
    register: dict[str, Any],
    load_metrics: dict[str, Any] | None,
    weather_years: dict[str, Any] | None,
    *,
    load_scale: float = 0.15,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if load_metrics:
        daily = float(load_metrics["daily_average_kwh"])
        rows.append(
            {
                "homer_field": "Electric Load > Scaled annual average (kWh/day)",
                "parameter_id": "load.daily_energy",
                "unit": "kWh/day",
                "low": round(daily * (1.0 - load_scale), 1),
                "base": round(daily, 1),
                "high": round(daily * (1.0 + load_scale), 1),
                "low_basis": f"synthesised profile scaled by {1 - load_scale:.2f}",
                "high_basis": f"synthesised profile scaled by {1 + load_scale:.2f}",
                "note": "Alternatively import load_total_low.txt and load_total_high.txt as separate scenarios",
            }
        )
        critical = float(load_metrics["critical_fraction"])
        rows.append(
            {
                "homer_field": "Critical load fraction (analysis input)",
                "parameter_id": "resilience.critical_load_fraction",
                "unit": "%",
                "low": round(100.0 * load_metrics.get("critical_fraction_low", critical), 1),
                "base": round(100.0 * critical, 1),
                "high": round(100.0 * load_metrics.get("critical_fraction_high", critical), 1),
                "low_basis": "cooling treated as fully sheddable",
                "high_basis": "cooling treated as fully critical",
                "note": "Use load_critical_base.txt as the load in the reliability scenario",
            }
        )
    if weather_years:
        rows.append(
            {
                "homer_field": "Solar GHI > Scaled annual average (kWh/m2/day)",
                "parameter_id": "solar.ghi",
                "unit": "kWh/m2/day",
                "low": round(float(weather_years["annual_ghi_kwh_m2"]["low"]) / 365.0, 3),
                "base": round(float(weather_years["annual_ghi_kwh_m2"]["base"]) / 365.0, 3),
                "high": round(float(weather_years["annual_ghi_kwh_m2"]["high"]) / 365.0, 3),
                "low_basis": f"NASA POWER year {weather_years['years']['low']} (lowest annual GHI)",
                "high_basis": f"NASA POWER year {weather_years['years']['high']} (highest annual GHI)",
                "note": "Or import ghi_low.txt and ghi_high.txt; temperature files for the same years are provided",
            }
        )
    rows.append(
        {
            "homer_field": "PV > Derating factor (%)",
            "parameter_id": "pv.derating_factor",
            "unit": "%",
            "low": 72.0,
            "base": 80.0,
            "high": 88.0,
            "low_basis": "heavy soiling, infrequent cleaning (literature side)",
            "high_basis": "weekly cleaning, low soiling (literature side)",
            "note": "literature_side: the evidence pipeline owns this parameter; values here are placeholders for the sensitivity grid",
        }
    )
    for item in register.get("parameters", []):
        rows.append(
            {
                "homer_field": f"{item['homer_component']} > {item['homer_field']}",
                "parameter_id": item["parameter_id"],
                "unit": item["unit"],
                "low": item["low"],
                "base": item["base"],
                "high": item["high"],
                "low_basis": item.get("basis", ""),
                "high_basis": item.get("basis", ""),
                "note": f"status={item.get('validation_status', 'candidate')}; {item.get('notes', '')}".strip("; "),
            }
        )
    return rows


def monte_carlo_cases(rows: list[dict[str, Any]], draws: int, seed: int) -> list[dict[str, Any]]:
    """Triangular draws between low and high with the base as mode, one column per parameter."""
    rng = random.Random(int(seed))
    parameters = [row for row in rows if row["low"] is not None and row["high"] is not None]
    cases = []
    for case in range(1, int(draws) + 1):
        record: dict[str, Any] = {"case_id": case}
        for row in parameters:
            low = float(row["low"])
            high = float(row["high"])
            base = float(row["base"])
            if high <= low:
                record[row["parameter_id"]] = base
                continue
            mode = min(max(base, low), high)
            record[row["parameter_id"]] = round(rng.triangular(low, high, mode), 6)
        cases.append(record)
    return cases


def percentiles(cases: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    if not cases:
        return {}
    summary: dict[str, dict[str, float]] = {}
    keys = [key for key in cases[0] if key != "case_id"]
    for key in keys:
        values = sorted(float(case[key]) for case in cases)
        quantiles = statistics.quantiles(values, n=10) if len(values) >= 2 else [values[0]] * 9
        summary[key] = {
            "p10": round(quantiles[0], 6),
            "p50": round(statistics.median(values), 6),
            "p90": round(quantiles[-1], 6),
            "mean": round(statistics.fmean(values), 6),
        }
    return summary
