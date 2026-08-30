from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from . import db


def load_records(path: str | Path, root_key: str | None = None) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        if root_key:
            payload = payload.get(root_key, [])
        if not isinstance(payload, list):
            raise ValueError(f"Expected a list in {source}")
        return [dict(row) for row in payload]
    raise ValueError("Only CSV and JSON record files are supported")


def import_proposals(db_path: str | Path, records: Iterable[dict[str, Any]]) -> list[int]:
    proposal_ids = []
    for record in records:
        item = dict(record)
        item["proposed_value"] = float(item["proposed_value"])
        proposal_ids.append(db.upsert_proposed_parameter(db_path, item))
    return proposal_ids


def export_scenario(
    db_path: str | Path,
    scenario_id: str,
    output_path: str | Path,
    *,
    context_location: str | None = None,
) -> Path:
    rows = db.scenario_parameter_rows(db_path, scenario_id, context_location)
    if not rows:
        raise ValueError(f"No approved parameters found for scenario {scenario_id}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        output.write_text(
            json.dumps(
                {
                    "scenario_id": scenario_id,
                    "context_location": context_location,
                    "parameters": rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    elif output.suffix.lower() == ".csv":
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        raise ValueError("Scenario export must end in .csv or .json")
    return output
