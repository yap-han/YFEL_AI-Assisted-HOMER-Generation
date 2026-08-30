# Implemented software changes — version 0.3

Version 0.3 implements the seven quantitative changes required to turn the v0.2 literature-screening prototype into a controlled parameter-validation workflow.

| Requested change | Implementation | Verification |
|---|---|---|
| 1. Proposed-parameter table | `proposed_parameters` stores ontology ID, scenario, location, technology, proposed value/unit, boundary, basis, criticality and researcher. CSV/JSON import and single-record CLI are provided. | Tests create and retrieve a proposal; the demo imports three proposals. |
| 2. Full-text evidence ingestion | `full_text_documents` and `document_pages` retain checksum, source metadata, pages and ingestion method. TXT, Markdown, JSON and PDF-through-`pdftotext` are supported. A strict external-AI task contract and transparent regex pre-extractor are included. | Test ingests text and extracts a located PV-cost range; demo stores one document. |
| 3. Numerical evidence table | `evidence_observations` separates raw and normalized ranges and stores source identity, independent-source key, boundary, conditions, locator, extraction confidence, applicability and review state. | Demo stores six approved synthetic observations and one candidate extraction. |
| 4. Deterministic normalization | Explicit rules cover compatible power, energy, ratio, time, cost-basis and emissions conversions. FX and cost-year escalation require supplied assumptions; physical checks use the ontology. | Tests verify USD/Wdc→USD/kWdc, fraction→%, and rejection of missing FX/escalation assumptions. |
| 5. Parameter-validation engine | Proposed values are compared with an evidence low/median/high envelope. Decisions consider independent sources, applicability, physical checks, conflicts, authoritative sources and candidate status. | Tests produce supported, conditional and insufficient-evidence outcomes. |
| 6. Human-review interface | CLI commands approve/reject/correct observations and approve/reject/modify validations. Reviewer, time and notes are retained. Unsupported results cannot be approved unchanged; candidates do not enter scenarios. | Test confirms no scenario exists before review and that approval promotes the proposed value with evidence IDs. |
| 7. Model input/output registry | Approved scenarios export to CSV/JSON. A model run freezes inputs and checksum; imported outputs link to the run. | Test freezes an input snapshot and attaches an output; demo registers three inputs and six outputs. |

## Synthetic end-to-end result

`quant-demo` is a reproducible software demonstration. It processes three proposed parameters—PV capital cost, generator capital cost and battery round-trip efficiency—against six approved synthetic observations. All three pass the configured validation logic, are promoted through a scripted demonstration of the human gate, and become one frozen model-run snapshot. Six synthetic output metrics are registered.

These values demonstrate behavior only. They are clearly labelled synthetic in source fixtures, database metadata, command output and model-run notes. HOMER is not executed by the demo.

## Research-use boundary

The software is ready for a pilot using real academic, official and supplier evidence. A research conclusion still requires lawful full texts, human verification of every final numerical record, documented system boundaries, actual HOMER runs and robustness analysis. The modular structure allows a different study profile and ontology to reuse the same evidence, validation and model-registry modules.
