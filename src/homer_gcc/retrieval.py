from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import normalize_doi
from .ontology import load_json


DATABASE_NAMES = {
    "crossref": "Crossref",
    "openalex": "OpenAlex",
    "offline": "Verified offline fixture",
}


@dataclass(frozen=True)
class QueryPlan:
    family_id: str
    location: str | None
    queries: list[str]
    retrieval_terms: list[str]
    domain_terms: list[str]


def _family(ontology: dict[str, Any], family_id: str) -> dict[str, Any]:
    for family in ontology.get("families", []):
        if family.get("id") == family_id:
            return family
    raise KeyError(f"Unknown family: {family_id}")


def _quoted_clause(terms: list[str], limit: int | None = None) -> str:
    selected = terms[:limit] if limit else terms
    return " OR ".join(f'"{term}"' if " " in term else term for term in selected)


def family_screening_terms(ontology: dict[str, Any], family_id: str) -> list[str]:
    family = _family(ontology, family_id)
    terms = list(family.get("retrieval_terms", []))
    for parameter in family.get("parameters", []):
        terms.extend(parameter.get("extraction_terms", []))
    return list(dict.fromkeys(term.strip() for term in terms if term and term.strip()))


def build_query_plan(
    ontology: dict[str, Any],
    family_id: str,
    location: str | None = None,
    profile: dict[str, Any] | None = None,
) -> QueryPlan:
    """Build queries from an ontology and a replaceable study-context profile."""
    family = _family(ontology, family_id)
    query_terms = family.get("retrieval_terms", [])
    if not query_terms:
        raise ValueError(f"Family {family_id} has no retrieval terms")

    profile = profile or {
        "region_search_term": "global",
        "domain_terms": ["energy system", "microgrid", "power system"],
        "query_domain_terms": ["energy system", "microgrid", "power system"],
        "analogue_terms": ["comparable climate", "comparable market"],
        "energy_system_terms": ["energy model", "renewable energy", "conventional generation"],
        "query_templates": [
            '{domain_clause} ({family_clause}) "{location}"',
            "{domain_clause} ({family_clause}) ({analogue_clause})",
            "{domain_clause} ({family_clause}) ({energy_clause})",
        ],
    }
    context_location = location or profile.get("region_search_term", "global")
    values = {
        "domain_clause": f'({_quoted_clause(profile.get("query_domain_terms") or profile.get("domain_terms", []), 8)})',
        "family_clause": _quoted_clause(query_terms, 8),
        "location": context_location,
        "analogue_clause": _quoted_clause(profile.get("analogue_terms", []), 6),
        "energy_clause": _quoted_clause(profile.get("energy_system_terms", []), 8),
    }
    queries = [template.format(**values) for template in profile.get("query_templates", [])]
    return QueryPlan(
        family_id=family_id,
        location=location,
        queries=queries,
        retrieval_terms=family_screening_terms(ontology, family_id),
        domain_terms=list(profile.get("domain_terms", [])),
    )


def _request_json(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "modular-homer-evidence-prototype/0.2 (academic research prototype)",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _strip_markup(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip() or None


def fetch_crossref(query: str, limit: int = 10) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "query.bibliographic": query,
            "rows": limit,
            "select": "DOI,title,abstract,author,published,container-title,type,URL,subtype",
        }
    )
    payload = _request_json(f"https://api.crossref.org/works?{params}")
    results: list[dict[str, Any]] = []
    for work in payload.get("message", {}).get("items", []):
        title_values = work.get("title") or []
        authors = []
        for author in work.get("author", []):
            name = " ".join(part for part in [author.get("given"), author.get("family")] if part)
            if name:
                authors.append(name)
        date_parts = (work.get("published") or {}).get("date-parts") or []
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        venue_values = work.get("container-title") or []
        work_type = work.get("type", "")
        results.append(
            {
                "title": title_values[0] if title_values else "Untitled",
                "abstract": _strip_markup(work.get("abstract")),
                "authors": authors,
                "year": year,
                "doi": work.get("DOI"),
                "venue": venue_values[0] if venue_values else None,
                "url": work.get("URL"),
                "peer_reviewed": work_type in {"journal-article", "proceedings-article"},
                "source_type": "academic_peer_reviewed" if work_type == "journal-article" else "academic_other",
                "raw": work,
            }
        )
    return results


