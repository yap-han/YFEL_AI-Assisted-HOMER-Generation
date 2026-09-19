"""PVGIS typical meteorological year cross-check for the NASA POWER series.

PVGIS (European Commission JRC, v5.3) builds a TMY from SARAH3 irradiance and
ERA5 meteorology. Timestamps are UTC, so the hourly series is rolled to local
standard time before any comparison with NASA POWER.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .calendar import DAYS_IN_MONTH, HOURS_PER_YEAR, MONTH_NAMES, month_hour_ranges

PVGIS_TMY_ENDPOINT = "https://re.jrc.ec.europa.eu/api/v5_3/tmy"


class PVGISError(ValueError):
    pass


def build_pvgis_tmy_url(latitude: float, longitude: float) -> str:
    return f"{PVGIS_TMY_ENDPOINT}?lat={latitude}&lon={longitude}&outputformat=json"


def cache_path(cache_dir: str | Path, latitude: float, longitude: float) -> Path:
    return Path(cache_dir) / f"pvgis_tmy_{latitude:.3f}_{longitude:.3f}.json"


def tile_fixture_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload["outputs"]["tmy_hourly"]
    if not rows:
        raise PVGISError("PVGIS fixture has no rows")
    tiled = [dict(rows[index % len(rows)]) for index in range(HOURS_PER_YEAR)]
    return {
        "inputs": payload.get("inputs", {}),
        "outputs": {**payload["outputs"], "tmy_hourly": tiled},
        "fixture_tiled": True,
    }


def fetch_pvgis_tmy(
    latitude: float,
    longitude: float,
    cache_dir: str | Path,
    *,
    timeout: float = 90.0,
    fixture: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    accessed = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if fixture is not None:
        payload = tile_fixture_payload(json.loads(Path(fixture).read_text(encoding="utf-8")))
        return payload, {"source": "fixture", "path": str(fixture), "accessed": accessed}
    target = cache_path(cache_dir, latitude, longitude)
    url = build_pvgis_tmy_url(latitude, longitude)
    if target.is_file():
        return json.loads(target.read_text(encoding="utf-8")), {
            "source": "cache",
            "path": str(target),
            "url": url,
            "accessed": accessed,
        }
    request = urllib.request.Request(url, headers={"User-Agent": "homer-gcc-site-layer/0.7"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PVGISError(f"PVGIS request failed: {exc}") from exc
    payload = json.loads(body.decode("utf-8"))
    if "outputs" not in payload or "tmy_hourly" not in payload["outputs"]:
        raise PVGISError("PVGIS response has no tmy_hourly block")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return payload, {"source": "download", "path": str(target), "url": url, "accessed": accessed}


def roll_to_local(values: list[float], utc_offset_hours: int) -> list[float]:
    """Shift a UTC-indexed annual series so index 0 is local midnight on 1 January."""
    n = len(values)
    shift = int(utc_offset_hours) % n
    # Local hour i corresponds to UTC hour i - offset.
    return [values[(index - shift) % n] for index in range(n)]


def parse_pvgis_tmy(payload: dict[str, Any], utc_offset_hours: int) -> dict[str, Any]:
    rows = payload["outputs"]["tmy_hourly"]
    if len(rows) != HOURS_PER_YEAR:
        raise PVGISError(f"PVGIS TMY must have {HOURS_PER_YEAR} rows, found {len(rows)}")
    ghi = roll_to_local([float(row["G(h)"]) / 1000.0 for row in rows], utc_offset_hours)
    temperature = roll_to_local([float(row["T2m"]) for row in rows], utc_offset_hours)
    wind = roll_to_local([float(row["WS10m"]) for row in rows], utc_offset_hours)
    meta = payload.get("inputs", {}).get("meteo_data", {})
    return {
        "ghi_kw_m2": ghi,
        "temperature_c": temperature,
        "wind_ms": wind,
        "radiation_db": meta.get("radiation_db"),
        "meteo_db": meta.get("meteo_db"),
        "year_min": meta.get("year_min"),
        "year_max": meta.get("year_max"),
        "months_selected": payload.get("outputs", {}).get("months_selected"),
    }


def compare_ghi(
    nasa_ghi_kw_m2: list[float],
    pvgis_ghi_kw_m2: list[float],
    *,
    nasa_reference_annual: float | None = None,
    tolerance: float = 0.08,
) -> dict[str, Any]:
    nasa_annual = nasa_reference_annual if nasa_reference_annual is not None else sum(nasa_ghi_kw_m2)
    pvgis_annual = sum(pvgis_ghi_kw_m2)
    difference = (pvgis_annual - nasa_annual) / nasa_annual if nasa_annual else float("inf")
    monthly = []
    for month_index, (start, end) in enumerate(month_hour_ranges()):
        days = DAYS_IN_MONTH[month_index]
        nasa_month = sum(nasa_ghi_kw_m2[start:end]) / days
        pvgis_month = sum(pvgis_ghi_kw_m2[start:end]) / days
        monthly.append(
            {
                "month": MONTH_NAMES[month_index],
                "nasa_kwh_m2_day": round(nasa_month, 3),
                "pvgis_kwh_m2_day": round(pvgis_month, 3),
                "difference_pct": round(100.0 * (pvgis_month - nasa_month) / nasa_month, 1) if nasa_month else None,
            }
        )
    return {
        "status": "pass" if abs(difference) <= tolerance else "warn",
        "tolerance_pct": round(100.0 * tolerance, 1),
        "nasa_annual_kwh_m2": round(nasa_annual, 1),
        "pvgis_annual_kwh_m2": round(pvgis_annual, 1),
        "difference_pct": round(100.0 * difference, 2),
        "monthly": monthly,
    }
