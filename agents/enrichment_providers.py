"""
Enrichment providers — modular API integrations.
Each provider implements a standard interface and handles its own
rate limiting, error handling, and response normalization.

In production, these would hit real APIs. This implementation includes
both the real integration code AND mock fallbacks for portfolio demos.
"""

import asyncio
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from config.settings import ENRICHMENT

logger = logging.getLogger(__name__)


# ── Base Provider ────────────────────────────────────────

class EnrichmentProvider(ABC):
    """Base class for all enrichment providers."""

    def __init__(self, name: str, api_key: str, rpm_limit: int = 60):
        self.name = name
        self.api_key = api_key
        self.rpm_limit = rpm_limit
        self._request_times: list[float] = []
        self._cache: dict[str, dict] = {}

    async def _rate_limit(self):
        """Simple sliding window rate limiter."""
        now = time.time()
        self._request_times = [t for t in self._request_times if now - t < 60]
        if len(self._request_times) >= self.rpm_limit:
            wait_time = 60 - (now - self._request_times[0])
            logger.warning(f"[{self.name}] Rate limit hit, waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time)
        self._request_times.append(time.time())

    def _cache_key(self, identifier: str) -> str:
        return hashlib.md5(f"{self.name}:{identifier}".encode()).hexdigest()

    @abstractmethod
    async def enrich_company(self, domain: str) -> Optional[dict]:
        pass

    @abstractmethod
    async def enrich_contact(self, email: str) -> Optional[dict]:
        pass


# ── Clearbit Provider ────────────────────────────────────

class ClearbitProvider(EnrichmentProvider):
    """
    Clearbit Enrichment API integration.
    Docs: https://dashboard.clearbit.com/docs
    """

    BASE_URL = "https://company.clearbit.com/v2"
    PERSON_URL = "https://person.clearbit.com/v2"

    def __init__(self):
        super().__init__(
            name="clearbit",
            api_key=ENRICHMENT.clearbit_api_key,
            rpm_limit=ENRICHMENT.clearbit_rpm
        )

    async def enrich_company(self, domain: str) -> Optional[dict]:
        """Fetch company data from Clearbit."""
        cache_key = self._cache_key(domain)
        if cache_key in self._cache:
            return self._cache[cache_key]

        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=ENRICHMENT.enrichment_timeout) as client:
                response = await client.get(
                    f"{self.BASE_URL}/companies/find",
                    params={"domain": domain},
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    result = self._normalize_company(data)
                    self._cache[cache_key] = result
                    return result
                elif response.status_code == 404:
                    logger.info(f"[clearbit] No company found for {domain}")
                    return None
                elif response.status_code == 422:
                    logger.info(f"[clearbit] Invalid domain: {domain}")
                    return None
                else:
                    logger.error(f"[clearbit] API error {response.status_code}: {response.text}")
                    return None

        except httpx.TimeoutException:
            logger.error(f"[clearbit] Timeout for {domain}")
            return None
        except Exception as e:
            logger.error(f"[clearbit] Error enriching {domain}: {e}")
            return None

    async def enrich_contact(self, email: str) -> Optional[dict]:
        """Fetch person data from Clearbit."""
        cache_key = self._cache_key(email)
        if cache_key in self._cache:
            return self._cache[cache_key]

        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=ENRICHMENT.enrichment_timeout) as client:
                response = await client.get(
                    f"{self.PERSON_URL}/people/find",
                    params={"email": email},
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    result = self._normalize_contact(data)
                    self._cache[cache_key] = result
                    return result
                else:
                    return None

        except Exception as e:
            logger.error(f"[clearbit] Error enriching contact {email}: {e}")
            return None

    def _normalize_company(self, data: dict) -> dict:
        """Normalize Clearbit company response to our standard schema."""
        return {
            "source": "clearbit",
            "legal_name": data.get("name"),
            "domain": data.get("domain"),
            "industry": data.get("category", {}).get("industry"),
            "sub_industry": data.get("category", {}).get("subIndustry"),
            "employee_count": data.get("metrics", {}).get("employees"),
            "estimated_revenue": data.get("metrics", {}).get("estimatedAnnualRevenue"),
            "funding_stage": data.get("crunchbase", {}).get("lastFundingType"),
            "total_funding": None,
            "year_founded": data.get("foundedYear"),
            "headquarters_city": data.get("geo", {}).get("city"),
            "headquarters_state": data.get("geo", {}).get("state"),
            "headquarters_country": data.get("geo", {}).get("country"),
            "description": data.get("description"),
            "linkedin_url": data.get("linkedin", {}).get("handle"),
            "twitter_url": data.get("twitter", {}).get("handle"),
            "technologies": data.get("tech", []) or [],
            "keywords": data.get("category", {}).get("industryGroup", "").split(",") if data.get("category", {}).get("industryGroup") else [],
        }

    def _normalize_contact(self, data: dict) -> dict:
        """Normalize Clearbit person response."""
        employment = data.get("employment", {}) or {}
        return {
            "source": "clearbit",
            "full_name": data.get("name", {}).get("fullName"),
            "title": employment.get("title"),
            "seniority": employment.get("seniority"),
            "department": employment.get("role"),
            "linkedin_url": data.get("linkedin", {}).get("handle"),
            "location_city": data.get("geo", {}).get("city"),
            "location_state": data.get("geo", {}).get("state"),
            "location_country": data.get("geo", {}).get("country"),
        }


# ── Apollo Provider ──────────────────────────────────────

class ApolloProvider(EnrichmentProvider):
    """
    Apollo.io API integration.
    Docs: https://apolloio.github.io/apollo-api-docs/
    """

    BASE_URL = "https://api.apollo.io/v1"

    def __init__(self):
        super().__init__(
            name="apollo",
            api_key=ENRICHMENT.apollo_api_key,
            rpm_limit=ENRICHMENT.apollo_rpm
        )

    async def enrich_company(self, domain: str) -> Optional[dict]:
        """Fetch organization data from Apollo."""
        cache_key = self._cache_key(domain)
        if cache_key in self._cache:
            return self._cache[cache_key]

        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=ENRICHMENT.enrichment_timeout) as client:
                response = await client.post(
                    f"{self.BASE_URL}/organizations/enrich",
                    json={"api_key": self.api_key, "domain": domain}
                )

                if response.status_code == 200:
                    data = response.json().get("organization", {})
                    if data:
                        result = self._normalize_company(data)
                        self._cache[cache_key] = result
                        return result
                return None

        except Exception as e:
            logger.error(f"[apollo] Error enriching {domain}: {e}")
            return None

    async def enrich_contact(self, email: str) -> Optional[dict]:
        """Fetch person data from Apollo."""
        cache_key = self._cache_key(email)
        if cache_key in self._cache:
            return self._cache[cache_key]

        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=ENRICHMENT.enrichment_timeout) as client:
                response = await client.post(
                    f"{self.BASE_URL}/people/match",
                    json={"api_key": self.api_key, "email": email}
                )

                if response.status_code == 200:
                    data = response.json().get("person", {})
                    if data:
                        result = self._normalize_contact(data)
                        self._cache[cache_key] = result
                        return result
                return None

        except Exception as e:
            logger.error(f"[apollo] Error enriching contact {email}: {e}")
            return None

    def _normalize_company(self, data: dict) -> dict:
        return {
            "source": "apollo",
            "legal_name": data.get("name"),
            "domain": data.get("primary_domain"),
            "industry": data.get("industry"),
            "sub_industry": data.get("subindustry"),
            "employee_count": data.get("estimated_num_employees"),
            "estimated_revenue": data.get("annual_revenue_printed"),
            "funding_stage": data.get("latest_funding_stage"),
            "total_funding": data.get("total_funding_printed"),
            "year_founded": data.get("founded_year"),
            "headquarters_city": data.get("city"),
            "headquarters_state": data.get("state"),
            "headquarters_country": data.get("country"),
            "description": data.get("short_description"),
            "linkedin_url": data.get("linkedin_url"),
            "twitter_url": data.get("twitter_url"),
            "technologies": data.get("current_technologies", []) or [],
            "keywords": data.get("keywords", []) or [],
        }

    def _normalize_contact(self, data: dict) -> dict:
        return {
            "source": "apollo",
            "full_name": data.get("name"),
            "title": data.get("title"),
            "seniority": data.get("seniority"),
            "department": data.get("departments", [""])[0] if data.get("departments") else None,
            "linkedin_url": data.get("linkedin_url"),
            "phone_direct": data.get("phone_numbers", [{}])[0].get("sanitized_number") if data.get("phone_numbers") else None,
            "location_city": data.get("city"),
            "location_state": data.get("state"),
            "location_country": data.get("country"),
            "previous_companies": [
                exp.get("organization_name", "")
                for exp in (data.get("employment_history", []) or [])[:5]
                if exp.get("organization_name")
            ],
        }


