# Modular energy-mix evidence prototype — v0.5

This runnable Python prototype supports a controlled workflow for rapidly assessing renewable, grid, storage and dispatchable conventional generation with HOMER or another optimisation model. The supplied application is a GCC fish farm, but the context profile, parameter ontology, evidence policy, validation engine and model registry are separate modules so the method can be reused for other locations and facilities.

Version 0.5 adds configurable OpenAI-compatible LLM extraction, batch submission of ingested documents, strict quotation/location validation, ontology-constrained JSON validation and bounded retries that preserve the original evidence task. It retains the complete path from a researcher-defined parameter table to validated scenario inputs and linked model results.

## What the prototype now does

| Stage | Function | Human control |
|---|---|---|
| Define | Stores proposed parameter values by scenario, technology and location | Researcher defines the required model inputs and initial values |
| Retrieve | Searches, deduplicates, ranks and screens academic evidence | Reviewer includes papers after title, abstract and full-text checks |
| Ingest | Registers and batch-ingests a versioned TXT, Markdown, JSON or PDF corpus | Rights notes and completeness gates are required by the configured workflow |
| Extract | Batch-submits schema-constrained tasks to a configured LLM or runs transparent regex triage | AI or regex output is always a candidate |
| Normalize | Converts approved units, currencies and cost years with deterministic rules | Currency rates and escalation assumptions must be supplied explicitly |
| Validate | Compares proposals with a low/base/high evidence envelope | Candidate evidence is excluded by default |
| Approve | Records approve, reject or modify decisions with reviewer identity and notes | No parameter enters a model scenario before this gate |
| Model | Freezes an input checksum and registers model outputs | The researcher runs HOMER and imports the resulting metrics |

The ontology contains 81 fields in eight families, including renewable resources, conventional generation, storage, grid/fuels, economics/resilience and context-specific loads. The source registry contains 15 verified peer-reviewed seed papers.

## Quick start

Run the manifest-driven workflow, including ingestion:

```bash
PYTHONPATH=src python -m homer_gcc run-workflow \
  --config config/workflow.example.json \
  --reset
```

The example uses one explicitly synthetic text fixture. Copy the workflow and
corpus-manifest examples before adapting them to real evidence. Relative paths
allow the same files to work for every GitHub collaborator.

Run the synthetic end-to-end quantitative demonstration:

```bash
PYTHONPATH=src python -m homer_gcc quant-demo
```

The demonstration exercises the seven quantitative modules, but every numerical evidence record and model output is explicitly synthetic. It is a software test, not a fish-farm result and not citable evidence.

Run all tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Run the reproducible academic-retrieval demonstration:

```bash
PYTHONPATH=src python -m homer_gcc demo
```

## Research workflow

### Integrated route

The recommended collaborative route is `run-workflow`. Its JSON configuration
connects initialization, corpus ingestion, extraction-task generation, optional
proposal and observation imports, validation, and audit exports. Each stage
remains modular and may also be run independently.

See `docs/GITHUB_COLLABORATION.md`, `CONTRIBUTING.md`,
`config/workflow.example.json` and `evidence/corpus_manifest.example.json`.

### 1. Import the manually defined parameter database

```bash
PYTHONPATH=src python -m homer_gcc import-proposals \
  --db output/study.sqlite \
  --file data/proposed_parameters_demo.csv
```

### 2. Retrieve and screen academic sources

```bash
PYTHONPATH=src python -m homer_gcc batch-retrieve \
  --db output/study.sqlite \
  --providers crossref,openalex \
  --include-region \
  --limit 50
```

Record title/abstract and full-text decisions with `screen`. A complete corpus
can then be ingested after lawful access:

```bash
PYTHONPATH=src python -m homer_gcc ingest-corpus \
  --db output/study.sqlite \
  --manifest evidence/corpus_manifest.json \
  --report output/ingestion_report.json
```

For a one-off document:

```bash
PYTHONPATH=src python -m homer_gcc ingest-fulltext \
  --db output/study.sqlite \
  --file evidence/paper.pdf \
  --source-id paper_01 \
  --doi 10.xxxx/example
```

### 3. Run configurable batch LLM extraction

Set the API key only in the environment; never place it in a tracked JSON file:

```bash
export OPENAI_API_KEY="your-key"
PYTHONPATH=src python -m homer_gcc llm-extract-batch \
  --db output/study.sqlite \
  --config config/llm.openai.example.json \
  --parameters pv.capital_cost,generator.capital_cost,battery.round_trip_efficiency \
  --output output/llm_extraction
```

The command submits every ingested document by default. Use `--document-ids`
only when deliberately processing a subset. Each accepted observation must:

