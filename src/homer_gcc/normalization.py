from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedRange:
    low: float
    central: float
    high: float
    canonical_unit: str
    unit_factor: float
    fx_rate: float | None
    escalation_factor: float
    source_cost_year: int | None
    target_cost_year: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "central": self.central,
            "high": self.high,
            "canonical_unit": self.canonical_unit,
            "unit_factor": self.unit_factor,
            "fx_rate": self.fx_rate,
            "escalation_factor": self.escalation_factor,
            "source_cost_year": self.source_cost_year,
            "target_cost_year": self.target_cost_year,
        }


UNIT_ALIASES = {
    "percent": "%",
    "percentage": "%",
    "1": "fraction",
    "pu": "fraction",
    "per unit": "fraction",
    "years": "year",
    "yr": "year",
    "yrs": "year",
    "hours": "hour",
    "hr": "hour",
    "hrs": "hour",
    "h": "hour",
    "minutes": "minute",
    "min": "minute",
    "days": "day",
    "w": "W",
    "kw": "kW",
    "mw": "MW",
    "wh": "Wh",
    "kwh": "kWh",
    "mwh": "MWh",
    "usd/kw": "USD/kW",
    "usd/w": "USD/W",
    "usd/kwdc": "USD/kWdc",
    "usd/wdc": "USD/Wdc",
    "usd/kwh": "USD/kWh",
    "usd/wh": "USD/Wh",
    "usd/year": "USD/year",
    "usd/operating_hour": "USD/operating_hour",
    "usd/gj": "USD/GJ",
    "usd/l": "USD/L",
    "kgco2e/kwh": "kgCO2e/kWh",
    "gco2e/kwh": "gCO2e/kWh",
}


# unit -> (dimension, factor to the dimension base unit)
UNITS = {
    "W": ("power", 0.001),
    "kW": ("power", 1.0),
    "MW": ("power", 1000.0),
    "Wh": ("energy", 0.001),
    "kWh": ("energy", 1.0),
    "MWh": ("energy", 1000.0),
    "GWh": ("energy", 1_000_000.0),
    "fraction": ("ratio", 1.0),
    "%": ("ratio", 0.01),
    "minute": ("time", 1.0 / 60.0),
    "hour": ("time", 1.0),
    "day": ("time", 24.0),
    "year": ("time", 8760.0),
    "USD/W": ("cost_power_ac", 1000.0),
    "USD/kW": ("cost_power_ac", 1.0),
    "USD/Wdc": ("cost_power_dc", 1000.0),
    "USD/kWdc": ("cost_power_dc", 1.0),
    "USD/Wh": ("cost_energy", 1000.0),
    "USD/kWh": ("cost_energy", 1.0),
    "gCO2e/kWh": ("emissions_energy", 0.001),
    "kgCO2e/kWh": ("emissions_energy", 1.0),
}


IDENTITY_UNITS = {
    "USD/year",
    "USD/operating_hour",
    "USD/GJ",
    "USD/L",
    "USD/kW/month",
    "events/year",
    "events/month",
    "kg/kWh",
    "g/kWh",
    "MJ/kWh",
    "MJ/kg",
    "MJ/L",
    "Btu/kWh",
    "kWh/m2/hour",
    "W/m2",
    "kWh/m2/day",
    "%/year",
    "fraction/year",
    "%/degC",
    "1/degC",
    "kg/m3",
    "g/L",
    "t/year",
    "kg/year",
    "count",
    "none",
}


def canonicalize_unit(unit: str) -> str:
    cleaned = " ".join(str(unit).strip().split())
    return UNIT_ALIASES.get(cleaned.lower(), cleaned)


def _local_currency_conversion(
    from_unit: str,
    to_unit: str,
    fx_rate: float | None,
) -> tuple[str, float]:
    if not from_unit.lower().startswith("local_currency/"):
        return from_unit, 1.0
    if fx_rate is None or fx_rate <= 0:
        raise NormalizationError(
            f"Conversion from {from_unit} to {to_unit} requires a positive fx_rate in USD per local-currency unit"
        )
    converted = "USD/" + from_unit.split("/", 1)[1]
    return converted, float(fx_rate)


def conversion_factor(
    from_unit: str,
    to_unit: str,
    fx_rate: float | None = None,
) -> tuple[float, float | None]:
    source = canonicalize_unit(from_unit)
    target = canonicalize_unit(to_unit)
    source, currency_factor = _local_currency_conversion(source, target, fx_rate)
    source = canonicalize_unit(source)

    if source == target:
        return currency_factor, fx_rate if currency_factor != 1.0 else None
    if source in IDENTITY_UNITS or target in IDENTITY_UNITS:
        raise NormalizationError(f"No deterministic conversion rule from {from_unit} to {to_unit}")
    if source not in UNITS or target not in UNITS:
        raise NormalizationError(f"Unsupported unit conversion from {from_unit} to {to_unit}")
    source_dimension, source_factor = UNITS[source]
    target_dimension, target_factor = UNITS[target]
    if source_dimension != target_dimension:
        raise NormalizationError(
            f"Incompatible unit dimensions: {from_unit} ({source_dimension}) and {to_unit} ({target_dimension})"
        )
    return currency_factor * source_factor / target_factor, fx_rate if currency_factor != 1.0 else None


def escalation_factor(
    source_cost_year: int | None,
    target_cost_year: int | None,
    annual_escalation_rate: float | None,
) -> float:
    if source_cost_year is None or target_cost_year is None or source_cost_year == target_cost_year:
        return 1.0
    if annual_escalation_rate is None:
        raise NormalizationError(
            "Cost-year normalization requires annual_escalation_rate when source and target years differ"
        )
    if annual_escalation_rate <= -1:
        raise NormalizationError("annual_escalation_rate must be greater than -1")
    return (1.0 + annual_escalation_rate) ** (target_cost_year - source_cost_year)


def normalize_range(
    low: float | None,
    central: float,
    high: float | None,
    from_unit: str,
    to_unit: str,
    *,
    fx_rate: float | None = None,
    source_cost_year: int | None = None,
    target_cost_year: int | None = None,
    annual_escalation_rate: float | None = None,
) -> NormalizedRange:
    low_value = float(central if low is None else low)
    central_value = float(central)
    high_value = float(central if high is None else high)
    if low_value > central_value or central_value > high_value:
        raise NormalizationError("Expected low <= central <= high")
    unit_factor, applied_fx = conversion_factor(from_unit, to_unit, fx_rate)
    cost_factor = escalation_factor(source_cost_year, target_cost_year, annual_escalation_rate)
    total_factor = unit_factor * cost_factor
    return NormalizedRange(
        low=low_value * total_factor,
        central=central_value * total_factor,
        high=high_value * total_factor,
        canonical_unit=canonicalize_unit(to_unit),
        unit_factor=unit_factor,
        fx_rate=applied_fx,
        escalation_factor=cost_factor,
        source_cost_year=source_cost_year,
        target_cost_year=target_cost_year,
    )


def physical_range_check(
    low: float,
    high: float,
    validation_rule: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    rule = validation_rule or {}
    reasons: list[str] = []
    if rule.get("min") is not None and low < float(rule["min"]):
        reasons.append(f"value below physical minimum {rule['min']}")
    if rule.get("max") is not None and high > float(rule["max"]):
        reasons.append(f"value above physical maximum {rule['max']}")
    return not reasons, reasons
