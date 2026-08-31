from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from homer_gcc import db
from homer_gcc.evidence import build_ai_extraction_task, read_document
from homer_gcc.llm import LLMResponseError, run_llm_extraction_batch, validate_llm_response
from homer_gcc.ontology import load_json


ROOT = Path(__file__).resolve().parents[1]


class LLMExtractionTests(unittest.TestCase):
    def _database(self, root: Path) -> Path:
        database = root / "llm.sqlite"
        db.initialize(
            database,
            load_json(ROOT / "config" / "ontology.json"),
            load_json(ROOT / "config" / "study_profile.json"),
        )
        document = read_document(ROOT / "data" / "demo_fulltext.txt")
        document_id = db.insert_full_text_document(
            database,
            document,
            source_id="synthetic_workflow_document_001",
            title="Synthetic quantitative extraction fixture",
        )
        self.assertEqual(document_id, 1)
        return database

    def _task(self, database: Path) -> dict:
        document, pages = db.get_document(database, 1)
        parameter = db.get_parameter_definition(database, "pv.capital_cost")
        return build_ai_extraction_task(1, document["title"], pages, [parameter])

    def test_schema_requires_verbatim_quote_and_detailed_locator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(Path(directory))
            response = {
                "observations": [
                    {
                        "parameter_id": "pv.capital_cost",
                        "raw_value_min": 0.82,
                        "raw_value_central": 0.885,
                        "raw_value_max": 0.95,
                        "raw_unit": "USD/Wdc",
                        "raw_value_text": "0.82–0.95 USD/Wdc",
                        "page_number": 1,
                        "locator": "page 1",
                        "extraction_confidence": 0.9,
                    }
                ]
            }
            with self.assertRaisesRegex(LLMResponseError, "evidence_quote"):
                validate_llm_response(self._task(database), json.dumps(response))

    def test_batch_retries_unchanged_task_and_imports_valid_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            result = run_llm_extraction_batch(
                database,
                [1],
                ["pv.capital_cost"],
                {
                    "provider": "fixture",
                    "response_file": str(ROOT / "data" / "llm_fixture_responses.json"),
                    "max_retries": 2,
                    "normalization": {"target_cost_year": 2025, "annual_escalation_rate": 0.02},
                },
                root / "results",
            )
            self.assertEqual(result["successful_tasks"], 1)
            self.assertEqual(result["invalid_responses"], 1)
            self.assertEqual(result["retries_used"], 1)
            self.assertEqual(result["observations_imported"], 1)
            attempts = result["results"][0]["attempts"]
            self.assertEqual(attempts[0]["task_sha256"], attempts[1]["task_sha256"])
            value_range = result["parameter_ranges"][0]
            self.assertEqual(value_range["evidence_low"], 820)
            self.assertEqual(value_range["provisional_selected_value"], 885)
            self.assertEqual(value_range["evidence_high"], 950)
            self.assertIn("not a human-approved", value_range["selection_justification"])
            with db.connect(database) as connection:
                row = connection.execute("SELECT * FROM evidence_observations").fetchone()
            self.assertEqual(row["verification_status"], "candidate")
            self.assertIn("0.82–0.95 USD/Wdc", row["context_excerpt"])


if __name__ == "__main__":
    unittest.main()
