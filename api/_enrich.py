"""
Real enrichment providers: Apollo.io + People Data Labs.
Each returns normalized dicts that map to our lead schema.
"""

import os
import httpx
import logging

logger = logging.getLogger(__name__)

APOLLO_KEY = lambda: os.environ.get("APOLLO_API_KEY", "")
PDL_KEY = lambda: os.environ.get("PDL_API_KEY", "")


# ══════════════════════════════════════════════════════
#  APOLLO.IO
# ══════════════════════════════════════════════════════

async def apollo_enrich_company(domain: str) -> dict | None:
    """Enrich company via Apollo Organizations API."""
    if not APOLLO_KEY():
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.apollo.io/v1/organizations/enrich",
                json={"api_key": APOLLO_KEY(), "domain": domain},
            )
            if resp.status_code != 200:
                logger.error(f"[apollo] Company enrich failed: {resp.status_code}")
                return None
            org = resp.json().get("organization")
            if not org:
                return None
            return {
                "source": "apollo",
                "company_legal_name": org.get("name"),
                "company_industry": org.get("industry"),
                "company_sub_industry": org.get("subindustry"),
                "company_employee_count": org.get("estimated_num_employees"),
                "company_revenue": org.get("annual_revenue_printed"),
                "company_funding_stage": org.get("latest_funding_stage"),
                "company_total_funding": org.get("total_funding_printed"),
                "company_year_founded": org.get("founded_year"),
                "company_hq_city": org.get("city"),
                "company_hq_state": org.get("state"),
                "company_hq_country": org.get("country"),
                "company_description": org.get("short_description"),
                "company_linkedin_url": org.get("linkedin_url"),
                "company_technologies": org.get("current_technologies") or [],
                "company_keywords": org.get("keywords") or [],
            }
    except Exception as e:
        logger.error(f"[apollo] Company enrich error: {e}")
        return None


async def apollo_enrich_contact(email: str) -> dict | None:
    """Enrich contact via Apollo People Match API."""
    if not APOLLO_KEY():
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.apollo.io/v1/people/match",
                json={"api_key": APOLLO_KEY(), "email": email},
            )
            if resp.status_code != 200:
                return None
            person = resp.json().get("person")
            if not person:
                return None

            seniority = person.get("seniority") or _detect_seniority(person.get("title"))
            departments = person.get("departments") or []
            phones = person.get("phone_numbers") or []
            history = person.get("employment_history") or []

            return {
                "source": "apollo",
                "contact_full_name": person.get("name"),
                "contact_title": person.get("title"),
                "contact_seniority": seniority,
                "contact_department": departments[0] if departments else None,
                "contact_linkedin_url": person.get("linkedin_url"),
                "contact_phone_direct": phones[0].get("sanitized_number") if phones else None,
                "contact_location_city": person.get("city"),
                "contact_location_country": person.get("country"),
                "contact_previous_companies": [
                    e.get("organization_name") for e in history[:5] if e.get("organization_name")
                ],
                "contact_is_decision_maker": seniority in ("c_level", "vp", "director"),
            }
    except Exception as e:
        logger.error(f"[apollo] Contact enrich error: {e}")
        return None


# ══════════════════════════════════════════════════════
#  PEOPLE DATA LABS
# ══════════════════════════════════════════════════════

async def pdl_enrich_company(domain: str) -> dict | None:
    """Enrich company via PDL Company Enrichment API."""
    if not PDL_KEY():
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.peopledatalabs.com/v5/company/enrich",
                params={"website": domain},
                headers={"X-Api-Key": PDL_KEY()},
            )
            if resp.status_code != 200:
                logger.error(f"[pdl] Company enrich failed: {resp.status_code}")
                return None
            data = resp.json()
            if data.get("status") != 200:
                return None

            return {
                "source": "pdl",
                "company_legal_name": data.get("name"),
                "company_industry": data.get("industry"),
                "company_sub_industry": data.get("sub_industry"),
                "company_employee_count": data.get("employee_count"),
                "company_revenue": _format_revenue(data.get("estimated_annual_revenue")),
                "company_funding_stage": data.get("latest_funding_stage"),
                "company_total_funding": _format_funding(data.get("total_funding_raised")),
                "company_year_founded": data.get("founded"),
                "company_hq_city": data.get("location", {}).get("locality") if data.get("location") else None,
                "company_hq_state": data.get("location", {}).get("region") if data.get("location") else None,
                "company_hq_country": data.get("location", {}).get("country") if data.get("location") else None,
                "company_description": data.get("summary"),
                "company_linkedin_url": data.get("linkedin_url"),
                "company_technologies": data.get("tags") or [],
                "company_keywords": data.get("keywords") or [],
            }
    except Exception as e:
        logger.error(f"[pdl] Company enrich error: {e}")
        return None


