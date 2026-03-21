"""
Research Agent — the first agent in the pipeline.

Responsibilities:
  1. Normalize and deduplicate raw lead data
  2. Run parallel enrichment across multiple providers
  3. Merge and resolve conflicting enrichment results
  4. Call the LLM for deep analysis (company summary, ICP fit, buying signals)
  5. Validate data completeness and flag low-confidence leads
  6. Output an EnrichedLead ready for the Qualification Agent

Design decisions:
  - Enrichment providers run in parallel (asyncio.gather) for speed
  - Provider results are merged with a "most complete wins" strategy
  - LLM analysis uses structured JSON output for reliability
  - Confidence scoring is computed from data completeness, not just provider response
  - Leads below confidence threshold are flagged for human review
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

import httpx

from config.schemas import (
    AIAnalysis,
    BuyingSignal,
    CompanyEnrichment,
    CompanySize,
    ContactEnrichment,
    EnrichedLead,
    PainPoint,
    RawLead,
    Seniority,
)
from config.settings import ENRICHMENT, ICP
from agents.enrichment_providers import EnrichmentProvider, get_providers

logger = logging.getLogger(__name__)


# ── Seniority Detection ─────────────────────────────────

SENIORITY_PATTERNS = {
    Seniority.C_LEVEL: [
        r"\bCEO\b", r"\bCTO\b", r"\bCFO\b", r"\bCOO\b", r"\bCRO\b", r"\bCMO\b",
        r"\bChief\b", r"\bC-Level\b", r"\bFounder\b", r"\bCo-Founder\b",
    ],
    Seniority.VP: [
        r"\bVP\b", r"\bVice President\b", r"\bSVP\b", r"\bEVP\b",
    ],
    Seniority.DIRECTOR: [
        r"\bDirector\b", r"\bHead of\b",
    ],
    Seniority.MANAGER: [
        r"\bManager\b", r"\bLead\b", r"\bTeam Lead\b",
    ],
    Seniority.SENIOR_IC: [
        r"\bSenior\b", r"\bSr\.\b", r"\bPrincipal\b", r"\bStaff\b",
    ],
}

FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "mail.com", "protonmail.com",
    "zoho.com", "yandex.com", "gmx.com",
}


def detect_seniority(title: Optional[str]) -> Seniority:
    """Detect seniority level from job title using regex patterns."""
    if not title:
        return Seniority.UNKNOWN
    for seniority, patterns in SENIORITY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, title, re.IGNORECASE):
                return seniority
    return Seniority.IC


def detect_employee_range(count: Optional[int]) -> Optional[CompanySize]:
    """Map employee count to range bucket."""
    if count is None:
        return None
    if count <= 1:
        return CompanySize.SOLO
    if count <= 10:
        return CompanySize.MICRO
    if count <= 50:
        return CompanySize.SMALL
    if count <= 200:
        return CompanySize.MEDIUM
    if count <= 1000:
        return CompanySize.LARGE
    if count <= 5000:
        return CompanySize.ENTERPRISE
    return CompanySize.MEGA


def extract_domain(email: Optional[str]) -> Optional[str]:
    """Extract domain from email address."""
    if not email or "@" not in email:
        return None
    return email.split("@")[1].lower().strip()


# ── Research Agent ───────────────────────────────────────

class ResearchAgent:
    """
    Orchestrates the full lead research pipeline.

    Usage:
        agent = ResearchAgent(use_mock=True)
        enriched = await agent.research(raw_lead)
    """

    def __init__(self, use_mock: bool = False):
        self.providers: list[EnrichmentProvider] = get_providers(use_mock=use_mock)
        self.use_mock = use_mock

    async def research(self, lead: RawLead) -> EnrichedLead:
        """
        Main entry point — run the full research pipeline on a raw lead.

        Returns an EnrichedLead with all available data, AI analysis,
        confidence scores, and flags.
        """
        start_time = time.time()
        flags: list[str] = []
        review_reasons: list[str] = []

        logger.info(f"[research] Starting research for {lead.email or 'unknown'}")

        # ── Step 1: Normalize and extract identifiers ────
        domain = lead.company_domain or extract_domain(lead.email)
        email = lead.email

        if not email and not domain:
            flags.append("no_identifiers")
            review_reasons.append("No email or domain available for enrichment")
            return EnrichedLead(
                raw_lead=lead,
                flags=flags,
                needs_human_review=True,
                review_reasons=review_reasons,
                enrichment_started_at=datetime.utcnow(),
                enrichment_completed_at=datetime.utcnow(),
                enrichment_duration_ms=int((time.time() - start_time) * 1000),
            )

        if email and extract_domain(email) in FREE_EMAIL_DOMAINS:
            flags.append("free_email")
            if not domain:
                flags.append("no_company_domain")
                review_reasons.append("Free email with no company domain — cannot enrich company")

        # ── Step 2: Parallel enrichment ──────────────────
        company_data, contact_data = await self._parallel_enrich(domain, email)

        # ── Step 3: Merge provider results ───────────────
        company = self._merge_company_data(company_data, domain) if company_data else None
        contact = self._merge_contact_data(contact_data, email, lead) if contact_data else None

        # ── Step 4: AI analysis ──────────────────────────
        ai_analysis = await self._run_ai_analysis(lead, company, contact)

        # ── Step 5: Compute confidence ───────────────────
        confidence = self._compute_confidence(company, contact, ai_analysis, flags)

        if confidence < ENRICHMENT.min_confidence_with_review:
            flags.append("very_low_confidence")
            review_reasons.append(f"Overall confidence {confidence:.2f} below minimum threshold")

        if confidence < ENRICHMENT.min_confidence_auto_proceed:
            flags.append("low_confidence")
            if confidence >= ENRICHMENT.min_confidence_with_review:
                review_reasons.append(f"Confidence {confidence:.2f} requires review before proceeding")

        needs_review = len(review_reasons) > 0

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[research] Completed in {elapsed_ms}ms | confidence={confidence:.2f} | flags={flags}")

        return EnrichedLead(
            raw_lead=lead,
            company=company,
            contact=contact,
            ai_analysis=ai_analysis,
            enrichment_started_at=datetime.utcnow(),
            enrichment_completed_at=datetime.utcnow(),
            enrichment_duration_ms=elapsed_ms,
            overall_data_confidence=round(confidence, 3),
            flags=flags,
            needs_human_review=needs_review,
            review_reasons=review_reasons,
        )

    # ── Parallel Enrichment ──────────────────────────────

    async def _parallel_enrich(
        self, domain: Optional[str], email: Optional[str]
    ) -> tuple[list[dict], list[dict]]:
        """Run all providers in parallel for both company and contact."""
        tasks = []

        for provider in self.providers:
            if domain:
                tasks.append(("company", provider.name, provider.enrich_company(domain)))
            if email:
                tasks.append(("contact", provider.name, provider.enrich_contact(email)))

        results = await asyncio.gather(
            *[t[2] for t in tasks],
            return_exceptions=True
        )

        company_results = []
        contact_results = []

        for i, (data_type, provider_name, _) in enumerate(tasks):
            result = results[i]
            if isinstance(result, Exception):
                logger.error(f"[{provider_name}] {data_type} enrichment failed: {result}")
                continue
            if result is None:
                continue

            if data_type == "company":
                company_results.append(result)
            else:
                contact_results.append(result)

        return company_results, contact_results

    # ── Data Merging ─────────────────────────────────────

    def _merge_company_data(self, results: list[dict], domain: str) -> CompanyEnrichment:
        """
        Merge multiple provider results into a single CompanyEnrichment.
        Strategy: for each field, take the first non-null value.
        For lists (technologies, keywords), union all results.
        """
        merged = {
            "domain": domain,
            "enrichment_sources": [r.get("source", "unknown") for r in results],
        }

        # Scalar fields: first non-null wins
        scalar_fields = [
            "legal_name", "industry", "sub_industry", "employee_count",
            "estimated_revenue", "funding_stage", "total_funding",
            "year_founded", "headquarters_city", "headquarters_state",
            "headquarters_country", "description", "linkedin_url", "twitter_url",
        ]
        for field in scalar_fields:
            for result in results:
                val = result.get(field)
                if val is not None and val != "" and val != 0:
                    merged[field] = val
                    break

        # List fields: union
        for list_field in ["technologies", "keywords", "recent_news", "hiring_signals"]:
            combined = []
            seen = set()
            for result in results:
                for item in result.get(list_field, []):
                    if item and item.lower() not in seen:
                        combined.append(item)
                        seen.add(item.lower())
            merged[list_field] = combined

        # Compute enrichment confidence based on field completeness
        filled = sum(1 for f in scalar_fields if merged.get(f) is not None)
        merged["enrichment_confidence"] = round(filled / len(scalar_fields), 3)

        # Map employee count to range
        if merged.get("employee_count"):
            merged["employee_range"] = detect_employee_range(merged["employee_count"])

        return CompanyEnrichment(**{k: v for k, v in merged.items() if k in CompanyEnrichment.model_fields})

    def _merge_contact_data(
        self, results: list[dict], email: str, lead: RawLead
    ) -> ContactEnrichment:
        """Merge contact enrichment results."""
        merged = {
            "email": email,
            "enrichment_sources": [r.get("source", "unknown") for r in results],
        }

        # Use lead data as baseline
        if lead.first_name and lead.last_name:
            merged["full_name"] = f"{lead.first_name} {lead.last_name}"
        if lead.job_title:
            merged["normalized_title"] = lead.job_title

        # Overlay enrichment data
        scalar_fields = [
            "full_name", "department", "linkedin_url",
            "phone_direct", "phone_mobile", "location_city",
            "location_state", "location_country",
        ]
        for field in scalar_fields:
            for result in results:
                val = result.get(field)
                if val is not None and val != "":
                    merged[field] = val
                    break

        # Title: prefer enrichment over form data
        for result in results:
            title = result.get("title")
            if title:
                merged["normalized_title"] = title
                break

        # Detect seniority from title
        merged["seniority"] = detect_seniority(merged.get("normalized_title"))

        # Decision maker detection
        merged["is_decision_maker"] = any(r.get("is_decision_maker") for r in results)
        if merged["seniority"] in [Seniority.C_LEVEL, Seniority.VP]:
            merged["is_decision_maker"] = True

        # Previous companies
        prev = []
        seen = set()
        for result in results:
            for company in result.get("previous_companies", []):
                if company and company.lower() not in seen:
                    prev.append(company)
                    seen.add(company.lower())
        merged["previous_companies"] = prev

        # Confidence
        filled = sum(1 for f in scalar_fields if merged.get(f) is not None)
        merged["enrichment_confidence"] = round(filled / len(scalar_fields), 3)

        return ContactEnrichment(**{k: v for k, v in merged.items() if k in ContactEnrichment.model_fields})

    # ── AI Analysis ──────────────────────────────────────

    async def _run_ai_analysis(
        self,
        lead: RawLead,
        company: Optional[CompanyEnrichment],
        contact: Optional[ContactEnrichment],
    ) -> Optional[AIAnalysis]:
        """
        Call the LLM to produce a structured analysis of the lead.
        Uses Claude with structured JSON output.
        """
        if not company and not contact:
            logger.warning("[research] Skipping AI analysis — no enrichment data")
            return None

        prompt = self._build_analysis_prompt(lead, company, contact)

        try:
            async with httpx.AsyncClient(timeout=ENRICHMENT.llm_timeout) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ENRICHMENT.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 2000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )

                if response.status_code != 200:
                    logger.error(f"[research] LLM API error: {response.status_code} {response.text}")
                    return None

                data = response.json()
                text = data["content"][0]["text"]

                # Parse JSON from LLM response
                json_match = re.search(r"\{[\s\S]*\}", text)
                if not json_match:
                    logger.error("[research] LLM did not return valid JSON")
                    return None

                analysis_data = json.loads(json_match.group())
                return AIAnalysis(**analysis_data)

        except json.JSONDecodeError as e:
            logger.error(f"[research] Failed to parse LLM JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"[research] AI analysis failed: {e}")
            return None

    def _build_analysis_prompt(
        self,
        lead: RawLead,
        company: Optional[CompanyEnrichment],
        contact: Optional[ContactEnrichment],
    ) -> str:
        """Build the structured analysis prompt for the LLM."""

        company_section = ""
        if company:
            company_section = f"""
