"""
Engagement Agent — the fourth agent in the pipeline.

Responsibilities:
  1. Select engagement strategy based on lead temperature, seniority, signals
  2. Generate personalized email content using Claude API
  3. Build multi-touch outreach sequences (email → LinkedIn → email → phone)
  4. Produce A/B variants with different hooks and CTAs
  5. Output a structured EngagementPlan ready for HubSpot sequence enrollment

Design decisions:
  - Three strategies: Accelerate (hot), Educate (warm/cool), Warm Up (cold/nurture)
  - Each strategy has a different cadence, tone, and content depth
  - LLM generates personalized content using buying signals + pain points
  - A/B variants test different angles (ROI vs peer proof vs urgency)
  - Sequences are structured as step arrays that map directly to HubSpot
  - Fallback templates exist if LLM is unavailable
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field

import httpx

from config.schemas import (
    RoutingDecision,
    LeadTemperature,
    QualificationDecision,
    Seniority,
)
from config.settings import ENRICHMENT

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  DATA MODELS
# ══════════════════════════════════════════════════════════

class EngagementStrategy(str, Enum):
    ACCELERATE = "accelerate"   # Hot leads: fast, direct, demo-focused
    EDUCATE = "educate"         # Warm leads: value-driven, case studies
    WARM_UP = "warm_up"         # Cool/nurture: light touch, educational


class TouchType(str, Enum):
    EMAIL = "email"
    LINKEDIN = "linkedin"
    PHONE = "phone"


class SequenceStep(BaseModel):
    """A single step in an outreach sequence."""
    step_number: int
    day: int = Field(description="Day offset from sequence start")
    touch_type: TouchType
    subject: Optional[str] = None  # Email subject line
    body: str = Field(description="Message content")
    cta: Optional[str] = None     # Call to action
    internal_notes: Optional[str] = None  # Notes for the rep
    variant: str = "A"            # A/B variant label


class EngagementPlan(BaseModel):
    """Complete output of the Engagement Agent."""
    lead_email: str
    lead_name: str
    company_name: str
    rep_name: Optional[str] = None
    rep_email: Optional[str] = None

    strategy: EngagementStrategy
    strategy_reasoning: str

    sequence_steps: list[SequenceStep] = Field(default_factory=list)
    total_touches: int = 0
    sequence_duration_days: int = 0

    personalization_inputs: dict = Field(default_factory=dict, description="Signals/data used for personalization")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    model_version: str = "v1.0"


# ══════════════════════════════════════════════════════════
#  STRATEGY SELECTOR
# ══════════════════════════════════════════════════════════

# Cadence templates per strategy
CADENCES = {
    EngagementStrategy.ACCELERATE: [
        {"day": 0, "type": TouchType.EMAIL, "purpose": "personalized_intro"},
        {"day": 1, "type": TouchType.LINKEDIN, "purpose": "connection_request"},
        {"day": 2, "type": TouchType.PHONE, "purpose": "warm_call"},
        {"day": 4, "type": TouchType.EMAIL, "purpose": "value_follow_up"},
        {"day": 7, "type": TouchType.EMAIL, "purpose": "case_study"},
        {"day": 10, "type": TouchType.PHONE, "purpose": "final_attempt"},
    ],
    EngagementStrategy.EDUCATE: [
        {"day": 0, "type": TouchType.EMAIL, "purpose": "personalized_intro"},
        {"day": 3, "type": TouchType.LINKEDIN, "purpose": "connection_request"},
        {"day": 5, "type": TouchType.EMAIL, "purpose": "value_content"},
        {"day": 9, "type": TouchType.EMAIL, "purpose": "case_study"},
        {"day": 14, "type": TouchType.EMAIL, "purpose": "soft_cta"},
    ],
    EngagementStrategy.WARM_UP: [
        {"day": 0, "type": TouchType.EMAIL, "purpose": "light_intro"},
        {"day": 7, "type": TouchType.EMAIL, "purpose": "educational_content"},
        {"day": 14, "type": TouchType.LINKEDIN, "purpose": "connection_request"},
        {"day": 21, "type": TouchType.EMAIL, "purpose": "check_in"},
    ],
}


def select_strategy(routing: RoutingDecision) -> tuple[EngagementStrategy, str]:
    """
    Select engagement strategy based on lead characteristics.

    Rules:
      - Hot + qualified → Accelerate
      - Warm + qualified → Educate (or Accelerate if C-level)
      - Cool/nurture → Warm Up
      - Review leads → Educate (cautious approach)
    """
    score = routing.lead_score
    temp = score.temperature
    decision = score.decision
    enriched = score.enriched_lead
    seniority = enriched.contact.seniority if enriched.contact else Seniority.UNKNOWN

    # Hot + qualified → Accelerate
    if temp == LeadTemperature.HOT and decision == QualificationDecision.QUALIFIED:
        return EngagementStrategy.ACCELERATE, "Hot qualified lead — aggressive multi-touch cadence with demo focus"

    # Warm + C-level/VP → Accelerate (upgrade)
    if temp == LeadTemperature.WARM and seniority in [Seniority.C_LEVEL, Seniority.VP]:
        if decision == QualificationDecision.QUALIFIED:
            return EngagementStrategy.ACCELERATE, "Warm lead but C-level decision maker — upgraded to accelerate cadence"

    # Warm + qualified → Educate
    if temp == LeadTemperature.WARM and decision == QualificationDecision.QUALIFIED:
        return EngagementStrategy.EDUCATE, "Warm qualified lead — value-driven education sequence"

    # Review leads → Educate (cautious)
    if decision == QualificationDecision.REVIEW:
        return EngagementStrategy.EDUCATE, "Needs review — cautious education approach while data is verified"

    # Nurture → Warm Up
    if decision == QualificationDecision.NURTURE:
        return EngagementStrategy.WARM_UP, "Nurture lead — light-touch warm-up sequence"

    # Cool → Warm Up
    if temp in [LeadTemperature.COOL, LeadTemperature.COLD]:
        return EngagementStrategy.WARM_UP, "Cool/cold lead — gentle warm-up before pushing for engagement"

    # Default
    return EngagementStrategy.EDUCATE, "Default education strategy"


# ══════════════════════════════════════════════════════════
#  AI CONTENT GENERATOR
# ══════════════════════════════════════════════════════════

class ContentGenerator:
    """
    Uses Claude to generate personalized outreach content.
    Falls back to templates if LLM is unavailable.
    """

    async def generate_sequence(
        self,
        routing: RoutingDecision,
        strategy: EngagementStrategy,
    ) -> list[SequenceStep]:
        """Generate all sequence steps with personalized content."""

        cadence = CADENCES[strategy]
        enriched = routing.lead_score.enriched_lead
        ai = enriched.ai_analysis

        # Build context for the LLM
        context = self._build_context(routing, strategy)

        # Try LLM generation
        llm_steps = await self._generate_via_llm(context, cadence, strategy)

        if llm_steps:
            return llm_steps

        # Fallback to templates
        logger.info("[engagement] LLM unavailable — using template fallback")
        return self._generate_from_templates(routing, cadence, strategy)

    def _build_context(self, routing: RoutingDecision, strategy: EngagementStrategy) -> dict:
        """Build the personalization context for content generation."""
        enriched = routing.lead_score.enriched_lead
        ai = enriched.ai_analysis
        contact = enriched.contact
        company = enriched.company

        return {
            "lead_name": contact.full_name if contact else "there",
            "first_name": (contact.full_name.split()[0] if contact and contact.full_name else "there"),
            "title": contact.normalized_title if contact else "professional",
            "company": company.legal_name if company else "your company",
            "industry": company.industry if company else "your industry",
            "employees": company.employee_count if company else None,
            "revenue": company.estimated_revenue if company else None,
            "tech_stack": ", ".join(company.technologies[:5]) if company and company.technologies else None,
            "buying_signals": [s.signal for s in ai.buying_signals[:3]] if ai and ai.buying_signals else [],
            "pain_points": [p.pain_point for p in ai.pain_points[:3]] if ai and ai.pain_points else [],
            "talking_points": ai.recommended_talking_points[:3] if ai and ai.recommended_talking_points else [],
            "urgency": ai.urgency_assessment if ai else "exploratory",
            "company_summary": ai.company_summary if ai else None,
            "message": enriched.raw_lead.message,
            "source": enriched.raw_lead.source.value,
            "rep_name": routing.assigned_rep_name or "Our team",
            "strategy": strategy.value,
            "composite_score": routing.lead_score.composite_score,
            "temperature": routing.lead_score.temperature.value,
        }

    async def _generate_via_llm(
        self, context: dict, cadence: list[dict], strategy: EngagementStrategy,
    ) -> Optional[list[SequenceStep]]:
        """Call Claude to generate personalized content for each step."""

        step_descriptions = []
        for i, step in enumerate(cadence):
            step_descriptions.append(
                f"Step {i+1} (Day {step['day']}, {step['type'].value}): {step['purpose']}"
            )

        prompt = f"""You are an expert B2B sales engagement copywriter. Generate a personalized outreach sequence.

