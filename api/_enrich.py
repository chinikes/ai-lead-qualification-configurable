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
HUNTER_KEY = lambda: os.environ.get("HUNTER_API_KEY", "")


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

            # Extract phone numbers by type
            direct_phone = None
            mobile_phone = None
            for p in phones:
                ptype = (p.get("type") or "").lower()
                number = p.get("sanitized_number") or p.get("number")
                if not number:
                    continue
                if ptype in ("mobile", "personal"):
                    if not mobile_phone:
                        mobile_phone = number
                elif ptype in ("work_direct", "direct", "work"):
                    if not direct_phone:
                        direct_phone = number
                elif not direct_phone:
                    direct_phone = number

            return {
                "source": "apollo",
                "contact_full_name": person.get("name"),
                "contact_title": person.get("title"),
                "contact_seniority": seniority,
                "contact_department": departments[0] if departments else None,
                "contact_linkedin_url": person.get("linkedin_url"),
                "contact_phone_direct": direct_phone,
                "contact_phone_mobile": mobile_phone,
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
                "contact_phone_mobile": (data.get("mobile_phone") or data.get("personal_numbers") or [None])[0] if isinstance(data.get("mobile_phone") or data.get("personal_numbers"), list) else data.get("mobile_phone"),
                "contact_location_city": data.get("location_locality"),
                "contact_location_country": data.get("location_country"),
                "contact_previous_companies": prev,
                "contact_is_decision_maker": seniority_val in ("c_level", "vp", "director"),
            }
    except Exception as e:
        logger.error(f"[pdl] Contact enrich error: {e}")
        return None


# ══════════════════════════════════════════════════════
#  HUNTER.IO (Combined Enrichment)
# ══════════════════════════════════════════════════════
#
# Hunter's /v2/combined/find endpoint takes an email and returns
# both person AND company in one call (0.2 credits per call,
# charged only when data is returned). One-call replacement for
# Apollo's two-endpoint people/match + organizations/enrich pair.
#
# Docs: https://hunter.io/api-documentation/v2#combined-enrichment

async def hunter_enrich_combined(email: str) -> dict | None:
    """
    Enrich BOTH person and company via Hunter Combined Enrichment.
    Returns a dict with a 'person' and 'company' sub-dict matching
    our existing apollo_enrich_contact / apollo_enrich_company shape.
    The merge runner (enrich_lead) splits these two halves into the
    flat merged record.
    """
    if not HUNTER_KEY() or not email:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.hunter.io/v2/combined/find",
                params={"email": email, "api_key": HUNTER_KEY()},
            )
            if resp.status_code != 200:
                logger.error(f"[hunter] Combined enrich failed: {resp.status_code} {resp.text[:200]}")
                return None
            payload = resp.json().get("data") or {}
            person_raw = payload.get("person") or {}
            company_raw = payload.get("company") or {}

            # ---- Person mapping ----
            name_obj = person_raw.get("name") or {}
            employment = person_raw.get("employment") or {}
            geo = person_raw.get("geo") or {}
            title = employment.get("title")
            seniority_raw = employment.get("seniority")
            seniority = _map_hunter_seniority(seniority_raw) or _detect_seniority(title)

            person_dict = {
                "source": "hunter",
                "contact_full_name": name_obj.get("fullName") or person_raw.get("name") if isinstance(person_raw.get("name"), str) else name_obj.get("fullName"),
                "contact_title": title,
                "contact_seniority": seniority,
                "contact_department": employment.get("role"),
                "contact_linkedin_url": _hunter_linkedin(person_raw),
                "contact_phone_direct": None,   # Hunter doesn't return personal phones
                "contact_phone_mobile": None,
                "contact_location_city": geo.get("city"),
                "contact_location_country": geo.get("country"),
                "contact_previous_companies": [],  # Hunter Combined doesn't expose history
                "contact_is_decision_maker": seniority in ("c_level", "vp", "director"),
            }

            # ---- Company mapping ----
            cat = company_raw.get("category") or {}
            geo_co = company_raw.get("geo") or {}
            site = company_raw.get("site") or {}
            site_phones = site.get("phoneNumbers") or []
            metrics = company_raw.get("metrics") or {}

            company_dict = {
                "source": "hunter",
                "company_legal_name": company_raw.get("legalName") or company_raw.get("name"),
                "company_industry": cat.get("industry"),
                "company_sub_industry": cat.get("subIndustry"),
                "company_employee_count": metrics.get("employees") or _employees_range_to_int(company_raw.get("employees_range")),
                "company_revenue": _format_revenue(metrics.get("annualRevenue")),
                "company_funding_stage": None,   # Hunter doesn't expose funding stage
                "company_total_funding": _format_funding(metrics.get("raised")),
                "company_year_founded": company_raw.get("foundedYear") or company_raw.get("founded"),
                "company_hq_city": geo_co.get("city"),
                "company_hq_state": geo_co.get("state"),
                "company_hq_country": geo_co.get("country"),
                "company_description": company_raw.get("description"),
                "company_linkedin_url": _hunter_company_linkedin(company_raw),
                "company_technologies": company_raw.get("technologies") or [],
                "company_keywords": company_raw.get("tags") or [],
            }

            return {"person": person_dict, "company": company_dict}
    except Exception as e:
        logger.error(f"[hunter] Combined enrich error: {e}")
        return None


