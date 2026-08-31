# Implemented changes — v0.5

Version 0.5 makes LLM extraction executable while preserving the human approval boundary.

| Requirement | Implementation | Evidence of operation |
|---|---|---|
| Configurable connector | `openai_compatible` provider accepts model, endpoint, timeout, temperature and API-key environment variable. A scripted fixture provider supports offline tests. | `config/llm.openai.example.json` and connector tests. |
| Batch submission | `llm-extract-batch` processes every ingested document by default; `run-workflow` can invoke the same batch stage. | Integrated workflow test. |
| Quotations and locations | Every observation requires `evidence_quote`, `page_number` and a line, paragraph, section, table or figure locator. The quote must exist verbatim on the cited page and contain `raw_value_text`. | Missing-quote response is rejected in tests. |
| Ontology/schema validation | Parameter IDs, finite ordered values, permitted units, confidence, applicability and allowed fields are checked before import. | Invalid responses create no observation. |
| Immutable retries | Malformed responses are retried up to the configured limit. Each attempt records the same task SHA-256 plus response status. | Offline test rejects attempt one and imports attempt two. |
| Quantitative reporting | Every batch writes processing counts, report-to-evidence yield, observation counts, parameter ranges, provisional medians, conflict flags and selection justifications. | `QUANTITATIVE_PERFORMANCE.md`, `PARAMETER_RANGES.csv` and JSON summary. |

## Interpretation boundary

The prototype does not have an independently human-labelled gold-standard dataset. It therefore reports operational performance and quantitative evidence yield, not AI accuracy. A schema-valid response can still be contextually wrong. Every observation remains `candidate`, and the selected median remains provisional until a named reviewer approves the source and system boundary.
