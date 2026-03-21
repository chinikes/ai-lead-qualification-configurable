"""
HubSpot CRM integration layer.

Handles bidirectional sync between the AI system and HubSpot:
  - Create/update contacts with enrichment data
  - Create/update companies
  - Create deals when leads qualify
  - Set custom properties for AI scoring
  - Read existing data to avoid duplicate processing

Uses HubSpot API v3.
"""

import logging
from typing import Optional
from datetime import datetime

import httpx

from config.settings import ENRICHMENT
from config.schemas import (
    EnrichedLead,
    LeadScore,
    RoutingDecision,
    QualificationDecision,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"


class HubSpotClient:
    """Async HubSpot API client."""

    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or ENRICHMENT.hubspot_access_token
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    # ── Contact Operations ───────────────────────────────

    async def find_contact_by_email(self, email: str) -> Optional[dict]:
        """Search for existing contact by email."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{BASE_URL}/crm/v3/objects/contacts/search",
                    headers=self.headers,
                    json={
                        "filterGroups": [{
                            "filters": [{
                                "propertyName": "email",
                                "operator": "EQ",
                                "value": email
                            }]
                        }],
                        "properties": [
                            "email", "firstname", "lastname", "company",
                            "jobtitle", "phone", "lifecyclestage",
                            "ai_lead_score", "ai_qualification_status"
                        ]
                    }
                )
                if response.status_code == 200:
                    results = response.json().get("results", [])
                    return results[0] if results else None
                return None
        except Exception as e:
            logger.error(f"[hubspot] Contact search failed: {e}")
            return None

    async def create_or_update_contact(self, enriched_lead: EnrichedLead) -> Optional[str]:
        """
        Create or update a HubSpot contact with enrichment data.
        Returns the HubSpot contact ID.
        """
        email = enriched_lead.raw_lead.email
        if not email:
            return None

        # Check for existing contact
        existing = await self.find_contact_by_email(email)

        properties = self._build_contact_properties(enriched_lead)

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if existing:
                    contact_id = existing["id"]
                    response = await client.patch(
                        f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}",
                        headers=self.headers,
                        json={"properties": properties}
                    )
                    logger.info(f"[hubspot] Updated contact {contact_id}")
                else:
                    response = await client.post(
                        f"{BASE_URL}/crm/v3/objects/contacts",
                        headers=self.headers,
                        json={"properties": properties}
                    )
                    logger.info(f"[hubspot] Created new contact")

                if response.status_code in (200, 201):
                    return response.json().get("id")
                else:
                    logger.error(f"[hubspot] Contact upsert failed: {response.status_code} {response.text}")
                    return None

        except Exception as e:
            logger.error(f"[hubspot] Contact upsert error: {e}")
            return None

    def _build_contact_properties(self, enriched: EnrichedLead) -> dict:
        """Map enriched lead data to HubSpot contact properties."""
        lead = enriched.raw_lead
        contact = enriched.contact
        ai = enriched.ai_analysis

        props = {}

        # Standard properties
        if lead.first_name:
            props["firstname"] = lead.first_name
        if lead.last_name:
            props["lastname"] = lead.last_name
        if lead.email:
            props["email"] = lead.email
        if lead.phone:
            props["phone"] = lead.phone

        # Enriched properties
        if contact:
            if contact.normalized_title:
                props["jobtitle"] = contact.normalized_title
            if contact.phone_direct:
                props["phone"] = contact.phone_direct
            if contact.linkedin_url:
                props["hs_linkedin_url"] = contact.linkedin_url
            if contact.location_city:
                props["city"] = contact.location_city
            if contact.location_state:
                props["state"] = contact.location_state
            if contact.location_country:
                props["country"] = contact.location_country

        # Custom AI properties (these need to be created in HubSpot first)
        if contact:
            props["ai_seniority"] = contact.seniority.value
            props["ai_is_decision_maker"] = str(contact.is_decision_maker).lower()

        if ai:
            props["ai_icp_fit_score"] = str(round(ai.icp_fit_score * 100))
            props["ai_urgency"] = ai.urgency_assessment
            props["ai_company_summary"] = ai.company_summary[:2000]  # HubSpot field limit
            props["ai_buying_signals"] = "; ".join(
                [s.signal for s in ai.buying_signals[:5]]
            )[:2000]
            props["ai_recommended_talking_points"] = "; ".join(
                ai.recommended_talking_points[:5]
            )[:2000]

        # Metadata
        props["ai_enrichment_confidence"] = str(round(enriched.overall_data_confidence * 100))
        props["ai_enrichment_flags"] = ", ".join(enriched.flags) if enriched.flags else "none"
        props["ai_enriched_at"] = datetime.utcnow().isoformat()

        # Lead source tracking
        if lead.source:
            props["hs_lead_source"] = lead.source.value
        if lead.utm_source:
            props["utm_source"] = lead.utm_source
        if lead.utm_medium:
            props["utm_medium"] = lead.utm_medium
        if lead.utm_campaign:
            props["utm_campaign"] = lead.utm_campaign

        return props

    # ── Company Operations ───────────────────────────────

    async def create_or_update_company(self, enriched_lead: EnrichedLead) -> Optional[str]:
        """Create or update a HubSpot company from enrichment data."""
        company = enriched_lead.company
        if not company:
            return None

        properties = {
            "domain": company.domain,
        }

        if company.legal_name:
            properties["name"] = company.legal_name
        if company.industry:
            properties["industry"] = company.industry
        if company.employee_count:
            properties["numberofemployees"] = str(company.employee_count)
        if company.estimated_revenue:
            properties["annualrevenue"] = company.estimated_revenue
        if company.description:
            properties["description"] = company.description[:2000]
        if company.headquarters_city:
            properties["city"] = company.headquarters_city
        if company.headquarters_state:
            properties["state"] = company.headquarters_state
        if company.headquarters_country:
            properties["country"] = company.headquarters_country
        if company.linkedin_url:
            properties["linkedin_company_page"] = company.linkedin_url
        if company.year_founded:
            properties["founded_year"] = str(company.year_founded)

        # Custom AI properties
        if company.technologies:
            properties["ai_tech_stack"] = ", ".join(company.technologies[:20])
        if company.hiring_signals:
            properties["ai_hiring_signals"] = "; ".join(company.hiring_signals[:5])
        if company.funding_stage:
            properties["ai_funding_stage"] = company.funding_stage

        try:
            # Search for existing company by domain
            async with httpx.AsyncClient(timeout=15) as client:
                search_response = await client.post(
                    f"{BASE_URL}/crm/v3/objects/companies/search",
                    headers=self.headers,
                    json={
                        "filterGroups": [{
                            "filters": [{
                                "propertyName": "domain",
                                "operator": "EQ",
                                "value": company.domain
                            }]
                        }]
                    }
                )

                existing = None
                if search_response.status_code == 200:
                    results = search_response.json().get("results", [])
                    existing = results[0] if results else None

                if existing:
                    company_id = existing["id"]
                    response = await client.patch(
                        f"{BASE_URL}/crm/v3/objects/companies/{company_id}",
                        headers=self.headers,
                        json={"properties": properties}
                    )
                else:
                    response = await client.post(
                        f"{BASE_URL}/crm/v3/objects/companies",
                        headers=self.headers,
                        json={"properties": properties}
                    )

                if response.status_code in (200, 201):
                    return response.json().get("id")
                return None

        except Exception as e:
            logger.error(f"[hubspot] Company upsert error: {e}")
            return None

    # ── Deal Operations ──────────────────────────────────

    async def create_deal(
        self,
        routing: RoutingDecision,
        contact_id: str,
        company_id: Optional[str] = None,
    ) -> Optional[str]:
        """Create a deal when a lead qualifies."""
        score = routing.lead_score
        enriched = score.enriched_lead

        company_name = enriched.company.legal_name if enriched.company else "Unknown"
        contact_name = enriched.contact.full_name if enriched.contact else enriched.raw_lead.email

        properties = {
            "dealname": f"{company_name} — {contact_name}",
            "pipeline": "default",
            "dealstage": self._map_deal_stage(score.decision),
            "ai_composite_score": str(round(score.composite_score)),
            "ai_temperature": score.temperature.value,
            "ai_qualification_decision": score.decision.value,
            "ai_decision_reasoning": score.decision_reasoning[:2000],
        }

        if routing.assigned_rep_id:
            properties["hubspot_owner_id"] = routing.assigned_rep_id
        if routing.priority:
            properties["ai_priority"] = routing.priority
        if routing.territory:
            properties["ai_territory"] = routing.territory

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{BASE_URL}/crm/v3/objects/deals",
                    headers=self.headers,
                    json={"properties": properties}
                )

                if response.status_code == 201:
                    deal_id = response.json().get("id")

                    # Associate deal with contact
                    await self._associate(client, "deals", deal_id, "contacts", contact_id)

                    # Associate deal with company
                    if company_id:
                        await self._associate(client, "deals", deal_id, "companies", company_id)

                    logger.info(f"[hubspot] Created deal {deal_id}")
                    return deal_id
                else:
                    logger.error(f"[hubspot] Deal creation failed: {response.status_code}")
                    return None

        except Exception as e:
            logger.error(f"[hubspot] Deal creation error: {e}")
            return None

    async def _associate(
        self,
        client: httpx.AsyncClient,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
    ):
        """Create an association between two HubSpot objects."""
        try:
            await client.put(
                f"{BASE_URL}/crm/v3/objects/{from_type}/{from_id}/associations/{to_type}/{to_id}/1",
                headers=self.headers,
            )
        except Exception as e:
            logger.error(f"[hubspot] Association failed: {e}")

    def _map_deal_stage(self, decision: QualificationDecision) -> str:
        """Map qualification decision to HubSpot deal stage."""
        stage_map = {
            QualificationDecision.QUALIFIED: "qualifiedtobuy",
            QualificationDecision.NURTURE: "appointmentscheduled",
            QualificationDecision.REVIEW: "appointmentscheduled",
            QualificationDecision.DISQUALIFIED: "closedlost",
        }
        return stage_map.get(decision, "appointmentscheduled")

    # ── Custom Property Setup ────────────────────────────

    async def ensure_custom_properties(self):
        """
        Create custom HubSpot properties needed by the AI system.
        Run once during initial setup.
        """
        contact_properties = [
            {"name": "ai_seniority", "label": "AI: Seniority", "type": "string", "groupName": "ai_enrichment"},
            {"name": "ai_is_decision_maker", "label": "AI: Decision Maker", "type": "string", "groupName": "ai_enrichment"},
            {"name": "ai_icp_fit_score", "label": "AI: ICP Fit Score", "type": "number", "groupName": "ai_enrichment"},
            {"name": "ai_urgency", "label": "AI: Urgency", "type": "string", "groupName": "ai_enrichment"},
            {"name": "ai_company_summary", "label": "AI: Company Summary", "type": "string", "groupName": "ai_enrichment"},
            {"name": "ai_buying_signals", "label": "AI: Buying Signals", "type": "string", "groupName": "ai_enrichment"},
            {"name": "ai_recommended_talking_points", "label": "AI: Talking Points", "type": "string", "groupName": "ai_enrichment"},
            {"name": "ai_enrichment_confidence", "label": "AI: Enrichment Confidence", "type": "number", "groupName": "ai_enrichment"},
            {"name": "ai_enrichment_flags", "label": "AI: Enrichment Flags", "type": "string", "groupName": "ai_enrichment"},
            {"name": "ai_enriched_at", "label": "AI: Enriched At", "type": "string", "groupName": "ai_enrichment"},
            {"name": "ai_lead_score", "label": "AI: Lead Score", "type": "number", "groupName": "ai_scoring"},
        ]

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Create property group first
                await client.post(
                    f"{BASE_URL}/crm/v3/properties/contacts/groups",
                    headers=self.headers,
                    json={"name": "ai_enrichment", "label": "AI Enrichment"}
                )
                await client.post(
                    f"{BASE_URL}/crm/v3/properties/contacts/groups",
                    headers=self.headers,
                    json={"name": "ai_scoring", "label": "AI Scoring"}
                )

                # Create properties
                for prop in contact_properties:
                    await client.post(
                        f"{BASE_URL}/crm/v3/properties/contacts",
                        headers=self.headers,
                        json=prop
                    )
                    logger.info(f"[hubspot] Created property: {prop['name']}")

        except Exception as e:
            logger.error(f"[hubspot] Property setup error: {e}")
