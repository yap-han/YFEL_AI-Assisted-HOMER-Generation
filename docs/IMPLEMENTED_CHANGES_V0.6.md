# Implemented changes — v0.6

## Outcome

Version 0.6 implements a publication-oriented, fail-fast 22-paper workflow with separate models for high-volume relevance screening and evidence extraction. The command never substitutes fixture results when a live provider, original PDF, manual review or HOMER result is unavailable.

## Eight requested stages

| Requested stage | Implementation | Gate/output |
|---|---|---|
| Run all 22 with a real LLM | Manifest requires exactly 22 PDFs; provider preflight makes a real call to Luna and Terra before OCR | `PIPELINE_STATUS.json` records any blocker |
| Manual review | Deterministic sample covers parameters first, then additional observations | `HUMAN_REVIEW.csv`, `human_review_metrics.json` |
| Chunking and relevance | Overlapping page-preserving chunks; Luna selects chunks and parameter IDs | Per-document screening cache and chunk counts |
| OCR and tables | Mistral OCR 4.1 with Markdown tables, blocks and confidence scores | Raw per-document OCR caches |
| System boundaries | Deterministic boundary taxonomy; separate parameter/boundary aggregates | `boundary_aggregates.json` |
| Telemetry and cost | Model, prompt, hashes, usage, latency and price snapshot per call | `telemetry.json` |
| Complete scenario | 32-field minimum PV–diesel–battery–grid contract | `scenario_coverage.json` |
| Mix impact | Compares preferred architecture in two real HOMER exports | `energy_mix_impact.json` |

## Pause/resume behavior

The process exits with code 3 at a research gate. OCR and LLM responses are cached by PDF or task hash, so completing manual review and rerunning does not require repeating unchanged provider calls. Use `--reset` only for the generated SQLite database; it does not delete provider caches.

## Correctness metrics

The manual sample reports four separate proportions:

- numerical correctness;
- semantic parameter correctness;
- verbatim quotation/location correctness; and
- system-boundary correctness.

It also reports the proportion passing all four dimensions. The software refuses to calculate these metrics when a row lacks a named reviewer or any yes/no judgement.

## Remaining external prerequisites

The repository cannot supply provider credentials, third-party PDFs or a HOMER licence/run. Collaborators must add those inputs locally, keep secrets outside Git, and retain evidence of lawful document access.
