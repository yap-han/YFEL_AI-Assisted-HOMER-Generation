"""NASA POWER hourly weather retrieval, cleaning and HOMER-unit conversion.

Data source: NASA Prediction Of Worldwide Energy Resources (POWER) hourly API,
``community=RE``, local solar time standard. The API is free, needs no key and
returns one calendar year per request. Raw responses are cached so that a rerun
is reproducible and auditable without a second network call.
"""

from __future__ import annotations

import json
import math
import statistics
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .calendar import (
    DAYS_IN_MONTH,
    HOURS_PER_DAY,
    KLEIN_REPRESENTATIVE_DAYS,
    MONTH_NAMES,
    hour_keys,
    month_hour_ranges,
)

POWER_ENDPOINT = "https://power.larc.nasa.gov/api/temporal/hourly/point"
DEFAULT_PARAMETERS = ("ALLSKY_SFC_SW_DWN", "T2M", "WS10M", "RH2M")
FILL_VALUE = -999.0
SOLAR_CONSTANT_KW_M2 = 1.367

# Physical plausibility limits applied after filling, in HOMER units.
PHYSICAL_LIMITS = {
    "ghi_kw_m2": (0.0, 1.4),
    "temperature_c": (-15.0, 60.0),
    "wind_ms": (0.0, 50.0),
    "humidity_pct": (0.0, 100.0),
}


class WeatherError(ValueError):
    pass


def build_power_url(
    latitude: float,
    longitude: float,
    year: int,
    parameters: tuple[str, ...] = DEFAULT_PARAMETERS,
) -> str:
    return (
        f"{POWER_ENDPOINT}?parameters={','.join(parameters)}&community=RE"
        f"&longitude={longitude}&latitude={latitude}"
        f"&start={year}0101&end={year}1231&time-standard=LST&format=JSON"
    )


def cache_path(cache_dir: str | Path, latitude: float, longitude: float, year: int) -> Path:
    return Path(cache_dir) / f"nasa_power_{latitude:.3f}_{longitude:.3f}_{year}.json"


def tile_fixture_payload(payload: dict[str, Any], year: int) -> dict[str, Any]:
    """Expand a short fixture (for example 48 hours) into a full-year payload.

    Used only for offline tests. The tiled payload is marked so it can never be
    mistaken for downloaded data.
    """
    keys = hour_keys(year)
    tiled: dict[str, Any] = {}
    source_hours = 0
    for name, series in payload["properties"]["parameter"].items():
        values = [series[key] for key in sorted(series)]
        if not values:
            raise WeatherError(f"Fixture parameter {name} is empty")
        source_hours = len(values)
        tiled[name] = {key: values[index % len(values)] for index, key in enumerate(keys)}
    return {
        "type": "Feature",
        "geometry": payload.get("geometry", {}),
        "header": {**payload.get("header", {}), "fixture_tiled": True, "fixture_source_hours": source_hours},
        "parameters": payload.get("parameters", {}),
        "properties": {"parameter": tiled},
    }