- match a requested ontology parameter and permitted unit;
- include a finite low/central/high value in the correct order;
- contain a verbatim quotation found on the cited page;
- include the page and a line, paragraph, section, table or figure locator; and
- pass deterministic normalization and physical-range checks.

Malformed responses are retried up to `max_retries`. The task SHA-256 remains
unchanged across attempts, so retries cannot silently alter the supplied source
text. Results include `QUANTITATIVE_PERFORMANCE.md`, `PARAMETER_RANGES.csv`, a
JSON summary and per-document attempt logs.

To enable this within `run-workflow`, set `extraction.llm.enabled` to `true` in
the workflow configuration.

An offline integration example deliberately returns one malformed response and
then a valid correction, allowing the retry and validation path to be reproduced
without presenting fixture data as AI performance:

```bash
PYTHONPATH=src python -m homer_gcc run-workflow \
  --config config/workflow.llm_fixture.example.json \
  --reset
```

### 4. Create an extraction task without submitting it

The prototype emits a strict JSON contract that can be sent to an external AI system:

```bash
PYTHONPATH=src python -m homer_gcc make-extraction-task \
  --db output/study.sqlite \
  --document-id 1 \
  --parameters pv.capital_cost,generator.capital_cost \
  --output output/extraction_task.json
```

The task-only command remains available for providers that are not API-compatible. AI-produced JSON is always retained at `candidate` status until checked against the cited page, table or figure.

### 5. Review observations and validate proposals

```bash
PYTHONPATH=src python -m homer_gcc review-observation \
  --db output/study.sqlite \
  --observation-id 1 \
  --decision approved \
  --reviewer reviewer_1

PYTHONPATH=src python -m homer_gcc validate-parameter \
  --db output/study.sqlite \
  --proposal-id 1
```

### 6. Apply the human scenario gate

```bash
PYTHONPATH=src python -m homer_gcc review-validation \
  --db output/study.sqlite \
  --validation-id 1 \
  --decision approve \
  --reviewer engineering_reviewer
```

`approve` retains a supported proposed value and the evidence-derived low/high range. `modify` records a reviewer-selected low/base/high range and requires notes. Unsupported results cannot be approved unchanged.

### 7. Export inputs and register results

```bash
PYTHONPATH=src python -m homer_gcc export-scenario \
  --db output/study.sqlite \
  --scenario-id base \
  --output output/base_scenario.csv

PYTHONPATH=src python -m homer_gcc create-model-run \
  --db output/study.sqlite \
  --scenario-id base \
  --model-name "HOMER Pro" \
  --run-id homer_run_01

PYTHONPATH=src python -m homer_gcc import-model-results \
  --db output/study.sqlite \
  --run-id homer_run_01 \
  --file output/homer_results.json
```

The prototype exports approved tabular inputs and registers results. It does not create a native HOMER project file, operate the HOMER desktop interface or claim to have run HOMER.

## Audit outputs

`report` exports the ontology and source-screening reports plus:

- `proposed_parameters.csv`
- `full_text_documents.csv`
- `evidence_observations.csv`
- `parameter_validations.csv`
- `scenario_parameters.csv`
- `model_runs.csv`
- `model_inputs.csv`
- `model_outputs.csv`
- `prototype_summary.json`

See `docs/METHODOLOGY.md` for the research protocol,
`docs/IMPLEMENTED_CHANGES_V0.4.md` for the ingestion-workflow changes and
`docs/ADAPTATION_GUIDE.md` for reuse outside GCC aquaculture.

## Reproduce the 22-paper real-evidence pilot

When the optional local real-pilot corpus is present, the pilot ingests 22
open-access academic full texts, source-checks
candidate observations for PV capital cost, diesel-generator capital cost and
lithium-ion round-trip efficiency, normalizes cost records to 2025 USD, and
evaluates evidence-pipeline yield and the deterministic extraction baseline. It
does not report extraction accuracy because no independent human-labelled gold
standard currently exists. Run:

```bash
PYTHONPATH=src python scripts/run_real_evidence_pilot.py
```

Results are written to `real_pilot/output/`, including:

- `EFFECTIVENESS_EVALUATION.md`
- `RECALCULATED_VALUES.csv`
- `HUMAN_REVIEW_QUEUE.csv`
- `real_evidence_pilot.sqlite`
- `corpus_manifest.json`
- deterministic-extraction benchmark and complete audit exports

The full texts and generated pilot database are excluded from Git by default;
collaborators should obtain lawful copies independently. The recalculated values
are exploratory. All ten usable observations remain at
`candidate` status and the two ambiguous observations remain
`needs_correction`; the script deliberately creates no model-ready scenario
parameters until a named human reviewer approves the evidence.
