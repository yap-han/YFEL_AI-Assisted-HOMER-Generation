# Implemented changes, v0.7

## Outcome

Version 0.7 adds the site-data layer that the scenario contract declared out of scope in v0.6. The 13 `site_data_required` and `researcher_assumption` inputs of `config/minimum_homer_scenario.json` are now produced as candidates from public data and a documented archetype, and the chronological inputs are written in the format HOMER Pro imports. Combined with the 19 literature-supported inputs of the evidence pipeline, `site-coverage` reports 32 of 32.

## Added

| Area | Files |
|---|---|
| Package | `src/homer_gcc/site/` (calendar, weather, pvgis, load, metrics, register, sensitivity, outages, llm_inventory, manifest, pipeline, cli_site) |
| CLI | `site-fetch-weather`, `site-synthesise-load`, `site-export`, `site-coverage`, `site-generate`, `site-sweep`, registered from `cli.py` in two lines |
| Configuration | `config/site/site.saudi_allith.example.json`, `config/site/site_register.saudi_allith.json`, `config/site/archetypes/ras_intensive_marine.json`, `config/site/benchmarks.json`, `config/site/gcc_reference_sites.json`, `config/site/llm_inventory.fixture.json`, `config/site/llm_inventory.openai.example.json` |
| Fixtures | `data/site/nasa_power_fixture_allith_48h.json` (48 real hours, public domain NASA data), `data/site/pvgis_tmy_fixture_allith_48h.json` (48 real rows, JRC open data), `data/site/llm_inventory_fixture.json` |
| Tests | `tests/test_site_weather.py`, `tests/test_site_load.py`, `tests/test_site_exports.py`: 24 offline tests |
| Documentation | `docs/SITE_LAYER.md`, this file, README section, one line in the adaptation guide |

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