def _openalex_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    ordered = sorted(
        ((position, word) for word, positions in inverted_index.items() for position in positions),
        key=lambda item: item[0],
    )
    return " ".join(word for _, word in ordered) or None


def _parse_openalex_work(work: dict[str, Any]) -> dict[str, Any]:
    authors = [
        item.get("author", {}).get("display_name")
        for item in work.get("authorships", [])
        if item.get("author", {}).get("display_name")
    ]
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    work_type = work.get("type", "")
    return {
        "title": work.get("title") or "Untitled",
        "abstract": _openalex_abstract(work.get("abstract_inverted_index")),
        "authors": authors,
        "year": work.get("publication_year"),
        "doi": normalize_doi(work.get("doi")),
        "venue": source.get("display_name"),
        "url": primary.get("landing_page_url") or work.get("id"),
        "peer_reviewed": work_type in {"article", "review"},
        "source_type": "academic_peer_reviewed" if work_type in {"article", "review"} else "academic_other",
        "open_access": (work.get("open_access") or {}).get("is_oa"),
        "openalex_id": work.get("id"),
        "raw": work,
    }


def fetch_openalex(query: str, limit: int = 10) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"search": query, "per-page": min(limit, 50)})
    payload = _request_json(f"https://api.openalex.org/works?{params}")
    return [_parse_openalex_work(work) for work in payload.get("results", [])]