## Company Data
- Name: {company.legal_name or 'Unknown'}
- Domain: {company.domain}
- Industry: {company.industry or 'Unknown'} / {company.sub_industry or 'Unknown'}
- Employees: {company.employee_count or 'Unknown'}
- Revenue: {company.estimated_revenue or 'Unknown'}
- Funding: {company.funding_stage or 'Unknown'} ({company.total_funding or 'Unknown'})
- Founded: {company.year_founded or 'Unknown'}
- Location: {company.headquarters_city or ''}, {company.headquarters_state or ''}, {company.headquarters_country or ''}
- Description: {company.description or 'None available'}
- Tech Stack: {', '.join(company.technologies[:15]) if company.technologies else 'Unknown'}
- Recent News: {json.dumps(company.recent_news[:5]) if company.recent_news else 'None'}
- Hiring Signals: {json.dumps(company.hiring_signals[:5]) if company.hiring_signals else 'None'}
"""

        contact_section = ""
        if contact:
            contact_section = f"""
## Contact Data
- Name: {contact.full_name or 'Unknown'}
- Title: {contact.normalized_title or 'Unknown'}
- Seniority: {contact.seniority.value}
- Department: {contact.department or 'Unknown'}
- Decision Maker: {'Yes' if contact.is_decision_maker else 'No'}
- Location: {contact.location_city or ''}, {contact.location_country or ''}
- Previous Companies: {', '.join(contact.previous_companies[:5]) if contact.previous_companies else 'None'}
"""

        message_section = ""
        if lead.message:
            message_section = f"""
