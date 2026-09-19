# Experimental design: does regional and application-specific evidence change the HOMER answer?

This document fixes the experiment the repository is built to run, from the first input to the reported result. It is the reference for the paper's methodology section and for anyone reproducing the study.

## 1. Research question

For a GCC aquaculture facility screened in HOMER Pro, does replacing generic technology parameters (global datasets and software defaults) with evidence extracted from GCC and aquaculture literature change the preferred renewable-conventional architecture, and by how much does it move the economic and reliability outcomes?

## 2. Hypotheses and decision variable

- **H1.** The preferred architecture (the minimum net present cost configuration HOMER returns) or its net present cost changes materially (more than 5 percent NPC, or a different preferred architecture) when the evidence-updated register replaces the baseline generic register, all site inputs held fixed.
- **H0.** It does not; generic defaults are sufficient for screening in this application.

Either outcome is publishable. H1 supports the paper's claim that a data-driven, application-specific parameter database is a necessary part of a decision-support model for the region. H0 is a useful negative result: screening can proceed on public datasets and effort should go to site data instead.

The decision variable is the preferred architecture and its NPC. Secondary outcomes are the levelized cost of energy, renewable fraction, unmet load, fuel consumption, CO2 emissions and the capacity shortage. Tertiary outcomes are the stability of the ranking across the sensitivity grid and the Monte Carlo cases.

## 3. The scenario contract

`config/minimum_homer_scenario.json` declares the 32 inputs a PV-diesel-battery-grid screening scenario needs, each with a system boundary and an evidence role.

| Evidence role | Count | Supplied by | What it contains |
|---|---|---|---|
| `site_data_required` | 11 | Site layer | Hourly load and its peak, GHI and temperature series, tariff, sellback, outage frequency and duration, diesel price, discount rate, inflation |
| `researcher_assumption` | 2 | Site layer, declared | Project lifetime, maximum annual capacity shortage |
| `literature_supported` | 19 | Technology register (two arms) | PV capital, replacement, O&M, derating, lifetime; generator capital, replacement, O&M, efficiency, lifetime, minimum load; battery capital, replacement, O&M, round-trip efficiency, minimum SOC, float life; converter efficiency and capital |

`site-coverage` refuses to report a runnable scenario until all 32 have a candidate.

## 4. Two-arm design

The 19 literature-supported inputs are the manipulated variable. Everything else is held fixed.

| | Control arm | Treatment arm |
|---|---|---|
| Register | `config/technology/technology_register.baseline_generic.json` | `technology_register.evidence_updated.*.json` |
| Evidence tier | `baseline_generic` | `evidence_updated` |
| Source class | Public techno-economic datasets and software documentation: NREL ATB, IRENA, PNNL, BloombergNEF, HOMER Pro documentation, manufacturer data | Peer-reviewed GCC and aquaculture literature through the evidence pipeline (OCR, screening, extraction, validation, human review, boundary-aware aggregation), or a manual extraction from the verified seed papers entered in the same schema |
| Specificity | Global, not regional, not application-specific | Regional and application-specific |
| Status | Candidate | Candidate until approved by a named reviewer |

**Held fixed across both arms:** the site (Al-Lith, 20.15 N 40.27 E), the archetype (intensive marine RAS, 500 t/yr), the synthesised hourly load and its critical split, the NASA POWER weather years (base 2016, low 2024, high 2020), the site register (tariff, sellback, outages, diesel, discount rate, inflation), the two researcher assumptions, the component search space, the dispatch strategy and the HOMER version.

**Why generic versus specific is the right contrast.** The paper's thesis is that a data-driven model for GCC aquaculture needs application-specific evidence. The cleanest test of that thesis is to compare the answer obtained with the best public generic values against the answer obtained with regional, application-specific evidence, holding the site fixed. Comparing the treatment arm against HOMER's factory defaults would conflate "specific versus generic" with "curated versus uncurated". The baseline register is therefore curated with the same rigour as the treatment register (source, locator, year, bounds, basis), so that the only systematic difference between the arms is specificity.

## 5. Procedure

| Stage | Action | Owner | Command or file | Output |
|---|---|---|---|---|
| S0 | Freeze the contract, the site spec, the archetype and the register seeds | Team | `config/` | Git commit hash in every manifest |
| S1 | Generate the site package: weather, load, site register, sensitivity, outages, manifest | Site layer | `site-generate --spec config/site/site.saudi_allith.example.json --output output/site --pvgis` | `output/site/` including `homer_input_sheet.csv` |
| S2 | Confirm 32 of 32 with the baseline register | Site layer | `site-coverage --register output/site/site_register_resolved.json --technology-register config/technology/technology_register.baseline_generic.json` | exit 0 |
| S3 | Build the HOMER project from the sheet: import the five time series, enter every scalar from the base column, define the search space | HOMER analyst | `homer_input_sheet.csv`, `SITE_SUMMARY.md` entry map | `.homer` project (control) |
| S4 | Run the control optimisation; export the architecture table | HOMER analyst | HOMER Pro; export on `evidence/homer_results/example.schema.json` | `evidence/homer_results/baseline.json` |
| S5 | Produce the treatment register: run the evidence pipeline to approved aggregates, or fill `technology_register.evidence_updated.template.json` by manual extraction, then import both registers through the human gate | Evidence lead | `run-advanced-pipeline`; `import-proposals --file output/site/proposed_parameters_technology.csv` | `technology_register.evidence_updated.v1.json`, approvals in SQLite |
| S6 | Compare the two registers before any run and record which inputs moved outside the baseline bounds | Evidence lead | `site-compare-registers --a baseline.json --b evidence_updated.json --output output/site/register_comparison.csv` | `register_comparison.csv` |
| S7 | Copy the control project, replace only the 19 technology inputs, run, export | HOMER analyst | HOMER Pro | `evidence/homer_results/updated.json` |
| S8 | Compare rankings | Any | `site-compare-results --baseline baseline.json --updated updated.json --output output/site/energy_mix_impact.json` | `energy_mix_impact.json` |
| S9 | Sensitivity: enter the low and high values from `sensitivity_table.csv` as HOMER sensitivity variables in both arms; pair the low and high GHI years with their own temperature files and load variants | HOMER analyst | `sensitivity_table.csv`, `weather/*_{low,high}.txt`, `load/load_total_weather_{low,high}.txt` | Ranking stability surface |
| S10 | Resilience: run the critical-load scenario with the outage statistics and the required autonomy | HOMER analyst | `load/load_critical_base.txt`, `outage_stats.json`, register row `resilience.required_autonomy` | Autonomy and unmet critical load |
| S11 | Generalisation: repeat S3 to S4 for the other GCC reference sites using the sweep weather, if in scope | HOMER analyst | `site-sweep`, `output/site/sweep/` | Country sensitivity |
| S12 | Report | Team | Section 8 below | Paper tables and figures |