## Lead Context
- Name: {context['first_name']} ({context['title']})
- Company: {context['company']} ({context['industry']}, ~{context['employees']} employees)
- Their message: "{context.get('message', 'No message')}"
- Buying signals: {json.dumps(context['buying_signals'])}
- Pain points: {json.dumps(context['pain_points'])}
- Recommended talking points: {json.dumps(context['talking_points'])}
- Urgency: {context['urgency']}
- Source: {context['source']}

## Strategy: {strategy.value.upper()}
{"Fast, direct, demo-focused. Short punchy emails. Create urgency." if strategy == EngagementStrategy.ACCELERATE else
"Value-driven, educational. Share insights and case studies. Build trust." if strategy == EngagementStrategy.EDUCATE else
"Light touch, no pressure. Share useful content. Keep door open."}

## Sequence Steps
{chr(10).join(step_descriptions)}

## Your Rep
Signing as: {context['rep_name']}

Generate ONLY a JSON array of steps. Each step:
{{
  "step_number": 1,
  "day": 0,
  "touch_type": "email|linkedin|phone",
  "subject": "Email subject (null for non-email)",
  "body": "Full message text. Use {{first_name}} for personalization. Keep emails under 150 words. LinkedIn messages under 80 words. Phone = call script bullet points.",
  "cta": "Clear call to action",
  "internal_notes": "Tips for the rep"
}}

