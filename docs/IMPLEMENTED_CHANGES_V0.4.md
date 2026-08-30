# Implemented changes — v0.4

Version 0.4 integrates full-text ingestion into the configured research
workflow and prepares the prototype for GitHub collaboration.

| Change | Implementation | Verification |
|---|---|---|
| Versioned corpus manifest | `ingestion.py` validates schema 1.0, stable source IDs, unique DOIs and relative file paths. | Unit tests cover valid and invalid manifests. |
| Batch ingestion | `ingest-corpus` accepts TXT, Markdown, JSON and PDF inputs already supported by the evidence module. | Integration test ingests and retrieves a document. |
| Rights control | A manifest-level or document-level rights note is required by default; `allowed_to_ingest: false` blocks the record. | Missing-rights test produces a failed ingestion result. |
| Full-text quality gate | Minimum character count and required section names are configurable globally or per document. | Tests exercise pass and failure cases. |
| Deduplication | SHA-256 reuse returns the existing document ID and reports `duplicate`. | Rerun test confirms one database document. |
| Source registration | Manifest metadata is written to the source registry before successful document storage. | Source and document counts are asserted. |
| Integrated workflow | `run-workflow` orchestrates initialization, ingestion, extraction-task creation, optional imports, validation and reports. | Example workflow runs in tests and CI. |
| Idempotent observation imports | Parameter, source, locator and raw text identify previously imported observations. | Workflow code reports skipped record IDs. |
| GitHub collaboration | `.gitignore`, `.gitattributes`, CI tests, examples, `CONTRIBUTING.md` and collaboration guidance are included. | The CI workflow runs tests and the example workflow. |

The workflow still does not download restricted papers, invoke a proprietary AI
service, approve evidence or run HOMER. Those boundaries preserve lawful source
access and the methodology's human-validation requirements.
