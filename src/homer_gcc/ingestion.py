from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import db
from .evidence import DocumentIngestionError, read_document


MANIFEST_SCHEMA_VERSION = "1.0"


class CorpusManifestError(ValueError):
    pass


def load_corpus_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusManifestError(f"Cannot read corpus manifest {manifest_path}: {exc}") from exc
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CorpusManifestError(
            f"Unsupported corpus manifest schema {payload.get('schema_version')!r}; "
            f"expected {MANIFEST_SCHEMA_VERSION!r}"
        )
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise CorpusManifestError("Corpus manifest requires a non-empty 'documents' array")
    source_ids: set[str] = set()
    dois: set[str] = set()
    for index, item in enumerate(documents):
        if not isinstance(item, dict):
            raise CorpusManifestError(f"documents[{index}] must be an object")
        source_id = str(item.get("source_id") or item.get("id") or "").strip()
        if not source_id:
            raise CorpusManifestError(f"documents[{index}] requires source_id")
        if source_id in source_ids:
            raise CorpusManifestError(f"Duplicate source_id in manifest: {source_id}")
        source_ids.add(source_id)
        doi = db.normalize_doi(item.get("doi"))
        if doi and doi in dois:
            raise CorpusManifestError(f"Duplicate DOI in manifest: {doi}")
        if doi:
            dois.add(doi)
        if not item.get("title"):
            raise CorpusManifestError(f"documents[{index}] requires title")
        if not (item.get("file") or item.get("file_name")):
            raise CorpusManifestError(f"documents[{index}] requires file or file_name")
    return payload


def _source_record(item: dict[str, Any]) -> dict[str, Any]:
    source_id = str(item.get("source_id") or item.get("id"))
    return {
        "id": source_id,
        "title": item["title"],
        "authors": item.get("authors", []),
        "year": item.get("year"),
        "doi": item.get("doi"),
        "journal": item.get("journal"),
        "source_type": item.get("source_type", "journal_article"),
        "peer_reviewed": bool(item.get("peer_reviewed", False)),
        "open_access": item.get("open_access"),
        "region_scope": item.get("region_scope", "global"),
        "countries": item.get("countries", []),
        "system_types": item.get("system_types", []),
        "parameter_families": item.get("parameter_families", []),
        "url": item.get("url"),
        "evidence_level": item.get("evidence_level", "full_text_candidate"),
        "relevance_note": item.get("relevance_note"),
        "verification_status": item.get("source_verification_status", "manifest_registered"),
        "quality_score": float(item.get("quality_score", 0)),
    }


def _resolve_file(
    item: dict[str, Any],
    manifest_path: Path,
    document_root: str | Path | None,
) -> Path:
    raw = Path(str(item.get("file") or item.get("file_name")))
    if raw.is_absolute():
        return raw
    base = Path(document_root) if document_root else manifest_path.parent
    return (base / raw).resolve()


def _quality_gate(
    pages: list[str],
    defaults: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> tuple[bool, list[str], dict[str, Any]]:
    rules = {**defaults, **(overrides or {})}
    body = "\n".join(pages)
    reasons: list[str] = []
    minimum = int(rules.get("minimum_characters", 100))
    if len(body) < minimum:
        reasons.append(f"character count {len(body)} is below minimum {minimum}")
    required_sections = [str(value) for value in rules.get("required_sections", [])]
    missing = [section for section in required_sections if section.lower() not in body.lower()]
    if missing:
        reasons.append("missing required sections: " + ", ".join(missing))
    return not reasons, reasons, {
        "character_count": len(body),
        "minimum_characters": minimum,
        "required_sections": required_sections,
        "missing_sections": missing,
    }


def ingest_corpus(
    db_path: str | Path,
    manifest_path: str | Path,
    *,
    document_root: str | Path | None = None,
    require_rights: bool = True,
    fail_fast: bool = False,
) -> dict[str, Any]:
    """Register and ingest every full text in a versioned corpus manifest.

    A manifest separates tracked metadata from locally held article files. This
    allows collaborators to share source identity, inclusion decisions and
    checksums without committing licensed PDFs to Git.
    """

    manifest_file = Path(manifest_path).resolve()
    manifest = load_corpus_manifest(manifest_file)
    default_rights = manifest.get("default_rights_note")
    default_gate = dict(manifest.get("quality_gate") or {})
    results: list[dict[str, Any]] = []

    with db.connect(db_path) as connection:
        known_hashes = {
            row["sha256"]: int(row["document_id"])
            for row in connection.execute("SELECT document_id, sha256 FROM full_text_documents")
        }

    for item in manifest["documents"]:
        source_id = str(item.get("source_id") or item.get("id"))
        path = _resolve_file(item, manifest_file, document_root)
        rights_note = item.get("rights_note") or default_rights
        row: dict[str, Any] = {
            "source_id": source_id,
            "title": item["title"],
            "doi": db.normalize_doi(item.get("doi")),
            "file": str(path),
        }
        try:
            if item.get("allowed_to_ingest") is False:
                raise CorpusManifestError("manifest marks this document as not allowed to ingest")
            if require_rights and not rights_note:
                raise CorpusManifestError("rights_note is required before full-text ingestion")
            document = read_document(path)
            passed, reasons, gate = _quality_gate(
                document["pages"], default_gate, item.get("quality_gate")
            )
            row["quality_gate"] = {"passed": passed, "reasons": reasons, **gate}
            if not passed:
                raise CorpusManifestError("; ".join(reasons))
            db.insert_sources(db_path, [_source_record(item)])
            duplicate_id = known_hashes.get(document["sha256"])
            document_id = db.insert_full_text_document(
                db_path,
                document,
                source_id=source_id,
                title=item["title"],
                doi=item.get("doi"),
                metadata={
                    "manifest_schema_version": manifest["schema_version"],
                    "corpus_id": manifest.get("corpus_id"),
                    "rights_note": rights_note,
                    "url": item.get("url"),
                    "year": item.get("year"),
                    "journal": item.get("journal"),
                    "quality_gate": row["quality_gate"],
                },
            )
            known_hashes[document["sha256"]] = document_id
            row.update(
                {
                    "status": "duplicate" if duplicate_id else "ingested",
                    "document_id": document_id,
                    "sha256": document["sha256"],
                    "page_count": len(document["pages"]),
                    "ingestion_method": document["ingestion_method"],
                }
            )
        except (CorpusManifestError, DocumentIngestionError, OSError, ValueError) as exc:
            row.update({"status": "failed", "error": str(exc)})
            results.append(row)
            if fail_fast:
                raise CorpusManifestError(f"Failed to ingest {source_id}: {exc}") from exc
            continue
        results.append(row)

    counts = {
        status: sum(row["status"] == status for row in results)
        for status in ("ingested", "duplicate", "failed")
    }
    return {
        "status": "completed" if not counts["failed"] else "completed_with_errors",
        "manifest": str(manifest_file),
        "corpus_id": manifest.get("corpus_id"),
        "attempted": len(results),
        **counts,
        "documents": results,
    }