# ── Hunter.io Provider (Email Verification) ─────────────

class HunterProvider(EnrichmentProvider):
    """Hunter.io for email verification and domain search."""

    BASE_URL = "https://api.hunter.io/v2"

    def __init__(self):
        super().__init__(
            name="hunter",
            api_key=ENRICHMENT.hunter_api_key,
            rpm_limit=ENRICHMENT.hunter_rpm
        )

    async def enrich_company(self, domain: str) -> Optional[dict]:
        """Get company email patterns and key contacts."""
        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=ENRICHMENT.enrichment_timeout) as client:
                response = await client.get(
                    f"{self.BASE_URL}/domain-search",
                    params={"domain": domain, "api_key": self.api_key, "limit": 5}
                )

                if response.status_code == 200:
                    data = response.json().get("data", {})
                    return {
                        "source": "hunter",
                        "email_pattern": data.get("pattern"),
                        "organization": data.get("organization"),
                        "key_contacts": [
                            {
                                "email": e.get("value"),
                                "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                                "position": e.get("position"),
                                "department": e.get("department"),
                                "seniority": e.get("seniority"),
                            }
                            for e in data.get("emails", [])[:5]
                        ]
                    }
                return None

        except Exception as e:
            logger.error(f"[hunter] Error for {domain}: {e}")
            return None

    async def enrich_contact(self, email: str) -> Optional[dict]:
        """Verify an email address."""
        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=ENRICHMENT.enrichment_timeout) as client:
                response = await client.get(
                    f"{self.BASE_URL}/email-verifier",
                    params={"email": email, "api_key": self.api_key}
                )

                if response.status_code == 200:
                    data = response.json().get("data", {})
                    return {
                        "source": "hunter",
                        "email_status": data.get("status"),  # valid, invalid, accept_all, unknown
                        "email_score": data.get("score"),
                        "is_disposable": data.get("disposable"),
                        "is_webmail": data.get("webmail"),
                    }
                return None

        except Exception as e:
            logger.error(f"[hunter] Error verifying {email}: {e}")
            return None