def fetch_openalex_citation_chain(
    seed_doi: str,
    direction: str,
    limit: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retrieve backward or forward citation candidates from OpenAlex."""
    normalized = normalize_doi(seed_doi)
    if not normalized:
        raise ValueError("A valid seed DOI is required")
    encoded = urllib.parse.quote(f"https://doi.org/{normalized}", safe="")
    seed_work = _request_json(f"https://api.openalex.org/works/{encoded}")
    seed = _parse_openalex_work(seed_work)
    if direction == "cited_by":
        openalex_id = str(seed_work.get("id", "")).rsplit("/", 1)[-1]
        params = urllib.parse.urlencode({"filter": f"cites:{openalex_id}", "per-page": min(limit, 50)})
        payload = _request_json(f"https://api.openalex.org/works?{params}")
        related = [_parse_openalex_work(work) for work in payload.get("results", [])]
    elif direction == "references":
        related = []
        for work_id in (seed_work.get("referenced_works") or [])[:limit]:
            related.append(_parse_openalex_work(_request_json(work_id)))
    else:
        raise ValueError("direction must be 'references' or 'cited_by'")
    return seed, related


def fetch_offline(path: str | Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    return payload.get("candidates", [])


def _candidate_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in [item.get("title", ""), item.get("venue", ""), item.get("abstract", "")]
        if value
    ).lower()


def _term_matches(terms: list[str], text_value: str) -> list[str]:
    matches: list[str] = []
    for term in terms:
        normalized = re.sub(r"\s+", " ", term.strip().lower())
        if not normalized:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])"
        if re.search(pattern, text_value):
            matches.append(term)
    return list(dict.fromkeys(matches))


def canonical_candidate_key(candidate: dict[str, Any]) -> str:
    doi = normalize_doi(candidate.get("doi"))
    if doi:
        return f"doi:{doi}"
    title_key = re.sub(r"[^a-z0-9]+", " ", candidate.get("title", "").lower()).strip()
    year = candidate.get("year") or "unknown"
    return f"title:{title_key}|year:{year}"


def score_candidate(
    item: dict[str, Any],
    policy: dict[str, Any],
    family_terms: list[str],
    location: str | None,
    domain_terms: list[str] | None = None,
    known_locations: list[str] | None = None,
) -> dict[str, Any]:
    """Apply separate source-quality, topical-relevance and applicability gates."""
    candidate = dict(item)
    text_value = _candidate_text(item)
    domain_terms = domain_terms or []
    known_locations = known_locations or []
    inclusion_reasons: list[str] = []
    exclusion_reasons: list[str] = []

    weights = policy.get("source_type_weights", {})
    bonuses = policy.get("bonuses", {})
    source_quality = float(weights.get(item.get("source_type"), 0))
    if item.get("peer_reviewed"):
        source_quality += float(bonuses.get("peer_reviewed", 0))
        inclusion_reasons.append("peer-reviewed source")
    if normalize_doi(item.get("doi")):
        source_quality += float(bonuses.get("doi_present", 0))
        inclusion_reasons.append("verified DOI identity available")
    current_year = int(policy.get("current_year", 2026))
    if item.get("year") and current_year - int(item["year"]) <= policy.get("recent_year_window", 6):
        source_quality += float(bonuses.get("recent", 0))
        inclusion_reasons.append("within the recent-publication window")

    url = (item.get("url") or "").lower()
    blocked = any(domain.lower() in url for domain in policy.get("blocked_domains", []))
    general_web = item.get("source_type") == "web_general"
    if blocked:
        exclusion_reasons.append("blocked source domain")
    if general_web and not item.get("peer_reviewed"):
        exclusion_reasons.append("unverified general-web source")
    if blocked or general_web:
        source_quality = 0.0

    family_matches = _term_matches(family_terms, text_value)
    domain_matches = _term_matches(domain_terms, text_value)
    minimum_matches = int(policy.get("relevance_gate", {}).get("minimum_family_term_matches", 1))
    topical_pass = len(family_matches) >= minimum_matches
    if topical_pass:
        inclusion_reasons.append(f"parameter-family terms: {', '.join(family_matches[:6])}")
    else:
        exclusion_reasons.append("no parameter-family term match in title, abstract or venue")
    if domain_matches:
        inclusion_reasons.append(f"study-domain terms: {', '.join(domain_matches[:5])}")

    topical_score = min(100.0, (60.0 if topical_pass else 0.0) + 8.0 * len(family_matches) + 3.0 * len(domain_matches))
    applicability = 0.0
    region_scope = item.get("region_scope", "global")
    if location and location.lower() in text_value:
        applicability = 100.0
        region_scope = "location_specific"
        inclusion_reasons.append(f"location match: {location}")
    elif any(place.lower() in text_value for place in known_locations):
        applicability = 75.0
        region_scope = "region_specific"
        inclusion_reasons.append("regional location match")
    elif any(term in text_value for term in ["arid", "middle east", "hot climate", "hot and humid"]):
        applicability = 50.0
        region_scope = "climate_analogue"
        inclusion_reasons.append("climate analogue")
    elif domain_matches:
        applicability = 25.0

    source_quality = round(max(0.0, min(100.0, source_quality)), 1)
    quality_minimum = float(policy.get("quality_gate", {}).get("minimum_score", 55))
    quality_pass = source_quality >= quality_minimum and not blocked and not general_web
    if not quality_pass and not blocked and not general_web:
        exclusion_reasons.append(f"source-quality score below {quality_minimum:g}")

    rank_weights = policy.get("ranking_weights", {"quality": 0.55, "topical": 0.30, "applicability": 0.15})
    ranking_score = round(
        source_quality * float(rank_weights.get("quality", 0.55))
        + topical_score * float(rank_weights.get("topical", 0.30))
        + applicability * float(rank_weights.get("applicability", 0.15)),
        1,
    )

    if blocked or general_web:
        decision = "reject"
    elif not topical_pass:
        decision = "exclude"
    elif not quality_pass:
        decision = "review"
    else:
        decision = "shortlist"

    screened_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    candidate.update(
        {
            "doi": normalize_doi(candidate.get("doi")),
            "canonical_key": canonical_candidate_key(candidate),
            "region_scope": region_scope,
            "source_quality_score": source_quality,
            "quality_score": source_quality,
            "topical_relevance_score": round(topical_score, 1),
            "applicability_score": round(applicability, 1),
            "ranking_score": ranking_score,
            "source_quality_pass": quality_pass,
            "topical_relevance_pass": topical_pass,
            "relevance_gate_passed": topical_pass,
            "family_term_matches": family_matches,
            "domain_term_matches": domain_matches,
            "matched_terms": family_matches,
            "system_matches": domain_matches,
            "decision": decision,
            "title_abstract_decision": "include" if decision == "shortlist" else ("review" if decision == "review" else "exclude"),
            "screening_status": "automated_pending_human" if decision in {"shortlist", "review"} else "automated_excluded",
            "screened_by": "deterministic_keyword_quality_gate_v0.2",
            "screened_at": screened_at,
            "inclusion_reasons": inclusion_reasons,
            "exclusion_reasons": exclusion_reasons,
            "decision_reasons": inclusion_reasons + exclusion_reasons,
            "human_decision": "pending" if decision in {"shortlist", "review"} else "not_required",
        }
    )
    return candidate


def _candidate_provenance(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": candidate.get("provider"),
        "database_name": candidate.get("database_name"),
        "retrieved_at": candidate.get("retrieved_at"),
        "query_text": candidate.get("query_text"),
        "url": candidate.get("url"),
        "title": candidate.get("title"),
        "doi": normalize_doi(candidate.get("doi")),
    }


def deduplicate(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate across queries and providers while preserving every provenance record."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        candidate = dict(row)
        candidate["canonical_key"] = canonical_candidate_key(candidate)
        groups.setdefault(candidate["canonical_key"], []).append(candidate)

    merged: list[dict[str, Any]] = []
    for key, rows in groups.items():
        primary = max(
            rows,
            key=lambda row: (
                float(row.get("ranking_score", row.get("quality_score", 0))),
                len(row.get("abstract") or ""),
            ),
        )
        record = dict(primary)
        databases = sorted(
            {
                value
                for row in rows
                for value in (row.get("database_names") or [row.get("database_name")])
                if value
            }
        )
        providers = sorted(
            {
                value
                for row in rows
                for value in (row.get("providers") or [row.get("provider")])
                if value
            }
        )
        retrieval_dates = sorted(
            {
                value
                for row in rows
                for value in (row.get("retrieval_dates") or [row.get("retrieved_at")])
                if value
            }
        )
        record.update(
            {
                "canonical_key": key,
                "database_names": databases,
                "providers": providers,
                "retrieval_dates": retrieval_dates,
                "duplicate_count": max(0, len(rows) - 1),
                "provenance_records": [_candidate_provenance(row) for row in rows],
                "provenance": [_candidate_provenance(row) for row in rows],
            }
        )
        for list_field in (
            "family_term_matches",
            "domain_term_matches",
            "inclusion_reasons",
            "exclusion_reasons",
            "decision_reasons",
        ):
            record[list_field] = list(
                dict.fromkeys(value for row in rows for value in row.get(list_field, []))
            )
        merged.append(record)
    return sorted(merged, key=lambda row: (-row.get("ranking_score", row.get("quality_score", 0)), row.get("title", "")))


def retrieve(
    provider: str,
    query: str,
    limit: int,
    fixture: str | Path | None = None,
) -> list[dict[str, Any]]:
    if provider == "crossref":
        rows = fetch_crossref(query, limit)
    elif provider == "openalex":
        rows = fetch_openalex(query, limit)
    elif provider == "offline":
        if not fixture:
            raise ValueError("Offline provider requires --fixture")
        rows = fetch_offline(fixture)[:limit]
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    database_name = DATABASE_NAMES.get(provider, provider)
    for row in rows:
        row["provider"] = provider
        row["database_name"] = database_name
        row["retrieved_at"] = retrieved_at
    return rows


def safe_retrieve(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return retrieve(*args, **kwargs), None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return [], str(exc)
