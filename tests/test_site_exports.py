from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from homer_gcc import cli, db
from homer_gcc.llm import LLMResponseError
from homer_gcc.ontology import load_json
from homer_gcc.quantitative import import_proposals, load_records
from homer_gcc.site import register, sensitivity
from homer_gcc.site.homer_export import sha256_file
from homer_gcc.site.llm_inventory import build_inventory_task, draft_inventory, validate_inventory_response
from homer_gcc.site.pipeline import run_site_pipeline, run_site_sweep

ROOT = Path(__file__).resolve().parents[1]
NASA_FIXTURE = ROOT / "data" / "site" / "nasa_power_fixture_allith_48h.json"
PVGIS_FIXTURE = ROOT / "data" / "site" / "pvgis_tmy_fixture_allith_48h.json"
SPEC = ROOT / "config" / "site" / "site.saudi_allith.example.json"
LITERATURE_IDS = {
    "pv.capital_cost", "pv.replacement_cost", "pv.om_cost", "pv.derating_factor", "pv.lifetime",
    "generator.capital_cost", "generator.replacement_cost", "generator.om_cost", "generator.electrical_efficiency",
    "generator.operating_lifetime", "fuel.generator_minimum_load", "battery.capital_cost", "battery.replacement_cost",
    "battery.om_cost", "battery.round_trip_efficiency", "battery.minimum_soc", "battery.float_life",
    "converter.efficiency", "converter.capital_cost",
}


class RegisterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_json(ROOT / "config" / "ontology.json")
        cls.scenario = load_json(ROOT / "config" / "minimum_homer_scenario.json")
        cls.register = load_json(ROOT / "config" / "site" / "site_register.saudi_allith.json")

    def test_register_validates_against_ontology(self) -> None:
        self.assertEqual(register.validate_register(self.register, self.ontology), [])
        broken = json.loads(json.dumps(self.register))
        broken["parameters"][0]["source_url"] = ""
        broken["parameters"][1]["low"] = broken["parameters"][1]["high"] + 1
        errors = register.validate_register(broken, self.ontology)
        self.assertEqual(len(errors), 2)
        self.assertIn("missing source_url", errors[0])
        self.assertIn("low <= base <= high", errors[1])

    def test_master_register_has_ten_columns(self) -> None:
        rows = register.to_master_register_rows(self.register, self.ontology)
        self.assertEqual(list(rows[0].keys()), register.MASTER_COLUMNS)
        self.assertEqual(len(rows), len(self.register["parameters"]))
        self.assertTrue(all(row["validation_status"] == "candidate" for row in rows))

    def test_coverage_with_mock_literature(self) -> None:
        derived = {"load.ac_primary_profile", "load.peak_demand", "solar.ghi", "site.ambient_temperature"}
        raw = register.coverage_report(self.register, self.scenario, LITERATURE_IDS)
        # The raw register lacks the four pipeline-derived rows, which attach_derived adds at run time.
        self.assertEqual(raw["required"], 32)
        self.assertEqual(set(raw["gaps"]), derived)
        self.assertEqual(raw["covered"], 28)
        resolved = {**self.register, "parameters": self.register["parameters"] + [
            register.derived_row(parameter_id, homer_component="x", homer_field="y", unit="kW", base=1.0, low=0.5, high=1.5, basis="test", notes="")
            for parameter_id in derived
        ]}
        full = register.coverage_report(resolved, self.scenario, LITERATURE_IDS)
        self.assertEqual(full["gaps"], [])
        self.assertEqual(full["covered"], 32)
        partial = register.coverage_report(resolved, self.scenario, LITERATURE_IDS - {"pv.capital_cost"})
        self.assertEqual(partial["gaps"], ["pv.capital_cost"])
        self.assertEqual(partial["covered"], 31)


class SensitivityTests(unittest.TestCase):
    def test_table_and_monte_carlo_are_seeded_and_bounded(self) -> None:
        register_payload = load_json(ROOT / "config" / "site" / "site_register.saudi_allith.json")
        table = sensitivity.build_sensitivity_table(register_payload, None, None)
        self.assertEqual(len(table), len(register_payload["parameters"]) + 1)
        cases = sensitivity.monte_carlo_cases(table, 200, 5)
        self.assertEqual(len(cases), 200)
        self.assertEqual(cases, sensitivity.monte_carlo_cases(table, 200, 5))
        bounds = {row["parameter_id"]: (float(row["low"]), float(row["high"])) for row in table}
        for case in cases:
            for key, value in case.items():
                if key == "case_id":
                    continue
                low, high = bounds[key]
                self.assertTrue(low - 1e-9 <= value <= high + 1e-9, (key, value))
        summary = sensitivity.percentiles(cases)
        self.assertIn("fuel.diesel_price", summary)


