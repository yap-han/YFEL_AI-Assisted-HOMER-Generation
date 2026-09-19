"""Site-layer subcommands, registered into the main CLI parser."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..ontology import load_json
from .pipeline import (
    DEFAULT_ONTOLOGY,
    DEFAULT_SCENARIO,
    PROJECT_ROOT,
    load_technology_register,
    run_export_stage,
    run_load_stage,
    run_site_pipeline,
    run_site_sweep,
    run_weather_stage,
)
from .register import coverage_report, literature_ids_from_aggregates, write_csv
from .spec import load_site_spec
from .technology import COMPARISON_COLUMNS, compare_registers, tier_map, validate_technology_register
from .weather import parse_year_range

DEFAULT_SPEC = PROJECT_ROOT / "config" / "site" / "site.saudi_allith.example.json"
DEFAULT_SITES = PROJECT_ROOT / "config" / "site" / "gcc_reference_sites.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "site"
DEFAULT_TECHNOLOGY = PROJECT_ROOT / "config" / "technology" / "technology_register.baseline_generic.json"


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
        technology_register=load_technology_register(spec, spec_base, args.technology_register, disabled=args.no_technology_register),
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
        aggregates = Path(args.aggregates)
        if not aggregates.exists():
            for line in (
                f"Aggregates file not found: {aggregates}",
                "scenario_coverage.json is written by stage 7 of run-advanced-pipeline. The site layer",
                "does not produce it and it is not part of the site output package. Either run the",
                "evidence pipeline first, or omit --aggregates to report site-layer coverage only, or",
                "pass the literature-supported parameter IDs with --literature-ids.",
            ):
                print(line, file=sys.stderr)
            return 2
        literature |= literature_ids_from_aggregates(load_json(aggregates))
    technology = None
    if args.technology_register:
        technology = load_json(args.technology_register)
        errors = validate_technology_register(technology, load_json(args.ontology), scenario)
        if errors:
            print("Technology register is invalid: " + "; ".join(errors), file=sys.stderr)
            return 2
    report = coverage_report(register, scenario, literature, tier_map(technology))
    _print(report)
    return 0 if not report["gaps"] else 1


def command_site_compare_registers(args: argparse.Namespace) -> int:
    a = load_json(args.a)
    b = load_json(args.b)
    rows = compare_registers(a, b)
    if args.output:
        write_csv(args.output, rows, COMPARISON_COLUMNS)
    compared = [row for row in rows if row["delta_base"] is not None]
    outside = [row["parameter_id"] for row in compared if row["b_base_within_a_bounds"] is False]
    _print(
        {
            "a": {"register_id": a.get("register_id"), "evidence_tier": a.get("evidence_tier"), "rows": len(a.get("parameters", []))},
            "b": {"register_id": b.get("register_id"), "evidence_tier": b.get("evidence_tier"), "rows": len(b.get("parameters", []))},
            "compared": len(compared),
            "only_in_a": [row["parameter_id"] for row in rows if row["b_base"] is None],
            "only_in_b": [row["parameter_id"] for row in rows if row["a_base"] is None],
            "b_base_outside_a_bounds": outside,
            "largest_relative_changes": sorted(
                ({"parameter_id": row["parameter_id"], "delta_base_pct": row["delta_base_pct"]} for row in compared if row["delta_base_pct"] is not None),
                key=lambda item: abs(item["delta_base_pct"]),
                reverse=True,
            )[:5],
            "output": args.output,
            "rows": rows,
        }
    )
    return 0


def command_site_compare_results(args: argparse.Namespace) -> int:
    from ..advanced_pipeline import compare_mix_results

    for label, path in (("baseline", args.baseline), ("updated", args.updated)):
        if not Path(path).is_file():
            print(f"{label} HOMER results file not found: {path}", file=sys.stderr)
            print("Export each HOMER run's architecture table to JSON on evidence/homer_results/example.schema.json first.", file=sys.stderr)
            return 2
    result = compare_mix_results(Path(args.baseline), Path(args.updated))
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result["output"] = args.output
    _print(result)
    return 0


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
        technology_register=args.technology_register,
        no_technology_register=args.no_technology_register,
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


def _technology_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--technology-register",
        help="Technology register JSON for the 19 literature-supported inputs; default is the spec's technology_register entry",
    )
    command.add_argument(
        "--no-technology-register",
        action="store_true",
        help="Ignore the spec's technology register and export the site layer alone",
    )


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
    _technology_arguments(export)
    export.set_defaults(function=command_site_export)

    coverage = sub.add_parser("site-coverage", help="Report coverage of the minimum HOMER scenario contract")
    coverage.add_argument("--register", required=True, help="Resolved or raw site register JSON")
    coverage.add_argument("--scenario", default=DEFAULT_SCENARIO)
    coverage.add_argument("--ontology", default=DEFAULT_ONTOLOGY)
    coverage.add_argument("--literature-ids", help="Comma-separated parameter IDs the evidence pipeline covers")
    coverage.add_argument("--aggregates", help="scenario_coverage.json from run-advanced-pipeline")
    coverage.add_argument("--technology-register", help="Technology register JSON (baseline_generic or evidence_updated) to count as candidates")
    coverage.set_defaults(function=command_site_coverage)

    compare = sub.add_parser("site-compare-registers", help="Compare two technology registers parameter by parameter (control versus treatment)")
    compare.add_argument("--a", required=True, help="Register A, normally the baseline_generic register")
    compare.add_argument("--b", required=True, help="Register B, normally the evidence_updated register")
    compare.add_argument("--output", help="CSV to write the comparison table to")
    compare.set_defaults(function=command_site_compare_registers)

    results = sub.add_parser("site-compare-results", help="Compare two exported HOMER architecture rankings (stage 8 of the evidence pipeline, standalone)")
    results.add_argument("--baseline", required=True, help="HOMER results JSON for the baseline run, on evidence/homer_results/example.schema.json")
    results.add_argument("--updated", required=True, help="HOMER results JSON for the evidence-updated run")
    results.add_argument("--output", help="Write energy_mix_impact.json here")
    results.set_defaults(function=command_site_compare_results)

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
    _technology_arguments(generate)
    generate.set_defaults(function=command_site_generate)

    sweep = sub.add_parser("site-sweep", help="Weather sweep across the GCC reference sites")
    sweep.add_argument("--sites", default=DEFAULT_SITES)
    sweep.add_argument("--output", default=DEFAULT_OUTPUT / "sweep")
    sweep.add_argument("--years")
    sweep.add_argument("--fixture")
    sweep.set_defaults(function=command_site_sweep)
