from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from homer_gcc import db
from scripts.run_real_evidence_pilot import run


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_CORPUS_AVAILABLE = len(list((PROJECT_ROOT / "real_pilot" / "fulltexts").glob("*.txt"))) >= 22


class RealEvidencePilotTests(unittest.TestCase):
    @unittest.skipUnless(REAL_CORPUS_AVAILABLE, "optional 22-paper local corpus is not present")
    def test_real_corpus_ingests_and_recalculates_without_bypassing_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "pilot"
            metrics = run(PROJECT_ROOT, output)
            self.assertEqual(metrics["documents_ingested"], 22)
            self.assertEqual(metrics["full_text_gate_passed"], 22)
            self.assertEqual(metrics["usable_observations"], 10)
            self.assertEqual(metrics["parameters_covered"], 3)
            by_parameter = {row["parameter_id"]: row for row in metrics["validation_results"]}
            self.assertAlmostEqual(by_parameter["battery.round_trip_efficiency"]["aggregate_base"], 91.25)
            self.assertTrue(by_parameter["pv.capital_cost"]["conflict_flag"])
            self.assertTrue(all(row["decision"] == "conditionally_supported" for row in by_parameter.values()))
            with db.connect(output / "real_evidence_pilot.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM scenario_parameters").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence_observations WHERE verification_status='candidate'").fetchone()[0], 10)
            self.assertTrue((output / "HUMAN_REVIEW_QUEUE.csv").is_file())
            self.assertTrue((output / "RECALCULATED_VALUES.csv").is_file())


if __name__ == "__main__":
    unittest.main()
