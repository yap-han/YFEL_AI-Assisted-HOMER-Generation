"""Validation metrics for a synthesised load profile.

Includes the two random-variability figures HOMER Pro uses to perturb a
synthetic load, estimated from the generated series so that the analyst knows
what is already embedded in the imported file.
"""

from __future__ import annotations

import statistics
from typing import Any

from .calendar import DAYS_IN_MONTH, HOURS_PER_DAY, HOURS_PER_YEAR, hour_label, month_hour_ranges


def annual_kwh(series: list[float]) -> float:
    return round(sum(series), 1)


def peak_kw(series: list[float]) -> tuple[float, int]:
    index = max(range(len(series)), key=lambda position: series[position])
    return round(series[index], 3), index


def load_factor(series: list[float]) -> float:
    peak, _ = peak_kw(series)
    return round(statistics.fmean(series) / peak, 4) if peak else 0.0


def specific_energy(series: list[float], production_t_per_year: float) -> float:
    kilograms = production_t_per_year * 1000.0
    return round(sum(series) / kilograms, 3) if kilograms else float("inf")


def fraction(part: list[float], whole: list[float]) -> float:
    total = sum(whole)
    return round(sum(part) / total, 4) if total else 0.0


def daily_energy(series: list[float]) -> list[float]:
    return [sum(series[day * HOURS_PER_DAY:(day + 1) * HOURS_PER_DAY]) for day in range(len(series) // HOURS_PER_DAY)]


def monthly_energy(series: list[float]) -> list[float]:
    return [round(sum(series[start:end]), 1) for start, end in month_hour_ranges()]


def day_to_day_variability_pct(series: list[float]) -> float:
    """Standard deviation of daily energy relative to the month's mean daily energy, in percent."""
    daily = daily_energy(series)
    ratios: list[float] = []
    day_cursor = 0
    for days in DAYS_IN_MONTH:
        block = daily[day_cursor:day_cursor + days]
        mean = statistics.fmean(block)
        if mean > 0:
            ratios.extend(value / mean for value in block)
        day_cursor += days
    return round(100.0 * statistics.pstdev(ratios), 2) if len(ratios) > 1 else 0.0


def timestep_variability_pct(series: list[float]) -> float:
    """Standard deviation of hourly values around each month's mean normalised daily shape, in percent."""
    daily = daily_energy(series)
    ratios: list[float] = []
    day_cursor = 0
    for month_index, days in enumerate(DAYS_IN_MONTH):
        shape = [0.0] * HOURS_PER_DAY
        counted = 0
        for day in range(day_cursor, day_cursor + days):
            energy = daily[day]
            if energy <= 0:
                continue
            for hour in range(HOURS_PER_DAY):
                shape[hour] += HOURS_PER_DAY * series[day * HOURS_PER_DAY + hour] / energy
            counted += 1
        if counted == 0:
            day_cursor += days
            continue
        shape = [value / counted for value in shape]
        for day in range(day_cursor, day_cursor + days):
            energy = daily[day]
            if energy <= 0:
                continue
            for hour in range(HOURS_PER_DAY):
                expected = shape[hour] * energy / HOURS_PER_DAY
                if expected > 0:
                    ratios.append(series[day * HOURS_PER_DAY + hour] / expected)
        day_cursor += days
    return round(100.0 * statistics.pstdev(ratios), 2) if len(ratios) > 1 else 0.0


def average_daily_profile(series: list[float]) -> list[float]:
    days = len(series) // HOURS_PER_DAY
    return [round(sum(series[day * HOURS_PER_DAY + hour] for day in range(days)) / days, 3) for hour in range(HOURS_PER_DAY)]


def benchmark_check(metrics: dict[str, Any], benchmarks: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare metrics with literature bands; warn within 25 percent of a band, fail beyond."""
    rows = []
    for key, band in benchmarks.get("bands", {}).items():
        value = metrics.get(key)
        if value is None:
            continue
        low = float(band["low"])
        high = float(band["high"])
        span = high - low
        if low <= value <= high:
            status = "pass"
        elif low - 0.25 * span <= value <= high + 0.25 * span:
            status = "warn"
        else:
            status = "fail"
        rows.append(
            {
                "metric": key,
                "value": value,
                "band_low": low,
                "band_high": high,
                "anchor": band.get("anchor"),
                "status": status,
                "source": band.get("source"),
            }
        )
    return rows


def summarise_load(
    total: list[float],
    critical: list[float],
    essential: list[float],
    deferrable: list[float],
    by_ontology_kwh: dict[str, float],
    production_t_per_year: float,
    benchmarks: dict[str, Any],
) -> dict[str, Any]:
    if len(total) != HOURS_PER_YEAR:
        raise ValueError("Load summary requires an 8,760-hour series")
    peak, peak_index = peak_kw(total)
    energy = annual_kwh(total)
    aeration = by_ontology_kwh.get("load.aeration_power", 0.0) + by_ontology_kwh.get("load.oxygenation_power", 0.0)
    cooling = by_ontology_kwh.get("load.cooling_power", 0.0)
    metrics: dict[str, Any] = {
        "annual_kwh": energy,
        "daily_average_kwh": round(energy / 365.0, 1),
        "mean_kw": round(statistics.fmean(total), 2),
        "peak_kw": peak,
        "peak_hour_index": peak_index,
        "peak_time": hour_label(peak_index),
        "load_factor": load_factor(total),
        "kwh_per_kg": specific_energy(total, production_t_per_year),
        "critical_fraction": fraction(critical, total),
        "essential_fraction": fraction(essential, total),
        "deferrable_fraction": fraction(deferrable, total),
        "critical_peak_kw": peak_kw(critical)[0],
        "aeration_oxygenation_share": round(aeration / energy, 4) if energy else 0.0,
        "cooling_share": round(cooling / energy, 4) if energy else 0.0,
        "share_by_ontology_id": {key: round(value / energy, 4) for key, value in sorted(by_ontology_kwh.items())} if energy else {},
        "monthly_kwh": monthly_energy(total),
        "average_daily_profile_kw": average_daily_profile(total),
        "homer_day_to_day_variability_pct": day_to_day_variability_pct(total),
        "homer_timestep_variability_pct": timestep_variability_pct(total),
    }
    metrics["benchmark_checks"] = benchmark_check(metrics, benchmarks)
    metrics["benchmark_status"] = (
        "fail" if any(row["status"] == "fail" for row in metrics["benchmark_checks"])
        else "warn" if any(row["status"] == "warn" for row in metrics["benchmark_checks"])
        else "pass"
    )
    return metrics
