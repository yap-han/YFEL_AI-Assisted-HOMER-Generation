from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from homer_gcc.advanced_pipeline import (
    PipelineStopped,
    chunk_pages,
    compare_mix_results,
    evaluate_review,
    load_advanced_config,
    run_advanced_pipeline,
)


ROOT = Path(__file__).resolve().parents[1]


class AdvancedPipelineContractTests(unittest.TestCase):
    def test_config_pins_luna_and_terra_and_corpus_has_22_papers(self) -> None:
        config, _ = load_advanced_config(ROOT / "config" / "advanced_pipeline.example.json")
        corpus = json.loads((ROOT / "evidence" / "corpus_manifest.22.example.json").read_text(encoding="utf-8"))
        scenario = json.loads((ROOT / "config" / "minimum_homer_scenario.json").read_text(encoding="utf-8"))
        self.assertEqual(config["screening"]["model"], "gpt-5.6-luna")
        self.assertEqual(config["extraction"]["model"], "gpt-5.6-terra")
        self.assertEqual(len(corpus["documents"]), 22)
        self.assertGreaterEqual(len(scenario["required_parameters"]), 20)

    def test_chunking_preserves_page_and_overlap(self) -> None:
        text = "A" * 700 + "\n" + "B" * 700
        chunks = chunk_pages(7, [{"page_number": 3, "page_text": text}], max_characters=800, overlap_characters=100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(row["page_number"] == 3 for row in chunks))
        self.assertTrue(all(row["chunk_id"].startswith("d0007-p0003") for row in chunks))
        self.assertLess(chunks[1]["character_start"], chunks[0]["character_end"])

    def test_review_metrics_require_named_complete_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            fields = ["numerical_correct", "semantic_correct", "quotation_correct", "boundary_correct", "reviewer"]
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({**{field: "yes" for field in fields[:-1]}, "reviewer": "Researcher A"})
                writer.writerow({"numerical_correct": "no", "semantic_correct": "yes", "quotation_correct": "yes", "boundary_correct": "no", "reviewer": "Researcher B"})
            result = evaluate_review(path)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["numerical_correct"], 0.5)
            self.assertEqual(result["semantic_correct"], 1.0)
            self.assertEqual(result["all_dimensions_correct"], 0.5)

    def test_mix_comparison_detects_changed_preferred_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            updated = root / "updated.json"
            baseline.write_text(json.dumps({"scenario_id": "base", "architectures": [
                {"architecture_id": "diesel", "npc_usd": 100, "renewable_fraction": 0},
                {"architecture_id": "pv-diesel", "npc_usd": 120, "renewable_fraction": 0.5},
            ]}), encoding="utf-8")
            updated.write_text(json.dumps({"scenario_id": "updated", "architectures": [
                {"architecture_id": "diesel", "npc_usd": 130, "renewable_fraction": 0},
                {"architecture_id": "pv-diesel", "npc_usd": 105, "renewable_fraction": 0.5},
            ]}), encoding="utf-8")
            result = compare_mix_results(baseline, updated)
            self.assertTrue(result["preferred_mix_changed"])
            self.assertEqual(result["provisional_preferred_architecture"], "pv-diesel")

    def test_live_pipeline_fails_before_fixtures_when_provider_keys_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = json.loads((ROOT / "config" / "advanced_pipeline.example.json").read_text(encoding="utf-8"))
            source["output_dir"] = str(root / "output")
            source["database"] = str(root / "output" / "evidence.sqlite")
            source["context"] = {
                "ontology": str(ROOT / "config" / "ontology.json"),
                "profile": str(ROOT / "config" / "study_profile.json"),
            }
            source["corpus"]["manifest"] = str(ROOT / "evidence" / "corpus_manifest.22.example.json")
            source["corpus"]["pdf_root"] = str(root / "pdfs")
            source["scenario_requirements"] = str(ROOT / "config" / "minimum_homer_scenario.json")
            source["boundary_taxonomy"] = str(ROOT / "config" / "system_boundary_taxonomy.json")
            config = root / "config.json"
            config.write_text(json.dumps(source), encoding="utf-8")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "", "MISTRAL_API_KEY": ""}, clear=False):
                with self.assertRaises(PipelineStopped) as caught:
                    run_advanced_pipeline(config)
            self.assertEqual(caught.exception.status, "blocked_preflight")
            status = json.loads((root / "output" / "PIPELINE_STATUS.json").read_text(encoding="utf-8"))
            codes = {row["code"] for row in status["blockers"]}
            self.assertIn("missing_openai_api_key", codes)
            self.assertIn("missing_mistral_api_key", codes)
            self.assertFalse(status["fixture_fallback_used"])


if __name__ == "__main__":
    unittest.main()