## Lead's Message
\"{lead.message}\"
"""

        return f"""You are an expert B2B sales analyst. Analyze the following lead data and produce a structured assessment.

# Ideal Customer Profile (ICP)
- Target Industries: {', '.join(ICP.target_industries[:8])}
- Employee Range: {ICP.min_employee_count} - {ICP.max_employee_count}
- Revenue: {ICP.target_revenue_range}
- Target Titles: {', '.join(ICP.target_titles[:6])}
- Target Departments: {', '.join(ICP.target_departments)}
- Positive Tech Signals: {', '.join(ICP.positive_tech_signals[:10])}

# Lead Data

- Source: {lead.source.value}
- Email: {lead.email or 'Unknown'}
- UTM: source={lead.utm_source or 'none'}, medium={lead.utm_medium or 'none'}, campaign={lead.utm_campaign or 'none'}
{company_section}
{contact_section}
{message_section}

# Your Task

Analyze this lead and respond with ONLY a JSON object (no markdown fencing, no preamble) matching this exact schema:

{{
  "company_summary": "2-3 sentence overview of the company and what they do",
  "icp_fit_narrative": "Explanation of how well this lead matches the ICP, citing specific data points",
  "icp_fit_score": 0.0 to 1.0,
  "buying_signals": [
    {{"signal": "description", "source": "where detected", "strength": "strong|moderate|weak"}}
  ],
  "pain_points": [
    {{"pain_point": "description", "evidence": "what suggests this", "relevance_to_product": "high|medium|low"}}
  ],
  "recommended_talking_points": ["point 1", "point 2", "point 3"],
  "urgency_assessment": "immediate|near_term|exploratory|not_ready",
  "confidence": 0.0 to 1.0,
  "reasoning": "Step-by-step explanation of your assessment"
}}

