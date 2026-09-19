"""Orchestration of the site layer: weather, load, register, sensitivity, outages, manifest."""

from __future__ import annotations

import copy
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..ontology import load_json
from . import SITE_LAYER_VERSION
from .calendar import HOURS_PER_YEAR, hour_label
from .homer_export import sha256_file, write_homer_series
from .load import synthesise_load, validate_inventory
from .manifest import build_manifest, write_json
from .metrics import summarise_load
from .outages import generate_outage_schedule, outage_stats, replicate_stats
from .pvgis import compare_ghi, fetch_pvgis_tmy, parse_pvgis_tmy
from .register import (
    MASTER_COLUMNS,
    PROPOSED_COLUMNS,
    attach_derived,
    coverage_report,
    to_master_register_rows,
    to_proposed_parameter_rows,
    validate_register,
    write_csv,
)
from .sensitivity import SENSITIVITY_COLUMNS, build_sensitivity_table, monte_carlo_cases, percentiles
from .spec import load_site_spec, resolve
from .weather import load_weather_years, monthly_summary, parse_year_range, select_reference_years

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ONTOLOGY = PROJECT_ROOT / "config" / "ontology.json"
DEFAULT_SCENARIO = PROJECT_ROOT / "config" / "minimum_homer_scenario.json"
LABELS = ("base", "low", "high")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    return write_csv(path, rows, fieldnames)


def run_weather_stage(
    spec: dict[str, Any],
    output_dir: Path,
    *,
    years: list[int] | None = None,
    fixture: str | Path | None = None,
    pvgis: bool = False,
    pvgis_fixture: str | Path | None = None,
) -> dict[str, Any]:
    latitude = float(spec["latitude"])
    longitude = float(spec["longitude"])
    weather_config = spec.get("weather", {})
    years = years or parse_year_range(weather_config.get("years", "2015-2024"))
    cache_dir = output_dir / "cache"
    weather_dir = output_dir / "weather"
    weather_dir.mkdir(parents=True, exist_ok=True)

    data, provenance = load_weather_years(
        latitude,
        longitude,
        years,
        cache_dir,
        fixture=fixture,
        max_fill_fraction=float(weather_config.get("max_fill_fraction", 0.02)),
    )
    annual = {year: record["annual_ghi_kwh_m2"] for year, record in zip(years, provenance)}
    selection = select_reference_years(annual)
    files: dict[str, dict[str, str]] = {}
    for label in LABELS:
        year = selection[label]
        series = data[year]
        for name, key, decimals in (("ghi", "ghi_kw_m2", 4), ("temperature", "temperature_c", 2), ("wind", "wind_ms", 2)):
            path = write_homer_series(weather_dir / f"{name}_{label}.txt", series[key], decimals)
            files[f"{name}_{label}"] = {"path": str(path), "sha256": sha256_file(path), "year": str(year)}

    base_series = data[selection["base"]]
    monthly = monthly_summary(base_series, latitude)
    _write_csv_rows(weather_dir / "weather_monthly_base.csv", monthly, list(monthly[0].keys()))

    weather_years = {
        "provider": "NASA POWER hourly API, community RE, local solar time",
        "latitude": latitude,
        "longitude": longitude,
        "years_requested": years,
        "base_year_rule": weather_config.get("base_year_rule", "median_year"),
        "years": {label: selection[label] for label in LABELS},
        "median_annual_ghi_kwh_m2": selection["median_kwh_m2"],
        "annual_ghi_kwh_m2": {label: annual[selection[label]] for label in LABELS},
        "mean_temperature_c": {label: next(r["mean_temperature_c"] for r in provenance if r["year"] == selection[label]) for label in LABELS},
        "max_temperature_c": {label: next(r["max_temperature_c"] for r in provenance if r["year"] == selection[label]) for label in LABELS},
        "ten_year_mean_ghi_kwh_m2": round(sum(annual.values()) / len(annual), 1),
        "per_year": [
            {key: value for key, value in record.items() if key != "fill_stats"} | {"fill_stats": record.get("fill_stats")}
            for record in provenance
        ],
        "files": files,
        "wind_anemometer_height_m": 10,
    }
    if pvgis:
        payload, pvgis_provenance = fetch_pvgis_tmy(latitude, longitude, cache_dir, fixture=pvgis_fixture)
        parsed = parse_pvgis_tmy(payload, int(spec.get("utc_offset_hours", 0)))
        comparison = compare_ghi(
            base_series["ghi_kw_m2"],
            parsed["ghi_kw_m2"],
            nasa_reference_annual=weather_years["ten_year_mean_ghi_kwh_m2"],
            tolerance=float(weather_config.get("pvgis_tolerance", 0.08)),
        )
        comparison.update(
            {
                "pvgis_radiation_db": parsed["radiation_db"],
                "pvgis_meteo_db": parsed["meteo_db"],
                "pvgis_years": [parsed["year_min"], parsed["year_max"]],
                "provenance": pvgis_provenance,
            }
        )
        write_json(weather_dir / "weather_crosscheck.json", comparison)
        weather_years["pvgis_crosscheck"] = {key: comparison[key] for key in ("status", "difference_pct", "pvgis_annual_kwh_m2", "nasa_annual_kwh_m2")}
    write_json(weather_dir / "weather_years.json", weather_years)
    weather_years["_series"] = {label: data[selection[label]] for label in LABELS}
    weather_years["_provenance"] = provenance
    return weather_years


