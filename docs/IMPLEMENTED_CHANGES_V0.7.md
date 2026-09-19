# Implemented changes, v0.7

## Outcome

Version 0.7 adds the site-data layer that the scenario contract declared out of scope in v0.6. The 13 `site_data_required` and `researcher_assumption` inputs of `config/minimum_homer_scenario.json` are now produced as candidates from public data and a documented archetype, and the chronological inputs are written in the format HOMER Pro imports. Combined with the 19 literature-supported inputs of the evidence pipeline, `site-coverage` reports 32 of 32.

## Added

| Area | Files |
|---|---|
| Package | `src/homer_gcc/site/` (calendar, weather, pvgis, load, metrics, register, technology, sensitivity, outages, llm_inventory, manifest, pipeline, cli_site) |
| CLI | `site-fetch-weather`, `site-synthesise-load`, `site-export`, `site-coverage`, `site-generate`, `site-sweep`, `site-compare-registers`, `site-compare-results`, registered from `cli.py` in two lines |
| Configuration | `config/site/site.saudi_allith.example.json`, `config/site/site_register.saudi_allith.json`, `config/site/archetypes/ras_intensive_marine.json`, `config/site/benchmarks.json`, `config/site/gcc_reference_sites.json`, `config/site/llm_inventory.fixture.json`, `config/site/llm_inventory.openai.example.json`, `config/technology/technology_register.baseline_generic.json`, `config/technology/technology_register.evidence_updated.template.json` |
| Fixtures | `data/site/nasa_power_fixture_allith_48h.json` (48 real hours, public domain NASA data), `data/site/pvgis_tmy_fixture_allith_48h.json` (48 real rows, JRC open data), `data/site/llm_inventory_fixture.json`, `data/site/homer_results_example_{baseline,updated}.json` (illustrative exports on the results schema) |
| Tests | `tests/test_site_weather.py`, `tests/test_site_load.py`, `tests/test_site_exports.py`, `tests/test_site_technology.py`: 35 offline tests |
| Documentation | `docs/SITE_LAYER.md`, `docs/EXPERIMENTAL_DESIGN.md`, this file, README section, one line in the adaptation guide |

## Technology register and experimental design

The 19 `literature_supported` inputs now have a cited, bounded **baseline generic register** (`config/technology/technology_register.baseline_generic.json`: NREL ATB, IRENA, PNNL, BloombergNEF, HOMER documentation, manufacturer data) so a scenario is runnable before the evidence pipeline has produced its GCC and aquaculture values. The spec's `technology_register` entry folds it into the master register, the sensitivity table, the Monte Carlo cases, the coverage report and a consolidated `homer_input_sheet.csv` with all 32 inputs in contract order. `docs/EXPERIMENTAL_DESIGN.md` fixes the two-arm experiment: baseline generic is the control, the evidence pipeline's approved values (or a manual extraction on `technology_register.evidence_updated.template.json`) are the treatment, everything else held fixed. `site-compare-registers` documents which inputs moved and whether they left the baseline bounds; `site-compare-results` runs the pipeline's stage 8 comparison standalone on two exported HOMER rankings.

`literature_ids_from_aggregates` now reads the `coverage_status` field that `run-advanced-pipeline` actually writes to `scenario_coverage.json`.

## Changed

- `pyproject.toml` and `homer_gcc.__version__` to 0.7.0.
- `cli.py` imports `register_site_commands` and calls it at the end of `parser()`. No existing command changed.

## Governance

- Every site value is `candidate`. The 11-column export goes through `import-proposals` and the same human gate as literature evidence.
- The register stores source title, URL, access date, confidence and basis for every row; the manifest stores the URL, access time and SHA-256 of every raw download and every output.
- The archetype inventory is marked as drafted with AI assistance from named literature anchors and requires expert review before research use.
- The optional LLM inventory step reuses the pipeline's connector, keeps the immutable task hash, retries with feedback, and writes a candidate file that is never used unless `--use-llm-draft` is passed.

## Not done

- No `.homer` project file and no HOMER execution.
- No measured farm load; the profile is synthetic and calibrated to literature bands.
- Only one archetype (intensive marine RAS) is supplied; pond, flow-through and cage archetypes are inventory files the team can add without code changes.
- The Windows test run on Python 3.12.0rc3 hits a CPython temporary-directory cleanup bug in the pre-existing tests; the new tests pass `ignore_cleanup_errors=True`. CI on Python 3.11 is unaffected.
