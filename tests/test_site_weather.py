from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from homer_gcc.site import calendar as site_calendar
from homer_gcc.site import pvgis, weather
from homer_gcc.site.homer_export import read_homer_series, sha256_file, write_homer_series

ROOT = Path(__file__).resolve().parents[1]
NASA_FIXTURE = ROOT / "data" / "site" / "nasa_power_fixture_allith_48h.json"
PVGIS_FIXTURE = ROOT / "data" / "site" / "pvgis_tmy_fixture_allith_48h.json"


class CalendarTests(unittest.TestCase):
    def test_hour_keys_drop_leap_day(self) -> None:
        keys = site_calendar.hour_keys(2020)
        self.assertEqual(len(keys), 8760)
        self.assertEqual(keys[0], "2020010100")
        self.assertEqual(keys[-1], "2020123123")
        self.assertFalse(any(key.startswith("20200229") for key in keys))

    def test_month_and_label(self) -> None:
        index = 181 * 24 + 12  # 1 July 12:00
        self.assertEqual(site_calendar.month_of_hour(index), 6)
        self.assertEqual(site_calendar.hour_label(index), "07-01 12:00")
        self.assertEqual(len(site_calendar.month_hour_ranges()), 12)
        self.assertEqual(site_calendar.month_hour_ranges()[-1][1], 8760)

    def test_drop_leap_day_removes_29_february(self) -> None:
        values = list(range(8784))
        trimmed = site_calendar.drop_leap_day(values, 2020)
        self.assertEqual(len(trimmed), 8760)
        self.assertEqual(trimmed[59 * 24], 60 * 24)


class WeatherTests(unittest.TestCase):
    def test_parse_fixture_gives_8760_per_parameter_in_physical_ranges(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            series, provenance = weather.load_year(20.15, 40.27, 2023, root, fixture=NASA_FIXTURE)
        self.assertEqual(provenance["source"], "fixture")
        for name in ("ghi_kw_m2", "temperature_c", "wind_ms", "humidity_pct"):
            self.assertEqual(len(series[name]), 8760, name)
            self.assertFalse(any(value == -999 for value in series[name]))
        self.assertTrue(all(0.0 <= value <= 1.2 for value in series["ghi_kw_m2"]))
        self.assertTrue(all(-5.0 <= value <= 55.0 for value in series["temperature_c"]))
        self.assertTrue(all(0.0 <= value <= 40.0 for value in series["wind_ms"]))
        self.assertEqual(series["ghi_kw_m2"][0], 0.0)

    def test_fill_gaps_interpolates_short_runs_and_rejects_long(self) -> None:
        values: list[float | None] = [10.0] * 48
        values[10:13] = [None, None, None]
        values[9] = 8.0
        values[13] = 16.0
        filled, stats = weather.fill_gaps(values, max_fill_fraction=0.1)
        self.assertEqual(stats["missing_hours"], 3)
        self.assertEqual(stats["interpolated_hours"], 3)
        self.assertEqual([round(value, 2) for value in filled[10:13]], [10.0, 12.0, 14.0])
        long_gap: list[float | None] = [5.0] * 100
        long_gap[10:14] = [None] * 4
        with self.assertRaises(weather.WeatherError):
            weather.fill_gaps(long_gap, max_fill_fraction=0.02)
        day_gap: list[float | None] = [float(index % 24) for index in range(96)]
        day_gap[30:40] = [None] * 10
        filled, stats = weather.fill_gaps(day_gap, max_fill_fraction=0.2)
        self.assertEqual(stats["day_pattern_filled_hours"], 10)
        self.assertEqual(filled[30], 6.0)

    def test_select_reference_years_median_min_max(self) -> None:
        selection = weather.select_reference_years({2015: 2100.0, 2016: 2180.0, 2017: 2240.0, 2018: 2160.0, 2019: 2200.0})
        self.assertEqual(selection["base"], 2016)
        self.assertEqual(selection["low"], 2015)
        self.assertEqual(selection["high"], 2017)

    def test_monthly_summary_and_clearness_index(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            series, _ = weather.load_year(20.15, 40.27, 2023, root, fixture=NASA_FIXTURE)
        rows = weather.monthly_summary(series, 20.15)
        self.assertEqual(len(rows), 12)
        for row in rows:
            self.assertGreater(row["clearness_index"], 0.0)
            self.assertLess(row["clearness_index"], 1.0)
        june = weather.extraterrestrial_daily_kwh_m2(20.0, 162)
        self.assertAlmostEqual(june, 10.93, delta=0.11)  # Duffie and Beckman table value at 20 N

    def test_parse_year_range(self) -> None:
        self.assertEqual(weather.parse_year_range("2015-2017,2020"), [2015, 2016, 2017, 2020])
        with self.assertRaises(weather.WeatherError):
            weather.parse_year_range("")

    def test_to_homer_units_rejects_out_of_range(self) -> None:
        with self.assertRaises(weather.WeatherError):
            weather.to_homer_units({"T2M": [80.0] * 3})


class PVGISTests(unittest.TestCase):
    def test_roll_to_local_shifts_by_offset(self) -> None:
        values = [float(index) for index in range(8760)]
        rolled = pvgis.roll_to_local(values, 3)
        self.assertEqual(rolled[3], 0.0)
        self.assertEqual(rolled[0], 8757.0)

    def test_compare_status_pass_and_warn(self) -> None:
        nasa = [0.25] * 8760
        self.assertEqual(pvgis.compare_ghi(nasa, [0.26] * 8760)["status"], "pass")
        self.assertEqual(pvgis.compare_ghi(nasa, [0.29] * 8760)["status"], "warn")

    def test_fixture_parses_to_full_year(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            payload, provenance = pvgis.fetch_pvgis_tmy(20.15, 40.27, root, fixture=PVGIS_FIXTURE)
        parsed = pvgis.parse_pvgis_tmy(payload, 3)
        self.assertEqual(provenance["source"], "fixture")
        self.assertEqual(len(parsed["ghi_kw_m2"]), 8760)
        self.assertEqual(parsed["radiation_db"], "PVGIS-SARAH3")


class HomerExportTests(unittest.TestCase):
    def test_write_series_exact_8760_lines_no_header(self) -> None:
        values = [round(index * 0.0001, 4) for index in range(8760)]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            path = write_homer_series(Path(root) / "series.txt", values, 4)
            raw = path.read_bytes()
            lines = raw.decode("ascii").split("\n")
            self.assertEqual(lines[-1], "")
            self.assertEqual(len(lines) - 1, 8760)
            self.assertEqual(lines[0], "0.0000")
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(read_homer_series(path), values)
            self.assertEqual(len(sha256_file(path)), 64)
        with self.assertRaises(ValueError):
            write_homer_series(Path(tempfile.gettempdir()) / "bad.txt", [1.0] * 100)


if __name__ == "__main__":
    unittest.main()
