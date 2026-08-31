# Contributing

This repository separates reusable methodology and code from study-specific
evidence. Contributions should preserve provenance, human review gates and the
ability to adapt the workflow to another location or energy system.

## Recommended collaboration process

1. Create a short branch such as `feature/corpus-ingestion` or
   `evidence/pv-cost-review`.
2. Add source metadata to a corpus manifest. Do not commit a full-text file
   unless its licence clearly permits redistribution and the project has agreed
   to store it.
3. Run the configured workflow and review `workflow_run.json`.
4. Keep extracted values at `candidate` status until another contributor checks
   the cited table, page or line.
5. Run the full test suite before opening a pull request.
6. In the pull request, explain changes to units, currency years, technology
   boundaries, applicability scores and review decisions.

## Commands

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m homer_gcc run-workflow \
  --config config/workflow.example.json
PYTHONPATH=src python -m homer_gcc run-advanced-pipeline \
  --config config/advanced_pipeline.example.json
```

Use `--reset` only for disposable databases. Normal reruns preserve the database,
deduplicate documents by SHA-256 and skip duplicate observation imports.

## Pull-request checklist

- [ ] Source identity and DOI are correct.
- [ ] The corpus manifest records a rights note.
- [ ] Full-text quality gates pass.
- [ ] Raw values, units and source locators are preserved.
- [ ] Normalization assumptions are explicit.
- [ ] Candidate evidence has not been represented as human-approved.
- [ ] `HUMAN_REVIEW.csv` contains named, genuinely manual judgements; it was not completed by an LLM.
- [ ] Evidence was aggregated only within a compatible system boundary.
- [ ] Provider telemetry records the model, prompt version, tokens, latency and pricing snapshot.
- [ ] HOMER impact claims come from baseline and evidence-updated HOMER exports, not the synthetic example.
- [ ] New behavior has automated tests.
- [ ] Documentation remains applicable outside the GCC fish-farm case.

## Repository licence

The project owners should select a software licence before making the repository
public. A licence for the code does not automatically grant permission to
redistribute third-party papers or datasets.
