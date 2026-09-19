"""Optional AI-assisted drafting of an equipment inventory.

The connector and fixture pattern are the evidence pipeline's own
(``homer_gcc.llm``). The model receives the site description, the permitted
ontology load IDs and a strict output schema; its answer is validated field by
field and written as ``inventory_draft.json`` with status ``candidate``. The
draft is never used for a run unless the analyst passes ``--use-llm-draft``,
and even then the archetype metadata (drivers, cooling model) comes from the
reviewed archetype file, not from the model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..llm import LLMResponseError, _json_payload, build_connector, task_sha256
from ..ontology import iter_parameters
from .load import CRITICALITIES, DRIVERS, validate_inventory

INVENTORY_PROMPT_VERSION = "site_inventory_v0.7"


def build_inventory_task(spec: dict[str, Any], ontology: dict[str, Any], archetype: dict[str, Any]) -> dict[str, Any]:
    load_parameters = [
        {"ontology_id": parameter["id"], "label": parameter.get("label"), "description": parameter.get("description")}
        for parameter in iter_parameters(ontology)
        if parameter.get("family_id") == "aquaculture_load" and parameter.get("value_kind") == "scalar"
        and parameter["id"] != "load.specific_energy_consumption"
    ]
    return {
        "task": "draft_equipment_inventory",
        "prompt_version": INVENTORY_PROMPT_VERSION,
        "document_id": f"inventory:{spec['site_id']}",
        "site": {
            "site_name": spec.get("site_name"),
            "country": spec.get("country"),
            "system_type": archetype.get("system_type"),
            "production_t_per_year": spec.get("production_t_per_year"),
            "water_temperature": archetype.get("water_temperature"),
        },
        "instructions": [
            "Propose the electrical equipment inventory of this aquaculture facility.",
            "Use only ontology_id values from allowed_ontology_ids.",
            "Give rated_kw per unit, an integer quantity and a duty_cycle between 0 and 1.",
            "Set driver to constant, schedule, diurnal_do or temperature and criticality to critical, essential or deferrable.",
            "Every item must state a basis: a manufacturer class, a published study or an engineering estimate. Never invent a citation.",
            "Return JSON only, matching output_schema. Return an empty equipment list rather than guessing.",
        ],
        "allowed_ontology_ids": load_parameters,
        "allowed_drivers": sorted(DRIVERS),
        "allowed_criticalities": sorted(CRITICALITIES),
        "output_schema": {
            "type": "object",
            "properties": {
                "equipment": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "ontology_id": {"type": "string"},
                            "rated_kw": {"type": "number"},
                            "quantity": {"type": "integer"},
                            "duty_cycle": {"type": "number"},
                            "driver": {"type": "string"},
                            "criticality": {"type": "string"},
                            "basis": {"type": "string"},
                            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                        },
                        "required": ["id", "ontology_id", "rated_kw", "quantity", "duty_cycle", "driver", "criticality", "basis"],
                    },
                }
            },
            "required": ["equipment"],
        },
    }


def validate_inventory_response(task: dict[str, Any], raw: str, ontology: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _json_payload(raw)
    equipment = payload.get("equipment")
    if not isinstance(equipment, list):
        raise LLMResponseError("equipment must be a list")
    allowed = {entry["ontology_id"] for entry in task["allowed_ontology_ids"]}
    cleaned = []
    for index, item in enumerate(equipment):
        if not isinstance(item, dict):
            raise LLMResponseError(f"equipment[{index}] must be an object")
        missing = [key for key in task["output_schema"]["properties"]["equipment"]["items"]["required"] if key not in item]
        if missing:
            raise LLMResponseError(f"equipment[{index}] missing {', '.join(missing)}")
        if item["ontology_id"] not in allowed:
            raise LLMResponseError(f"equipment[{index}].ontology_id {item['ontology_id']!r} is not permitted")
        if not isinstance(item.get("basis"), str) or not item["basis"].strip():
            raise LLMResponseError(f"equipment[{index}].basis must be a non-empty string")
        cleaned.append(
            {
                "id": str(item["id"]),
                "label": str(item.get("label", item["id"])),
                "ontology_id": item["ontology_id"],
                "rated_kw": item["rated_kw"],
                "quantity": item["quantity"],
                "duty_cycle": item["duty_cycle"],
                "driver": item["driver"],
                "criticality": item["criticality"],
                "biomass_scaling": float(item.get("biomass_scaling", 0.0)),
                "noise_class": "other",
                "source": {"basis": item["basis"], "ref": "AI draft; unverified", "confidence": item.get("confidence", "low")},
            }
        )
    errors = validate_inventory({"equipment": cleaned}, ontology) if cleaned else ["equipment list is empty"]
    if errors:
        raise LLMResponseError("; ".join(errors))
    return cleaned


def draft_inventory(
    spec: dict[str, Any],
    ontology: dict[str, Any],
    archetype: dict[str, Any],
    llm_config: dict[str, Any],
    output_dir: str | Path,
    *,
    config_base: Path | None = None,
    connector: Any | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    connector = connector or build_connector(llm_config, config_base=config_base)
    task = build_inventory_task(spec, ontology, archetype)
    digest = task_sha256(task)
    feedback: list[str] = []
    attempts = []
    equipment: list[dict[str, Any]] | None = None
    for attempt in range(1, max_retries + 2):
        raw = connector.complete(task, feedback or None)
        try:
            equipment = validate_inventory_response(task, raw, ontology)
            attempts.append({"attempt": attempt, "status": "valid"})
            break
        except LLMResponseError as exc:
            attempts.append({"attempt": attempt, "status": "rejected", "reason": str(exc)})
            feedback = [str(exc)]
    result = {
        "status": "candidate" if equipment else "rejected",
        "provider": getattr(connector, "provider_name", "unknown"),
        "model": getattr(connector, "model_name", "unknown"),
        "prompt_version": INVENTORY_PROMPT_VERSION,
        "task_sha256": digest,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "attempts": attempts,
        "warning": "AI-drafted inventory. Not reviewed. Do not use for a research result until an engineer checks every line.",
        "equipment": equipment or [],
    }
    output = Path(output_dir) / "inventory_draft.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["output"] = str(output)
    return result