async def pdl_enrich_contact(email: str) -> dict | None:
    """Enrich contact via PDL Person Enrichment API."""
    if not PDL_KEY():
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.peopledatalabs.com/v5/person/enrich",
                params={"email": email},
                headers={"X-Api-Key": PDL_KEY()},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("status") != 200:
                return None

            title = data.get("job_title")
            seniority = data.get("job_title_levels") or [_detect_seniority(title)]
            seniority_val = _map_pdl_seniority(seniority[0] if seniority else "")

            experience = data.get("experience") or []
            prev = [
                e.get("company", {}).get("name")
                for e in experience[1:6]
                if e.get("company", {}).get("name")
            ]

            return {
                "source": "pdl",
                "contact_full_name": data.get("full_name"),
                "contact_title": title,
                "contact_seniority": seniority_val,
                "contact_department": data.get("job_company_industry"),
                "contact_linkedin_url": data.get("linkedin_url"),
                "contact_phone_direct": (data.get("phone_numbers") or [None])[0],
                "contact_location_city": data.get("location_locality"),
                "contact_location_country": data.get("location_country"),
                "contact_previous_companies": prev,
                "contact_is_decision_maker": seniority_val in ("c_level", "vp", "director"),
            }
    except Exception as e:
        logger.error(f"[pdl] Contact enrich error: {e}")
        return None


# ══════════════════════════════════════════════════════
#  PARALLEL ENRICHMENT RUNNER
# ══════════════════════════════════════════════════════

import asyncio

async def enrich_lead(domain: str | None, email: str | None) -> dict:
    """
    Run all enrichment providers in parallel.
    Returns merged dict with 'first non-null wins' for scalars
    and 'union' for lists.
    """
    tasks = []
    task_labels = []

    if domain:
        tasks.append(apollo_enrich_company(domain))
        task_labels.append("apollo_company")
        tasks.append(pdl_enrich_company(domain))
        task_labels.append("pdl_company")
    if email:
        tasks.append(apollo_enrich_contact(email))
        task_labels.append("apollo_contact")
        tasks.append(pdl_enrich_contact(email))
        task_labels.append("pdl_contact")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged = {"enrichment_sources": []}
    for i, result in enumerate(results):
        if isinstance(result, Exception) or result is None:
            continue
        source = result.pop("source", task_labels[i])
        if source not in merged["enrichment_sources"]:
            merged["enrichment_sources"].append(source)

        for key, value in result.items():
            if value is None or value == "" or value == 0:
                continue
            if isinstance(value, list):
                existing = merged.get(key, [])
                seen = set(str(v).lower() for v in existing)
                for item in value:
                    if str(item).lower() not in seen:
                        existing.append(item)
                        seen.add(str(item).lower())
                merged[key] = existing
            elif key not in merged or merged[key] is None:
                merged[key] = value

    return merged


# ══════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════

import re

def _detect_seniority(title: str | None) -> str:
    if not title:
        return "unknown"
    t = title.lower()
    if re.search(r"\b(ceo|cto|cfo|coo|cro|cmo|chief|founder)\b", t):
        return "c_level"
    if re.search(r"\b(vp|vice president|svp|evp)\b", t):
        return "vp"
    if re.search(r"\b(director|head of)\b", t):
        return "director"
    if re.search(r"\b(manager|lead|team lead)\b", t):
        return "manager"
    if re.search(r"\b(senior|sr\.|principal|staff)\b", t):
        return "senior_ic"
    return "ic"


def _map_pdl_seniority(level: str) -> str:
    mapping = {
        "cxo": "c_level", "owner": "c_level", "partner": "c_level",
        "vp": "vp",
        "director": "director",
        "manager": "manager",
        "senior": "senior_ic",
        "entry": "ic", "training": "ic", "unpaid": "ic",
    }
    return mapping.get(level.lower(), "unknown") if level else "unknown"


def _format_revenue(val) -> str | None:
    if not val:
        return None
    if isinstance(val, (int, float)):
        if val >= 1_000_000_000:
            return f"${val / 1_000_000_000:.1f}B"
        if val >= 1_000_000:
            return f"${val / 1_000_000:.0f}M"
        return f"${val:,.0f}"
    return str(val)


def _format_funding(val) -> str | None:
    if not val:
        return None
    if isinstance(val, (int, float)):
        if val >= 1_000_000:
            return f"${val / 1_000_000:.0f}M"
        return f"${val:,.0f}"
    return str(val)
