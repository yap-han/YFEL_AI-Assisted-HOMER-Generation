# AI-assisted rapid energy-mix assessment methodology

## Purpose and methodological claim

The method rapidly tests the best mix of renewable and traditional generation for a defined demand context. HOMER or another optimiser performs the techno-economic comparison; this prototype controls how quantitative inputs are defined, discovered, extracted, checked, approved and linked to the resulting decision.

The defensible research claim is not that AI determines engineering truth. AI accelerates evidence discovery and numerical extraction. Deterministic rules perform conversions and checks. A human remains accountable for every published base-case input and any change to the evidence-derived range.

## Modular structure

| Module | Stable function | Replaceable configuration | GCC fish-farm example |
|---|---|---|---|
| Decision context | Defines demand, objectives and constraints | Study profile and context ontology families | Aeration and circulation reliability |
| Technology ontology | Defines comparable model fields | Renewable, generator, storage and grid families | PV, diesel/gas, battery and grid |
| Evidence discovery | Retrieves and deduplicates records | Providers, queries and source policy | Crossref and OpenAlex |
| Full-text evidence | Stores documents, pages and locators | Lawfully obtained source files | Academic paper or official report |
| Quantitative evidence | Separates reported values from assumptions | Observation records | Cost, efficiency or lifetime |
| Normalization | Applies deterministic conversion rules | Unit registry, FX and escalation inputs | USD/Wdc to USD/kWdc |
| Validation | Compares proposals with evidence envelopes | Thresholds and sensitivity priority | Supported, conditional or unsupported |
| Human gate | Promotes reviewed values into a scenario | Reviewer decision and notes | Oman low/base/high case |
| Model registry | Freezes inputs and links outputs | Model adapter and output metrics | HOMER NPC, LCOE and renewable fraction |

Changing a study should normally require replacing configuration and evidence, not rewriting the database or validation logic.

## End-to-end protocol

### Phase A — Predefine the decision

Specify the system boundary, time resolution, candidate technologies, critical and flexible loads, objectives and decision metrics. Predeclare how trade-offs among cost, emissions, renewable fraction and reliability will be handled.

For fish farms, aeration, oxygenation and circulation are safety-critical. A hospital, data centre or industrial site substitutes its own non-negotiable service constraints.

### Phase B — Define the proposed parameter table

The researcher defines the parameters required by the model based on system requirements. Each proposal stores:

- Stable ontology parameter ID
- Scenario, location and technology
- Proposed value and unit
- Component/system boundary
- Basis or rationale
- Criticality, researcher and notes

This is a hypothesis table, not approved model truth. Its purpose is to make the initial engineering assumptions explicit before AI-assisted validation.

### Phase C — Retrieve and screen evidence

For every parameter family and location:

1. Construct queries from the context profile and ontology.
2. Search the configured academic databases.
3. Save database, provider, query and retrieval time.
4. Normalize DOI identity and deduplicate records.
5. Apply independent source-quality and topical-relevance gates.
6. Screen titles and abstracts manually.
7. Expand strong sources through backward and forward citation chaining.
8. Confirm full-text eligibility before numerical extraction.

Academic-first does not mean academic-only. Government resource data, tariffs, fuel prices, manufacturer specifications and local quotations can be more authoritative for particular fields, but must enter through documented source classes.

### Phase D — Ingest full text and extract evidence

The ingestion layer stores a document checksum, metadata, text pages and locators. It accepts text, Markdown and structured JSON directly and uses `pdftotext` when available for PDFs.

An AI extraction task uses a rigid schema and requires the model to:

- Extract only explicitly stated numbers
- Preserve original wording, value and unit
- Return null instead of guessing
- Cite a page plus table, figure or line locator
- Capture technology, geography, scale, operating conditions and boundary
- Avoid averaging, normalization and source reconciliation

The software also provides a transparent regex extractor for pre-screening and testing. Neither mechanism approves evidence. Extracted observations remain candidates until a reviewer checks them against the source.

### Phase E — Normalize deterministically

