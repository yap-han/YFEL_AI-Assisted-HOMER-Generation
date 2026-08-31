#!/usr/bin/env python3
"""Run a reproducible 22-paper real-evidence pilot for three HOMER parameters.

The script intentionally keeps the source-checked observations at ``candidate``
status.  It produces exploratory evidence envelopes, but it does not promote
them to model-ready scenario parameters without a named human reviewer.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import fmean

from homer_gcc import db
from homer_gcc.evidence import extract_numeric_candidates, read_document
from homer_gcc.ontology import load_json
from homer_gcc.quantitative import import_proposals
from homer_gcc.reporting import export_reports
from homer_gcc.validation import prepare_observation, validate_proposal


SOURCES = [
    ("01", "01_utility_scale_pv_battery.txt", "Techno-Economic Analysis of Utility-Scale Solar Photovoltaic Plus Battery Power Plant", "10.3390/en14238145", 2021, "Energies", "Mauritius"),
    ("02", "02_mono_poly_pv_feasibility.txt", "A Techno-Economic Feasibility Analysis of Mono-Si and Poly-Si Photovoltaic Systems in the Rooftop Area of Commercial Building under the Feed-in Tariff Scheme", "10.3390/su13094709", 2021, "Sustainability", "Hong Kong"),
    ("03", "03_grid_integrated_renewables.txt", "Modeling, Analysis and Optimization of Grid-Integrated and Islanded Solar PV Systems for the Ethiopian Residential Sector", "10.3390/en14113360", 2021, "Energies", "Ethiopia"),
    ("05", "05_pv_techno_economic_comparison.txt", "Comparative Techno-Economic Evaluation of a Standalone Solar Power System for Different Photovoltaic Technologies", "10.3390/en16176262", 2023, "Energies", "Global"),
    ("06", "06_renewable_feasibility_homer.txt", "A Techno-Economic-Environmental Feasibility Study of Residential Solar Photovoltaic/Biomass Power Generation for Rural Electrification", "10.3390/su16052036", 2024, "Sustainability", "Global"),
    ("07", "07_standalone_hybrid_optimization.txt", "Optimization and Evaluation of a Stand-Alone Hybrid System Based on Renewable and Biomass Energy Using HOMER Pro", "10.3390/su16209012", 2024, "Sustainability", "Cameroon"),
    ("08", "08_solar_pv_diesel_analysis.txt", "Technical and Economic Analysis of Solar PV/Diesel Generator Smart Hybrid Power Plant Using Different Battery Storage Technologies for SRM IST, Delhi-NCR Campus", "10.3390/su15043666", 2023, "Sustainability", "India"),
    ("09", "09_hybrid_diesel_generators.txt", "Techno-Economic Analysis of Hybrid Diesel Generators and Renewable Energy for a Remote Island in Indonesia", "10.3390/su14169846", 2022, "Sustainability", "Indonesia"),
    ("10", "10_homer_modeling_optimization.txt", "A Hybrid Photovoltaic/Diesel System for Off-Grid Applications in Lubumbashi, DR Congo: A HOMER Pro Modeling and Optimization Study", "10.3390/su15108162", 2023, "Sustainability", "DR Congo"),
    ("12", "12_pv_diesel_battery_management.txt", "Energy Management and Optimization of a PV/Diesel/Battery Hybrid Energy System Using a Combined Dispatch Strategy", "10.3390/su11030683", 2019, "Sustainability", "Malaysia"),
    ("13", "13_data_center_pv_battery.txt", "Development of a PV/Battery Micro-Grid for a Data Center in Bangladesh: An Energy Management and Techno-Economic Analysis", "10.3390/su152215691", 2023, "Sustainability", "Bangladesh"),
    ("14", "14_microgrids_storage_comparison.txt", "Techno-Economic Comparison of Microgrids and Traditional Grid Expansion in Myanmar", "10.3390/en18184988", 2025, "Energies", "Myanmar"),
    ("15", "15_standalone_microgrid.txt", "From Renewable Extremes to Practical Hybrids: Techno-Economic Analysis of a Standalone Microgrid", "10.3390/app16041761", 2026, "Applied Sciences", "Global"),
    ("16", "16_storage_lithium_lead_hydrogen.txt", "Comparative Techno-Economic and Life Cycle Assessment of Stationary Energy Storage Systems: Lithium-Ion, Lead-Acid, and Hydrogen", "10.3390/batteries11100382", 2025, "Batteries", "Global"),
    ("17", "17_second_life_lfp.txt", "Second-Life Assessment of Commercial LiFePO4 Batteries Retired from Electric Vehicles", "10.3390/batteries10090306", 2024, "Batteries", "Global"),
    ("18", "18_bess_optimization_review.txt", "A Review of Battery Energy Storage Optimization in the Built Environment", "10.3390/batteries11050179", 2025, "Batteries", "Global"),
    ("19", "19_bess_performance.txt", "Battery Energy Storage System Performance in Providing Various Electricity Market Services", "10.3390/batteries10030069", 2024, "Batteries", "Germany"),
    ("20", "20_lead_lithium_lifetime_comparison.txt", "Comparison of Lead-Acid and Li-Ion Batteries Lifetime Prediction Models in Stand-Alone Photovoltaic Systems", "10.3390/app11031099", 2021, "Applied Sciences", "Global"),
    ("21", "21_battery_capacity_practical_applications.txt", "Determination of Lithium-Ion Battery Capacity for Practical Applications", "10.3390/batteries9090459", 2023, "Batteries", "Global"),
    ("22", "22_storage_evaluation.txt", "An Extended Approach to the Evaluation of Energy Storage Systems: A Case Study of Li-Ion Batteries", "10.3390/electronics12112391", 2023, "Electronics", "Global"),
    ("23", "23_lfp_advances_review.txt", "Recent Advances in Lithium Iron Phosphate Battery Technology: A Review", "10.3390/batteries10120424", 2024, "Batteries", "Global"),
    ("24", "24_liion_ev_applications.txt", "Li-Ion Batteries for Electric Vehicle Applications: Current Knowledge and Future Perspectives", "10.3390/en18040786", 2025, "Energies", "Global"),
]


OBSERVATIONS = [
    {
        "parameter_id": "pv.capital_cost", "source_id": "real_pilot_01",
        "raw_value_text": "USD 1097/kWdc fixed tilt; USD 1181/kWdc single axis",
        "raw_value_min": 1097, "raw_value_central": 1139, "raw_value_max": 1181,
        "raw_unit": "USD/kWdc", "source_cost_year": 2021, "target_cost_year": 2025,
        "annual_escalation_rate": 0.02, "technology": "PV",
        "system_boundary": "Total installed utility-scale PV cost, DC nameplate",
        "context_location": "Mauritius", "scale": "utility",
        "locator": "web full text line 97; immediately after Table 3",
        "context_excerpt": "Total installed PV costs are reported separately for fixed-tilt and single-axis systems.",
        "extraction_confidence": 0.98, "applicability_score": 0.65,
    },
    {
        "parameter_id": "pv.capital_cost", "source_id": "real_pilot_02",
        "raw_value_text": "USD 3354/kW mono-Si; USD 3250/kW poly-Si",
        "raw_value_min": 3250, "raw_value_central": 3302, "raw_value_max": 3354,
        "raw_unit": "USD/kWdc", "source_cost_year": 2020, "target_cost_year": 2025,
        "annual_escalation_rate": 0.02, "technology": "rooftop PV",
        "system_boundary": "PV module, inverter, other hardware and labor",
        "context_location": "Hong Kong", "scale": "commercial rooftop",
        "locator": "web full text line 450; Table 4 discussion",
        "context_excerpt": "The study estimates separate initial investment costs for mono-Si and poly-Si systems.",
        "extraction_confidence": 0.98, "applicability_score": 0.35,
    },
    {
        "parameter_id": "pv.capital_cost", "source_id": "real_pilot_03",
        "raw_value_text": "USD 798 to USD 619 per kW",
        "raw_value_min": 619, "raw_value_central": 708.5, "raw_value_max": 798,
        "raw_unit": "USD/kWdc", "source_cost_year": 2021, "target_cost_year": 2025,
        "annual_escalation_rate": 0.02, "technology": "PV",
        "system_boundary": "PV capital input used in grid/PV sensitivity analysis",
        "context_location": "Ethiopia", "scale": "residential",
        "locator": "web full text lines 624-625; Figure 13 discussion",
        "context_excerpt": "PV capital cost was varied from USD 798 to USD 619 in the sensitivity analysis.",
        "extraction_confidence": 0.97, "applicability_score": 0.55,
    },
    {
        "parameter_id": "pv.capital_cost", "source_id": "real_pilot_14",
        "raw_value_text": "PV capital cost: 1250-2500 USD/kW",
        "raw_value_min": 1250, "raw_value_central": 1875, "raw_value_max": 2500,
        "raw_unit": "USD/kWdc", "source_cost_year": 2025, "target_cost_year": 2025,
        "technology": "PV", "system_boundary": "HOMER technology-library PV capital input",
        "context_location": "Myanmar", "scale": "village microgrid",
        "locator": "web full text lines 409-417; Table 6 and sensitivity parameters",
        "context_excerpt": "The HOMER study uses a PV capital-cost sensitivity range of USD 1250-2500/kW.",
        "extraction_confidence": 0.97, "applicability_score": 0.45,
    },
    {
        "parameter_id": "generator.capital_cost", "source_id": "real_pilot_08",
        "raw_value_text": "Diesel generator capital cost: 665 USD/kW",
        "raw_value_min": 665, "raw_value_central": 665, "raw_value_max": 665,
        "raw_unit": "USD/kW", "source_cost_year": 2023, "target_cost_year": 2025,
        "annual_escalation_rate": 0.02, "technology": "diesel generator",
        "system_boundary": "HOMER generator capital-cost input",
        "context_location": "India", "scale": "academic campus",
        "locator": "Table 5, diesel generator row",
        "context_excerpt": "The component input table reports a diesel-generator capital cost of USD 665/kW.",
        "extraction_confidence": 0.96, "applicability_score": 0.60,
    },
    {
        "parameter_id": "generator.capital_cost", "source_id": "real_pilot_14",
        "raw_value_text": "Diesel generator capital cost: 500 USD/kW",
        "raw_value_min": 500, "raw_value_central": 500, "raw_value_max": 500,
        "raw_unit": "USD/kW", "source_cost_year": 2025, "target_cost_year": 2025,
        "technology": "diesel generator", "system_boundary": "HOMER generator capital-cost input",
        "context_location": "Myanmar", "scale": "village microgrid",
        "locator": "Table 6, diesel generator row",
        "context_excerpt": "The HOMER input table reports generator capital cost on a USD/kW basis.",
        "extraction_confidence": 0.94, "applicability_score": 0.45,
    },
    {
        "parameter_id": "battery.round_trip_efficiency", "source_id": "real_pilot_08",
        "raw_value_text": "round-trip efficiency of 85-95%",
        "raw_value_min": 85, "raw_value_central": 90, "raw_value_max": 95,
        "raw_unit": "%", "technology": "lithium-ion battery",
        "system_boundary": "battery technology, boundary not fully specified",
        "context_location": "global review values", "scale": "technology range",
        "operating_conditions": "not specified",
        "locator": "web full text line 78",
        "context_excerpt": "The technology review gives an 85-95% round-trip-efficiency range for lithium-ion.",
        "extraction_confidence": 0.98, "applicability_score": 0.65,
    },
    {
        "parameter_id": "battery.round_trip_efficiency", "source_id": "real_pilot_16",
        "raw_value_text": "round-trip efficiency 90-95%",
        "raw_value_min": 90, "raw_value_central": 92.5, "raw_value_max": 95,
        "raw_unit": "%", "technology": "stationary lithium-ion battery",
        "system_boundary": "stationary battery pack, DC boundary",
        "context_location": "global assessment", "scale": "stationary pack",
        "operating_conditions": "controlled thermal conditions; approximately 80% depth of discharge",
        "locator": "web full text lines 67 and 197",
        "context_excerpt": "The assessment reports 90-95% for lithium-ion stationary storage.",
        "extraction_confidence": 0.98, "applicability_score": 0.75,
    },
    {
        "parameter_id": "battery.round_trip_efficiency", "source_id": "real_pilot_17",
        "raw_value_text": "about 94% to 95% at 0.6 C charging and 1 C discharge",
        "raw_value_min": 94, "raw_value_central": 94.5, "raw_value_max": 95,
        "raw_unit": "%", "technology": "commercial LFP cell",
        "system_boundary": "cell-level DC energy efficiency",
        "context_location": "laboratory test", "scale": "cell",
        "operating_conditions": "0.6C charge, 1C discharge, room temperature",
        "locator": "web full text line 223",
        "context_excerpt": "Commercial LFP cells are reported at about 94-95% under stated C-rates.",
        "extraction_confidence": 0.99, "applicability_score": 0.60,
    },
    {
        "parameter_id": "battery.round_trip_efficiency", "source_id": "real_pilot_20",
        "raw_value_text": "roundtrip efficiency of 90%",
        "raw_value_min": 90, "raw_value_central": 90, "raw_value_max": 90,
        "raw_unit": "%", "technology": "commercial LFP battery pack",
        "system_boundary": "battery pack DC boundary",
        "context_location": "stand-alone PV case study", "scale": "10.24 kWh pack",
        "operating_conditions": "manufacturer/case-study assumption",
        "locator": "web full text line 733",
        "context_excerpt": "The commercial LFP pack is modeled with 90% round-trip efficiency.",
        "extraction_confidence": 0.98, "applicability_score": 0.75,
    },
]


FLAGGED = [
    {
        "parameter_id": "generator.capital_cost", "source_id": "real_pilot_07",
        "raw_value_text": "narrative: USD 500/kW; table: USD 200/kW",
        "raw_value_min": 200, "raw_value_central": 350, "raw_value_max": 500,
        "raw_unit": "USD/kW", "source_cost_year": 2024, "target_cost_year": 2025,
        "annual_escalation_rate": 0.02, "technology": "diesel generator",
        "system_boundary": "HOMER generator capital-cost input",
        "context_location": "Cameroon", "scale": "stand-alone system",
        "locator": "web full text line 905 and component-cost table",
        "context_excerpt": "The narrative and table report inconsistent generator capital costs.",
        "extraction_confidence": 0.40, "applicability_score": 0.45,
        "review_notes": "Needs author/table adjudication; excluded from recalculation.",
    },
    {
        "parameter_id": "pv.capital_cost", "source_id": "real_pilot_07",
        "raw_value_text": "PV cost 463 USD/kW",
        "raw_value_min": 463, "raw_value_central": 463, "raw_value_max": 463,
        "raw_unit": "USD/kWdc", "source_cost_year": 2024, "target_cost_year": 2025,
        "annual_escalation_rate": 0.02, "technology": "PV",
        "system_boundary": "unclear whether component-only or turnkey installed",
        "context_location": "Cameroon", "scale": "stand-alone system",
        "locator": "component-cost table",
        "context_excerpt": "The reported cost lacks a sufficiently clear installed-system boundary.",
        "extraction_confidence": 0.65, "applicability_score": 0.35,
        "review_notes": "System boundary unresolved; excluded from turnkey-PV recalculation.",
    },
]


PROPOSALS = [
    {"parameter_id": "pv.capital_cost", "scenario_id": "oman_real_pilot", "context_location": "Oman", "technology": "PV", "proposed_value": 900, "proposed_unit": "USD/kWdc", "system_boundary": "Turnkey installed PV, DC nameplate", "source_basis": "Initial researcher value", "criticality": "medium", "entered_by": "researcher", "notes": "Initial value tested against candidate real evidence"},
    {"parameter_id": "generator.capital_cost", "scenario_id": "oman_real_pilot", "context_location": "Oman", "technology": "Diesel generator", "proposed_value": 500, "proposed_unit": "USD/kW", "system_boundary": "Installed generator, AC nameplate", "source_basis": "Initial researcher value", "criticality": "medium", "entered_by": "researcher", "notes": "Initial value tested against candidate real evidence"},
    {"parameter_id": "battery.round_trip_efficiency", "scenario_id": "oman_real_pilot", "context_location": "Oman", "technology": "Lithium-ion battery", "proposed_value": 90, "proposed_unit": "%", "system_boundary": "Battery DC-to-DC", "source_basis": "Initial researcher value", "criticality": "high", "entered_by": "researcher", "notes": "Initial value tested against candidate real evidence"},
]


def source_records() -> list[dict]:
    result = []
    for number, file_name, title, doi, year, journal, location in SOURCES:
        result.append({
            "id": f"real_pilot_{number}", "file_name": file_name, "title": title,
            "authors": [], "year": year, "doi": doi, "journal": journal,
            "source_type": "journal_article", "peer_reviewed": True, "open_access": True,
            "region_scope": "country_specific" if location != "Global" else "global",
            "countries": [] if location == "Global" else [location],
            "system_types": ["hybrid_energy_system", "energy_storage"],
            "parameter_families": ["renewable_resources", "grid_and_fuels", "storage_conversion", "economics_resilience"],
            "url": f"https://doi.org/{doi}", "evidence_level": "peer_reviewed_full_text",
            "relevance_note": "Included in 22-paper ingestion and extraction-effectiveness pilot.",
            "verification_status": "verified_full_text", "quality_score": 80,
        })
    return result


def find_source(source_id: str) -> dict:
    return next(item for item in source_records() if item["id"] == source_id)


def build_manifest(fulltext_dir: Path) -> dict:
    rows = []
    for source in source_records():
        path = fulltext_dir / source["file_name"]
        body = path.read_text(encoding="utf-8")
        rows.append({
            **source,
            "local_path": str(path),
            "character_count": len(body),
            "has_abstract": "Abstract" in body,
            "has_references": "References" in body,
            "full_text_gate_passed": len(body) >= 12000 and "Abstract" in body and "References" in body,
        })
    return {"corpus_name": "GCC HOMER real-evidence pilot", "document_count": len(rows), "documents": rows}


def deterministic_benchmark(db_path: Path, document_ids: dict[str, int], fulltext_dir: Path) -> dict:
    expected = defaultdict(list)
    for item in OBSERVATIONS:
        expected[(item["source_id"], item["parameter_id"])].append(item)
    total_candidates = 0
    expected_pairs_detected = 0
    pair_rows = []
    for source in source_records():
        document = read_document(fulltext_dir / source["file_name"])
        for parameter_id in ("pv.capital_cost", "generator.capital_cost", "battery.round_trip_efficiency"):
            definition = db.get_parameter_definition(db_path, parameter_id)
            candidates = extract_numeric_candidates(document["pages"], definition)
            total_candidates += len(candidates)
            reference_candidates = expected.get((source["id"], parameter_id), [])
            detected = False
            if reference_candidates:
                expected_numbers = {
                    float(value)
                    for item in reference_candidates
                    for value in (item["raw_value_min"], item["raw_value_central"], item["raw_value_max"])
                }
                detected = any(
                    any(abs(float(candidate[key]) - value) < 1e-9 for value in expected_numbers)
                    for candidate in candidates
                    for key in ("raw_value_min", "raw_value_central", "raw_value_max")
                )
                expected_pairs_detected += int(detected)
            pair_rows.append({
                "source_id": source["id"], "document_id": document_ids[source["id"]],
                "parameter_id": parameter_id, "candidate_count": len(candidates),
                "curated_candidate_pair": bool(reference_candidates),
                "curated_candidate_numeric_detected": detected,
            })
    candidate_pair_count = len(expected)
    return {
        "document_parameter_pairs": len(pair_rows),
        "deterministic_candidates": total_candidates,
        "curated_candidate_document_parameter_pairs": candidate_pair_count,
        "curated_candidate_numeric_detected_pairs": expected_pairs_detected,
        "candidate_pair_detection_rate": expected_pairs_detected / candidate_pair_count if candidate_pair_count else 0,
        "accuracy_status": "not_measured_no_human_gold_standard",
        "pair_results": pair_rows,
    }


def write_markdown(path: Path, metrics: dict, validations: list[dict]) -> None:
    validation_by_parameter = {item["parameter_id"]: item for item in validations}
    lines = [
        "# Real Full-Text Pilot: Results and Effectiveness",
        "",
        "## Scope and status",
        "",
        "This run ingests real open-access academic full texts and recalculates exploratory evidence envelopes for three HOMER parameters. Its source-checked candidate observations predate the configurable v0.5 LLM connector and therefore provide a corpus/yield baseline, not a live-model benchmark. The observations remain **candidate evidence** because no human domain reviewer has approved them. Consequently, no value is promoted to the model-ready scenario table.",
        "",
        "## Corpus and ingestion",
        "",
        f"- Full-text documents attempted: {metrics['documents_attempted']}",
        f"- Full-text documents ingested: {metrics['documents_ingested']}",
        f"- Full-text completeness gates passed: {metrics['full_text_gate_passed']}",
        f"- Ingestion success rate: {metrics['ingestion_success_rate']:.1%}",
        f"- Unique documents contributing usable quantitative evidence: {metrics['contributing_documents']}",
        f"- Document-to-usable-evidence yield: {metrics['document_yield']:.1%}",
        "",
        "## Exploratory recalculation (2025 USD for costs)",
        "",
        "Cost records were harmonized to 2025 USD with an explicit 2% annual escalation assumption; the 2020 Hong Kong study value uses its stated cost year and other records use the publication year as a provisional proxy. The recalculated central value is the unweighted median of source central estimates, while the envelope is the minimum low to maximum high. These assumptions are transparent and should be replaced by a reviewer-approved cost index and boundary-specific subset before publication.",
        "",
        "| Parameter | Initial value | Candidate evidence | Provisional selected value | Evidence envelope | Decision |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for parameter_id, initial in (("pv.capital_cost", 900), ("generator.capital_cost", 500), ("battery.round_trip_efficiency", 90)):
        row = validation_by_parameter[parameter_id]
        unit = row["canonical_unit"]
        lines.append(
            f"| `{parameter_id}` | {initial:g} {unit} | {row['evidence_count']} sources | "
            f"{row['aggregate_base']:.2f} {unit} | {row['aggregate_low']:.2f}-{row['aggregate_high']:.2f} {unit} | {row['decision']} |"
        )
    lines.extend([
        "",
        "The PV envelope is broad and the prototype flags a material conflict. That is a useful result: the papers mix utility, rooftop, residential, and generic HOMER cost assumptions from several countries, so a single global median should not be treated as an Oman quotation.",
        "",
        "The generator evidence meets the minimum count of two independent sources but is still candidate-only and only moderately transferable to Oman. The battery evidence is stronger in count, but mixes cell, pack, and generic technology boundaries; the 90% initial value remains inside the observed envelope.",
        "",
        "### Why each provisional value was selected",
        "",
        "For every parameter, the prototype selects the unweighted median of candidate source central estimates. This rule is reproducible and less sensitive to one extreme source than the arithmetic mean. The minimum low and maximum high are retained as the evidence envelope rather than being hidden by the central estimate.",
        "",
        "- `pv.capital_cost`: 1553.95 USD/kWdc is the candidate median, but the 670.03-3703.09 USD/kWdc envelope is conflict-flagged because sources mix utility, rooftop, residential and generic HOMER boundaries. It is not suitable as a final model input without boundary filtering.",
        "- `generator.capital_cost`: 595.93 USD/kW is the median of two candidate sources. It is retained provisionally because both values fall within a much narrower 500.00-691.87 USD/kW envelope, although GCC applicability still requires review.",
        "- `battery.round_trip_efficiency`: 91.25% is the median of four candidate sources and sits within an 85-95% envelope. It is the most stable of the three provisional selections, but cell, pack and system-level boundaries must still be separated.",
        "",
        "## Extraction effectiveness",
        "",
        f"- Source-checked usable observations: {metrics['usable_observations']}",
        f"- Flagged/excluded observations: {metrics['flagged_observations']}",
        f"- Parameters covered: {metrics['parameters_covered']}/3",
        f"- Deterministic regex candidates generated: {metrics['deterministic_candidates']}",
        f"- Curated candidate document-parameter pairs numerically detected by regex: {metrics['curated_candidate_numeric_detected_pairs']}/{metrics['curated_candidate_document_parameter_pairs']} ({metrics['candidate_pair_detection_rate']:.1%})",
        "- Accuracy: not measured because there is no independently human-labelled gold-standard dataset",
        "",
        "The regex extractor is a transparent prescreening baseline, not a reliable quantitative extractor. Long HTML-derived lines and omitted table cells cause it to select unrelated numbers or miss table values. The v0.5 connector now supplies schema-constrained AI extraction, but a live 22-report batch still requires a configured provider credential and human verification.",
        "",
        "## Reliability judgement at this stage",
        "",
        "| Layer | Judgement | Reason |",
        "|---|---|---|",
        "| Full-text ingestion | Effective | The corpus was loaded, hashed, registered, and linked to source metadata. |",
        "| Evidence discovery | Useful for triage | Eight papers yielded ten usable candidate observations across all three parameters. |",
        "| Deterministic extraction | Insufficient alone | Table loss and numeric clutter create misses and false matches. |",
        "| Recalculation | Effective for exploratory ranges | Normalization and aggregation are reproducible, but geographic/system-boundary heterogeneity remains visible. |",
        "| Model readiness | Not yet achieved | Candidate observations require human approval; no scenario parameters were exported. |",
        "",
        "## Human action required",
        "",
        "A reviewer should check the ten candidate observations against the cited page/table locations, resolve the two flagged records, and decide which system boundaries are comparable to a GCC fish-farm installation. After that review, rerun validation without `include_candidates`; only supported values should be promoted to HOMER scenarios.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_review_outputs(db_path: Path, output_dir: Path, validations: list[dict]) -> None:
    with db.connect(db_path) as connection:
        observations = [dict(row) for row in connection.execute(
            """
            SELECT o.observation_id, o.verification_status, o.parameter_id,
                   o.source_title, o.source_doi, s.url AS source_url,
                   o.raw_value_text, o.normalized_value_min,
                   o.normalized_value_central, o.normalized_value_max,
                   o.canonical_unit, o.system_boundary, o.context_location,
                   o.operating_conditions, o.locator, o.context_excerpt,
                   o.extraction_confidence, o.applicability_score,
                   o.review_notes
            FROM evidence_observations o
            LEFT JOIN source_registry s ON s.source_id=o.source_id
            ORDER BY o.verification_status DESC, o.parameter_id, o.observation_id
            """
        )]
    review_path = output_dir / "HUMAN_REVIEW_QUEUE.csv"
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(observations[0]))
        writer.writeheader()
        writer.writerows(observations)

    initial = {item["parameter_id"]: item["proposed_value"] for item in PROPOSALS}
    rows = [
        {
            "parameter_id": item["parameter_id"],
            "initial_value": initial[item["parameter_id"]],
            "recalculated_central": item["aggregate_base"],
            "evidence_low": item["aggregate_low"],
            "evidence_high": item["aggregate_high"],
            "canonical_unit": item["canonical_unit"],
            "candidate_source_count": item["independent_source_count"],
            "conflict_flag": item["conflict_flag"],
            "decision": item["decision"],
            "human_status": item["human_status"],
            "selection_method": "unweighted_median_of_candidate_source_central_estimates",
            "selection_justification": (
                "Median limits sensitivity to extreme source values; the full low-high envelope is retained. "
                + ("Wide dispersion is conflict-flagged; human boundary filtering is required. " if item["conflict_flag"] else "")
                + "Candidate-only result; not a human-approved HOMER input."
            ),
        }
        for item in validations
    ]
    values_path = output_dir / "RECALCULATED_VALUES.csv"
    with values_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(project_root: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "real_evidence_pilot.sqlite"
    if db_path.exists():
        db_path.unlink()
    ontology = load_json(project_root / "config" / "ontology.json")
    profile = load_json(project_root / "config" / "study_profile.json")
    db.initialize(db_path, ontology, profile)
    db.insert_sources(db_path, source_records())

    fulltext_dir = project_root / "real_pilot" / "fulltexts"
    manifest = build_manifest(fulltext_dir)
    (output_dir / "corpus_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    document_ids = {}
    for source in source_records():
        document = read_document(fulltext_dir / source["file_name"])
        document_ids[source["id"]] = db.insert_full_text_document(
            db_path, document, source_id=source["id"], title=source["title"], doi=source["doi"],
            metadata={"url": source["url"], "year": source["year"], "journal": source["journal"], "full_text_gate": "passed"},
        )

    proposal_ids = import_proposals(db_path, PROPOSALS)
    observation_ids = []
    for raw in OBSERVATIONS:
        source = find_source(raw["source_id"])
        item = {
            **raw, "document_id": document_ids[raw["source_id"]],
            "source_title": source["title"], "source_doi": source["doi"],
            "independent_source_key": source["doi"],
            "currency": "USD" if "USD/" in raw["raw_unit"] else None,
            "extraction_method": "codex_source_checked_manual_assist_v1",
            "verification_status": "candidate", "authoritative_source": False,
        }
        prepared = prepare_observation(db_path, item)
        observation_ids.append(db.insert_evidence_observation(db_path, prepared))

    flagged_ids = []
    for raw in FLAGGED:
        source = find_source(raw["source_id"])
        item = {
            **raw, "document_id": document_ids[raw["source_id"]],
            "source_title": source["title"], "source_doi": source["doi"],
            "independent_source_key": source["doi"], "currency": "USD",
            "extraction_method": "codex_source_checked_manual_assist_v1",
            "verification_status": "candidate", "authoritative_source": False,
        }
        prepared = prepare_observation(db_path, item)
        observation_id = db.insert_evidence_observation(db_path, prepared)
        db.review_evidence_observation(db_path, observation_id, "needs_correction", "automated_conflict_and_boundary_check", raw["review_notes"])
        flagged_ids.append(observation_id)

    validations = [validate_proposal(db_path, proposal_id, include_candidates=True) for proposal_id in proposal_ids]
    benchmark = deterministic_benchmark(db_path, document_ids, fulltext_dir)

    contributing = len({item["source_id"] for item in OBSERVATIONS})
    metrics = {
        "llm_status": "connector_implemented_but_not_called_for_this_22_report_baseline",
        "documents_attempted": len(SOURCES), "documents_ingested": len(document_ids),
        "full_text_gate_passed": sum(item["full_text_gate_passed"] for item in manifest["documents"]),
        "ingestion_success_rate": len(document_ids) / len(SOURCES),
        "contributing_documents": contributing, "document_yield": contributing / len(SOURCES),
        "usable_observations": len(observation_ids), "flagged_observations": len(flagged_ids),
        "parameters_covered": len({item["parameter_id"] for item in OBSERVATIONS}),
        "average_applicability": fmean(item["applicability_score"] for item in OBSERVATIONS),
        **{key: value for key, value in benchmark.items() if key != "pair_results"},
        "validation_results": validations,
        "candidate_observation_ids": observation_ids, "flagged_observation_ids": flagged_ids,
    }
    (output_dir / "effectiveness_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (output_dir / "deterministic_extraction_benchmark.json").write_text(json.dumps(benchmark, indent=2), encoding="utf-8")
    (output_dir / "selected_observations.json").write_text(json.dumps({"observations": OBSERVATIONS, "flagged": FLAGGED}, indent=2), encoding="utf-8")
    write_markdown(output_dir / "EFFECTIVENESS_EVALUATION.md", metrics, validations)
    write_review_outputs(db_path, output_dir, validations)
    export_reports(db_path, output_dir / "exports")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.project_root / "real_pilot" / "output"
    metrics = run(args.project_root.resolve(), output.resolve())
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