def _critical_variant(archetype: dict[str, Any], share: float) -> dict[str, Any]:
    variant = copy.deepcopy(archetype)
    for item in variant["equipment"]:
        if item.get("criticality") == "essential" and "critical_share" in item:
            item["critical_share"] = share
    return variant


def run_load_stage(
    spec: dict[str, Any],
    spec_base: Path,
    output_dir: Path,
    weather: dict[str, Any],
    ontology: dict[str, Any],
    *,
    seed: int | None = None,
    archetype_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    load_dir = output_dir / "load"
    load_dir.mkdir(parents=True, exist_ok=True)
    archetype = archetype_override or load_json(resolve(spec_base, spec["archetype"]))
    benchmarks = load_json(resolve(spec_base, spec.get("benchmarks", "benchmarks.json")))
    errors = validate_inventory(archetype, ontology)
    if errors:
        raise ValueError("Archetype inventory is invalid: " + "; ".join(errors))
    load_config = spec.get("load", {})
    seed = int(seed if seed is not None else load_config.get("seed", 1))
    scale = float(load_config.get("low_high_scale", 0.15))
    production = float(spec["production_t_per_year"])

    files: dict[str, dict[str, str]] = {}
    results: dict[str, Any] = {}
    for label in LABELS:
        result = synthesise_load(spec, archetype, weather["_series"][label], seed)
        results[label] = result
        suffix = "base" if label == "base" else f"weather_{label}"
        for name, series in (("load_total", result.total), ("load_critical", result.critical)):
            path = write_homer_series(load_dir / f"{name}_{suffix}.txt", series, 3)
            files[f"{name}_{suffix}"] = {"path": str(path), "sha256": sha256_file(path), "weather_year": str(weather["years"][label])}
    base = results["base"]
    for label, factor in (("low", 1.0 - scale), ("high", 1.0 + scale)):
        for name, series in (("load_total", base.total), ("load_critical", base.critical)):
            path = write_homer_series(load_dir / f"{name}_{label}.txt", [value * factor for value in series], 3)
            files[f"{name}_{label}"] = {"path": str(path), "sha256": sha256_file(path), "scale": str(round(factor, 3))}

    summary = summarise_load(base.total, base.critical, base.essential, base.deferrable, base.by_ontology_id_kwh, production, benchmarks)
    low_variant = synthesise_load(spec, _critical_variant(archetype, 0.0), weather["_series"]["base"], seed)
    high_variant = synthesise_load(spec, _critical_variant(archetype, 1.0), weather["_series"]["base"], seed)
    summary["critical_fraction_low"] = round(sum(low_variant.critical) / sum(low_variant.total), 4)
    summary["critical_fraction_high"] = round(sum(high_variant.critical) / sum(high_variant.total), 4)
    summary["low_high_scale"] = scale
    summary["seed"] = seed
    summary["weather_year"] = weather["years"]["base"]
    summary["production_t_per_year"] = production
    summary["by_equipment_kwh"] = base.by_equipment_kwh
    summary["by_ontology_id_kwh"] = base.by_ontology_id_kwh
    summary["water_temperature_mean_c"] = round(sum(base.water_temperature_c) / HOURS_PER_YEAR, 2)
    summary["assumptions"] = base.assumptions
    summary["weather_variants"] = {
        label: {
            "annual_kwh": round(sum(results[label].total), 1),
            "peak_kw": round(max(results[label].total), 3),
        }
        for label in LABELS
    }
    summary["files"] = files
    write_json(load_dir / "load_summary.json", summary)

    equipment_ids = sorted(base.by_equipment)
    with (load_dir / "load_disaggregated_base.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["hour_index", "label", "total_kw", "critical_kw", "essential_kw", "deferrable_kw", "ambient_c", *equipment_ids])
        ambient = weather["_series"]["base"]["temperature_c"]
        for index in range(HOURS_PER_YEAR):
            writer.writerow(
                [
                    index,
                    hour_label(index),
                    base.total[index],
                    base.critical[index],
                    base.essential[index],
                    base.deferrable[index],
                    ambient[index],
                    *[round(base.by_equipment[item_id][index], 3) for item_id in equipment_ids],
                ]
            )
    write_json(load_dir / "inventory_used.json", {**archetype, "seed": seed, "production_t_per_year": production})
    return summary


def run_export_stage(
    spec: dict[str, Any],
    spec_base: Path,
    output_dir: Path,
    ontology: dict[str, Any],
    scenario: dict[str, Any],
    weather: dict[str, Any],
    load_summary: dict[str, Any],
    *,
    scenarios: tuple[str, ...] = ("base",),
    literature_ids: set[str] | None = None,
) -> dict[str, Any]:
    register = load_json(resolve(spec_base, spec["register"]))
    accessed = _now()
    files = {**weather["files"], **load_summary["files"]}
    resolved = attach_derived(
        register,
        load_summary=load_summary,
        weather_years=weather,
        files=files,
        accessed=accessed,
        country=str(spec.get("country", "")),
    )
    errors = validate_register(resolved, ontology)
    if errors:
        raise ValueError("Site register is invalid: " + "; ".join(errors))
    write_json(output_dir / "site_register_resolved.json", resolved)

    proposed = to_proposed_parameter_rows(
        resolved,
        ontology,
        scenario,
        scenario_id=str(spec["scenario_id"]),
        context_location=str(spec.get("context_location", spec.get("country", ""))),
        entered_by=str(spec.get("entered_by", "site_layer")),
        scenarios=scenarios,
    )
    proposed_path = _write_csv_rows(output_dir / "proposed_parameters_site.csv", proposed, PROPOSED_COLUMNS)
    master = to_master_register_rows(resolved, ontology)
    master_path = _write_csv_rows(output_dir / "master_parameter_register.csv", master, MASTER_COLUMNS)
    coverage = coverage_report(resolved, scenario, literature_ids)
    write_json(output_dir / "scenario_coverage_site.json", coverage)

    table = build_sensitivity_table(resolved, load_summary, weather, load_scale=float(load_summary.get("low_high_scale", 0.15)))
    sensitivity_path = _write_csv_rows(output_dir / "sensitivity_table.csv", table, SENSITIVITY_COLUMNS)
    sensitivity_config = spec.get("sensitivity", {})
    cases = monte_carlo_cases(table, int(sensitivity_config.get("monte_carlo_draws", 200)), int(sensitivity_config.get("seed", 1)))
    case_columns = list(cases[0].keys()) if cases else ["case_id"]
    cases_path = _write_csv_rows(output_dir / "monte_carlo_cases.csv", cases, case_columns)
    write_json(output_dir / "monte_carlo_summary.json", {"draws": len(cases), "seed": sensitivity_config.get("seed", 1), "percentiles": percentiles(cases)})

    by_id = {item["parameter_id"]: item for item in resolved["parameters"]}
    frequency = float(by_id["grid.outage_frequency"]["base"]) if "grid.outage_frequency" in by_id else 0.0
    repair = float(by_id["grid.mean_outage_duration"]["base"]) if "grid.mean_outage_duration" in by_id else 0.0
    outage_seed = int((spec.get("grid") or {}).get("outage_seed", 1))
    schedule = generate_outage_schedule(frequency, repair, outage_seed)
    schedule_path = output_dir / "grid_outage_schedule.txt"
    schedule_path.write_text("\n".join(str(value) for value in schedule) + "\n", encoding="ascii", newline="\n")
    stats = outage_stats(schedule)
    stats.update(
        {
            "seed": outage_seed,
            "target_frequency_per_year": frequency,
            "target_mean_duration_h": repair,
            "replicates": replicate_stats(frequency, repair, list(range(outage_seed, outage_seed + 20))),
        }
    )
    write_json(output_dir / "outage_stats.json", stats)
    return {
        "register_rows": len(resolved["parameters"]),
        "proposed_parameters_csv": str(proposed_path),
        "proposed_rows": len(proposed),
        "master_register_csv": str(master_path),
        "coverage": {key: coverage[key] for key in ("required", "covered", "gaps", "site_layer_rows", "literature_rows")},
        "sensitivity_csv": str(sensitivity_path),
        "sensitivity_rows": len(table),
        "monte_carlo_csv": str(cases_path),
        "monte_carlo_draws": len(cases),
        "outage_schedule": str(schedule_path),
        "outage_stats": {key: stats[key] for key in ("events", "total_outage_hours", "mean_duration_h", "longest_h")},
    }


def write_site_summary(output_dir: Path, spec: dict[str, Any], weather: dict[str, Any], load_summary: dict[str, Any], exports: dict[str, Any]) -> Path:
    years = weather["years"]
    annual = weather["annual_ghi_kwh_m2"]
    checks = "\n".join(
        f"| {row['metric']} | {row['value']} | {row['band_low']} to {row['band_high']} | {row['status']} |"
        for row in load_summary["benchmark_checks"]
    )
    crosscheck = weather.get("pvgis_crosscheck")
    crosscheck_line = (
        f"PVGIS TMY cross-check: {crosscheck['status']} ({crosscheck['difference_pct']:+.1f} percent against the NASA ten-year mean)."
        if crosscheck else "PVGIS cross-check not run."
    )
    text = f"""# Site data summary: {spec.get('site_name', spec['site_id'])}

Generated by {SITE_LAYER_VERSION}. Every value is a candidate for human review.

## Weather (NASA POWER hourly, {weather['latitude']} N {weather['longitude']} E)

| Label | Year | Annual GHI (kWh/m2) | Mean temperature (degC) |
|---|---|---|---|
| base | {years['base']} | {annual['base']} | {weather['mean_temperature_c']['base']} |
| low | {years['low']} | {annual['low']} | {weather['mean_temperature_c']['low']} |
| high | {years['high']} | {annual['high']} | {weather['mean_temperature_c']['high']} |

{crosscheck_line}

## Load (archetype, {load_summary['production_t_per_year']:g} t/yr, seed {load_summary['seed']})

| Metric | Value |
|---|---|
| Annual energy (kWh) | {load_summary['annual_kwh']} |
| Daily average (kWh/day) | {load_summary['daily_average_kwh']} |
| Peak (kW) | {load_summary['peak_kw']} at {load_summary['peak_time']} |
| Load factor | {load_summary['load_factor']} |
| Specific energy (kWh/kg) | {load_summary['kwh_per_kg']} |
| Critical fraction | {load_summary['critical_fraction']} (range {load_summary['critical_fraction_low']} to {load_summary['critical_fraction_high']}) |
| Day-to-day variability already in the file (%) | {load_summary['homer_day_to_day_variability_pct']} |
| Time-step variability already in the file (%) | {load_summary['homer_timestep_variability_pct']} |

| Benchmark | Value | Band | Status |
|---|---|---|---|
{checks}

## HOMER entry map

| HOMER dialog | File or value |
|---|---|
| Electric Load > Import time series (kW) | `load/load_total_base.txt`; set both random variability fields to 0 percent |
| Electric Load (critical bus scenario) | `load/load_critical_base.txt` |
| Solar GHI Resource > Import time series (kW/m2) | `weather/ghi_base.txt` |
| Temperature Resource > Import time series (degC) | `weather/temperature_base.txt` |
| Wind Resource > Import time series (m/s), anemometer 10 m | `weather/wind_base.txt` |
| Grid, Fuel, Economics, Constraints | `master_parameter_register.csv` base column; low and high in `sensitivity_table.csv` |
| Evidence pipeline import | `proposed_parameters_site.csv` via `import-proposals` |

Scenario coverage: {exports['coverage']['covered']} of {exports['coverage']['required']} required inputs have a site-layer candidate or assumption; the remaining {len(exports['coverage']['gaps'])} are literature-side parameters owned by the evidence pipeline.
"""
    path = output_dir / "SITE_SUMMARY.md"
    path.write_text(text, encoding="utf-8")
    return path


def run_site_pipeline(
    spec_path: str | Path,
    output_dir: str | Path,
    *,
    ontology_path: str | Path | None = None,
    scenario_path: str | Path | None = None,
    years: str | None = None,
    seed: int | None = None,
    fixture: str | Path | None = None,
    pvgis: bool = False,
    pvgis_fixture: str | Path | None = None,
    scenarios: str = "base",
    literature_ids: set[str] | None = None,
    llm_config: str | Path | None = None,
    use_llm_draft: bool = False,
    reset: bool = False,
) -> dict[str, Any]:
    spec, spec_base = load_site_spec(spec_path)
    output = Path(output_dir)
    if reset and output.exists():
        for child in output.iterdir():
            if child.name == "cache":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    output.mkdir(parents=True, exist_ok=True)
    ontology = load_json(ontology_path or DEFAULT_ONTOLOGY)
    scenario = load_json(scenario_path or DEFAULT_SCENARIO)

    weather = run_weather_stage(
        spec,
        output,
        years=parse_year_range(years) if years else None,
        fixture=fixture,
        pvgis=pvgis,
        pvgis_fixture=pvgis_fixture,
    )

    archetype_override = None
    llm_result = None
    if llm_config:
        from .llm_inventory import draft_inventory

        config_path = Path(llm_config).resolve()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        archetype = load_json(resolve(spec_base, spec["archetype"]))
        llm_result = draft_inventory(spec, ontology, archetype, config, output / "load", config_base=config_path.parent)
        if use_llm_draft and llm_result["status"] == "candidate":
            archetype_override = {**archetype, "equipment": llm_result["equipment"], "archetype_id": archetype["archetype_id"] + "_llm_draft"}

    load_summary = run_load_stage(spec, spec_base, output, weather, ontology, seed=seed, archetype_override=archetype_override)
    exports = run_export_stage(
        spec,
        spec_base,
        output,
        ontology,
        scenario,
        weather,
        load_summary,
        scenarios=tuple(part.strip() for part in scenarios.split(",") if part.strip()),
        literature_ids=literature_ids,
    )
    summary_path = write_site_summary(output, spec, weather, load_summary, exports)

    outputs = [Path(entry["path"]) for entry in {**weather["files"], **load_summary["files"]}.values()]
    outputs += [
        output / "weather" / "weather_years.json",
        output / "weather" / "weather_monthly_base.csv",
        output / "load" / "load_summary.json",
        output / "load" / "load_disaggregated_base.csv",
        output / "load" / "inventory_used.json",
        output / "site_register_resolved.json",
        output / "proposed_parameters_site.csv",
        output / "master_parameter_register.csv",
        output / "scenario_coverage_site.json",
        output / "sensitivity_table.csv",
        output / "monte_carlo_cases.csv",
        output / "monte_carlo_summary.json",
        output / "grid_outage_schedule.txt",
        output / "outage_stats.json",
        summary_path,
    ]
    if pvgis:
        outputs.append(output / "weather" / "weather_crosscheck.json")
    sources = [
        {key: value for key, value in record.items() if key in ("source", "path", "url", "year", "accessed")}
        for record in weather["_provenance"]
    ]
    manifest = build_manifest(
        spec=spec,
        spec_path=Path(spec_path).resolve(),
        sources=sources,
        outputs=outputs,
        seed=int(load_summary["seed"]),
        project_root=PROJECT_ROOT,
        extra={
            "weather_years": weather["years"],
            "pvgis_crosscheck": weather.get("pvgis_crosscheck"),
            "benchmark_status": load_summary["benchmark_status"],
            "llm_inventory": {key: llm_result[key] for key in ("status", "provider", "model", "output")} if llm_result else None,
        },
    )
    write_json(output / "run_manifest.json", manifest)
    return {
        "status": "completed",
        "site_id": spec["site_id"],
        "output_dir": str(output),
        "weather": {
            "years": weather["years"],
            "annual_ghi_kwh_m2": weather["annual_ghi_kwh_m2"],
            "pvgis_crosscheck": weather.get("pvgis_crosscheck"),
        },
        "load": {
            key: load_summary[key]
            for key in (
                "annual_kwh", "peak_kw", "peak_time", "load_factor", "kwh_per_kg", "critical_fraction",
                "homer_day_to_day_variability_pct", "homer_timestep_variability_pct", "benchmark_status",
            )
        },
        "benchmark_checks": [{"metric": row["metric"], "value": row["value"], "status": row["status"]} for row in load_summary["benchmark_checks"]],
        "exports": exports,
        "llm_inventory": {key: llm_result[key] for key in ("status", "provider", "model", "output")} if llm_result else None,
        "manifest": str(output / "run_manifest.json"),
        "summary": str(summary_path),
    }


def run_site_sweep(
    sites_path: str | Path,
    output_dir: str | Path,
    *,
    years: str | None = None,
    fixture: str | Path | None = None,
) -> dict[str, Any]:
    payload = load_json(sites_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for site in payload.get("sites", []):
        spec = {
            **site,
            "schema_version": "1.0",
            "weather": {"years": years or payload.get("years", "2015-2024")},
        }
        site_dir = output / site["site_id"]
        weather = run_weather_stage(spec, site_dir, years=parse_year_range(years) if years else None, fixture=fixture)
        base = weather["_series"]["base"]
        rows.append(
            {
                "site_id": site["site_id"],
                "country": site["country"],
                "site_name": site.get("site_name", ""),
                "latitude": site["latitude"],
                "longitude": site["longitude"],
                "base_year": weather["years"]["base"],
                "annual_ghi_mean_kwh_m2": weather["ten_year_mean_ghi_kwh_m2"],
                "annual_ghi_min_kwh_m2": weather["annual_ghi_kwh_m2"]["low"],
                "annual_ghi_max_kwh_m2": weather["annual_ghi_kwh_m2"]["high"],
                "mean_temperature_c": weather["mean_temperature_c"]["base"],
                "max_temperature_c": weather["max_temperature_c"]["base"],
                "hours_above_35c": sum(1 for value in base["temperature_c"] if value > 35.0),
                "hours_above_40c": sum(1 for value in base["temperature_c"] if value > 40.0),
                "mean_wind_ms": round(sum(base["wind_ms"]) / HOURS_PER_YEAR, 2),
            }
        )
    summary_path = write_csv(output / "sweep_summary.csv", rows, list(rows[0].keys()) if rows else ["site_id"])
    return {"status": "completed", "sites": len(rows), "summary": str(summary_path), "rows": rows}
