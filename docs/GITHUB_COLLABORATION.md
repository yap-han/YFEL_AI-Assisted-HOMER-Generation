# GitHub Collaboration and Integrated Ingestion

## Purpose

Version 0.4 makes full-text ingestion part of the configured research workflow.
The shared repository tracks source metadata, inclusion rules, parameter
definitions and review decisions. Full-text files can remain local when their
licences do not permit redistribution.

## Repository structure

| Path | Commit to Git? | Purpose |
|---|---|---|
| `config/` | Yes | Ontology, study profile and workflow configuration |
| `evidence/*.json` | Yes | Versioned corpus manifests |
| `evidence/fulltexts/` | Normally no | Locally obtained PDFs, TXT or JSON full texts |
| `src/` | Yes | Reusable ingestion, extraction and validation modules |
| `tests/` | Yes | Unit and integration tests |
| `output/` | No | Generated databases, task JSON and audit reports |

## Corpus manifest

Each document record provides a stable `source_id`, title, DOI, local file path,
source metadata and a rights note. Paths should be relative so that the same
manifest works on every contributor's computer.

The manifest-level quality gate can require a minimum character count and named
sections such as `Abstract` and `References`. A document-specific `quality_gate`
may override these defaults. Files are hashed with SHA-256, so rerunning the
workflow does not create duplicate document records.

For a real academic corpus, a suitable starting gate is:

```json
{
  "quality_gate": {
    "minimum_characters": 12000,
    "required_sections": ["Abstract", "References"]
  }
}
```

This gate detects incomplete downloads but does not prove that a document is
relevant, correctly parsed or legally redistributable.

## One-command workflow

```bash
PYTHONPATH=src python -m homer_gcc run-workflow \
  --config config/workflow.example.json
```

The command performs:

1. Ontology and study-profile validation.
2. Database initialization or migration.
3. Source registration and manifest-driven full-text ingestion.
4. Rights-note and text-completeness checks.
5. SHA-256 deduplication.
6. Schema-constrained AI extraction-task generation.
7. Optional proposal and observation imports.
8. Optional parameter validation.
9. Audit-report and run-summary export.

It does not call a proprietary AI service or approve evidence automatically.
The generated extraction tasks can be processed by the team's chosen AI system,
after which its JSON observations are imported for review.

## Adding a paper

1. Obtain the full text lawfully and place it in `evidence/fulltexts/`.
2. Add one document object to the team's corpus manifest.
3. Record the DOI, publication metadata, licence or access basis, and intended
   parameter families.
4. Run `ingest-corpus` or the complete workflow.
5. Commit the manifest change, not the full text, unless redistribution is
   explicitly permitted.

Batch ingestion can also be run independently:

```bash
PYTHONPATH=src python -m homer_gcc ingest-corpus \
  --db output/study.sqlite \
  --manifest evidence/corpus_manifest.json \
  --report output/ingestion_report.json
```

## Review ownership

For a two-person team, use independent responsibilities:

- Contributor A adds and ingests the source, then prepares candidate values.
- Contributor B checks the source locator, system boundary and normalized value.
- Either contributor may propose a HOMER value, but final scenario approval
  should name the reviewer and retain any modification notes.

This separation makes the database suitable for a methodology paper: the audit
trail shows which work was automated, which evidence was accepted and which
engineering choices remained human decisions.

## Publishing the repository

Start with a private GitHub repository while the team confirms source licences
and removes local outputs. From the extracted project directory:

```bash
git init
git add .
git commit -m "Initial modular HOMER evidence workflow"
git branch -M main
git remote add origin <your-github-repository-url>
git push -u origin main
```

Before making the repository public, select a software licence and inspect the
staged file list with `git status`. If a full text was accidentally staged or
committed, `.gitignore` will not remove an already tracked file; remove it from
the Git index with `git rm --cached <path>` and commit that correction.
