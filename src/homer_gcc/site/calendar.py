"""Hour indexing on the 365-day, 8,760-hour calendar that HOMER Pro imports."""

from __future__ import annotations

HOURS_PER_YEAR = 8760
HOURS_PER_DAY = 24
DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
# Klein (1977) representative day of each month for monthly-average extraterrestrial radiation.
KLEIN_REPRESENTATIVE_DAYS = (17, 47, 75, 105, 135, 162, 198, 228, 258, 288, 318, 344)


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def hour_keys(year: int) -> list[str]:
    """Return the 8,760 ``YYYYMMDDHH`` keys of a year with 29 February omitted.

    NASA POWER keys hourly records this way. Dropping the leap day keeps every
    year at exactly 8,760 rows, which is what HOMER expects for hourly data.
    """
    keys: list[str] = []
    for month_index, days in enumerate(DAYS_IN_MONTH, start=1):
        for day in range(1, days + 1):
            for hour in range(HOURS_PER_DAY):
                keys.append(f"{year:04d}{month_index:02d}{day:02d}{hour:02d}")
    return keys


def hour_of_day(index: int) -> int:
    return index % HOURS_PER_DAY


def day_of_year(index: int) -> int:
    """Zero-based day of the 365-day year for an hour index."""
    return index // HOURS_PER_DAY


def month_of_hour(index: int) -> int:
    """Zero-based month for an hour index."""
    day = day_of_year(index)
    for month_index, days in enumerate(DAYS_IN_MONTH):
        if day < days:
            return month_index
        day -= days
    raise ValueError(f"Hour index {index} is outside a 365-day year")


def month_hour_ranges() -> list[tuple[int, int]]:
    """Half-open [start, end) hour ranges for the twelve months."""
    ranges = []
    start = 0
    for days in DAYS_IN_MONTH:
        end = start + days * HOURS_PER_DAY
        ranges.append((start, end))
        start = end
    return ranges


def drop_leap_day(values: list[float], year: int) -> list[float]:
    """Remove the 24 hours of 29 February from an 8,784-value leap-year series."""
    if not is_leap_year(year) or len(values) != HOURS_PER_YEAR + HOURS_PER_DAY:
        return list(values)
    start = (31 + 28) * HOURS_PER_DAY
    return list(values[:start]) + list(values[start + HOURS_PER_DAY:])


def hour_label(index: int) -> str:
    """Human-readable ``MM-DD HH:00`` label for an hour index."""
    day = day_of_year(index)
    for month_index, days in enumerate(DAYS_IN_MONTH, start=1):
        if day < days:
            return f"{month_index:02d}-{day + 1:02d} {hour_of_day(index):02d}:00"
        day -= days
    raise ValueError(f"Hour index {index} is outside a 365-day year")