class LLMInventoryTests(unittest.TestCase):
    def test_fixture_retry_then_valid_and_candidate_status(self) -> None:
        ontology = load_json(ROOT / "config" / "ontology.json")
        archetype = load_json(ROOT / "config" / "site" / "archetypes" / "ras_intensive_marine.json")
        spec = {"site_id": "test_site", "country": "Saudi Arabia", "production_t_per_year": 500}
        config_path = ROOT / "config" / "site" / "llm_inventory.fixture.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            result = draft_inventory(spec, ontology, archetype, config, root, config_base=config_path.parent)
            self.assertEqual(result["status"], "candidate")
            self.assertEqual([attempt["status"] for attempt in result["attempts"]], ["rejected", "valid"])
            self.assertEqual(len(result["equipment"]), 2)
            self.assertTrue((Path(root) / "inventory_draft.json").is_file())
        task = build_inventory_task(spec, ontology, archetype)
        with self.assertRaises(LLMResponseError):
            validate_inventory_response(task, json.dumps({"equipment": [{"id": "x", "ontology_id": "nope", "rated_kw": 1, "quantity": 1, "duty_cycle": 1, "driver": "constant", "criticality": "critical", "basis": "b"}]}), ontology)


class PipelineTests(unittest.TestCase):
    def test_site_generate_offline_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            output = Path(root) / "site"
            code = cli.main(
                [
                    "site-generate",
                    "--spec", str(SPEC),
                    "--output", str(output),
                    "--years", "2021-2023",
                    "--fixture", str(NASA_FIXTURE),
                    "--pvgis", "--pvgis-fixture", str(PVGIS_FIXTURE),
                    "--literature-ids", ",".join(sorted(LITERATURE_IDS)),
                    "--llm-config", str(ROOT / "config" / "site" / "llm_inventory.fixture.json"),
                ]
            )
            self.assertEqual(code, 0)
            expected = [
                "weather/ghi_base.txt", "weather/ghi_low.txt", "weather/ghi_high.txt",
                "weather/temperature_base.txt", "weather/wind_base.txt", "weather/weather_years.json",
                "weather/weather_monthly_base.csv", "weather/weather_crosscheck.json",
                "load/load_total_base.txt", "load/load_critical_base.txt", "load/load_total_low.txt",
                "load/load_total_high.txt", "load/load_total_weather_low.txt", "load/load_summary.json",
                "load/load_disaggregated_base.csv", "load/inventory_used.json", "load/inventory_draft.json",
                "site_register_resolved.json", "proposed_parameters_site.csv", "master_parameter_register.csv",
                "scenario_coverage_site.json", "sensitivity_table.csv", "monte_carlo_cases.csv",
                "monte_carlo_summary.json", "grid_outage_schedule.txt", "outage_stats.json",
                "run_manifest.json", "SITE_SUMMARY.md",
            ]
            for name in expected:
                self.assertTrue((output / name).is_file(), name)
            for name in ("weather/ghi_base.txt", "load/load_total_base.txt", "load/load_critical_base.txt", "grid_outage_schedule.txt"):
                lines = (output / name).read_text(encoding="ascii").splitlines()
                self.assertEqual(len(lines), 8760, name)

            with (output / "proposed_parameters_site.csv").open(encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle))
            with (ROOT / "data" / "proposed_parameters_demo.csv").open(encoding="utf-8", newline="") as handle:
                demo_header = next(csv.reader(handle))
            self.assertEqual(header, demo_header)
            ontology_ids = {parameter["id"] for family in load_json(ROOT / "config" / "ontology.json")["families"] for parameter in family["parameters"]}
            records = load_records(output / "proposed_parameters_site.csv", "proposals")
            self.assertTrue(all(record["parameter_id"] in ontology_ids for record in records))
            database = Path(root) / "site.sqlite"
            db.initialize(database, load_json(ROOT / "config" / "ontology.json"), load_json(ROOT / "config" / "study_profile.json"))
            self.assertEqual(len(import_proposals(database, records)), len(records))

            coverage = json.loads((output / "scenario_coverage_site.json").read_text(encoding="utf-8"))
            self.assertEqual(coverage["required"], 32)
            self.assertEqual(coverage["covered"], 32)
            self.assertEqual(coverage["gaps"], [])

            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            for entry in manifest["outputs"]:
                self.assertEqual(entry["sha256"], sha256_file(entry["path"]))
            self.assertEqual(manifest["llm_inventory"]["status"], "candidate")
            summary = json.loads((output / "load" / "load_summary.json").read_text(encoding="utf-8"))
            self.assertNotEqual(summary["benchmark_status"], "fail")

    def test_site_coverage_command_exit_codes(self) -> None:
        code = cli.main(
            [
                "site-coverage",
                "--register", str(ROOT / "config" / "site" / "site_register.saudi_allith.json"),
                "--literature-ids", ",".join(sorted(LITERATURE_IDS)),
            ]
        )
        self.assertEqual(code, 1)  # raw register lacks the four pipeline-derived rows

    def test_sweep_with_fixture(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            result = run_site_sweep(ROOT / "config" / "site" / "gcc_reference_sites.json", root, years="2022-2023", fixture=NASA_FIXTURE)
            self.assertEqual(result["sites"], 6)
            self.assertTrue(Path(result["summary"]).is_file())


if __name__ == "__main__":
    unittest.main()
