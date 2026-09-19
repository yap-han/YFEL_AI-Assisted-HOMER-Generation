"""Equipment-based synthesis of an hourly aquaculture electrical load profile.

The model is bottom-up: every inventory item has a rated power, a quantity, a
duty cycle and a driver that shapes it through the year. Drivers are
deterministic functions of the calendar, the biomass cycle and the fetched
ambient temperature. A seeded Gaussian noise term adds the short-term
variability HOMER would otherwise synthesise. Because the seed is stored, the
same specification always produces the same file.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from .calendar import HOURS_PER_DAY, HOURS_PER_YEAR, day_of_year, hour_of_day

DRIVERS = {"constant", "schedule", "diurnal_do", "temperature"}
CRITICALITIES = {"critical", "essential", "deferrable"}
SCHEDULE_TYPES = {"continuous", "hours", "events_per_day"}


class LoadModelError(ValueError):
    pass


@dataclass
class LoadResult:
    total: list[float]
    critical: list[float]
    essential: list[float]
    deferrable: list[float]
    by_equipment: dict[str, list[float]]
    by_ontology_id_kwh: dict[str, float]
    by_equipment_kwh: dict[str, float]
    water_temperature_c: list[float]
    assumptions: list[str] = field(default_factory=list)


def validate_inventory(archetype: dict[str, Any], ontology: dict[str, Any] | None = None) -> list[str]:
    """Return a list of error strings; empty means the archetype is usable."""
    errors: list[str] = []
    allowed_ids: set[str] | None = None
    if ontology is not None:
        allowed_ids = set()
        for family in ontology.get("families", []):
            if family.get("id") == "aquaculture_load":
                allowed_ids.update(parameter["id"] for parameter in family.get("parameters", []))
    items = archetype.get("equipment")
    if not isinstance(items, list) or not items:
        return ["archetype.equipment must be a non-empty list"]
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"equipment[{index}]"
        item_id = item.get("id")
        if not item_id:
            errors.append(f"{where}: missing id")
        elif item_id in seen:
            errors.append(f"{where}: duplicate id {item_id}")
        seen.add(str(item_id))
        ontology_id = item.get("ontology_id")
        if allowed_ids is not None and ontology_id not in allowed_ids:
            errors.append(f"{where}: ontology_id {ontology_id!r} is not in the aquaculture_load family")
        try:
            if float(item.get("rated_kw", 0)) <= 0:
                errors.append(f"{where}: rated_kw must be positive")
        except (TypeError, ValueError):
            errors.append(f"{where}: rated_kw must be numeric")
        try:
            if int(item.get("quantity", 0)) < 1:
                errors.append(f"{where}: quantity must be at least 1")
        except (TypeError, ValueError):
            errors.append(f"{where}: quantity must be an integer")
        duty = item.get("duty_cycle", 1.0)
        if not isinstance(duty, (int, float)) or not 0 <= float(duty) <= 1:
            errors.append(f"{where}: duty_cycle must be within [0, 1]")
        if item.get("driver", "constant") not in DRIVERS:
            errors.append(f"{where}: driver must be one of {sorted(DRIVERS)}")
        if item.get("criticality") not in CRITICALITIES:
            errors.append(f"{where}: criticality must be one of {sorted(CRITICALITIES)}")
        share = item.get("critical_share")
        if share is not None and (not isinstance(share, (int, float)) or not 0 <= float(share) <= 1):
            errors.append(f"{where}: critical_share must be within [0, 1]")
        schedule = item.get("schedule") or {"type": "continuous"}
        if schedule.get("type", "continuous") not in SCHEDULE_TYPES:
            errors.append(f"{where}: schedule.type must be one of {sorted(SCHEDULE_TYPES)}")
    return errors


def biomass_index(config: dict[str, Any], hour_index: int) -> float:
    """Relative standing biomass, 1.0 at the annual mean."""
    mode = config.get("mode", "constant")
    doy = day_of_year(hour_index)
    if mode == "constant":
        return 1.0
    amplitude = float(config.get("amplitude", 0.1))
    if mode == "staggered":
        peak = int(config.get("peak_doy", 300))
        return 1.0 + amplitude * math.sin(2.0 * math.pi * (doy - peak) / 365.0 + math.pi / 2.0)
    if mode == "batch":
        harvest = int(config.get("harvest_doy", 330))
        phase = ((doy - harvest) % 365) / 365.0
        floor = 1.0 - amplitude
        ceiling = 1.0 + amplitude
        return floor + (ceiling - floor) * phase
    raise LoadModelError(f"Unknown biomass mode {mode!r}")


def water_temperature(config: dict[str, Any], ambient_c: list[float]) -> list[float]:
    mode = config.get("mode", "setpoint")
    if mode == "setpoint":
        return [float(config.get("setpoint_c", 28.0))] * len(ambient_c)
    if mode == "ambient_lag":
        lag_hours = int(config.get("lag_days", 7)) * HOURS_PER_DAY
        offset = float(config.get("offset_c", 0.0))
        series = []
        running = 0.0
        window: list[float] = []
        for value in ambient_c:
            window.append(value)
            running += value
            if len(window) > lag_hours:
                running -= window.pop(0)
            series.append(running / len(window) + offset)
        return series
    raise LoadModelError(f"Unknown water temperature mode {mode!r}")


def metabolic_factor(config: dict[str, Any], water_c: float) -> float:
    k = float(config.get("k_per_degc", 0.0))
    t_ref = float(config.get("t_ref_c", 28.0))
    low, high = config.get("clip", [0.85, 1.15])
    return max(float(low), min(float(high), 1.0 + k * (water_c - t_ref)))


def diurnal_factor(config: dict[str, Any], hour: int) -> float:
    amplitude = float(config.get("amplitude", 0.15))
    peak_hour = float(config.get("peak_hour", 4))
    return 1.0 + amplitude * math.cos(2.0 * math.pi * (hour - peak_hour) / 24.0)


def schedule_factor(schedule: dict[str, Any] | None, hour: int) -> float:
    if not schedule:
        return 1.0
    kind = schedule.get("type", "continuous")
    if kind == "continuous":
        return 1.0
    if kind == "hours":
        return 1.0 if hour in {int(value) for value in schedule.get("on", [])} else 0.0
    if kind == "events_per_day":
        count = int(schedule.get("count", 1))
        minutes = float(schedule.get("minutes", 15))
        first = int(schedule.get("first_hour", 6))
        span = int(schedule.get("span_hours", 16))
        if count <= 0:
            return 0.0
        step = span / count
        for event in range(count):
            event_hour = int(first + event * step) % HOURS_PER_DAY
            if event_hour == hour:
                return min(1.0, minutes / 60.0)
        return 0.0
    raise LoadModelError(f"Unknown schedule type {kind!r}")


def cooling_kw(item: dict[str, Any], cooling: dict[str, Any], ambient_c: float) -> float:
    """Electrical demand of a temperature-driven cooling item."""
    params = {**cooling, **item.get("cooling", {})}
    t_set = float(params.get("t_set_c", 28.0))
    k_th = float(params.get("k_th_kw_per_degc", 0.0))
    q_base = float(params.get("q_base_kw_th", 0.0))
    cop_nominal = float(params.get("cop_nominal", 3.0))
    derate = float(params.get("cop_derate_per_degc_above_35", 0.02))
    cop_floor = float(params.get("cop_floor", 0.6))
    thermal = max(0.0, k_th * (ambient_c - t_set) + q_base)
    cop = cop_nominal * max(cop_floor, 1.0 - derate * max(0.0, ambient_c - 35.0))
    electrical = thermal / cop if cop > 0 else 0.0
    cap = float(item["rated_kw"]) * int(item.get("quantity", 1))
    return min(cap, electrical)


def synthesise_load(
    spec: dict[str, Any],
    archetype: dict[str, Any],
    weather: dict[str, list[float]],
    seed: int,
) -> LoadResult:
    ambient = weather["temperature_c"]
    if len(ambient) != HOURS_PER_YEAR:
        raise LoadModelError(f"Weather series must have {HOURS_PER_YEAR} hours")
    errors = validate_inventory(archetype)
    if errors:
        raise LoadModelError("; ".join(errors))

    production = float(spec.get("production_t_per_year", archetype.get("reference_production_t_per_year", 1)))
    reference = float(archetype.get("reference_production_t_per_year", production))
    scale = production / reference if archetype.get("scale_with_production", True) and reference else 1.0

    noise_config = (spec.get("load") or {}).get("noise", {})
    clip_low, clip_high = (spec.get("load") or {}).get("noise_clip", [0.8, 1.2])
    rng = random.Random(int(seed))

    water = water_temperature(archetype.get("water_temperature", {}), ambient)
    biomass_config = archetype.get("biomass", {"mode": "constant"})
    metabolic_config = archetype.get("metabolic", {})
    diurnal_config = archetype.get("aeration_diurnal", {})
    cooling_config = archetype.get("cooling", {})

    biomass = [biomass_index(biomass_config, index) for index in range(HOURS_PER_YEAR)]
    metabolic = [metabolic_factor(metabolic_config, water[index]) for index in range(HOURS_PER_YEAR)]
    diurnal = [diurnal_factor(diurnal_config, hour) for hour in range(HOURS_PER_DAY)]

    total = [0.0] * HOURS_PER_YEAR
    critical = [0.0] * HOURS_PER_YEAR
    essential = [0.0] * HOURS_PER_YEAR
    deferrable = [0.0] * HOURS_PER_YEAR
    by_equipment: dict[str, list[float]] = {}
    by_ontology_kwh: dict[str, float] = {}
    by_equipment_kwh: dict[str, float] = {}

    for item in sorted(archetype["equipment"], key=lambda entry: str(entry["id"])):
        item_id = str(item["id"])
        rated = float(item["rated_kw"]) * int(item.get("quantity", 1))
        duty = float(item.get("duty_cycle", 1.0))
        driver = item.get("driver", "constant")
        exponent = float(item.get("biomass_scaling", 0.0))
        schedule = item.get("schedule")
        sigma = float(noise_config.get(item.get("noise_class", "other"), noise_config.get("other", 0.05)))
        criticality = item["criticality"]
        critical_share = float(item.get("critical_share", 1.0 if criticality == "critical" else 0.0))
        series = [0.0] * HOURS_PER_YEAR
        for index in range(HOURS_PER_YEAR):
            hour = hour_of_day(index)
            if driver == "temperature":
                power = cooling_kw(item, cooling_config, ambient[index]) * scale
            else:
                power = scale * rated * duty * (biomass[index] ** exponent)
                if driver == "diurnal_do":
                    power *= metabolic[index] * diurnal[hour]
                if driver == "schedule" or schedule:
                    power *= schedule_factor(schedule, hour)
            noise = max(clip_low, min(clip_high, rng.gauss(1.0, sigma))) if sigma > 0 else 1.0
            power = max(0.0, power * noise)
            series[index] = power
            total[index] += power
            critical[index] += power * critical_share
            remainder = power * (1.0 - critical_share)
            if criticality == "deferrable":
                deferrable[index] += remainder
            else:
                essential[index] += remainder
        by_equipment[item_id] = series
        energy = sum(series)
        by_equipment_kwh[item_id] = round(energy, 1)
        by_ontology_kwh[item["ontology_id"]] = round(by_ontology_kwh.get(item["ontology_id"], 0.0) + energy, 1)

    for index in range(HOURS_PER_YEAR):
        if abs(total[index] - (critical[index] + essential[index] + deferrable[index])) > 1e-6:
            raise LoadModelError("Load split does not sum to the total")

    assumptions = [
        f"Production scale factor {scale:.3f} against the archetype reference of {reference:g} t/yr",
        f"Biomass driver {biomass_config.get('mode', 'constant')}",
        f"Water temperature mode {archetype.get('water_temperature', {}).get('mode', 'setpoint')}",
        f"Noise seed {seed}, classes {noise_config}, clip [{clip_low}, {clip_high}]",
        "Cooling demand is a linear function of ambient dry-bulb temperature above the setpoint; latent load is not modelled",
    ]
    return LoadResult(
        total=[round(value, 3) for value in total],
        critical=[round(value, 3) for value in critical],
        essential=[round(value, 3) for value in essential],
        deferrable=[round(value, 3) for value in deferrable],
        by_equipment=by_equipment,
        by_ontology_id_kwh=by_ontology_kwh,
        by_equipment_kwh=by_equipment_kwh,
        water_temperature_c=[round(value, 2) for value in water],
        assumptions=assumptions,
    )
