from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from homer_gcc import cli, db
from homer_gcc.evidence import extract_numeric_candidates, read_document
from homer_gcc.normalization import NormalizationError, normalize_range
from homer_gcc.ontology import iter_parameters, load_json, validate_ontology
from homer_gcc.quantitative import export_scenario
from homer_gcc.reporting import export_reports
from homer_gcc.retrieval import (
    build_query_plan,
    deduplicate,
    score_candidate,
)
from homer_gcc.study import validate_study_profile
from homer_gcc.validation import prepare_observation, validate_proposal


ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_PATH = ROOT / "config" / "ontology.json"
PROFILE_PATH = ROOT / "config" / "study_profile.json"
POLICY_PATH = ROOT / "config" / "source_policy.json"
SOURCES_PATH = ROOT / "data" / "seed_sources.json"
FIXTURE_PATH = ROOT / "data" / "offline_candidates.json"
CITATION_FIXTURE_PATH = ROOT / "data" / "offline_citation_chain.json"


class ModularConfigurationTests(unittest.TestCase):
    def test_ontology_and_profile_validate(self) -> None:
        ontology = load_json(ONTOLOGY_PATH)
        profile = load_json(PROFILE_PATH)
        ontology_errors = [issue for issue in validate_ontology(ontology) if issue.level == "error"]
        profile_errors = [issue for issue in validate_study_profile(profile) if issue.level == "error"]
        self.assertEqual(ontology_errors, [])
        self.assertEqual(profile_errors, [])
        self.assertGreaterEqual(len(list(iter_parameters(ontology))), 75)
        family_ids = {family["id"] for family in ontology["families"]}
        self.assertIn("renewable_resources", family_ids)
        self.assertIn("conventional_generation", family_ids)

    def test_query_plan_is_profile_and_family_driven(self) -> None:
        ontology = load_json(ONTOLOGY_PATH)
        profile = load_json(PROFILE_PATH)
        plan = build_query_plan(ontology, "aquaculture_load", "Qatar", profile)
        joined = " ".join(plan.queries).lower()
        self.assertIn("qatar", joined)
        self.assertIn("electricity consumption", joined)
        self.assertIn("aquaculture", joined)

        adapted = dict(profile)
        adapted["domain_terms"] = ["hospital", "healthcare facility"]
        adapted["query_domain_terms"] = ["hospital", "healthcare facility"]
        adapted["locations"] = ["Singapore"]
        adapted["region_search_term"] = "Southeast Asia"
        other = build_query_plan(ontology, "renewable_resources", "Singapore", adapted)
        other_joined = " ".join(other.queries).lower()
        self.assertIn("hospital", other_joined)
        self.assertIn("singapore", other_joined)
        self.assertNotIn("fish farm", other_joined)


class RelevanceAndDeduplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ontology = load_json(ONTOLOGY_PATH)
        self.profile = load_json(PROFILE_PATH)
        self.policy = load_json(POLICY_PATH)

    def _score(self, item: dict, family: str = "renewable_resources") -> dict:
        plan = build_query_plan(self.ontology, family, "Oman", self.profile)
        return score_candidate(
            item,
            self.policy,
            plan.retrieval_terms,
            "Oman",
            plan.domain_terms,
            self.profile["locations"],
        )

    def test_high_quality_off_topic_paper_fails_hard_relevance_gate(self) -> None:
        off_topic = {
            "title": "Assessing climate risk to aquaculture in Oman",
            "abstract": "Aquaculture water temperature and dissolved oxygen risks.",
            "year": 2022,
            "doi": "10.1000/off-topic",
            "url": "https://doi.org/10.1000/off-topic",
            "peer_reviewed": True,
            "source_type": "academic_peer_reviewed",
        }
        scored = self._score(off_topic)
        self.assertGreaterEqual(scored["source_quality_score"], 70)
        self.assertFalse(scored["relevance_gate_passed"])
        self.assertEqual(scored["decision"], "exclude")
        self.assertTrue(scored["exclusion_reasons"])

    def test_relevant_academic_paper_is_shortlisted_with_screening_fields(self) -> None:
        relevant = {
            "title": "Solar photovoltaic energy for aquaculture",
            "abstract": "PV cost and renewable fish-farm microgrid performance.",
            "year": 2024,
            "doi": "10.1000/relevant",
            "url": "https://doi.org/10.1000/relevant",
            "peer_reviewed": True,
            "source_type": "academic_peer_reviewed",
        }
        scored = self._score(relevant)
        self.assertTrue(scored["relevance_gate_passed"])
        self.assertEqual(scored["decision"], "shortlist")
        self.assertEqual(scored["title_abstract_decision"], "include")
        self.assertTrue(scored["matched_terms"])
        self.assertTrue(scored["inclusion_reasons"])
        self.assertEqual(scored["human_decision"], "pending")

    def test_blocked_web_source_is_rejected(self) -> None:
        bad = {
            "title": "Amazing solar fish farm claim",
            "year": 2026,
            "url": "https://facebook.com/example",
            "peer_reviewed": False,
            "source_type": "web_general",
        }
        scored = self._score(bad)
        self.assertEqual(scored["decision"], "reject")
        self.assertEqual(scored["source_quality_score"], 0)

    def test_cross_provider_doi_dedup_preserves_provenance(self) -> None:
        base = {
            "title": "Solar photovoltaic energy for aquaculture",
            "abstract": "PV cost and renewable aquaculture systems.",
            "year": 2024,
            "doi": "10.1000/shared",
            "url": "https://doi.org/10.1000/shared",
            "peer_reviewed": True,
            "source_type": "academic_peer_reviewed",
        }
        crossref = self._score(
            {
                **base,
                "provider": "crossref",
                "database_name": "Crossref",
                "retrieved_at": "2026-08-29T00:00:00+00:00",
                "query_text": "query one",
            }
        )
        openalex = self._score(
            {
                **base,
                "doi": "https://doi.org/10.1000/shared",
                "provider": "openalex",
                "database_name": "OpenAlex",
                "retrieved_at": "2026-08-29T00:01:00+00:00",
                "query_text": "query two",
            }
        )
        merged = deduplicate([crossref, openalex])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["database_names"], ["Crossref", "OpenAlex"])
        self.assertEqual(len(merged[0]["provenance"]), 2)


class DatabaseAndWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ontology = load_json(ONTOLOGY_PATH)
        self.profile = load_json(PROFILE_PATH)
        self.policy = load_json(POLICY_PATH)

    def _candidate_pair(self) -> list[dict]:
        plan = build_query_plan(self.ontology, "renewable_resources", "Oman", self.profile)
        base = {
            "title": "Solar PV cost for aquaculture systems",
            "abstract": "Photovoltaic capital cost for a fish farm.",
            "year": 2025,
            "doi": "10.1000/database-shared",
            "url": "https://doi.org/10.1000/database-shared",
            "peer_reviewed": True,
            "source_type": "academic_peer_reviewed",
        }
        rows = []
        for provider, database_name, timestamp in [
            ("crossref", "Crossref", "2026-08-29T00:00:00+00:00"),
            ("openalex", "OpenAlex", "2026-08-29T00:01:00+00:00"),
        ]:
            item = {
                **base,
                "provider": provider,
                "database_name": database_name,
                "retrieved_at": timestamp,
                "query_text": f"{provider} query",
            }
            rows.append(
                score_candidate(
                    item,
                    self.policy,
                    plan.retrieval_terms,
                    "Oman",
                    plan.domain_terms,
                    self.profile["locations"],
                )
            )
        return deduplicate(rows)

    def test_database_dedup_screening_citation_and_reports(self) -> None:
        sources = load_json(SOURCES_PATH)["sources"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "test.sqlite"
            db.initialize(database, self.ontology, self.profile)
            inserted, skipped = db.insert_sources(database, sources)
            self.assertEqual((inserted, skipped), (len(sources), 0))

            candidates = self._candidate_pair()
            stored = db.insert_candidates(
                database,
                "multi_provider",
                "renewable_resources",
                "Oman",
                candidates,
            )
            self.assertEqual(stored, 1)
            with db.connect(database) as connection:
                candidate = connection.execute("SELECT * FROM evidence_candidates").fetchone()
                provenance_count = connection.execute("SELECT COUNT(*) FROM retrieval_provenance").fetchone()[0]
            self.assertEqual(provenance_count, 2)
            self.assertEqual(json.loads(candidate["database_names_json"]), ["Crossref", "OpenAlex"])

            db.record_screening(
                database,
                candidate["candidate_id"],
                "title_abstract",
                "include",
                "test-reviewer",
                ["relevant energy parameter"],
            )
            links = db.insert_citation_links(
                database,
                "renewable_resources",
                "Oman",
                "10.1000/seed",
                "Seed paper",
                "cited_by",
                "openalex",
                "OpenAlex",
                candidates,
            )
            self.assertEqual(links, 1)

            reports = export_reports(database, root / "reports")
            report_names = {path.name for path in reports}
            self.assertIn("retrieval_provenance.csv", report_names)
            self.assertIn("screening_decisions.csv", report_names)
            self.assertIn("citation_chains.csv", report_names)
            with db.connect(database) as connection:
                human_decision = connection.execute(
                    "SELECT human_screen_decision FROM evidence_candidates"
                ).fetchone()[0]
            self.assertEqual(human_decision, "include")

    def test_offline_batch_retrieve_runs_multiple_families_and_locations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "batch.sqlite"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = cli.main(
                    [
                        "batch-retrieve",
                        "--db",
                        str(database),
                        "--providers",
                        "offline",
                        "--families",
                        "renewable_resources,conventional_generation",
                        "--locations",
                        "Oman,Qatar",
                        "--limit",
                        "8",
                    ]
                )
            self.assertEqual(status, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["scope_count"], 4)
            self.assertEqual(payload["query_count"], 12)
            with db.connect(database) as connection:
                scopes = connection.execute(
                    "SELECT COUNT(DISTINCT family_id || '|' || context_location) FROM evidence_candidates"
                ).fetchone()[0]
            self.assertEqual(scopes, 4)

    def test_academic_seed_and_fixtures_are_present(self) -> None:
        sources = load_json(SOURCES_PATH)["sources"]
        academic = [source for source in sources if source["source_type"] == "academic_peer_reviewed"]
        self.assertGreaterEqual(len(sources), 12)
        self.assertGreaterEqual(len(academic) / len(sources), 0.9)
        self.assertTrue(all(source.get("doi") for source in academic))
        self.assertGreaterEqual(len(load_json(FIXTURE_PATH)["candidates"]), 6)
        self.assertGreaterEqual(len(load_json(CITATION_FIXTURE_PATH)["candidates"]), 3)


class QuantitativePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ontology = load_json(ONTOLOGY_PATH)
        self.profile = load_json(PROFILE_PATH)

    def test_deterministic_unit_and_cost_year_normalization(self) -> None:
        normalized = normalize_range(
            0.82,
            0.88,
            0.95,
            "USD/Wdc",
            "USD/kWdc",
            source_cost_year=2024,
            target_cost_year=2025,
            annual_escalation_rate=0.02,
        )
        self.assertAlmostEqual(normalized.central, 897.6)
        ratio = normalize_range(0.87, 0.9, 0.93, "fraction", "%")
        self.assertAlmostEqual(ratio.central, 90.0)

    def test_currency_and_cost_year_assumptions_must_be_explicit(self) -> None:
        with self.assertRaises(NormalizationError):
            normalize_range(None, 100, None, "local_currency/kW", "USD/kW")
        with self.assertRaises(NormalizationError):
            normalize_range(
                None,
                100,
                None,
                "USD/kW",
                "USD/kW",
                source_cost_year=2020,
                target_cost_year=2025,
            )

    def test_full_text_ingestion_and_transparent_candidate_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document_path = Path(directory) / "evidence.txt"
            document_path.write_text(
                "PV capital cost was 0.82-0.95 USD/Wdc.\n",
                encoding="utf-8",
            )
            document = read_document(document_path)
            parameter = next(
                item for item in iter_parameters(self.ontology) if item["id"] == "pv.capital_cost"
            )
            rows = extract_numeric_candidates(document["pages"], parameter)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["raw_unit"], "USD/Wdc")
            self.assertEqual(rows[0]["extraction_method"], "deterministic_regex_v0.3")

    def _approved_observation(
        self,
        database: Path,
        source: str,
        low: float,
        central: float,
        high: float,
    ) -> int:
        prepared = prepare_observation(
            database,
            {
                "parameter_id": "pv.capital_cost",
                "source_id": source,
                "independent_source_key": source,
                "raw_value_min": low,
                "raw_value_central": central,
                "raw_value_max": high,
                "raw_unit": "USD/kWdc",
                "locator": "test table",
                "extraction_method": "test_fixture",
                "extraction_confidence": 1,
                "applicability_score": 0.8,
                "verification_status": "approved",
                "reviewer": "test_reviewer",
            },
        )
        return db.insert_evidence_observation(database, prepared)

    def test_proposal_validation_human_gate_and_model_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "quant.sqlite"
            db.initialize(database, self.ontology, self.profile)
            proposal_id = db.upsert_proposed_parameter(
                database,
                {
                    "parameter_id": "pv.capital_cost",
                    "scenario_id": "test_case",
                    "context_location": "Oman",
                    "technology": "PV",
                    "proposed_value": 900,
                    "proposed_unit": "USD/kWdc",
                    "entered_by": "researcher",
                },
            )
            first = self._approved_observation(database, "source_a", 820, 880, 950)
            second = self._approved_observation(database, "source_b", 850, 920, 980)
            result = validate_proposal(database, proposal_id)
            self.assertEqual(result["decision"], "supported")
            self.assertEqual(result["evidence_ids"], [first, second])
            self.assertEqual(db.scenario_parameter_rows(database, "test_case"), [])

            scenario_parameter_id = db.review_parameter_validation(
                database,
                result["validation_id"],
                "approve",
                "engineering_reviewer",
            )
            self.assertIsInstance(scenario_parameter_id, int)
            scenario_rows = db.scenario_parameter_rows(database, "test_case", "Oman")
            self.assertEqual(len(scenario_rows), 1)
            self.assertEqual(scenario_rows[0]["selected_value"], 900)
            exported = export_scenario(database, "test_case", root / "scenario.json", context_location="Oman")
            self.assertTrue(exported.exists())

            run = db.create_model_run(
                database,
                "test_case",
                "HOMER Pro",
                context_location="Oman",
                run_id="test_run",
            )
            self.assertEqual(run["input_count"], 1)
            count = db.insert_model_outputs(
                database,
                "test_run",
                [{"metric_id": "npc", "metric_category": "economic", "value": 10, "unit": "USD"}],
            )
            self.assertEqual(count, 1)

    def test_candidate_ai_evidence_is_not_used_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "candidate.sqlite"
            db.initialize(database, self.ontology, self.profile)
            proposal_id = db.upsert_proposed_parameter(
                database,
                {
                    "parameter_id": "pv.capital_cost",
                    "proposed_value": 900,
                    "proposed_unit": "USD/kWdc",
                    "entered_by": "researcher",
                },
            )
            prepared = prepare_observation(
                database,
                {
                    "parameter_id": "pv.capital_cost",
                    "source_id": "ai_candidate",
                    "raw_value_central": 900,
                    "raw_unit": "USD/kWdc",
                    "locator": "page 1",
                    "extraction_confidence": 0.8,
                    "applicability_score": 0.8,
                    "verification_status": "candidate",
                },
            )
            db.insert_evidence_observation(database, prepared)
            result = validate_proposal(database, proposal_id)
            self.assertEqual(result["decision"], "insufficient_evidence")
            exploratory = validate_proposal(database, proposal_id, include_candidates=True)
            self.assertEqual(exploratory["decision"], "conditionally_supported")

    def test_quantitative_cli_demo_runs_all_seven_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = cli.main(
                    [
                        "quant-demo",
                        "--db",
                        str(root / "demo.sqlite"),
                        "--output",
                        str(root / "reports"),
                    ]
                )
            self.assertEqual(status, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["approved_scenario_parameters"], 3)
            self.assertEqual(payload["model_outputs"], 6)
            self.assertEqual(
                {row["decision"] for row in payload["validations"]},
                {"supported"},
            )
            self.assertTrue((root / "reports" / "parameter_validations.csv").exists())
            self.assertTrue((root / "reports" / "model_outputs.csv").exists())


if __name__ == "__main__":
    unittest.main()
