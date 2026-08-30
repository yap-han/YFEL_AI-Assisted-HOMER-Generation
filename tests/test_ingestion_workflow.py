from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from homer_gcc import db
from homer_gcc.ingestion import CorpusManifestError, ingest_corpus, load_corpus_manifest
from homer_gcc.ontology import load_json
from homer_gcc.workflow import run_workflow


ROOT = Path(__file__).resolve().parents[1]


class ManifestIngestionTests(unittest.TestCase):
    def _manifest(self, root: Path, *, rights: bool = True) -> Path:
        fulltext = root / "paper.txt"
        fulltext.write_text(
            "Abstract\n" + ("PV capital cost evidence. " * 20) + "\nReferences\nExample.",
            encoding="utf-8",
        )
        payload = {
            "schema_version": "1.0",
            "corpus_id": "unit_test_corpus",
            "quality_gate": {"minimum_characters": 200, "required_sections": ["Abstract", "References"]},
            "documents": [
                {
                    "source_id": "paper_001",
                    "title": "Test paper",
                    "file": "paper.txt",
                    "doi": "10.1000/test-ingestion",
                    "source_type": "journal_article",
                    "peer_reviewed": True,
                    **({"rights_note": "Test fixture may be ingested."} if rights else {}),
                }
            ],
        }
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        return manifest

    def test_manifest_ingestion_is_quality_gated_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "test.sqlite"
            db.initialize(database, load_json(ROOT / "config" / "ontology.json"), load_json(ROOT / "config" / "study_profile.json"))
            manifest = self._manifest(root)
            first = ingest_corpus(database, manifest)
            second = ingest_corpus(database, manifest)
            self.assertEqual((first["ingested"], first["failed"]), (1, 0))
            self.assertEqual((second["duplicate"], second["failed"]), (1, 0))
            with db.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM full_text_documents").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0], 1)

    def test_missing_rights_note_is_reported_and_fail_fast_can_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "test.sqlite"
            db.initialize(database, load_json(ROOT / "config" / "ontology.json"), load_json(ROOT / "config" / "study_profile.json"))
            manifest = self._manifest(root, rights=False)
            result = ingest_corpus(database, manifest)
            self.assertEqual(result["failed"], 1)
            with self.assertRaises(CorpusManifestError):
                ingest_corpus(database, manifest, fail_fast=True)

    def test_manifest_schema_rejects_duplicate_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["documents"].append(dict(payload["documents"][0]))
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(CorpusManifestError):
                load_corpus_manifest(manifest)


class ConfiguredWorkflowTests(unittest.TestCase):
    def test_workflow_includes_ingestion_and_extraction_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "schema_version": "1.0",
                "database": "workflow.sqlite",
                "context": {
                    "ontology": str(ROOT / "config" / "ontology.json"),
                    "profile": str(ROOT / "config" / "study_profile.json"),
                },
                "ingestion": {
                    "manifest": str(ROOT / "evidence" / "corpus_manifest.example.json"),
                    "require_rights": True,
                    "fail_fast": True,
                },
                "extraction": {
                    "parameters": ["pv.capital_cost", "generator.capital_cost"],
                    "output_dir": "tasks",
                },
                "validation": {"enabled": False},
                "reports": {"output_dir": "reports"},
                "run_summary": "workflow_run.json",
            }
            config_path = root / "workflow.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = run_workflow(config_path, reset_database=True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["stages"]["ingestion"]["ingested"], 1)
            self.assertEqual(result["stages"]["extraction_tasks"]["count"], 1)
            task = Path(result["stages"]["extraction_tasks"]["files"][0])
            task_payload = json.loads(task.read_text(encoding="utf-8"))
            self.assertEqual(len(task_payload["parameters"]), 2)
            self.assertTrue((root / "workflow_run.json").is_file())


if __name__ == "__main__":
    unittest.main()
