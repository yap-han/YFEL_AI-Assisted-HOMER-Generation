# Site-data layer (v0.7)

## What it fills

`config/minimum_homer_scenario.json` declares 32 inputs for a PV-diesel-battery-grid screening scenario. Nineteen are `literature_supported` and belong to the evidence pipeline. The other thirteen are site data or researcher assumptions that no literature search can supply: the hourly load and its peak, the GHI and temperature series, the grid tariff and sellback rate, outage frequency and duration, the diesel price, the discount rate, inflation, the project lifetime and the permitted capacity shortage. The site layer produces all thirteen as candidates, in the two formats the team uses, and writes the time series in the single-column format HOMER Pro imports.

Nothing the site layer produces is approved evidence. Every register value carries `validation_status: candidate` and enters the evidence pipeline through `import-proposals`, where a named reviewer approves, modifies or rejects it exactly as for a literature value.

## Modules

| Module | Function |
|---|---|
| `site/calendar.py` | 8,760-hour calendar; 29 February dropped so every year has the row count HOMER expects |
| `site/weather.py` | NASA POWER hourly API (free, no key): fetch, cache, gap filling, unit conversion, reference-year selection, monthly table with clearness index |
| `site/pvgis.py` | PVGIS v5.3 TMY as an independent irradiance cross-check, rolled from UTC to local time |
| `site/load.py` | Bottom-up hourly load from an equipment inventory with calendar, biomass, dissolved-oxygen and temperature drivers and seeded noise |
| `site/metrics.py` | Annual energy, peak, load factor, kWh per kg, class fractions, HOMER-equivalent variability, literature benchmark checks |
| `site/register.py` | Site parameter register validation, pipeline-derived rows, the 11-column and 10-column exports, scenario coverage |
| `site/sensitivity.py` | Sensitivity table with the basis of every bound; seeded triangular Monte Carlo cases |
| `site/outages.py` | Seeded outage schedule from mean frequency and mean repair time |
| `site/llm_inventory.py` | Optional AI-drafted inventory using the pipeline's connector and fixture pattern; output is always a candidate |
| `site/technology.py` | Technology register for the 19 literature-supported inputs in two tiers (`baseline_generic`, `evidence_updated`): validation, merge, comparison, the consolidated HOMER input sheet |
| `site/manifest.py` | Run manifest with source URLs, access times and SHA-256 of every input and output |
| `site/pipeline.py` | Orchestration and the six-site sweep |
| `site/cli_site.py` | The `site-*` subcommands |

## The technology register and the two-arm experiment

The 19 `literature_supported` inputs are the evidence pipeline's territory, but a scenario cannot run without them. `config/technology/technology_register.baseline_generic.json` supplies them as cited, bounded candidates from public techno-economic datasets and HOMER documentation (NREL ATB, IRENA, PNNL, BloombergNEF, manufacturer data), deliberately generic. The spec names it under `technology_register`, so `site-generate` folds it into the master register, the sensitivity table, the Monte Carlo cases, the coverage report and a consolidated `homer_input_sheet.csv` with all 32 inputs in contract order.

That register is the control arm of the experiment in `docs/EXPERIMENTAL_DESIGN.md`. The evidence pipeline's approved GCC and aquaculture values, or a manual extraction entered in `technology_register.evidence_updated.template.json`, are the treatment arm. `site-compare-registers` reports which inputs moved and whether they left the baseline bounds; `site-compare-results` compares two exported HOMER rankings. Pass `--no-technology-register` to export the site layer alone.

The package stays standard-library only.

## Commands