def fetch_power_year(
    latitude: float,
    longitude: float,
    year: int,
    cache_dir: str | Path,
    *,
    parameters: tuple[str, ...] = DEFAULT_PARAMETERS,
    timeout: float = 90.0,
    fixture: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(payload, provenance)`` for one year, from fixture, cache or the API."""
    accessed = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if fixture is not None:
        payload = tile_fixture_payload(json.loads(Path(fixture).read_text(encoding="utf-8")), year)
        return payload, {"source": "fixture", "path": str(fixture), "year": year, "accessed": accessed}

    target = cache_path(cache_dir, latitude, longitude, year)
    url = build_power_url(latitude, longitude, year, parameters)
    if target.is_file():
        payload = json.loads(target.read_text(encoding="utf-8"))
        return payload, {"source": "cache", "path": str(target), "url": url, "year": year, "accessed": accessed}

    request = urllib.request.Request(url, headers={"User-Agent": "homer-gcc-site-layer/0.7"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WeatherError(f"NASA POWER request failed for {year}: {exc}") from exc
    payload = json.loads(body.decode("utf-8"))
    if "properties" not in payload or "parameter" not in payload["properties"]:
        raise WeatherError(f"NASA POWER response for {year} has no parameter block")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return payload, {"source": "download", "path": str(target), "url": url, "year": year, "accessed": accessed}


def parse_power_year(payload: dict[str, Any], year: int) -> dict[str, list[float | None]]:
    """Map each parameter to 8,760 values in calendar order; missing hours become ``None``."""
    keys = hour_keys(year)
    block = payload["properties"]["parameter"]
    parsed: dict[str, list[float | None]] = {}
    for name, series in block.items():
        values: list[float | None] = []
        for key in keys:
            raw = series.get(key)
            if raw is None or float(raw) == FILL_VALUE:
                values.append(None)
            else:
                values.append(float(raw))
        parsed[name] = values
    return parsed


def fill_gaps(
    values: list[float | None],
    *,
    max_interp_hours: int = 6,
    max_fill_fraction: float = 0.02,
) -> tuple[list[float], dict[str, Any]]:
    """Fill missing hours deterministically.

    Runs of up to ``max_interp_hours`` are linearly interpolated. Longer runs
    take the mean of the same hour on the nearest available days. The function
    refuses series whose missing fraction exceeds ``max_fill_fraction``.
    """
    n = len(values)
    if n == 0:
        raise WeatherError("Cannot fill an empty series")
    missing = [index for index, value in enumerate(values) if value is None]
    fraction = len(missing) / n
    if fraction > max_fill_fraction:
        raise WeatherError(
            f"{len(missing)} of {n} hours are missing ({fraction:.1%}), above the {max_fill_fraction:.1%} limit"
        )
    if len(missing) == n:
        raise WeatherError("Every hour is missing")
    filled: list[float | None] = list(values)
    interpolated = 0
    day_filled = 0
    index = 0
    while index < n:
        if filled[index] is not None:
            index += 1
            continue
        start = index
        while index < n and filled[index] is None:
            index += 1
        end = index  # exclusive
        run = end - start
        previous = filled[start - 1] if start > 0 else None
        following = filled[end] if end < n else None
        if run <= max_interp_hours and previous is not None and following is not None:
            for offset in range(run):
                weight = (offset + 1) / (run + 1)
                filled[start + offset] = previous + (following - previous) * weight
            interpolated += run
        else:
            for position in range(start, end):
                neighbours = []
                for step in (HOURS_PER_DAY, -HOURS_PER_DAY, 2 * HOURS_PER_DAY, -2 * HOURS_PER_DAY):
                    candidate = position + step
                    if 0 <= candidate < n and values[candidate] is not None:
                        neighbours.append(float(values[candidate]))
                if not neighbours:
                    known = [float(value) for value in values if value is not None]
                    neighbours = [statistics.fmean(known)]
                filled[position] = statistics.fmean(neighbours)
            day_filled += run
    result = [float(value) for value in filled]  # type: ignore[arg-type]
    return result, {
        "missing_hours": len(missing),
        "missing_fraction": round(fraction, 5),
        "interpolated_hours": interpolated,
        "day_pattern_filled_hours": day_filled,
    }


def to_homer_units(parsed: dict[str, list[float]]) -> dict[str, list[float]]:
    """Convert NASA POWER units to the units HOMER dialogs expect.

    ``ALLSKY_SFC_SW_DWN`` is hourly irradiation in Wh/m2, numerically equal to
    the mean W/m2 over the hour; HOMER wants kW/m2. Temperature stays in degC,
    wind in m/s at 10 m, humidity in percent.
    """
    out: dict[str, list[float]] = {}
    if "ALLSKY_SFC_SW_DWN" in parsed:
        out["ghi_kw_m2"] = [round(max(0.0, value) / 1000.0, 4) for value in parsed["ALLSKY_SFC_SW_DWN"]]
    if "T2M" in parsed:
        out["temperature_c"] = [round(value, 2) for value in parsed["T2M"]]
    if "WS10M" in parsed:
        out["wind_ms"] = [round(max(0.0, value), 2) for value in parsed["WS10M"]]
    if "RH2M" in parsed:
        out["humidity_pct"] = [round(min(100.0, max(0.0, value)), 1) for value in parsed["RH2M"]]
    for name, series in out.items():
        low, high = PHYSICAL_LIMITS[name]
        bad = [value for value in series if value < low or value > high]
        if bad:
            raise WeatherError(f"{name} has {len(bad)} values outside [{low}, {high}], first {bad[0]}")
    return out


def load_year(
    latitude: float,
    longitude: float,
    year: int,
    cache_dir: str | Path,
    *,
    fixture: str | Path | None = None,
    max_fill_fraction: float = 0.02,
) -> tuple[dict[str, list[float]], dict[str, Any]]:
    """Fetch, parse, fill and convert one year. Returns ``(series, provenance)``."""
    payload, provenance = fetch_power_year(latitude, longitude, year, cache_dir, fixture=fixture)
    parsed = parse_power_year(payload, year)
    filled: dict[str, list[float]] = {}
    fill_stats: dict[str, Any] = {}
    for name, values in parsed.items():
        series, stats = fill_gaps(values, max_fill_fraction=max_fill_fraction)
        filled[name] = series
        fill_stats[name] = stats
    converted = to_homer_units(filled)
    provenance["fill_stats"] = fill_stats
    coordinates = payload.get("geometry", {}).get("coordinates") or [None, None, None]
    provenance["elevation_m"] = coordinates[2] if len(coordinates) > 2 else None
    header = payload.get("header", {})
    provenance["api_header"] = {
        key: header.get(key) for key in ("title", "sources", "time_standard", "fixture_tiled")
    }
    return converted, provenance


def annual_ghi_kwh_m2(ghi_kw_m2: list[float]) -> float:
    return round(sum(ghi_kw_m2), 1)


def select_reference_years(annual_by_year: dict[int, float]) -> dict[str, Any]:
    """Base = year nearest the median annual GHI, low = minimum, high = maximum."""
    if not annual_by_year:
        raise WeatherError("No years to select from")
    median = statistics.median(annual_by_year.values())
    base = min(sorted(annual_by_year), key=lambda year: (abs(annual_by_year[year] - median), year))
    low = min(sorted(annual_by_year), key=lambda year: (annual_by_year[year], year))
    high = max(sorted(annual_by_year), key=lambda year: (annual_by_year[year], -year))
    return {"base": base, "low": low, "high": high, "median_kwh_m2": round(median, 1)}


def extraterrestrial_daily_kwh_m2(latitude_deg: float, day_of_year: int) -> float:
    """Daily extraterrestrial irradiation on a horizontal surface (Duffie and Beckman)."""
    phi = math.radians(latitude_deg)
    declination = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0)))
    cos_omega = -math.tan(phi) * math.tan(declination)
    cos_omega = max(-1.0, min(1.0, cos_omega))
    omega_s = math.acos(cos_omega)
    eccentricity = 1.0 + 0.033 * math.cos(math.radians(360.0 * day_of_year / 365.0))
    return (
        (24.0 / math.pi)
        * SOLAR_CONSTANT_KW_M2
        * eccentricity
        * (
            math.cos(phi) * math.cos(declination) * math.sin(omega_s)
            + omega_s * math.sin(phi) * math.sin(declination)
        )
    )


def monthly_summary(series: dict[str, list[float]], latitude_deg: float) -> list[dict[str, Any]]:
    """Twelve rows of the statistics HOMER shows on its resource pages."""
    ghi = series["ghi_kw_m2"]
    temperature = series.get("temperature_c")
    wind = series.get("wind_ms")
    humidity = series.get("humidity_pct")
    rows = []
    for month_index, (start, end) in enumerate(month_hour_ranges()):
        days = DAYS_IN_MONTH[month_index]
        daily_ghi = sum(ghi[start:end]) / days
        h0 = extraterrestrial_daily_kwh_m2(latitude_deg, KLEIN_REPRESENTATIVE_DAYS[month_index])
        row: dict[str, Any] = {
            "month": month_index + 1,
            "month_name": MONTH_NAMES[month_index],
            "ghi_kwh_m2_day": round(daily_ghi, 3),
            "extraterrestrial_kwh_m2_day": round(h0, 3),
            "clearness_index": round(daily_ghi / h0, 3) if h0 > 0 else None,
        }
        if temperature is not None:
            block = temperature[start:end]
            row["temp_mean_c"] = round(statistics.fmean(block), 2)
            row["temp_max_c"] = round(max(block), 2)
            row["hours_above_35c"] = sum(1 for value in block if value > 35.0)
            row["hours_above_40c"] = sum(1 for value in block if value > 40.0)
        if wind is not None:
            row["wind_mean_ms"] = round(statistics.fmean(wind[start:end]), 2)
        if humidity is not None:
            row["rh_mean_pct"] = round(statistics.fmean(humidity[start:end]), 1)
        rows.append(row)
    return rows


def parse_year_range(text: str) -> list[int]:
    """Turn ``2015-2024`` or ``2020,2021`` into a sorted list of years."""
    years: set[int] = set()
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, last = part.split("-", 1)
            years.update(range(int(first), int(last) + 1))
        else:
            years.add(int(part))
    if not years:
        raise WeatherError("No years specified")
    return sorted(years)


def load_weather_years(
    latitude: float,
    longitude: float,
    years: list[int],
    cache_dir: str | Path,
    *,
    fixture: str | Path | None = None,
    max_fill_fraction: float = 0.02,
) -> tuple[dict[int, dict[str, list[float]]], list[dict[str, Any]]]:
    data: dict[int, dict[str, list[float]]] = {}
    provenance: list[dict[str, Any]] = []
    for year in years:
        series, record = load_year(
            latitude, longitude, year, cache_dir, fixture=fixture, max_fill_fraction=max_fill_fraction
        )
        data[year] = series
        record["annual_ghi_kwh_m2"] = annual_ghi_kwh_m2(series["ghi_kw_m2"])
        record["mean_temperature_c"] = round(statistics.fmean(series["temperature_c"]), 2)
        record["max_temperature_c"] = round(max(series["temperature_c"]), 2)
        provenance.append(record)
    return data, provenance
