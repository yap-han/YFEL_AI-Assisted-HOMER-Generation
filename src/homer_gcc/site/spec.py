"""Site specification loading, validation and path resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_SPEC_FIELDS = (
    "site_id",
    "country",
    "latitude",
    "longitude",
    "utc_offset_hours",
    "scenario_id",
    "archetype",
    "register",
    "production_t_per_year",
)


class SpecError(ValueError):
    pass


def resolve(base: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_site_spec(path: str | Path) -> tuple[dict[str, Any], Path]:
    spec_path = Path(path).resolve()
    if not spec_path.is_file():
        raise SpecError(f"Site spec does not exist: {spec_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    errors = validate_site_spec(spec)
    if errors:
        raise SpecError("; ".join(errors))
    return spec, spec_path.parent


def validate_site_spec(spec: dict[str, Any]) -> list[str]:
    errors = []
    if str(spec.get("schema_version")) != "1.0":
        errors.append("schema_version must be '1.0'")
    for field in REQUIRED_SPEC_FIELDS:
        if spec.get(field) in (None, ""):
            errors.append(f"missing {field}")
    try:
        latitude = float(spec.get("latitude", 0))
        longitude = float(spec.get("longitude", 0))
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            errors.append("latitude or longitude out of range")
    except (TypeError, ValueError):
        errors.append("latitude and longitude must be numeric")
    try:
        if float(spec.get("production_t_per_year", 0)) <= 0:
            errors.append("production_t_per_year must be positive")
    except (TypeError, ValueError):
        errors.append("production_t_per_year must be numeric")
    return errors