```bash
# Everything for one site (downloads ten years of NASA POWER on first run, then uses the cache)
PYTHONPATH=src python -m homer_gcc site-generate \
  --spec config/site/site.saudi_allith.example.json --output output/site --pvgis

# Stages separately
PYTHONPATH=src python -m homer_gcc site-fetch-weather --spec ... --pvgis
PYTHONPATH=src python -m homer_gcc site-synthesise-load --spec ...
PYTHONPATH=src python -m homer_gcc site-export --spec ... --scenarios base,low,high --db output/study.sqlite

# Coverage of the 32-input contract once the evidence pipeline has its provisional values
PYTHONPATH=src python -m homer_gcc site-coverage \
  --register output/site/site_register_resolved.json \
  --aggregates output/advanced_pipeline/scenario_coverage.json

# Site-side coverage only, before the evidence pipeline has been run: 13 of 32, with the
# 19 literature parameters listed as gaps. `--aggregates` is optional and the file it names
# is written by run-advanced-pipeline, not by the site layer.
PYTHONPATH=src python -m homer_gcc site-coverage \
  --register output/site/site_register_resolved.json

# Or name the literature parameters directly, which reports 32 of 32
PYTHONPATH=src python -m homer_gcc site-coverage \
  --register output/site/site_register_resolved.json \
  --literature-ids pv.capital_cost,pv.replacement_cost,pv.om_cost,pv.derating_factor,pv.lifetime,generator.capital_cost,generator.replacement_cost,generator.om_cost,generator.electrical_efficiency,generator.operating_lifetime,fuel.generator_minimum_load,battery.capital_cost,battery.replacement_cost,battery.om_cost,battery.round_trip_efficiency,battery.minimum_soc,battery.float_life,converter.efficiency,converter.capital_cost

# Coverage with the baseline technology register (32 of 32 before the evidence pipeline has run)
PYTHONPATH=src python -m homer_gcc site-coverage \
  --register output/site/site_register_resolved.json \
  --technology-register config/technology/technology_register.baseline_generic.json

# Control versus treatment: which of the 19 inputs moved, and did they leave the baseline bounds
PYTHONPATH=src python -m homer_gcc site-compare-registers \
  --a config/technology/technology_register.baseline_generic.json \
  --b config/technology/technology_register.evidence_updated.v1.json \
  --output output/site/register_comparison.csv

# Stage 8 standalone: compare two exported HOMER architecture rankings
PYTHONPATH=src python -m homer_gcc site-compare-results \
  --baseline evidence/homer_results/baseline.json --updated evidence/homer_results/updated.json \
  --output output/site/energy_mix_impact.json

# Phase 2 country sensitivity: weather for six GCC reference sites
PYTHONPATH=src python -m homer_gcc site-sweep --output output/site/sweep

# Offline demonstration with the bundled 48-hour fixtures (what the tests run)
PYTHONPATH=src python -m homer_gcc site-generate --spec config/site/site.saudi_allith.example.json \
  --output output/site_demo --years 2021-2023 --fixture data/site/nasa_power_fixture_allith_48h.json \
  --pvgis --pvgis-fixture data/site/pvgis_tmy_fixture_allith_48h.json
```

## Outputs

