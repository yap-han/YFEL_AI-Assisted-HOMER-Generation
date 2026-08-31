from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


NUMBER = r"-?\d+(?:,\d{3})*(?:\.\d+)?"
RANGE_PATTERN = re.compile(
    rf"(?P<low>{NUMBER})\s*(?:-|–|—|to)\s*(?P<high>{NUMBER})\s*(?P<unit>[%A-Za-z0-9_/\.]+)?",
    re.IGNORECASE,
)
SINGLE_PATTERN = re.compile(
    rf"(?P<value>{NUMBER})\s*(?P<unit>%|fraction|[A-Za-z]+(?:/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?)?)?",
    re.IGNORECASE,
)


class DocumentIngestionError(ValueError):
    pass


def _pdf_pages(path: Path) -> list[str]:
    executable = shutil.which("pdftotext")
    if not executable:
        raise DocumentIngestionError(
            "PDF ingestion requires the 'pdftotext' executable; alternatively provide TXT, Markdown or JSON pages"
        )
    result = subprocess.run(
        [executable, "-layout", str(path), "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DocumentIngestionError(result.stderr.strip() or "pdftotext failed")
    return result.stdout.split("\f")


def read_document(path: str | Path) -> dict[str, Any]:
    document_path = Path(path)
    if not document_path.is_file():
        raise DocumentIngestionError(f"Document does not exist: {document_path}")
    payload = document_path.read_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    suffix = document_path.suffix.lower()
    if suffix in {".txt", ".md"}:
        pages = [payload.decode("utf-8")]
        method = "plain_text"
    elif suffix == ".json":
        decoded = json.loads(payload.decode("utf-8"))
        if isinstance(decoded.get("pages"), list):
            pages = [str(page.get("text", "")) if isinstance(page, dict) else str(page) for page in decoded["pages"]]
        elif decoded.get("text") is not None:
            pages = [str(decoded["text"])]
        else:
            raise DocumentIngestionError("JSON document requires a 'text' field or 'pages' array")
        method = "structured_json"
    elif suffix == ".pdf":
        pages = _pdf_pages(document_path)
        method = "pdftotext_layout"
    else:
        raise DocumentIngestionError(f"Unsupported document type: {suffix or 'no extension'}")
    mime_type = mimetypes.guess_type(document_path.name)[0] or "application/octet-stream"
    return {
        "file_name": document_path.name,
        "file_path": str(document_path.resolve()),
        "mime_type": mime_type,
        "sha256": sha256,
        "ingestion_method": method,
        "pages": pages,
    }


def _number(value: str) -> float:
    return float(value.replace(",", ""))


def extract_numeric_candidates(
    pages: list[str],
    parameter: dict[str, Any],
) -> list[dict[str, Any]]:
    """Transparent fallback extractor used for testing and pre-screening.

    Production AI extraction should use the JSON contract returned by
    build_ai_extraction_task and import the resulting observations for review.
    """
    terms = [
        str(parameter.get("label", "")),
        *[str(term) for term in parameter.get("extraction_terms", [])],
    ]
    terms = [term.lower() for term in terms if term]
    allowed_units = [str(unit) for unit in parameter.get("allowed_units", [])]
    candidates: list[dict[str, Any]] = []
    for page_number, page_text in enumerate(pages, start=1):
        for line_number, line in enumerate(page_text.splitlines(), start=1):
            lowered = line.lower()
            matches = [term for term in terms if term in lowered]
            if not matches:
                continue
            range_match = RANGE_PATTERN.search(line)
            if range_match:
                low = _number(range_match.group("low"))
                high = _number(range_match.group("high"))
                raw_unit = (range_match.group("unit") or "").rstrip(".,;:)") or None
                candidates.append(
                    {
                        "parameter_id": parameter["id"],
                        "raw_value_min": low,
                        "raw_value_central": (low + high) / 2.0,
                        "raw_value_max": high,
                        "raw_unit": raw_unit,
                        "raw_value_text": range_match.group(0),
                        "page_number": page_number,
                        "locator": f"page {page_number}, line {line_number}",
                        "context_excerpt": line.strip(),
                        "matched_terms": matches,
                        "extraction_method": "deterministic_regex_v0.3",
                        "extraction_confidence": 0.55 if raw_unit in allowed_units else 0.35,
                    }
                )
                continue
            single = SINGLE_PATTERN.search(line)
            if single:
                raw_unit = (single.group("unit") or "").rstrip(".,;:)") or None
                value = _number(single.group("value"))
                candidates.append(
                    {
                        "parameter_id": parameter["id"],
                        "raw_value_min": value,
                        "raw_value_central": value,
                        "raw_value_max": value,
                        "raw_unit": raw_unit,
                        "raw_value_text": single.group(0),
                        "page_number": page_number,
                        "locator": f"page {page_number}, line {line_number}",
                        "context_excerpt": line.strip(),
                        "matched_terms": matches,
                        "extraction_method": "deterministic_regex_v0.3",
                        "extraction_confidence": 0.50 if raw_unit in allowed_units else 0.30,
                    }
                )
    return candidates


def build_ai_extraction_task(
    document_id: int,
    title: str,
    pages: list[dict[str, Any]],
    parameters: list[dict[str, Any]],
) -> dict[str, Any]:
    parameter_contracts = [
        {
            "parameter_id": parameter["id"],
            "label": parameter["label"],
            "description": parameter.get("description"),
            "canonical_unit": parameter.get("canonical_unit"),
            "allowed_units": parameter.get("allowed_units", []),
            "extraction_terms": parameter.get("extraction_terms", []),
        }
        for parameter in parameters
    ]
    parameter_ids = [item["parameter_id"] for item in parameter_contracts]
    allowed_units = sorted(
        {
            str(unit)
            for item in parameter_contracts
            for unit in [item.get("canonical_unit"), *item.get("allowed_units", [])]
            if unit
        }
    )
    return {
        "task": "extract_quantitative_parameter_evidence",
        "document_id": document_id,
        "document_title": title,
        "instructions": [
            "Extract only explicitly reported numerical evidence.",
            "Return null when a field is absent; never infer or calculate an unreported value.",
            "Preserve raw values and units exactly as written.",
            "Copy a short verbatim evidence_quote containing the reported number for every observation.",
            "Provide the page number and a table, figure, section, paragraph or line locator for every observation.",
            "The raw_value_text must appear inside evidence_quote, and evidence_quote must appear on the cited page.",
            "Capture technology, system boundary, geography, scale and operating conditions.",
            "Do not normalize units or reconcile sources.",
        ],
        "parameters": parameter_contracts,
        "output_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["observations"],
            "properties": {
                "observations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "parameter_id", "raw_value_central", "raw_unit",
                            "raw_value_text", "evidence_quote", "page_number",
                            "locator", "extraction_confidence",
                        ],
                        "properties": {
                            "parameter_id": {"type": "string", "enum": parameter_ids},
                            "raw_value_min": {"type": ["number", "null"]},
                            "raw_value_central": {"type": "number"},
                            "raw_value_max": {"type": ["number", "null"]},
                            "raw_unit": {"type": "string", "enum": allowed_units},
                            "raw_value_text": {"type": "string", "minLength": 1},
                            "evidence_quote": {"type": "string", "minLength": 1},
                            "page_number": {"type": "integer", "minimum": 1},
                            "table_id": {"type": ["string", "null"]},
                            "figure_id": {"type": ["string", "null"]},
                            "locator": {"type": "string", "minLength": 1},
                            "technology": {"type": ["string", "null"]},
                            "system_boundary": {"type": ["string", "null"]},
                            "context_location": {"type": ["string", "null"]},
                            "scale": {"type": ["string", "null"]},
                            "operating_conditions": {"type": ["string", "null"]},
                            "currency": {"type": ["string", "null"]},
                            "source_cost_year": {"type": ["integer", "null"]},
                            "extraction_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "applicability_score": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                    },
                }
            },
        },
        "pages": pages,
    }