# ── Mock Provider (for demos / testing) ──────────────────

class MockProvider(EnrichmentProvider):
    """
    Returns realistic mock data for portfolio demos.
    Generates deterministic results based on input hashing
    so the same email/domain always returns the same data.
    """

    MOCK_COMPANIES = {
        "techcorp.io": {
            "legal_name": "TechCorp Solutions Inc.",
            "industry": "SaaS",
            "sub_industry": "Sales Automation",
            "employee_count": 340,
            "estimated_revenue": "$45M",
            "funding_stage": "Series B",
            "total_funding": "$32M",
            "year_founded": 2017,
            "headquarters_city": "San Francisco",
            "headquarters_state": "CA",
            "headquarters_country": "US",
            "description": "TechCorp builds AI-powered sales automation tools for mid-market B2B companies. Their platform integrates with major CRMs to streamline prospecting and outreach workflows.",
            "linkedin_url": "https://linkedin.com/company/techcorp",
            "twitter_url": "https://twitter.com/techcorp",
            "technologies": ["React", "Node.js", "PostgreSQL", "AWS", "Salesforce", "HubSpot", "Outreach", "Gong"],
            "keywords": ["sales automation", "AI", "B2B", "SaaS", "CRM integration"],
            "recent_news": [
                "TechCorp raises $18M Series B to expand AI capabilities",
                "TechCorp announces Salesforce native integration",
                "TechCorp named in G2 Top 50 Sales Software 2024"
            ],
            "hiring_signals": [
                "Senior Sales Engineer (3 openings)",
                "VP of Revenue Operations",
                "Account Executive, Enterprise"
            ],
        },
        "megahealth.com": {
            "legal_name": "MegaHealth Systems",
            "industry": "Healthcare Technology",
            "sub_industry": "EHR/EMR",
            "employee_count": 1200,
            "estimated_revenue": "$180M",
            "funding_stage": "Series D",
            "total_funding": "$95M",
            "year_founded": 2013,
            "headquarters_city": "Boston",
            "headquarters_state": "MA",
            "headquarters_country": "US",
            "description": "MegaHealth provides cloud-based electronic health record systems for mid-size hospital networks. Strong growth trajectory with recent expansion into patient engagement.",
            "linkedin_url": "https://linkedin.com/company/megahealth",
            "twitter_url": "https://twitter.com/megahealth",
            "technologies": ["Java", "React", "AWS", "Salesforce", "Marketo", "Outreach"],
            "keywords": ["healthtech", "EHR", "patient engagement", "hospital systems"],
            "recent_news": [
                "MegaHealth acquires patient engagement startup for $22M",
                "MegaHealth expands into 12 new states",
            ],
            "hiring_signals": [
                "Director of Sales Operations",
                "Sales Development Representative (5 openings)",
            ],
        },
    }

    def __init__(self):
        super().__init__(name="mock", api_key="mock", rpm_limit=1000)

    def _pick_mock_company(self, domain: str) -> dict:
        """Generate deterministic mock data based on domain hash."""
        if domain in self.MOCK_COMPANIES:
            return self.MOCK_COMPANIES[domain]

        # Generate plausible data from hash
        h = int(hashlib.md5(domain.encode()).hexdigest(), 16)
        industries = ["SaaS", "FinTech", "E-commerce", "Healthcare Tech", "MarTech", "DevTools"]
        cities = [("San Francisco", "CA", "US"), ("New York", "NY", "US"), ("Austin", "TX", "US"),
                  ("London", None, "UK"), ("Toronto", "ON", "CA"), ("Berlin", None, "DE")]
        city = cities[h % len(cities)]

        return {
            "source": "mock",
            "legal_name": domain.split(".")[0].title() + " Inc.",
            "domain": domain,
            "industry": industries[h % len(industries)],
            "sub_industry": None,
            "employee_count": [15, 45, 120, 350, 800, 2500][h % 6],
            "estimated_revenue": ["$1M", "$5M", "$20M", "$50M", "$120M"][h % 5],
            "funding_stage": ["Seed", "Series A", "Series B", "Series C", "Growth"][h % 5],
            "total_funding": None,
            "year_founded": 2010 + (h % 14),
            "headquarters_city": city[0],
            "headquarters_state": city[1],
            "headquarters_country": city[2],
            "description": f"A {industries[h % len(industries)]} company specializing in innovative solutions.",
            "technologies": ["React", "Python", "AWS", "Salesforce", "HubSpot"][:3 + h % 3],
            "keywords": [],
            "recent_news": [],
            "hiring_signals": [],
        }

    async def enrich_company(self, domain: str) -> Optional[dict]:
        """Return mock company data."""
        await asyncio.sleep(0.1)  # Simulate API latency
        data = self._pick_mock_company(domain)
        data["source"] = "mock"
        return data

    async def enrich_contact(self, email: str) -> Optional[dict]:
        """Return mock contact data."""
        await asyncio.sleep(0.1)
        h = int(hashlib.md5(email.encode()).hexdigest(), 16)
        titles = ["VP of Sales", "Director of Revenue Operations", "Head of Growth",
                  "Sales Manager", "CRO", "VP Business Development"]
        seniorities = ["vp", "director", "c_level", "manager", "c_level", "vp"]
        departments = ["Sales", "Revenue Operations", "Growth", "Sales", "Executive", "Business Development"]

        name_parts = email.split("@")[0].replace(".", " ").replace("_", " ").title()

        return {
            "source": "mock",
            "full_name": name_parts,
            "title": titles[h % len(titles)],
            "seniority": seniorities[h % len(seniorities)],
            "department": departments[h % len(departments)],
            "linkedin_url": f"https://linkedin.com/in/{email.split('@')[0]}",
            "phone_direct": f"+1 (555) {h % 900 + 100}-{h % 9000 + 1000}",
            "location_city": "San Francisco",
            "location_state": "CA",
            "location_country": "US",
            "previous_companies": ["Salesforce", "Outreach", "Gong"][:h % 3 + 1],
            "email_status": "valid",
            "is_decision_maker": h % 3 != 0,
        }


# ── Provider Factory ─────────────────────────────────────

def get_providers(use_mock: bool = False) -> list[EnrichmentProvider]:
    """Return configured enrichment providers."""
    if use_mock:
        return [MockProvider()]

    providers = []

    if ENRICHMENT.clearbit_api_key:
        providers.append(ClearbitProvider())
    if ENRICHMENT.apollo_api_key:
        providers.append(ApolloProvider())
    if ENRICHMENT.hunter_api_key:
        providers.append(HunterProvider())

    # Always include mock as fallback if no real providers configured
    if not providers:
        logger.warning("No API keys configured — falling back to mock provider")
        providers.append(MockProvider())

    return providers