| Path | Content | HOMER dialog |
|---|---|---|
| `weather/ghi_{base,low,high}.txt` | 8,760 hourly GHI values, kW/m2 | Solar GHI Resource, Import |
| `weather/temperature_{base,low,high}.txt` | 8,760 hourly ambient temperatures, degC | Temperature Resource, Import |
| `weather/wind_{base,low,high}.txt` | 8,760 hourly wind speeds, m/s at 10 m | Wind Resource, Import (anemometer 10 m) |
| `weather/weather_years.json` | Which years were selected and why, annual GHI, fill statistics, file hashes | |
| `weather/weather_monthly_base.csv` | Monthly GHI, clearness index, temperature, wind, humidity for the base year | Compare with HOMER's computed monthly table |
| `weather/weather_crosscheck.json` | NASA versus PVGIS annual and monthly GHI | |
| `load/load_total_base.txt` | 8,760 hourly total load, kW | Electric Load, Import; set both random variability fields to 0 |
| `load/load_critical_base.txt` | Critical-class load only | Electric Load in the reliability scenario |
| `load/load_total_{low,high}.txt` | Base scaled by the spec's `low_high_scale` | Load sensitivity |
| `load/load_total_weather_{low,high}.txt` | Load synthesised on the low and high GHI years, to pair with those resource files | Paired weather scenarios |
| `load/load_summary.json` | Validation metrics, benchmark checks, shares by ontology ID, variability figures | |
| `load/load_disaggregated_base.csv` | Hour by hour, per class and per equipment item | |
| `load/inventory_used.json` | The exact inventory and seed behind the files | |
| `site_register_resolved.json` | Register with the pipeline-derived rows added | |
| `technology_register_resolved.json` | The technology register used in this run, with its tier | |
| `homer_input_sheet.csv` | All 32 contract inputs in order, plus supporting rows, each with origin, low, base, high, source, locator and status | The one sheet to build the HOMER project from |
| `proposed_parameters_site.csv` | Site rows in the evidence-pipeline format, importable with `import-proposals` | |
| `proposed_parameters_technology.csv` | Technology rows in the same format, through the same gate | |
| `master_parameter_register.csv` | Team format: ten columns from the methodology note, site and technology rows together | |
| `scenario_coverage_site.json` | 32-input coverage: site-layer candidates, technology-register candidates, literature, gaps | |
| `sensitivity_table.csv` | Low, base and high for every input with the basis of each bound | HOMER sensitivity variables |
| `monte_carlo_cases.csv`, `monte_carlo_summary.json` | 200 seeded triangular draws and P10/P50/P90 | Uncertainty discussion |
| `grid_outage_schedule.txt`, `outage_stats.json` | One seeded realisation of the outage statistics and its stats | Grid, Reliability: enter frequency and repair time from the register |
| `run_manifest.json` | Provenance and SHA-256 of everything above | |
| `SITE_SUMMARY.md` | Human-readable summary with the HOMER entry map | |

## Method

**Weather.** One request per calendar year to the NASA POWER hourly endpoint (`community=RE`, local solar time) for GHI, 2 m temperature, 10 m wind and relative humidity. Responses are cached under `<output>/cache` and hashed into the manifest. Missing hours (`-999`) are interpolated when the run is six hours or shorter, otherwise filled from the same hour on adjacent days; a series with more than 2 percent missing is rejected. GHI in Wh/m2 per hour is divided by 1,000 to give kW/m2. The base year is the one nearest the ten-year median annual GHI; low and high are the minimum and maximum years, and each label's temperature and wind files come from the same year so the cooling load stays consistent with the irradiance. The monthly table reports the clearness index against the extraterrestrial daily irradiation on Klein's representative days, which is what HOMER displays after import. PVGIS is an independent satellite product; a difference above 8 percent is reported as a warning.

**Load.** Each inventory item has a rated power, quantity, duty cycle, criticality class and a driver. Constant items follow the standing-biomass index; `diurnal_do` items (blowers, oxygen generators) add a pre-dawn peak and a metabolic factor; `temperature` items (chillers) follow ambient temperature through a linear thermal model divided by a COP that derates above 35 degC; `schedule` items follow feeding events or lighting hours. A seeded Gaussian factor per item and hour adds short-term variability, so the analyst enters 0 percent in HOMER's two random-variability fields. The critical, essential and deferrable classes are written separately and their sum is asserted equal to the total. Validation compares annual energy per kilogram of fish, load factor, aeration-plus-oxygenation share, cooling share and critical fraction with the bands in `config/site/benchmarks.json`, each of which cites its source.

**Register.** `config/site/site_register.<site>.json` holds every site-data and researcher-assumption parameter with base, low, high, unit, source title, URL, access date, confidence, basis and status. Units are validated against the ontology's allowed units and the ordering low <= base <= high is enforced. At run time the pipeline appends the derived rows (load profile mean and peak, GHI, temperature, critical fraction) with the file names and hashes they came from.

## Limits

- No measured farm data were available; the load is an engineering estimate calibrated to literature bands and needs aquaculture-engineering review of the inventory.
- NASA POWER is a satellite and reanalysis product on a coarse grid; a coastal cell can differ from a station. PVGIS bounds but does not remove that error.
- The cooling model is a linear function of dry-bulb temperature and ignores latent load.
- The tariff category, the non-residential sellback rate and the delivered diesel price are the register rows with the widest bounds and the lowest confidence.
- The AI inventory step is tested with the fixture connector only.
- The layer writes HOMER-importable files; it does not create a `.homer` project or run HOMER.
