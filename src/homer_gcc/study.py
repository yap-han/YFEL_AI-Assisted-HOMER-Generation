from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProfileIssue:
    level: str
    location: str
    message: str


def validate_study_profile(profile: dict[str, Any]) -> list[ProfileIssue]:
    """Validate the context-specific layer used by the generic retrieval engine."""
    issues: list[ProfileIssue] = []
    for field in ("id", "name", "version", "region_search_term"):
        if not profile.get(field):
            issues.append(ProfileIssue("error", field, f"Missing profile field: {field}"))

    if not profile.get("locations"):
        issues.append(ProfileIssue("error", "locations", "At least one location is required"))
    if not profile.get("domain_terms"):
        issues.append(ProfileIssue("error", "domain_terms", "At least one domain term is required"))
    if not profile.get("query_templates"):
        issues.append(ProfileIssue("error", "query_templates", "At least one query template is required"))

    permitted = {
        "domain_clause",
        "family_clause",
        "location",
        "analogue_clause",
        "energy_clause",
    }
    for index, template in enumerate(profile.get("query_templates", [])):
        if "{family_clause}" not in template:
            issues.append(
                ProfileIssue("error", f"query_templates[{index}]", "Template must contain {family_clause}")
            )
        fields = {
            part.split("}", 1)[0]
            for part in template.split("{")[1:]
            if "}" in part
        }
        unknown = sorted(fields - permitted)
        if unknown:
            issues.append(
                ProfileIssue(
                    "error",
                    f"query_templates[{index}]",
                    f"Unknown template fields: {', '.join(unknown)}",
                )
            )
    return issues


def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": profile.get("id"),
        "name": profile.get("name"),
        "version": profile.get("version"),
        "location_count": len(profile.get("locations", [])),
        "domain_term_count": len(profile.get("domain_terms", [])),
        "query_template_count": len(profile.get("query_templates", [])),
    }