Programmed rules convert compatible units, including power, energy, percentages, capital-cost bases and emissions factors. Currency conversion requires an explicit exchange rate. Cost-year conversion requires source year, target year and escalation rate.

The system refuses unsupported or dimensionally incompatible conversions. It also applies ontology physical limits, including the universal 0–100% limit for percentage ratios. Original values are never overwritten; normalized values and conversion factors are stored alongside them.

### Phase F — Validate quantitative proposals

For parameter \(i\), approved observations form an evidence envelope:

\[
L_i=\min_j(l_{ij}),\qquad
B_i=\operatorname{median}_j(b_{ij}),\qquad
H_i=\max_j(h_{ij})
\]

The proposed normalized value \(P_i\) is classified as below, within or above \([L_i,H_i]\). The algorithm also checks:

- Number of independent sources
- Number of human-approved observations
- Authoritative-source status
- Average applicability to the target context
- Physical validity
- Material conflict between non-overlapping source ranges
- Whether unreviewed candidates were included

Default classifications are:

| Decision | Minimum interpretation |
|---|---|
| Supported | Proposal is within the envelope, normally has at least two independent approved sources, applicability is at least 0.5 and no material conflict exists |
| Conditionally supported | Proposal is within the envelope but evidence is limited, less applicable, conflicting or includes candidates |
| Not supported | Proposal is physically invalid or outside the evidence envelope |
| Insufficient evidence | No eligible, physically valid evidence is available |

These thresholds are project defaults, not universal scientific constants. They should be reported and sensitivity-tested.

### Phase G — Apply human approval

Validation remains pending until a named reviewer chooses:

- `approve`: retain an algorithmically supported proposed value with its evidence-derived low/high range;
- `modify`: supply or accept a revised low/base/high range and document the reason;
- `reject`: prevent the parameter from entering the scenario.

Candidate AI evidence cannot directly support an approved scenario. Values failing physical checks cannot be approved. Published base cases require 100% human approval; safety-critical or architecture-changing parameters should receive independent expert review.

### Phase H — Run and register the model

The approved scenario export contains low, base, high and selected values. Before a model run, the registry freezes the selected inputs and creates a SHA-256 checksum. Imported outputs remain linked to that exact snapshot.

Recommended outputs include net present cost, levelized cost of energy, renewable fraction, fuel use, emissions, unmet load and capacity mix. The prototype registers these fields but does not operate HOMER or create its proprietary project format.

### Phase I — Assess robustness and update the living database

Run low/base/high and additional sensitivity cases for fuel price, tariffs, technology cost, degradation, demand, outages, discount rate and reliability constraints. Report whether the preferred portfolio changes and identify the parameters responsible.

When new evidence appears, add new observations and validations without overwriting prior model-run snapshots. This makes the method suitable for living updates and rapid reassessment.

## Human-validation requirement

| Record type | Required review |
|---|---|
| Published base-case parameter | 100% |
| High-impact, derived or safety-critical value | 100%, preferably independent expert review |
| Unit, currency, cost-year and system-boundary conversion | 100% of final inputs |
| Candidate AI extraction | 100% before it becomes approved evidence |
| Low-impact metadata | Risk-based sampled audit after benchmark performance is demonstrated |
| Time series | 100% automated integrity checks plus human review of aggregates, anomalies and selected periods |

The AI prototype is therefore sufficient to assist the proposed workflow, but not to replace ontology design, source access, engineering judgment, HOMER execution or final human validation.

## Current limits

- The included numerical fixtures are synthetic and cannot support a research conclusion.
- The package generates an external AI task contract but does not bundle a proprietary LLM service.
- PDF extraction depends on locally available `pdftotext`; tables may still require manual or specialized parsing.
- Exact retrieval relevance is transparent but metadata can be incomplete.
- The validation envelope does not replace formal meta-analysis where study designs justify one.
- Native HOMER project generation and automated HOMER execution are outside the current prototype.
- The GCC ontology requires aquaculture and power-system expert approval before research use.
