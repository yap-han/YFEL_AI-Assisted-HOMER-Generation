from __future__ import annotations

import copy
import math
import random
import tempfile
import unittest
from pathlib import Path

from homer_gcc.ontology import load_json
from homer_gcc.site import load as site_load
from homer_gcc.site import metrics, outages, weather

ROOT = Path(__file__).resolve().parents[1]
NASA_FIXTURE = ROOT / "data" / "site" / "nasa_power_fixture_allith_48h.json"


def _synthetic_weather() -> dict[str, list[float]]:
    temperature = []
    for index in range(8760):
        day = index // 24
        hour = index % 24
        seasonal = 29.0 + 5.0 * math.sin(2.0 * math.pi * (day - 110) / 365.0)
        diurnal = 3.0 * math.sin(2.0 * math.pi * (hour - 9) / 24.0)
        temperature.append(round(seasonal + diurnal, 2))
    ghi = [max(0.0, 0.9 * math.sin(math.pi * ((index % 24) - 6) / 12.0)) if 6 <= index % 24 <= 18 else 0.0 for index in range(8760)]
    return {"temperature_c": temperature, "ghi_kw_m2": ghi, "wind_ms": [4.0] * 8760, "humidity_pct": [60.0] * 8760}


class LoadSynthesisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = load_json(ROOT / "config" / "ontology.json")
        cls.archetype = load_json(ROOT / "config" / "site" / "archetypes" / "ras_intensive_marine.json")
        cls.benchmarks = load_json(ROOT / "config" / "site" / "benchmarks.json")
        cls.spec = load_json(ROOT / "config" / "site" / "site.saudi_allith.example.json")
        cls.weather = _synthetic_weather()

    def test_inventory_validation(self) -> None:
        self.assertEqual(site_load.validate_inventory(self.archetype, self.ontology), [])
        bad = copy.deepcopy(self.archetype)
        bad["equipment"][0]["ontology_id"] = "pv.capital_cost"
        bad["equipment"][1]["criticality"] = "vital"
        errors = site_load.validate_inventory(bad, self.ontology)
        self.assertEqual(len(errors), 2)
        self.assertIn("not in the aquaculture_load family", errors[0])

    def test_synthesis_is_reproducible_and_consistent(self) -> None:
        first = site_load.synthesise_load(self.spec, self.archetype, self.weather, 7)
        second = site_load.synthesise_load(self.spec, self.archetype, self.weather, 7)
        third = site_load.synthesise_load(self.spec, self.archetype, self.weather, 8)
        self.assertEqual(first.total, second.total)
        self.assertNotEqual(first.total, third.total)
        self.assertEqual(len(first.total), 8760)
        for index in range(8760):
            self.assertGreaterEqual(first.total[index], 0.0)
            self.assertLessEqual(first.critical[index], first.total[index] + 1e-6)
            self.assertAlmostEqual(first.total[index], first.critical[index] + first.essential[index] + first.deferrable[index], places=2)

    def test_metrics_within_benchmark_bands(self) -> None:
        result = site_load.synthesise_load(self.spec, self.archetype, self.weather, 7)
        summary = metrics.summarise_load(
            result.total, result.critical, result.essential, result.deferrable,
            result.by_ontology_id_kwh, float(self.spec["production_t_per_year"]), self.benchmarks,
        )
        self.assertNotEqual(summary["benchmark_status"], "fail", summary["benchmark_checks"])
        self.assertGreater(summary["load_factor"], 0.0)
        self.assertLessEqual(summary["load_factor"], 1.0)
        self.assertTrue(5.0 <= summary["kwh_per_kg"] <= 15.0)
        self.assertEqual(len(summary["monthly_kwh"]), 12)
        self.assertEqual(len(summary["average_daily_profile_kw"]), 24)

    def test_production_scaling(self) -> None:
        small = {**self.spec, "production_t_per_year": 250}
        result_small = site_load.synthesise_load(small, self.archetype, self.weather, 7)
        result_full = site_load.synthesise_load(self.spec, self.archetype, self.weather, 7)
        self.assertAlmostEqual(sum(result_small.total) / sum(result_full.total), 0.5, delta=0.01)

    def test_cooling_zero_below_setpoint_and_capped_above(self) -> None:
        item = {"rated_kw": 75.0, "quantity": 4}
        config = self.archetype["cooling"]
        below = site_load.cooling_kw(item, {**config, "q_base_kw_th": 0.0}, 20.0)
        self.assertEqual(below, 0.0)
        extreme = site_load.cooling_kw(item, config, 60.0)
        self.assertLessEqual(extreme, 300.0)
        self.assertGreater(site_load.cooling_kw(item, config, 36.0), site_load.cooling_kw(item, config, 30.0))

    def test_schedule_and_drivers(self) -> None:
        self.assertEqual(site_load.schedule_factor({"type": "hours", "on": [1, 2]}, 1), 1.0)
        self.assertEqual(site_load.schedule_factor({"type": "hours", "on": [1, 2]}, 5), 0.0)
        events = [site_load.schedule_factor({"type": "events_per_day", "count": 8, "minutes": 15, "first_hour": 6, "span_hours": 16}, hour) for hour in range(24)]
        self.assertAlmostEqual(sum(events), 2.0)
        self.assertAlmostEqual(site_load.diurnal_factor({"amplitude": 0.15, "peak_hour": 4}, 4), 1.15)
        self.assertAlmostEqual(site_load.biomass_index({"mode": "staggered", "amplitude": 0.1, "peak_doy": 300}, 300 * 24), 1.1)
        lagged = site_load.water_temperature({"mode": "ambient_lag", "lag_days": 1, "offset_c": -1.0}, [30.0] * 48)
        self.assertAlmostEqual(lagged[-1], 29.0)

    def test_fixture_weather_runs_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            series, _ = weather.load_year(20.15, 40.27, 2023, root, fixture=NASA_FIXTURE)
        result = site_load.synthesise_load(self.spec, self.archetype, series, 1)
        self.assertEqual(len(result.total), 8760)


class VariabilityEstimatorTests(unittest.TestCase):
    def test_estimators_recover_known_noise(self) -> None:
        rng = random.Random(3)
        shape = [1.0 + 0.2 * math.sin(2.0 * math.pi * (hour - 4) / 24.0) for hour in range(24)]
        series = []
        for day in range(365):
            daily = 1.0 + rng.gauss(0.0, 0.05)
            for hour in range(24):
                series.append(100.0 * daily * shape[hour] * (1.0 + rng.gauss(0.0, 0.03)))
        self.assertAlmostEqual(metrics.day_to_day_variability_pct(series), 5.0, delta=1.5)
        self.assertAlmostEqual(metrics.timestep_variability_pct(series), 3.0, delta=1.5)


class OutageTests(unittest.TestCase):
    def test_generator_matches_target_rates(self) -> None:
        replicate = outages.replicate_stats(6.0, 4.0, list(range(20)))
        self.assertAlmostEqual(replicate["realised_mean_frequency"], 6.0, delta=1.5)
        self.assertAlmostEqual(replicate["realised_mean_duration_h"], 4.0, delta=1.2)
        schedule = outages.generate_outage_schedule(6.0, 4.0, 1)
        self.assertEqual(len(schedule), 8760)
        self.assertEqual(set(schedule) - {0, 1}, set())
        self.assertEqual(schedule, outages.generate_outage_schedule(6.0, 4.0, 1))
        self.assertEqual(sum(outages.generate_outage_schedule(0.0, 4.0, 1)), 0)


if __name__ == "__main__":
    unittest.main()
