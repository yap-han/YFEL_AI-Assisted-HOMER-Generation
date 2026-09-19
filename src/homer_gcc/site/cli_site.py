"""Site-layer subcommands, registered into the main CLI parser."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..ontology import load_json
from .pipeline import (
    DEFAULT_ONTOLOGY,
    DEFAULT_SCENARIO,
    PROJECT_ROOT,
    run_export_stage,
    run_load_stage,
    run_site_pipeline,
    run_site_sweep,
    run_weather_stage,
)
from .register import coverage_report, literature_ids_from_aggregates
from .spec import load_site_spec
from .weather import parse_year_range

DEFAULT_SPEC = PROJECT_ROOT / "config" / "site" / "site.saudi_allith.example.json"
DEFAULT_SITES = PROJECT_ROOT / "config" / "site" / "gcc_reference_sites.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "site"


def _csv_set(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def command_site_fetch_weather(args: argparse.Namespace) -> int:
    spec, _ = load_site_spec(args.spec)
    weather = run_weather_stage(
        spec,
        Path(args.output),
        years=parse_year_range(args.years) if args.years else None,
        fixture=args.fixture,
        pvgis=args.pvgis,
        pvgis_fixture=args.pvgis_fixture,
    )
    _print({key: value for key, value in weather.items() if not key.startswith("_")})
    return 0


def command_site_synthesise_load(args: argparse.Namespace) -> int:
    spec, spec_base = load_site_spec(args.spec)
    output = Path(args.output)
    weather = run_weather_stage(spec, output, years=parse_year_range(args.years) if args.years else None, fixture=args.fixture)
    summary = run_load_stage(spec, spec_base, output, weather, load_json(args.ontology), seed=args.seed)
    _print({key: summary[key] for key in summary if key not in {"by_equipment_kwh", "monthly_kwh", "average_daily_profile_kw", "assumptions"}})
    return 0 if summary["benchmark_status"] != "fail" else 1


def command_site_export(args: argparse.Namespace) -> int:
    spec, spec_base = load_site_spec(args.spec)
    output = Path(args.output)
    ontology = load_json(args.ontology)
    weather = run_weather_stage(spec, output, years=parse_year_range(args.years) if args.years else None, fixture=args.fixture)
    summary = run_load_stage(spec, spec_base, output, weather, ontology, seed=args.seed)
    exports = run_export_stage(
        spec, spec_base, output, ontology, load_json(args.scenario), weather, summary,
        scenarios=tuple(part for part in args.scenarios.split(",") if part),
        literature_ids=_csv_set(args.literature_ids),
    )
    if args.db:
        from .. import db
        from ..quantitative import import_proposals, load_records

        db.initialize(args.db, ontology, load_json(args.profile))
        ids = import_proposals(args.db, load_records(exports["proposed_parameters_csv"], "proposals"))
        exports["imported_proposal_ids"] = ids
    _print(exports)
    return 0


def command_site_coverage(args: argparse.Namespace) -> int:
    register = load_json(args.register)
    scenario = load_json(args.scenario)
    literature = _csv_set(args.literature_ids)
    if args.aggregates:
        literature |= literature_ids_from_aggregates(load_json(args.aggregates))
    report = coverage_report(register, scenario, literature)
    _print(report)
    return 0 if not report["gaps"] else 1


def command_site_generate(args: argparse.Namespace) -> int:
    result = run_site_pipeline(
        args.spec,
        args.output,
        ontology_path=args.ontology,
        scenario_path=args.scenario,
        years=args.years,
        seed=args.seed,
        fixture=args.fixture,
        pvgis=args.pvgis,
        pvgis_fixture=args.pvgis_fixture,
        scenarios=args.scenarios,
        literature_ids=_csv_set(args.literature_ids),
        llm_config=args.llm_config,
        use_llm_draft=args.use_llm_draft,
        reset=args.reset,
    )
    if args.db:
        from .. import db
        from ..quantitative import import_proposals, load_records

        db.initialize(args.db, load_json(args.ontology), load_json(args.profile))
        result["imported_proposal_ids"] = import_proposals(
            args.db, load_records(result["exports"]["proposed_parameters_csv"], "proposals")
        )
    _print(result)
    return 0 if result["load"]["benchmark_status"] != "fail" else 1


def command_site_sweep(args: argparse.Namespace) -> int:
    result = run_site_sweep(args.sites, args.output, years=args.years, fixture=args.fixture)
    _print(result)
    return 0


def _common(command: argparse.ArgumentParser, *, output: Path = DEFAULT_OUTPUT) -> None:
    command.add_argument("--spec", default=DEFAULT_SPEC, help="Site specification JSON")
    command.add_argument("--output", default=output, help="Output directory; raw downloads are cached in <output>/cache")
    command.add_argument("--years", help="Year range for NASA POWER, e.g. 2015-2024; default from the spec")
    command.add_argument("--fixture", help="Offline NASA POWER fixture JSON (tests only)")


def register_site_commands(sub: argparse._SubParsersAction, profile_default: Path) -> None:
    fetch = sub.add_parser("site-fetch-weather", help="Download, clean and export NASA POWER weather in HOMER units")
    _common(fetch)
    fetch.add_argument("--pvgis", action="store_true", help="Cross-check annual GHI against the PVGIS TMY")
    fetch.add_argument("--pvgis-fixture", help="Offline PVGIS fixture JSON (tests only)")
    fetch.set_defaults(function=command_site_fetch_weather)

    synth = sub.add_parser("site-synthesise-load", help="Build the equipment-based hourly load profile and validate it")
    _common(synth)
    synth.add_argument("--ontology", default=DEFAULT_ONTOLOGY)
    synth.add_argument("--seed", type=int, help="Override the noise seed in the spec")
    synth.set_defaults(function=command_site_synthesise_load)

    export = sub.add_parser("site-export", help="Export the site register, proposed parameters, sensitivity table and outage schedule")
    _common(export)
    export.add_argument("--ontology", default=DEFAULT_ONTOLOGY)
    export.add_argument("--profile", default=profile_default)
    export.add_argument("--scenario", default=DEFAULT_SCENARIO)
    export.add_argument("--seed", type=int)
    export.add_argument("--scenarios", default="base", help="Comma-separated: base, low, high")
    export.add_argument("--literature-ids", help="Comma-separated parameter IDs covered by the evidence pipeline")
    export.add_argument("--db", help="Also import the proposed parameters into this SQLite registry")
    export.set_defaults(function=command_site_export)

    coverage = sub.add_parser("site-coverage", help="Report coverage of the minimum HOMER scenario contract")
    coverage.add_argument("--register", required=True, help="Resolved or raw site register JSON")
    coverage.add_argument("--scenario", default=DEFAULT_SCENARIO)
    coverage.add_argument("--literature-ids", help="Comma-separated parameter IDs the evidence pipeline covers")
    coverage.add_argument("--aggregates", help="scenario_coverage.json from run-advanced-pipeline")
    coverage.set_defaults(function=command_site_coverage)

    generate = sub.add_parser("site-generate", help="Run the whole site layer: weather, load, register, sensitivity, outages, manifest")
    _common(generate)
    generate.add_argument("--ontology", default=DEFAULT_ONTOLOGY)
    generate.add_argument("--profile", default=profile_default)
    generate.add_argument("--scenario", default=DEFAULT_SCENARIO)
    generate.add_argument("--seed", type=int)
    generate.add_argument("--pvgis", action="store_true")
    generate.add_argument("--pvgis-fixture")
    generate.add_argument("--scenarios", default="base")
    generate.add_argument("--literature-ids")
    generate.add_argument("--llm-config", help="Optional LLM config for an AI-drafted inventory (candidate only)")
    generate.add_argument("--use-llm-draft", action="store_true", help="Run the load stage on the AI draft instead of the archetype")
    generate.add_argument("--db", help="Also import the proposed parameters into this SQLite registry")
    generate.add_argument("--reset", action="store_true", help="Delete generated files in the output directory; the download cache is kept")
    generate.set_defaults(function=command_site_generate)

    sweep = sub.add_parser("site-sweep", help="Weather sweep across the GCC reference sites")
    sweep.add_argument("--sites", default=DEFAULT_SITES)
    sweep.add_argument("--output", default=DEFAULT_OUTPUT / "sweep")
    sweep.add_argument("--years")
    sweep.add_argument("--fixture")
    sweep.set_defaults(function=command_site_sweep)
