"""Writers and readers for the single-column time-series files HOMER Pro imports.

HOMER Pro reads a text file with one value per line. A file with 8,760 lines is
interpreted as hourly data starting at midnight on 1 January. Units are set by
the dialog the file is imported into: kW for electric load, kW/m2 for solar GHI,
degrees Celsius for temperature and m/s for wind speed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .calendar import HOURS_PER_YEAR


def write_homer_series(path: str | Path, values: list[float], decimals: int = 4) -> Path:
    if len(values) != HOURS_PER_YEAR:
        raise ValueError(f"HOMER hourly series must contain {HOURS_PER_YEAR} values, got {len(values)}")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for value in values:
        number = float(value)
        if number != number:  # NaN guard
            raise ValueError("HOMER series cannot contain NaN")
        lines.append(f"{number:.{decimals}f}")
    output.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return output


def read_homer_series(path: str | Path) -> list[float]:
    text = Path(path).read_text(encoding="ascii")
    values = [float(line) for line in text.splitlines() if line.strip()]
    if len(values) != HOURS_PER_YEAR:
        raise ValueError(f"Expected {HOURS_PER_YEAR} lines in {path}, found {len(values)}")
    return values


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()