Be specific and evidence-based. Cite actual data points from the lead data. Do not fabricate information not present in the data above."""

    # ── Confidence Computation ───────────────────────────

    def _compute_confidence(
        self,
        company: Optional[CompanyEnrichment],
        contact: Optional[ContactEnrichment],
        ai_analysis: Optional[AIAnalysis],
        flags: list[str],
    ) -> float:
        """
        Compute overall enrichment confidence.
        Weighted average of component confidences with penalty flags.
        """
        scores = []
        weights = []

        if company:
            scores.append(company.enrichment_confidence)
            weights.append(0.35)
        else:
            scores.append(0.0)
            weights.append(0.35)

        if contact:
            scores.append(contact.enrichment_confidence)
            weights.append(0.30)
        else:
            scores.append(0.0)
            weights.append(0.30)

        if ai_analysis:
            scores.append(ai_analysis.confidence)
            weights.append(0.35)
        else:
            scores.append(0.0)
            weights.append(0.35)

        # Weighted average
        total_weight = sum(weights)
        confidence = sum(s * w for s, w in zip(scores, weights)) / total_weight if total_weight > 0 else 0.0

        # Apply penalties
        if "free_email" in flags:
            confidence *= 0.8
        if "no_company_domain" in flags:
            confidence *= 0.6
        if "no_identifiers" in flags:
            confidence = 0.0

        return min(max(confidence, 0.0), 1.0)


# ── Convenience Runner ───────────────────────────────────

async def research_lead(lead_data: dict, use_mock: bool = True) -> dict:
    """
    Convenience function for n8n Code nodes.
    Accepts a dict, returns a dict.
    """
    lead = RawLead(**lead_data)
    agent = ResearchAgent(use_mock=use_mock)
    result = await agent.research(lead)
    return result.model_dump(mode="json")
