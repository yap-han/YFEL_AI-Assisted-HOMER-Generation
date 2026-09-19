"""Seeded random grid-outage schedule from a mean frequency and mean repair time.

HOMER Pro's grid reliability inputs are the mean outage frequency (per year)
and the mean repair time (hours); HOMER draws its own random outages from them.
The schedule written here is for reporting and for an optional custom-schedule
sensitivity case, and it lets the analyst see one realisation of the statistics
they are about to enter.
"""

from __future__ import annotations

import random
import statistics
from typing import Any

from .calendar import HOURS_PER_YEAR


def generate_outage_schedule(
    frequency_per_year: float,
    mean_repair_hours: float,
    seed: int,
    hours: int = HOURS_PER_YEAR,
) -> list[int]:
    """Return a 0/1 list; 1 marks an hour in which the grid is unavailable."""
    if frequency_per_year < 0 or mean_repair_hours < 0:
        raise ValueError("Outage frequency and repair time must be non-negative")
    schedule = [0] * hours
    if frequency_per_year == 0 or mean_repair_hours == 0:
        return schedule
    rng = random.Random(int(seed))
    rate = frequency_per_year / hours
    time = rng.expovariate(rate)
    while time < hours:
        duration = max(1, round(rng.expovariate(1.0 / mean_repair_hours)))
        start = int(time)
        for hour in range(start, min(hours, start + duration)):
            schedule[hour] = 1
        time = start + duration + rng.expovariate(rate)
    return schedule


def outage_stats(schedule: list[int]) -> dict[str, Any]:
    events = 0
    durations: list[int] = []
    current = 0
    for value in schedule:
        if value:
            current += 1
        elif current:
            events += 1
            durations.append(current)
            current = 0
    if current:
        events += 1
        durations.append(current)
    total = sum(durations)
    hours = len(schedule)
    return {
        "events": events,
        "total_outage_hours": total,
        "mean_duration_h": round(statistics.fmean(durations), 2) if durations else 0.0,
        "longest_h": max(durations) if durations else 0,
        "frequency_per_year": round(events * HOURS_PER_YEAR / hours, 3) if hours else 0.0,
        "availability": round(1.0 - total / hours, 5) if hours else 1.0,
    }


def replicate_stats(frequency_per_year: float, mean_repair_hours: float, seeds: list[int]) -> dict[str, Any]:
    """Average realised statistics over several seeds, to show the generator matches its targets."""
    frequencies = []
    durations = []
    for seed in seeds:
        stats = outage_stats(generate_outage_schedule(frequency_per_year, mean_repair_hours, seed))
        frequencies.append(stats["frequency_per_year"])
        durations.append(stats["mean_duration_h"])
    return {
        "seeds": len(seeds),
        "target_frequency_per_year": frequency_per_year,
        "target_mean_duration_h": mean_repair_hours,
        "realised_mean_frequency": round(statistics.fmean(frequencies), 3),
        "realised_mean_duration_h": round(statistics.fmean([value for value in durations if value > 0] or [0.0]), 3),
    }
