from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from homer_gcc import cli, db
from homer_gcc.ontology import load_json
from homer_gcc.quantitative import import_proposals, load_records
from homer_gcc.site import register, technology
from homer_gcc.site.pipeline import run_site_pipeline

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "config" / "technology" / "technology_register.baseline_generic.json"
TEMPLATE = ROOT / "config" / "technology" / "technology_register.evidence_updated.template.json"
SPEC = ROOT / "config" / "site" / "site.saudi_allith.example.json"
NASA_FIXTURE = ROOT / "data" / "site" / "nasa_power_fixture_allith_48h.json"
RESULTS_A = ROOT / "data" / "site" / "homer_results_example_baseline.json"
RESULTS_B = ROOT / "data" / "site" / "homer_results_example_updated.json"


class TechnologyRegisterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_json(ROOT / "config" / "ontology.json")
        cls.scenario = load_json(ROOT / "config" / "minimum_homer_scenario.json")
        cls.baseline = load_json(BASELINE)
        cls.site = load_json(ROOT / "config" / "site" / "site_register.saudi_allith.json")
        cls.literature = set(technology.literature_parameter_ids(cls.scenario))

    def test_baseline_register_covers_exactly_the_literature_inputs(self) -> None:
        self.assertEqual(technology.validate_technology_register(self.baseline, self.ontology, self.scenario), [])
        self.assertEqual(self.baseline["evidence_tier"], "baseline_generic")
        ids = {item["parameter_id"] for item in self.baseline["parameters"]}
        self.assertEqual(ids, self.literature)
        self.assertEqual(len(self.literature), 19)
        for item in self.baseline["parameters"]:
            self.assertTrue(item["source_url"].startswith("https://"), item["parameter_id"])
            self.assertIn(item["confidence"], {"high", "medium", "low"})
            self.assertEqual(item["validation_status"], "candidate")
            self.assertTrue(item["basis"])

    def test_validation_rejects_site_ids_wrong_tier_and_missing_basis(self) -> None:
        broken = json.loads(json.dumps(self.baseline))
        broken["parameters"][0]["parameter_id"] = "grid.purchase_price"
        broken["parameters"][1]["basis"] = ""
        broken["evidence_tier"] = "guess"
        errors = technology.validate_technology_register(broken, self.ontology, self.scenario)
        joined = "\n".join(errors)
        self.assertIn("not a literature_supported input", joined)
        self.assertIn("missing basis", joined)
        self.assertIn("evidence_tier must be one of", joined)

    def test_template_lists_the_same_inputs_and_is_not_loadable_as_evidence(self) -> None:
        template = load_json(TEMPLATE)
        self.assertEqual(template["evidence_tier"], "evidence_updated")
        ids = [item["parameter_id"] for item in template["parameters"]]
        self.assertEqual(set(ids), self.literature)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(technology.validate_technology_register(template, self.ontology, self.scenario))

    def test_coverage_counts_technology_rows_and_prefers_pipeline_evidence(self) -> None:
        tiers = technology.tier_map(self.baseline)
        raw = register.coverage_report(self.site, self.scenario, set(), tiers)
        # The raw site register lacks the four pipeline-derived rows that attach_derived adds at run time.
        self.assertEqual(raw["technology_rows"], 19)
        self.assertEqual(len(raw["gaps"]), 4)
        derived = {"load.ac_primary_profile", "load.peak_demand", "solar.ghi", "site.ambient_temperature"}
        resolved = {**self.site, "parameters": self.site["parameters"] + [
            register.derived_row(parameter_id, homer_component="x", homer_field="y", unit="kW", base=1.0, low=0.5, high=1.5, basis="test", notes="")
            for parameter_id in derived
        ]}
        with_technology = register.coverage_report(resolved, self.scenario, set(), tiers)
        self.assertEqual(with_technology["covered"], 32)
        self.assertEqual(with_technology["technology_rows"], 19)
        self.assertEqual(with_technology["gaps"], [])
        statuses = {row["status"] for row in with_technology["rows"]}
        self.assertIn("technology_baseline_generic_candidate", statuses)
        both = register.coverage_report(resolved, self.scenario, self.literature, tiers)
        self.assertEqual(both["literature_rows"], 19)
        self.assertEqual(both["technology_rows"], 0)

    def test_aggregates_parser_reads_the_pipeline_coverage_status(self) -> None:
        payload = {
            "parameters": [
                {"parameter_id": "pv.capital_cost", "evidence_role": "literature_supported", "coverage_status": "provisional_evidence_available"},
                {"parameter_id": "pv.om_cost", "evidence_role": "literature_supported", "coverage_status": "evidence_gap"},
            ]
        }
        self.assertEqual(register.literature_ids_from_aggregates(payload), {"pv.capital_cost"})

    def test_merge_and_compare(self) -> None:
        merged = technology.merge_registers(self.site, self.baseline)
        ids = [item["parameter_id"] for item in merged["parameters"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len(self.site["parameters"]) + 19)
        self.assertTrue(all(item.get("evidence_tier") == "baseline_generic" for item in merged["parameters"][-19:]))

        treatment = json.loads(json.dumps(self.baseline))
        treatment["register_id"] = "evidence_updated_test"
        treatment["evidence_tier"] = "evidence_updated"
        for item in treatment["parameters"]:
            item["evidence_tier"] = "evidence_updated"
        pv = next(item for item in treatment["parameters"] if item["parameter_id"] == "pv.capital_cost")
        pv["base"] = float(pv["high"]) * 2
        pv["high"] = pv["base"] * 1.1
        rows = technology.compare_registers(self.baseline, treatment)
        self.assertEqual(len(rows), 19)
        changed = next(row for row in rows if row["parameter_id"] == "pv.capital_cost")
        self.assertFalse(changed["b_base_within_a_bounds"])
        self.assertGreater(changed["delta_base_pct"], 0)
        unchanged = next(row for row in rows if row["parameter_id"] == "pv.lifetime")
        self.assertTrue(unchanged["b_base_within_a_bounds"])
        self.assertEqual(unchanged["delta_base"], 0.0)

        pv["unit"] = "USD/Wdc"
        with self.assertRaises(technology.TechnologyRegisterError):
            technology.compare_registers(self.baseline, treatment)

    def test_homer_input_sheet_follows_contract_order_and_marks_gaps(self) -> None:
        sheet = technology.homer_input_sheet(self.scenario, self.ontology, self.site, self.baseline)
        contract = [row["parameter_id"] for row in self.scenario["required_parameters"]]
        self.assertEqual([row["parameter_id"] for row in sheet[: len(contract)]], contract)
        origins = {row["parameter_id"]: row["origin"] for row in sheet}
        self.assertEqual(origins["pv.capital_cost"], "baseline_generic")
        self.assertEqual(origins["grid.purchase_price"], "site_register")
        self.assertEqual(origins["economics.project_lifetime"], "researcher_assumption")
        self.assertEqual(origins["load.ac_primary_profile"], "gap")
        without = technology.homer_input_sheet(self.scenario, self.ontology, self.site, None)
        self.assertEqual(sum(1 for row in without if row["origin"] == "gap"), 19 + 4)

    def test_technology_proposals_import_into_the_evidence_registry(self) -> None:
        rows = register.to_proposed_parameter_rows(
            self.baseline, self.ontology, self.scenario,
            scenario_id="test", context_location="generic", entered_by="technology_register",
        )
        self.assertEqual(len(rows), 19)
        self.assertEqual({row["technology"] for row in rows}, {"PV", "Diesel generator", "Lithium-ion battery", "Converter"})
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "proposed_parameters_technology.csv"
            register.write_csv(path, rows, register.PROPOSED_COLUMNS)
            with (ROOT / "data" / "proposed_parameters_demo.csv").open(encoding="utf-8") as handle:
                demo_header = next(csv.reader(handle))
            with path.open(encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle)), demo_header)
            database = Path(tmp) / "registry.sqlite"
            db.initialize(database, self.ontology, load_json(ROOT / "config" / "study_profile.json"))
            self.assertEqual(len(import_proposals(database, load_records(path, "proposals"))), 19)

    def test_offline_generate_writes_the_consolidated_sheet(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            output = Path(tmp) / "site"
            result = run_site_pipeline(SPEC, output, years="2021-2023", fixture=NASA_FIXTURE)
            exports = result["exports"]
            self.assertEqual(exports["coverage"]["covered"], 32)
            self.assertEqual(exports["coverage"]["technology_rows"], 19)
            self.assertEqual(exports["technology_proposed_rows"], 19)
            with (output / "homer_input_sheet.csv").open(encoding="utf-8") as handle:
                sheet = list(csv.DictReader(handle))
            self.assertEqual(len(sheet), exports["homer_input_sheet_rows"])
            self.assertFalse([row for row in sheet if row["origin"] == "gap"])
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            names = {Path(entry["path"]).name for entry in manifest["outputs"]}
            self.assertIn("technology_register_resolved.json", names)
            self.assertIn("homer_input_sheet.csv", names)
            summary = (output / "SITE_SUMMARY.md").read_text(encoding="utf-8")
            self.assertIn("19 from the technology register", summary)

            alone = run_site_pipeline(SPEC, Path(tmp) / "alone", years="2021-2023", fixture=NASA_FIXTURE, no_technology_register=True)
            self.assertEqual(alone["exports"]["coverage"]["covered"], 13)
            self.assertNotIn("technology_register_id", alone["exports"])

    def test_compare_results_command(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out = Path(tmp) / "energy_mix_impact.json"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.main(["site-compare-results", "--baseline", str(RESULTS_A), "--updated", str(RESULTS_B), "--output", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("preferred_mix_changed", payload)
            self.assertEqual(payload["baseline_preferred_architecture"], "pv-grid")
            self.assertEqual(payload["provisional_preferred_architecture"], "pv-battery-grid")
            self.assertTrue(payload["preferred_mix_changed"])
            with redirect_stdout(io.StringIO()):
                missing = cli.main(["site-compare-results", "--baseline", str(Path(tmp) / "none.json"), "--updated", str(RESULTS_B)])
            self.assertEqual(missing, 2)

    def test_compare_registers_command_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out = Path(tmp) / "comparison.csv"
            with redirect_stdout(io.StringIO()):
                code = cli.main(["site-compare-registers", "--a", str(BASELINE), "--b", str(BASELINE), "--output", str(out)])
            self.assertEqual(code, 0)
            with out.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 19)
            self.assertEqual(rows[0].keys().__len__(), len(technology.COMPARISON_COLUMNS))


if __name__ == "__main__":
    unittest.main()