Also generate a B variant for the first email (step_number 1) with a different hook/angle.

Return ONLY the JSON array, no markdown fencing."""

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
                        "max_tokens": 3000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )

                if response.status_code != 200:
                    logger.error(f"[engagement] LLM API error: {response.status_code}")
                    return None

                data = response.json()
                text = data["content"][0]["text"]

                # Parse JSON
                json_match = re.search(r"\[[\s\S]*\]", text)
                if not json_match:
                    return None

                steps_data = json.loads(json_match.group())
                return [SequenceStep(**step) for step in steps_data]

        except Exception as e:
            logger.error(f"[engagement] LLM generation failed: {e}")
            return None

    def _generate_from_templates(
        self, routing: RoutingDecision, cadence: list[dict], strategy: EngagementStrategy,
    ) -> list[SequenceStep]:
        """Generate content from templates when LLM is unavailable."""
        enriched = routing.lead_score.enriched_lead
        contact = enriched.contact
        company = enriched.company
        ai = enriched.ai_analysis

        first_name = contact.full_name.split()[0] if contact and contact.full_name else "there"
        company_name = company.legal_name if company else "your company"
        rep_name = routing.assigned_rep_name or "Our team"
        industry = company.industry if company else "your industry"

        # Build signal-specific snippets
        signal_line = ""
        if ai and ai.buying_signals:
            signal_line = f"I noticed {ai.buying_signals[0].signal.lower()}. "

        pain_line = ""
        if ai and ai.pain_points:
            pain_line = f"Many {industry} companies tell us they struggle with {ai.pain_points[0].pain_point.lower()}. "

        talking_line = ""
        if ai and ai.recommended_talking_points:
            talking_line = ai.recommended_talking_points[0]

        message_ref = ""
        if enriched.raw_lead.message:
            msg = enriched.raw_lead.message
            if len(msg) > 50:
                message_ref = f'You mentioned "{msg[:60]}..." — '
            else:
                message_ref = f'You mentioned "{msg}" — '

        # Template bank per purpose
        templates = {
            "personalized_intro": {
                "subject_a": f"{first_name}, quick question about {company_name}'s sales process",
                "body_a": f"Hi {first_name},\n\n{message_ref}{signal_line}I work with {industry} companies to automate their lead qualification and routing.\n\n{pain_line}Our AI-powered system typically saves sales teams 15-20 hours per week on manual lead review.\n\nWould you be open to a 15-minute call this week to see if there's a fit?\n\nBest,\n{rep_name}",
                "cta_a": "15-minute exploratory call this week",
                "subject_b": f"How {industry} teams are cutting lead review time by 80%",
                "body_b": f"Hi {first_name},\n\nI've been working with several {industry} companies that were spending 20+ hours per week manually reviewing and qualifying leads.\n\n{pain_line}After implementing AI-powered qualification, they're seeing:\n- 80% reduction in manual review time\n- 3x faster speed-to-lead\n- 25% improvement in conversion rates\n\nWould it be worth a quick conversation to explore if this could work for {company_name}?\n\nBest,\n{rep_name}",
                "cta_b": "Quick conversation about automation ROI",
            },
            "light_intro": {
                "subject_a": f"Thought you might find this useful, {first_name}",
                "body_a": f"Hi {first_name},\n\nI came across {company_name} and thought this might be relevant — we recently published a guide on how {industry} companies are using AI to qualify leads automatically.\n\nNo pitch, just genuinely useful content. Happy to share if you're interested.\n\nBest,\n{rep_name}",
                "cta_a": "Would you like me to send the guide?",
            },
            "connection_request": {
                "body_a": f"Hi {first_name}, I work with {industry} sales teams on lead qualification automation. Would love to connect — I share insights on sales ops and AI regularly. {signal_line.rstrip('. ')}",
                "cta_a": "Connect on LinkedIn",
            },
            "value_follow_up": {
                "subject_a": f"Following up — {talking_line[:50] if talking_line else 'sales automation insights'}",
                "body_a": f"Hi {first_name},\n\nJust following up on my previous note. {talking_line}\n\nI'd love to show you a quick demo of how this works in practice — it's usually an eye-opener for sales leaders.\n\nWould Tuesday or Wednesday work for a 15-minute walkthrough?\n\nBest,\n{rep_name}",
                "cta_a": "15-minute demo Tuesday or Wednesday",
            },
            "value_content": {
                "subject_a": f"{first_name}, new data on {industry} lead conversion",
                "body_a": f"Hi {first_name},\n\nWanted to share some interesting data we've collected from working with {industry} companies:\n\n- Companies using AI lead scoring see 35% higher conversion rates\n- Average speed-to-lead improves from 24 hours to under 5 minutes\n- Sales rep productivity increases by 40%\n\n{pain_line}Would any of this be relevant to what you're working on at {company_name}?\n\nBest,\n{rep_name}",
                "cta_a": "Is this relevant to your current priorities?",
            },
            "case_study": {
                "subject_a": f"How a {industry} company transformed their pipeline",
                "body_a": f"Hi {first_name},\n\nQuick case study I thought you'd find interesting — a {industry} company similar to {company_name} was struggling with lead prioritization.\n\nAfter implementing AI qualification:\n- Response time dropped from 8 hours to 12 minutes\n- Qualified pipeline grew 45% in one quarter\n- Reps spent 60% less time on unqualified leads\n\nHappy to walk you through the specifics if useful.\n\nBest,\n{rep_name}",
                "cta_a": "Want to see the full case study?",
            },
            "warm_call": {
                "body_a": f"Call script for {first_name} at {company_name}:\n\n• Opener: Reference their {enriched.raw_lead.source.value} inquiry and any specific message\n• Signal: {signal_line or 'General interest in sales automation'}\n• Pain probe: Ask about current lead qualification process and time spent\n• Value prop: AI-powered qualification saving 15-20 hrs/week\n• Ask: 15-minute demo this week\n• Objection prep: 'Just exploring' → offer no-commitment walkthrough",
                "cta_a": "Book 15-minute demo",
                "internal_notes": f"Lead score: {routing.lead_score.composite_score:.0f}/100. {routing.lead_score.temperature.value.upper()} lead. Decision maker: {contact.is_decision_maker if contact else 'Unknown'}.",
            },
            "educational_content": {
                "subject_a": f"3 trends reshaping {industry} sales ops",
                "body_a": f"Hi {first_name},\n\nI've been tracking some shifts in how {industry} companies are approaching sales operations:\n\n1. AI-first lead qualification is replacing manual scoring\n2. Speed-to-lead is becoming a competitive differentiator\n3. Revenue ops teams are consolidating tools to reduce stack bloat\n\nAny of these resonate with what you're seeing at {company_name}?\n\nBest,\n{rep_name}",
                "cta_a": "Any of these resonate?",
            },
            "soft_cta": {
                "subject_a": f"Checking in, {first_name}",
                "body_a": f"Hi {first_name},\n\nI shared a few things over the past couple weeks and wanted to check — is sales qualification automation something {company_name} is thinking about for this quarter?\n\nIf the timing isn't right, no worries at all. I'm happy to reconnect whenever it makes sense.\n\nBest,\n{rep_name}",
                "cta_a": "Is this on your radar this quarter?",
            },
            "check_in": {
                "subject_a": f"Still on your radar, {first_name}?",
                "body_a": f"Hi {first_name},\n\nJust a quick check-in — wanted to see if lead qualification automation is still something you're exploring at {company_name}.\n\nHappy to chat whenever the timing is right.\n\nBest,\n{rep_name}",
                "cta_a": "Open to reconnecting when timing works",
            },
            "final_attempt": {
                "body_a": f"Final call script for {first_name}:\n\n• Direct: 'I've reached out a couple times — want to make sure I'm not missing you'\n• Quick pitch: 30-second value prop on AI lead qualification\n• Binary close: 'Is this something worth 15 minutes, or should I check back next quarter?'\n• Graceful exit if no interest — leave door open",
                "cta_a": "15 minutes or check back next quarter?",
            },
        }

        steps = []
        for i, step_config in enumerate(cadence):
            purpose = step_config["purpose"]
            tpl = templates.get(purpose, templates.get("check_in"))

            # Variant A
            step_a = SequenceStep(
                step_number=i + 1,
                day=step_config["day"],
                touch_type=step_config["type"],
                subject=tpl.get("subject_a") if step_config["type"] == TouchType.EMAIL else None,
                body=tpl.get("body_a", ""),
                cta=tpl.get("cta_a"),
                internal_notes=tpl.get("internal_notes"),
                variant="A",
            )
            steps.append(step_a)

            # Variant B (only for first email)
            if i == 0 and step_config["type"] == TouchType.EMAIL and "subject_b" in tpl:
                step_b = SequenceStep(
                    step_number=i + 1,
                    day=step_config["day"],
                    touch_type=step_config["type"],
                    subject=tpl.get("subject_b"),
                    body=tpl.get("body_b", ""),
                    cta=tpl.get("cta_b"),
                    internal_notes="A/B variant — different hook angle",
                    variant="B",
                )
                steps.append(step_b)

        return steps


# ══════════════════════════════════════════════════════════
#  ENGAGEMENT AGENT
# ══════════════════════════════════════════════════════════

class EngagementAgent:
    """
    Orchestrates personalized outreach sequence generation.

    Usage:
        agent = EngagementAgent()
        plan = await agent.engage(routing_decision)
    """

    def __init__(self):
        self.generator = ContentGenerator()

    async def engage(self, routing: RoutingDecision) -> EngagementPlan:
        """
        Generate a complete engagement plan for a routed lead.

        Steps:
          1. Select strategy based on lead characteristics
          2. Generate personalized content (LLM or templates)
          3. Assemble the engagement plan
        """
        enriched = routing.lead_score.enriched_lead
        contact = enriched.contact
        company = enriched.company

        lead_email = enriched.raw_lead.email or "unknown"
        lead_name = contact.full_name if contact else "Unknown"
        company_name = company.legal_name if company else "Unknown"

        logger.info(f"[engagement] Generating plan for {lead_name} at {company_name}")

        # ── Step 1: Select strategy ──────────────────────
        strategy, strategy_reason = select_strategy(routing)

        # ── Step 2: Generate content ─────────────────────
        steps = await self.generator.generate_sequence(routing, strategy)

        # ── Step 3: Compute metadata ─────────────────────
        a_steps = [s for s in steps if s.variant == "A"]
        max_day = max(s.day for s in a_steps) if a_steps else 0

        # Personalization inputs for audit
        personalization = {}
        if enriched.ai_analysis:
            ai = enriched.ai_analysis
            personalization["buying_signals"] = [s.signal for s in ai.buying_signals[:3]] if ai.buying_signals else []
            personalization["pain_points"] = [p.pain_point for p in ai.pain_points[:3]] if ai.pain_points else []
            personalization["talking_points"] = ai.recommended_talking_points[:3] if ai.recommended_talking_points else []
            personalization["urgency"] = ai.urgency_assessment
        personalization["source"] = enriched.raw_lead.source.value
        personalization["message"] = enriched.raw_lead.message
        personalization["temperature"] = routing.lead_score.temperature.value
        personalization["composite_score"] = routing.lead_score.composite_score

        logger.info(
            f"[engagement] Strategy={strategy.value}, "
            f"Steps={len(a_steps)}, Duration={max_day}d, "
            f"Variants={len(steps) - len(a_steps)} B-variants"
        )

        return EngagementPlan(
            lead_email=lead_email,
            lead_name=lead_name,
            company_name=company_name,
            rep_name=routing.assigned_rep_name,
            rep_email=routing.assigned_rep_email,
            strategy=strategy,
            strategy_reasoning=strategy_reason,
            sequence_steps=steps,
            total_touches=len(a_steps),
            sequence_duration_days=max_day,
            personalization_inputs=personalization,
        )


# ── Convenience Function for n8n ─────────────────────────

async def generate_engagement(routing_data: dict) -> dict:
    """
    Convenience function for n8n Code nodes.
    Accepts a dict, returns a dict.
    """
    routing = RoutingDecision(**routing_data)
    agent = EngagementAgent()
    result = await agent.engage(routing)
    return result.model_dump(mode="json")