S4 must be exported before S5 begins, and the file hash recorded, so the control result cannot be adjusted after the treatment result is known.

## 6. Sensitivity and uncertainty protocol

- **One-at-a-time grid.** Every row of `sensitivity_table.csv` carries low, base and high and the basis of each bound. HOMER's sensitivity variables take these directly and run every combination the analyst selects. The rows to prioritise are the ones that moved outside the baseline bounds in S6, the tariff category, the diesel price, the discount rate, and the load scale.
- **Paired weather.** The low and high GHI years are run with the temperature files of the same year and the load synthesised on that year, so cooling demand and irradiance are never mismatched.
- **Monte Carlo.** `monte_carlo_cases.csv` holds 200 seeded triangular draws across every bounded input, with P10, P50 and P90 in `monte_carlo_summary.json`. HOMER does not read these; they support the discussion of which inputs dominate the spread and can be run as a subset of cases if time allows.
- **Variability.** The imported load already carries hourly noise (day-to-day 3.2 percent, time-step 2.5 percent), so both HOMER random-variability fields are set to zero.

## 7. Validity

| Threat | Control |
|---|---|
| Control arm adjusted after seeing the treatment result | S4 exported and hashed before S5; the manifest records the commit and the file hashes |
| Arms differ in more than specificity | Same site package, same search space, same HOMER version; `site-compare-registers` documents every input that differs and whether it left the baseline bounds |
| Unit or boundary mismatch between arms | Both registers validate against the ontology's allowed units and the contract's system boundaries; `site-compare-registers` refuses to compare rows whose units differ |
| Synthetic load drives the result | Load is identical in both arms, so it cannot produce a difference between them; its absolute effect is bounded by the low and high load scenarios and by the benchmark bands it passes |
| Weather data error | NASA POWER cross-checked against PVGIS TMY (3.6 percent); low and high years bound the resource |
| Non-reproducible numbers | Seeded random draws, cached raw downloads, SHA-256 of every input and output, standard-library code |
| AI-generated values entering a result | Every value is `candidate`; nothing enters a scenario without a named reviewer's approval through `import-proposals` |

## 8. What the paper reports

1. Table: the 32-input contract with evidence role, source class and validation status for each arm (`homer_input_sheet.csv`).
2. Table: the register comparison, base values and bounds of both arms, and which treatment values fall outside the baseline bounds (`register_comparison.csv`).
3. Figure: the synthesised load, a January and a June day and the monthly energy, with the benchmark checks.
4. Figure: annual GHI by year with the reference years and the PVGIS check.
5. Table: control and treatment optimisation results, preferred architecture, NPC, LCOE, renewable fraction, fuel, emissions (`energy_mix_impact.json` plus the HOMER exports).
6. Figure: ranking stability across the sensitivity grid in both arms.
7. Table: resilience scenario results.
8. Table, if in scope: six-site sweep.
9. Limitations, from section 10 of this document and `SITE_LAYER.md`.

## 9. Fallback if the evidence pipeline does not run

The treatment arm does not depend on the LLM route. `config/technology/technology_register.evidence_updated.template.json` lists the 19 inputs with the units the baseline uses. A researcher fills it by reading the verified seed papers in `data/seed_sources.json` and any further GCC or aquaculture sources, recording title, URL or DOI, locator, year, base, low, high and basis for each row, and marks every row `candidate`. The file then goes through the same `site-coverage` validation, `import-proposals` gate and `site-compare-registers` comparison as a pipeline-produced register. The method claim of the paper survives because the extraction protocol, the schema, the units, the bounds and the human gate are unchanged; what changes is that the extraction step is manual rather than model-assisted, which is stated.

## 10. Limitations to state

- The baseline register is generic by design; a GCC-specific project price for PV, in particular, can sit well below the global weighted average, and this is exactly what the treatment arm is meant to reveal.
- The load is a calibrated engineering estimate with no measured farm data behind it. It is identical in both arms and cannot generate an inter-arm difference, but the absolute NPC carries its uncertainty.
- One archetype and one site are run; the six-site sweep covers weather only.
- Outage statistics are system-level SEC figures, not feeder-level.
- The cooling model is a linear function of dry-bulb temperature.
- Cost years differ between sources; the register records the cost year of every row and the deterministic normalisation in `normalization.py` is available if the team decides to bring all costs to one year.
