"""Run manifest: provenance of every input and hash of every output."""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SITE_LAYER_VERSION
from .homer_export import sha256_file


def git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def build_manifest(
    *,
    spec: dict[str, Any],
    spec_path: Path,
    sources: list[dict[str, Any]],
    outputs: list[Path],
    seed: int,
    project_root: Path,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hashed_outputs = []
    for path in outputs:
        path = Path(path)
        if path.is_file():
            hashed_outputs.append({"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size})
    hashed_sources = []
    for source in sources:
        record = dict(source)
        path = record.get("path")
        if path and Path(path).is_file():
            record["sha256"] = sha256_file(path)
        hashed_sources.append(record)
    return {
        "site_layer_version": SITE_LAYER_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "python": platform.python_version(),
        "git_commit": git_commit(project_root),
        "spec": {"path": str(spec_path), "sha256": sha256_file(spec_path), "site_id": spec.get("site_id")},
        "seed": seed,
        "sources": hashed_sources,
        "outputs": hashed_outputs,
        **(extra or {}),
    }


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output