# ══════════════════════════════════════════════════════
#  PARALLEL ENRICHMENT RUNNER
# ══════════════════════════════════════════════════════

import asyncio

async def enrich_lead(domain: str | None, email: str | None) -> dict:
    """
    Run all configured enrichment providers in parallel.
    Each provider is gated on the presence of its API key env var,
    so the system gracefully degrades (or scales up) by setting/unsetting
    keys in Vercel — no code change needed.

    Active providers (when their key is set):
      • Hunter.io  Combined Enrichment  (HUNTER_API_KEY)  — primary
      • PDL        Person + Company     (PDL_API_KEY)     — secondary
      • Apollo.io  People + Org         (APOLLO_API_KEY)  — optional fallback

    Returns a merged dict with 'first non-null wins' for scalars
    and 'union' for lists.
    """
    tasks = []
    task_labels = []

    # Hunter — one call returns both person + company. Email-keyed.
    if email and HUNTER_KEY():
        tasks.append(hunter_enrich_combined(email))
        task_labels.append("hunter_combined")

    # Apollo — opt-in fallback if APOLLO_API_KEY is set.
    if APOLLO_KEY():
        if domain:
            tasks.append(apollo_enrich_company(domain))
            task_labels.append("apollo_company")
        if email:
            tasks.append(apollo_enrich_contact(email))
            task_labels.append("apollo_contact")

    # PDL — domain-keyed company + email-keyed person.
    if PDL_KEY():
        if domain:
            tasks.append(pdl_enrich_company(domain))
            task_labels.append("pdl_company")
        if email:
            tasks.append(pdl_enrich_contact(email))
            task_labels.append("pdl_contact")

    if not tasks:
        logger.warning("[enrich] No providers configured — set HUNTER_API_KEY, PDL_API_KEY, or APOLLO_API_KEY")
        return {"enrichment_sources": []}

    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged = {"enrichment_sources": []}

    def _absorb(result_dict: dict, default_source: str):
        """Merge a single provider's flat dict into the running record."""
        if not result_dict:
            return
        source = result_dict.pop("source", default_source)
        if source not in merged["enrichment_sources"]:
            merged["enrichment_sources"].append(source)

        for key, value in result_dict.items():
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

    for i, result in enumerate(results):
        if isinstance(result, Exception) or result is None:
            if isinstance(result, Exception):
                logger.error(f"[enrich] {task_labels[i]} raised: {result}")
            continue

        # Hunter returns {"person": {...}, "company": {...}}; everything else
        # returns a single flat dict. Normalize.
        if "person" in result or "company" in result:
            _absorb(result.get("person") or {}, task_labels[i])
            _absorb(result.get("company") or {}, task_labels[i])
        else:
            _absorb(result, task_labels[i])

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


# ---- Hunter-specific helpers ----

def _map_hunter_seniority(level: str | None) -> str | None:
    """Map Hunter's seniority strings to our internal taxonomy."""
    if not level:
        return None
    mapping = {
        "executive": "c_level",
        "c_level": "c_level",
        "founder": "c_level",
        "owner": "c_level",
        "vp": "vp",
        "senior": "senior_ic",
        "manager": "manager",
        "director": "director",
        "junior": "ic",
        "entry": "ic",
    }
    return mapping.get(level.lower())


def _hunter_linkedin(person: dict) -> str | None:
    """Extract LinkedIn URL from Hunter person payload."""
    handles = person.get("linkedin") or person.get("social") or {}
    if isinstance(handles, str):
        return handles if handles.startswith("http") else f"https://www.linkedin.com/in/{handles}"
    if isinstance(handles, dict):
        url = handles.get("url") or handles.get("handle")
        if not url:
            return None
        return url if url.startswith("http") else f"https://www.linkedin.com/in/{url}"
    return None


def _hunter_company_linkedin(company: dict) -> str | None:
    """Extract company LinkedIn URL from Hunter company payload."""
    li = company.get("linkedin") or {}
    if isinstance(li, str):
        return li if li.startswith("http") else f"https://www.linkedin.com/company/{li}"
    if isinstance(li, dict):
        url = li.get("url") or li.get("handle")
        if not url:
            return None
        return url if url.startswith("http") else f"https://www.linkedin.com/company/{url}"
    return None


def _employees_range_to_int(rng: str | None) -> int | None:
    """Convert Hunter's 'employees_range' string ('51-200') into a midpoint int."""
    if not rng or not isinstance(rng, str):
        return None
    parts = rng.replace("+", "").split("-")
    try:
        nums = [int(p.strip()) for p in parts if p.strip().isdigit()]
        if not nums:
            return None
        return sum(nums) // len(nums)
    except (ValueError, TypeError):
        return None
