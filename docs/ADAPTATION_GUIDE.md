# Adapting the methodology to another context

The generic engine does not contain fish-farm or GCC search terms. Those terms live in the study profile and context-specific ontology fields.

## 1. Copy and edit the study profile

Change:

- `id`, `name` and `version`
- `locations` and `region_search_term`
- `domain_terms`
- `query_domain_terms`
- Climate or regional analogue terms
- Query templates, if the databases require different syntax

For a Singapore hospital study, domain terms might include `hospital`, `healthcare facility`, `critical care` and `medical campus`.

## 2. Retain or replace ontology families

The reusable energy-core families are:

- `grid_and_fuels`
- `renewable_resources`
- `conventional_generation`
- `storage_conversion`
- `economics_resilience`

Replace:

- `farm_context` with the new facility context
- `aquaculture_load` with the new load decomposition
- `site_environment` with the relevant operating constraints

Add wind, hydro, biomass, combined heat and power or another technology as a new family. The retrieval and screening code does not require modification.

## 3. Review technology comparability

Each generation option should include, where applicable:

| Dimension | Typical parameters |
|---|---|
| Resource or fuel | Time series, price, availability and heating value |
| Capacity | Candidate sizes and operating limits |
| Performance | Efficiency, derating, fuel curve and degradation |
| Economics | Capital, replacement, O&M and lifetime |
| Environmental | Fuel or output emissions factors |
| Reliability | Availability, minimum loading and outage behavior |

This shared structure is what allows HOMER to compare renewable and traditional technologies consistently.

## 4. Validate before retrieval

```bash
PYTHONPATH=src python -m homer_gcc validate \
  --profile config/my_profile.json \
  --ontology config/my_ontology.json
```

## 5. Run a bounded pilot

```bash
PYTHONPATH=src python -m homer_gcc batch-retrieve \
  --profile config/my_profile.json \
  --ontology config/my_ontology.json \
  --providers crossref,openalex \
  --families renewable_resources,conventional_generation,storage_conversion \
  --locations Singapore \
  --limit 25
```

Manually inspect false inclusions and false exclusions before increasing the search scale.

## 6. Screen and expand

Record human title/abstract decisions, then citation-chain the strongest included papers. Run another batch only after updating profile terms or ontology synonyms based on documented screening evidence.

## 7. Export an audit snapshot

```bash
PYTHONPATH=src python -m homer_gcc report --output output/my_study
```

Version the profile, ontology, policy, SQLite database and exports together. This allows the research paper to report exactly which configuration produced each evidence set and later HOMER scenario.

## 8. Reuse the quantitative workflow

The v0.3 evidence, normalization, validation, human-review and model-registry tables are context-neutral. For a new application:

1. Prepare a proposed-parameter CSV using stable IDs from the adapted ontology.
2. Extend `normalization.py` only when the new ontology introduces a genuinely new unit dimension.
3. Set applicability scores using a documented geography, technology, scale and boundary rubric.
4. Review the default two-independent-source and 0.5-applicability thresholds.
5. Define which parameters are high-impact or safety-critical before validation.
6. Keep AI observations as candidates until a human verifies their exact locator.
7. Export approved inputs, run the chosen model externally and import its declared output metrics.

The same database can register HOMER, MATLAB, Python or another model because the model-run layer stores generic parameter/value/unit snapshots. A model-specific adapter may be added without changing the evidence schema.
