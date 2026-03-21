"""
Qualification Agent — the second agent in the pipeline.

Responsibilities:
  1. Score leads across 4 dimensions (firmographic, demographic, behavioral, AI fit)
  2. Compute weighted composite score with bonuses and penalties
  3. Classify lead temperature (Hot / Warm / Cool / Cold)
  4. Make qualification decision (Qualified / Nurture / Disqualified / Needs Review)
  5. Output a LeadScore ready for the Routing Agent

Scoring Philosophy:
  - Each dimension scores 0-100 independently
  - Composite = weighted sum + bonus multipliers - penalty deductions
  - Hard disqualifiers can override scores (competitor, bad data, etc.)
  - Decision thresholds are configurable in settings.py
  - The AI fit dimension leverages the LLM analysis from the Research Agent
    rather than re-calling the LLM — no duplicate API costs
"""

import logging
from datetime import datetime
from typing import Optional

from config.schemas import (
    EnrichedLead,
    LeadScore,
    LeadTemperature,
    QualificationDecision,
    Seniority,
    CompanySize,
)
from config.settings import ICP, SCORING

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  DIMENSION SCORERS
# ══════════════════════════════════════════════════════════


class FirmographicScorer:
    """
    Scores company fit against the Ideal Customer Profile.

    Factors:
      - Industry match (0-30 pts)
      - Employee count in range (0-25 pts)
      - Funding stage match (0-15 pts)
      - Geography match (0-15 pts)
      - Tech stack overlap (0-15 pts)

    Total: 100 pts
    """

    def score(self, lead: EnrichedLead) -> tuple[float, dict]:
        """Returns (score, breakdown_dict)."""
        company = lead.company
        if not company:
            return 0.0, {"reason": "No company data available"}

        breakdown = {}
        total = 0.0

        # ── Industry Match (0-30) ────────────────────────
        industry_score = 0.0
        if company.industry:
            industry_lower = company.industry.lower()
            for target in ICP.target_industries:
                if target.lower() in industry_lower or industry_lower in target.lower():
                    industry_score = 30.0
                    break
            if industry_score == 0 and company.sub_industry:
                sub_lower = company.sub_industry.lower()
                for target in ICP.target_industries:
                    if target.lower() in sub_lower or sub_lower in target.lower():
                        industry_score = 20.0  # Partial match on sub-industry
                        break
        breakdown["industry"] = {"score": industry_score, "max": 30, "value": company.industry}
        total += industry_score

        # ── Employee Count (0-25) ────────────────────────
        emp_score = 0.0
        if company.employee_count:
            count = company.employee_count
            min_emp = ICP.min_employee_count
            max_emp = ICP.max_employee_count

            if min_emp <= count <= max_emp:
                # Perfect range — score based on sweet spot (middle of range)
                sweet_spot = (min_emp + max_emp) / 2
                distance = abs(count - sweet_spot) / sweet_spot
                emp_score = 25.0 * max(0, 1 - distance * 0.5)
            elif count < min_emp:
                # Below range — partial credit if close
                ratio = count / min_emp
                emp_score = 25.0 * ratio * 0.5 if ratio > 0.3 else 0.0
            else:
                # Above range — partial credit for large companies
                emp_score = 12.0  # Large companies can still be ICP
        breakdown["employee_count"] = {"score": round(emp_score, 1), "max": 25, "value": company.employee_count}
        total += emp_score

        # ── Funding Stage (0-15) ─────────────────────────
        funding_score = 0.0
        if company.funding_stage:
            stage_lower = company.funding_stage.lower()
            for target in ICP.preferred_funding_stages:
                if target.lower() in stage_lower or stage_lower in target.lower():
                    funding_score = 15.0
                    break
            if funding_score == 0 and company.funding_stage:
                funding_score = 5.0  # Has funding info but not preferred stage
        breakdown["funding"] = {"score": funding_score, "max": 15, "value": company.funding_stage}
        total += funding_score

        # ── Geography (0-15) ─────────────────────────────
        geo_score = 0.0
        if company.headquarters_country:
            country = company.headquarters_country.upper()
            if country in [c.upper() for c in ICP.target_countries]:
                geo_score = 15.0
            else:
                geo_score = 5.0  # International but not target
        breakdown["geography"] = {"score": geo_score, "max": 15, "value": company.headquarters_country}
        total += geo_score

        # ── Tech Stack Overlap (0-15) ────────────────────
        tech_score = 0.0
        if company.technologies:
            tech_lower = {t.lower() for t in company.technologies}
            target_lower = {t.lower() for t in ICP.positive_tech_signals}
            overlap = tech_lower & target_lower
            if overlap:
                # Scale: 1 match = 5pts, 2 = 9, 3 = 12, 4+ = 15
                tech_score = min(15.0, 5.0 + (len(overlap) - 1) * 3.5)
        breakdown["tech_stack"] = {
            "score": round(tech_score, 1),
            "max": 15,
            "matches": list(overlap) if company.technologies else [],
        }
        total += tech_score

        return round(min(total, 100.0), 1), breakdown


class DemographicScorer:
    """
    Scores contact fit against the buyer persona.

    Factors:
      - Seniority level (0-30 pts)
      - Title match (0-25 pts)
      - Department match (0-20 pts)
      - Decision maker status (0-15 pts)
      - Contact completeness (0-10 pts)

    Total: 100 pts
    """

    def score(self, lead: EnrichedLead) -> tuple[float, dict]:
        contact = lead.contact
        if not contact:
            return 0.0, {"reason": "No contact data available"}

        breakdown = {}
        total = 0.0

        # ── Seniority (0-30) ─────────────────────────────
        seniority_scores = {
            Seniority.C_LEVEL: 30.0,
            Seniority.VP: 28.0,
            Seniority.DIRECTOR: 22.0,
            Seniority.MANAGER: 14.0,
            Seniority.SENIOR_IC: 8.0,
            Seniority.IC: 4.0,
            Seniority.UNKNOWN: 0.0,
        }
        sen_score = seniority_scores.get(contact.seniority, 0.0)
        breakdown["seniority"] = {"score": sen_score, "max": 30, "value": contact.seniority.value}
        total += sen_score

        # ── Title Match (0-25) ───────────────────────────
        title_score = 0.0
        if contact.normalized_title:
            title_lower = contact.normalized_title.lower()
            # Exact match
            for target in ICP.target_titles:
                if target.lower() == title_lower:
                    title_score = 25.0
                    break
            # Partial match (keywords overlap)
            if title_score == 0:
                title_words = set(title_lower.split())
                for target in ICP.target_titles:
                    target_words = set(target.lower().split())
                    overlap = title_words & target_words
                    # Meaningful overlap (not just "of", "the", etc.)
                    meaningful = overlap - {"of", "the", "and", "a", "in", "for"}
                    if len(meaningful) >= 1:
                        title_score = max(title_score, 15.0)
                    if len(meaningful) >= 2:
                        title_score = max(title_score, 20.0)
        breakdown["title"] = {"score": title_score, "max": 25, "value": contact.normalized_title}
        total += title_score

        # ── Department Match (0-20) ──────────────────────
        dept_score = 0.0
        if contact.department:
            dept_lower = contact.department.lower()
            for target in ICP.target_departments:
                if target.lower() in dept_lower or dept_lower in target.lower():
                    dept_score = 20.0
                    break
            if dept_score == 0:
                # Adjacent departments get partial credit
                adjacent = ["marketing", "operations", "strategy", "customer success"]
                if any(adj in dept_lower for adj in adjacent):
                    dept_score = 8.0
        breakdown["department"] = {"score": dept_score, "max": 20, "value": contact.department}
        total += dept_score

        # ── Decision Maker (0-15) ────────────────────────
        dm_score = 15.0 if contact.is_decision_maker else 0.0
        breakdown["decision_maker"] = {"score": dm_score, "max": 15, "value": contact.is_decision_maker}
        total += dm_score

        # ── Contact Completeness (0-10) ──────────────────
        fields = [
            contact.full_name, contact.normalized_title, contact.department,
            contact.linkedin_url, contact.phone_direct, contact.location_city,
        ]
        filled = sum(1 for f in fields if f)
        completeness_score = round((filled / len(fields)) * 10, 1)
        breakdown["completeness"] = {"score": completeness_score, "max": 10, "filled": filled, "total": len(fields)}
        total += completeness_score

        return round(min(total, 100.0), 1), breakdown


class BehavioralScorer:
    """
    Scores lead engagement signals and intent indicators.

    Factors:
      - Lead source quality (0-25 pts)
      - Message/inquiry quality (0-30 pts)
      - UTM campaign signals (0-20 pts)
      - Page intent (0-15 pts)
      - Timing signals (0-10 pts)

    Total: 100 pts
    """

    # Source quality tiers
    SOURCE_SCORES = {
        "referral": 25.0,
        "web_form": 20.0,
        "chat_widget": 18.0,
        "linkedin": 15.0,
        "email": 12.0,
        "event": 10.0,
        "api": 8.0,
    }

    # High-intent keywords in messages
    HIGH_INTENT_KEYWORDS = [
        "pricing", "demo", "trial", "buy", "purchase", "implement",
        "migrate", "replace", "budget", "timeline", "roi", "cost",
        "when can", "how soon", "ready to", "looking to",
    ]
    MEDIUM_INTENT_KEYWORDS = [
        "interested", "learn more", "information", "compare",
        "evaluate", "considering", "exploring", "options",
        "automate", "streamline", "improve", "optimize",
    ]

    # High-intent page patterns
    HIGH_INTENT_PAGES = [
        "/pricing", "/demo", "/trial", "/contact-sales",
        "/request-demo", "/get-started", "/book-a-call",
    ]
    MEDIUM_INTENT_PAGES = [
        "/solutions", "/product", "/features", "/case-studies",
        "/customers", "/integrations", "/enterprise",
    ]

    def score(self, lead: EnrichedLead) -> tuple[float, dict]:
        raw = lead.raw_lead
        breakdown = {}
        total = 0.0

        # ── Source Quality (0-25) ────────────────────────
        source_score = self.SOURCE_SCORES.get(raw.source.value, 5.0)
        breakdown["source"] = {"score": source_score, "max": 25, "value": raw.source.value}
        total += source_score

        # ── Message Intent (0-30) ────────────────────────
        msg_score = 0.0
        intent_signals = []
        if raw.message:
            msg_lower = raw.message.lower()
            high_matches = [kw for kw in self.HIGH_INTENT_KEYWORDS if kw in msg_lower]
            med_matches = [kw for kw in self.MEDIUM_INTENT_KEYWORDS if kw in msg_lower]

            if high_matches:
                msg_score = min(30.0, 20.0 + len(high_matches) * 3)
                intent_signals = high_matches[:3]
            elif med_matches:
                msg_score = min(20.0, 10.0 + len(med_matches) * 3)
                intent_signals = med_matches[:3]
            elif len(raw.message) > 50:
                msg_score = 8.0  # Long message shows engagement
            else:
                msg_score = 3.0  # At least they wrote something
        breakdown["message_intent"] = {"score": round(msg_score, 1), "max": 30, "signals": intent_signals}
        total += msg_score

        # ── UTM Signals (0-20) ───────────────────────────
        utm_score = 0.0
        if raw.utm_source:
            # Paid channels show higher intent
            if raw.utm_medium in ("cpc", "paid", "ppc"):
                utm_score += 12.0
            elif raw.utm_medium in ("email", "social"):
                utm_score += 8.0
            else:
                utm_score += 4.0

            # Campaign name signals
            if raw.utm_campaign:
                campaign_lower = raw.utm_campaign.lower()
                if any(kw in campaign_lower for kw in ["demo", "trial", "pricing", "bottom-funnel"]):
                    utm_score += 8.0
                elif any(kw in campaign_lower for kw in ["solution", "product", "comparison"]):
                    utm_score += 5.0
        breakdown["utm"] = {
            "score": min(round(utm_score, 1), 20.0),
            "max": 20,
            "source": raw.utm_source,
            "medium": raw.utm_medium,
            "campaign": raw.utm_campaign,
        }
        total += min(utm_score, 20.0)

        # ── Page Intent (0-15) ───────────────────────────
        page_score = 0.0
        if raw.page_url:
            url_lower = raw.page_url.lower()
            if any(p in url_lower for p in self.HIGH_INTENT_PAGES):
                page_score = 15.0
            elif any(p in url_lower for p in self.MEDIUM_INTENT_PAGES):
                page_score = 10.0
            else:
                page_score = 3.0  # At least visited the site
        breakdown["page_intent"] = {"score": page_score, "max": 15, "value": raw.page_url}
        total += page_score

        # ── Timing (0-10) ────────────────────────────────
        # Business hours and weekday submissions score higher
        timing_score = 5.0  # Base score
        if raw.timestamp:
            hour = raw.timestamp.hour
            weekday = raw.timestamp.weekday()
            if 0 <= weekday <= 4 and 8 <= hour <= 18:
                timing_score = 10.0  # Business hours
            elif 0 <= weekday <= 4:
                timing_score = 7.0   # Weekday, off-hours
        breakdown["timing"] = {"score": timing_score, "max": 10}
        total += timing_score

        return round(min(total, 100.0), 1), breakdown


class AIFitScorer:
    """
    Translates the Research Agent's AI analysis into a scoring dimension.

    This does NOT re-call the LLM — it uses the structured output
    that was already computed during enrichment.

    Factors:
      - ICP fit score from LLM (0-40 pts)
      - Buying signal strength (0-25 pts)
      - Pain point relevance (0-20 pts)
      - Urgency assessment (0-15 pts)

    Total: 100 pts
    """

    def score(self, lead: EnrichedLead) -> tuple[float, dict]:
        ai = lead.ai_analysis
        if not ai:
            # No AI analysis available — return neutral score based on data confidence
            neutral = lead.overall_data_confidence * 40  # Scale 0-40
            return round(neutral, 1), {"reason": "No AI analysis — using data confidence as proxy"}

        breakdown = {}
        total = 0.0

        # ── ICP Fit Score (0-40) ─────────────────────────
        icp_score = ai.icp_fit_score * 40.0
        breakdown["icp_fit"] = {"score": round(icp_score, 1), "max": 40, "raw": ai.icp_fit_score}
        total += icp_score

        # ── Buying Signals (0-25) ────────────────────────
        signal_score = 0.0
        if ai.buying_signals:
            strength_values = {"strong": 10.0, "moderate": 5.0, "weak": 2.0}
            for signal in ai.buying_signals[:5]:  # Cap at 5 signals
                signal_score += strength_values.get(signal.strength, 2.0)
            signal_score = min(signal_score, 25.0)
        breakdown["buying_signals"] = {
            "score": round(signal_score, 1),
            "max": 25,
            "count": len(ai.buying_signals) if ai.buying_signals else 0,
        }
        total += signal_score

        # ── Pain Point Relevance (0-20) ──────────────────
        pain_score = 0.0
        if ai.pain_points:
            relevance_values = {"high": 8.0, "medium": 4.0, "low": 1.5}
            for pp in ai.pain_points[:5]:
                pain_score += relevance_values.get(pp.relevance_to_product, 1.5)
            pain_score = min(pain_score, 20.0)
        breakdown["pain_points"] = {
            "score": round(pain_score, 1),
            "max": 20,
            "count": len(ai.pain_points) if ai.pain_points else 0,
        }
        total += pain_score

        # ── Urgency (0-15) ───────────────────────────────
        urgency_map = {
            "immediate": 15.0,
            "near_term": 10.0,
            "exploratory": 5.0,
            "not_ready": 1.0,
        }
        urgency_score = urgency_map.get(ai.urgency_assessment, 3.0)
        breakdown["urgency"] = {"score": urgency_score, "max": 15, "value": ai.urgency_assessment}
        total += urgency_score

        return round(min(total, 100.0), 1), breakdown


# ══════════════════════════════════════════════════════════
#  QUALIFICATION AGENT
# ══════════════════════════════════════════════════════════


class QualificationAgent:
    """
    Orchestrates the full qualification pipeline.

    Usage:
        agent = QualificationAgent()
        lead_score = agent.qualify(enriched_lead)
    """

    def __init__(self):
        self.firmographic = FirmographicScorer()
        self.demographic = DemographicScorer()
        self.behavioral = BehavioralScorer()
        self.ai_fit = AIFitScorer()

    def qualify(self, lead: EnrichedLead) -> LeadScore:
        """
        Run the full qualification pipeline.
        Returns a LeadScore with all dimensions, composite, temperature, and decision.
        """
        logger.info(f"[qualification] Scoring {lead.raw_lead.email or 'unknown'}")

        # ── Step 1: Score all dimensions ─────────────────
        firm_score, firm_breakdown = self.firmographic.score(lead)
        demo_score, demo_breakdown = self.demographic.score(lead)
        behav_score, behav_breakdown = self.behavioral.score(lead)
        ai_score, ai_breakdown = self.ai_fit.score(lead)

        # ── Step 2: Compute composite score ──────────────
        composite = self._compute_composite(
            firm_score, demo_score, behav_score, ai_score, lead
        )

        # ── Step 3: Check hard disqualifiers ─────────────
        disqualified, disqual_reason = self._check_disqualifiers(lead)

        # ── Step 4: Classify temperature ─────────────────
        temperature = self._classify_temperature(composite)

        # ── Step 5: Make decision ────────────────────────
        if disqualified:
            decision = QualificationDecision.DISQUALIFIED
            decision_reasoning = disqual_reason
        else:
            decision, decision_reasoning = self._make_decision(
                composite, temperature, lead, firm_breakdown, demo_breakdown
            )

        # ── Build full reasoning ─────────────────────────
        full_reasoning = self._build_reasoning(
            firm_score, firm_breakdown,
            demo_score, demo_breakdown,
            behav_score, behav_breakdown,
            ai_score, ai_breakdown,
            composite, temperature, decision, decision_reasoning,
        )

        logger.info(
            f"[qualification] Result: score={composite:.1f} "
            f"temp={temperature.value} decision={decision.value}"
        )

        return LeadScore(
            enriched_lead=lead,
            firmographic_score=firm_score,
            demographic_score=demo_score,
            behavioral_score=behav_score,
            ai_fit_score=ai_score,
            composite_score=round(composite, 1),
            temperature=temperature,
            decision=decision,
            decision_reasoning=full_reasoning,
            scored_at=datetime.utcnow(),
        )

    # ── Composite Score Calculation ──────────────────────

    def _compute_composite(
        self,
        firm: float,
        demo: float,
        behav: float,
        ai: float,
        lead: EnrichedLead,
    ) -> float:
        """
        Weighted composite with bonus multipliers and penalties.

        Base = weighted sum of 4 dimensions
        Bonuses: decision maker + high seniority, strong tech overlap, high urgency
        Penalties: free email, very low confidence, missing company data
        """
        # Base weighted sum
        base = (
            firm * SCORING.firmographic +
            demo * SCORING.demographic +
            behav * SCORING.behavioral +
            ai * SCORING.ai_fit
        )

        # ── Bonus multipliers ────────────────────────────
        bonus = 0.0

        # Decision maker at C-level or VP with strong company fit
        if lead.contact and lead.contact.is_decision_maker:
            if lead.contact.seniority in [Seniority.C_LEVEL, Seniority.VP]:
                if firm >= 60:
                    bonus += 8.0  # High-value decision maker at fitting company

        # Multiple strong buying signals
        if lead.ai_analysis and lead.ai_analysis.buying_signals:
            strong_signals = [s for s in lead.ai_analysis.buying_signals if s.strength == "strong"]
            if len(strong_signals) >= 2:
                bonus += 5.0

        # Immediate urgency
        if lead.ai_analysis and lead.ai_analysis.urgency_assessment == "immediate":
            bonus += 5.0

        # Company actively hiring in sales (growth signal)
        if lead.company and lead.company.hiring_signals:
            sales_hiring = [h for h in lead.company.hiring_signals
                          if any(kw in h.lower() for kw in ["sales", "revenue", "growth", "account exec"])]
            if sales_hiring:
                bonus += 3.0

        # ── Penalty deductions ───────────────────────────
        penalty = 0.0

        # Free email address
        if "free_email" in lead.flags:
            penalty += 10.0

        # Very low enrichment confidence
        if lead.overall_data_confidence < 0.3:
            penalty += 8.0
        elif lead.overall_data_confidence < 0.5:
            penalty += 4.0

        # Missing company data entirely
        if not lead.company:
            penalty += 12.0

        # No contact info beyond email
        if lead.contact and not lead.contact.phone_direct and not lead.contact.linkedin_url:
            penalty += 3.0

        composite = base + bonus - penalty
        return max(0.0, min(100.0, composite))

    # ── Hard Disqualifiers ───────────────────────────────

    def _check_disqualifiers(self, lead: EnrichedLead) -> tuple[bool, str]:
        """
        Check for hard disqualifiers that override scoring.
        Returns (is_disqualified, reason).
        """
        # Competitor domain
        if lead.company and lead.company.domain:
            if lead.company.domain.lower() in [d.lower() for d in ICP.competitor_domains]:
                return True, f"Competitor domain detected: {lead.company.domain}"

        # No identifiers at all
        if "no_identifiers" in lead.flags:
            return True, "No email or company domain — cannot process"

        # Company way too small (solo operator, not a business)
        if lead.company and lead.company.employee_count:
            if lead.company.employee_count < 5 and lead.company.funding_stage is None:
                return True, f"Company too small ({lead.company.employee_count} employees, no funding)"

        return False, ""

    # ── Temperature Classification ───────────────────────

    def _classify_temperature(self, composite: float) -> LeadTemperature:
        """Classify lead temperature from composite score."""
        if composite >= SCORING.hot_threshold:
            return LeadTemperature.HOT
        elif composite >= SCORING.warm_threshold:
            return LeadTemperature.WARM
        elif composite >= SCORING.cool_threshold:
            return LeadTemperature.COOL
        else:
            return LeadTemperature.COLD

    # ── Qualification Decision ───────────────────────────

    def _make_decision(
        self,
        composite: float,
        temperature: LeadTemperature,
        lead: EnrichedLead,
        firm_breakdown: dict,
        demo_breakdown: dict,
    ) -> tuple[QualificationDecision, str]:
        """
        Make the qualification decision based on score and contextual rules.

        Decision matrix:
          - composite >= 75 → Qualified (auto)
          - composite >= 45 → Depends on context:
              - Decision maker + good company fit → Qualified
              - Missing key data → Needs Review
              - Everything else → Nurture
          - composite < 45 → Disqualified (unless needs review)
        """
        # ── Auto-qualify (high score) ────────────────────
        if composite >= SCORING.auto_qualify_threshold:
            return QualificationDecision.QUALIFIED, "High composite score — auto-qualified"

        # ── Middle zone (contextual decisions) ───────────
        if composite >= SCORING.nurture_threshold:
            # Override to Qualified if decision maker at good company
            if (lead.contact
                and lead.contact.is_decision_maker
                and lead.contact.seniority in [Seniority.C_LEVEL, Seniority.VP]
                and lead.company
                and firm_breakdown.get("industry", {}).get("score", 0) >= 20):
                return (
                    QualificationDecision.QUALIFIED,
                    "Decision maker at ICP-matching company — upgraded from nurture"
                )

            # Flag for review if data is incomplete
            if lead.needs_human_review or lead.overall_data_confidence < 0.5:
                return (
                    QualificationDecision.REVIEW,
                    "Score in middle range with incomplete data — human review needed"
                )

            # Default middle zone → Nurture
            return QualificationDecision.NURTURE, "Score in nurture range — enroll in education sequence"

        # ── Below threshold ──────────────────────────────
        # Give benefit of doubt if we have very little data
        if lead.overall_data_confidence < 0.3 and "no_identifiers" not in lead.flags:
            return (
                QualificationDecision.REVIEW,
                "Low score but also low data confidence — review before discarding"
            )

        return QualificationDecision.DISQUALIFIED, "Score below qualification threshold"

    # ── Reasoning Builder ────────────────────────────────

    def _build_reasoning(
        self,
        firm_score, firm_breakdown,
        demo_score, demo_breakdown,
        behav_score, behav_breakdown,
        ai_score, ai_breakdown,
        composite, temperature, decision, decision_reason,
    ) -> str:
        """Build a human-readable explanation of the scoring decision."""
        lines = [
            f"QUALIFICATION SUMMARY",
            f"{'=' * 50}",
            f"",
            f"Composite Score: {composite:.1f}/100 → {temperature.value.upper()}",
            f"Decision: {decision.value.upper()} — {decision_reason}",
            f"",
            f"DIMENSION SCORES",
            f"{'-' * 50}",
            f"  Firmographic:  {firm_score:5.1f}/100 (weight: {SCORING.firmographic:.0%})",
            f"  Demographic:   {demo_score:5.1f}/100 (weight: {SCORING.demographic:.0%})",
            f"  Behavioral:    {behav_score:5.1f}/100 (weight: {SCORING.behavioral:.0%})",
            f"  AI Fit:        {ai_score:5.1f}/100 (weight: {SCORING.ai_fit:.0%})",
            f"",
            f"FIRMOGRAPHIC BREAKDOWN",
            f"{'-' * 50}",
        ]

        for key, val in firm_breakdown.items():
            if isinstance(val, dict) and "score" in val:
                lines.append(f"  {key:<18s}: {val['score']:5.1f}/{val['max']}  ({val.get('value', '')})")

        lines.extend([
            f"",
            f"DEMOGRAPHIC BREAKDOWN",
            f"{'-' * 50}",
        ])
        for key, val in demo_breakdown.items():
            if isinstance(val, dict) and "score" in val:
                lines.append(f"  {key:<18s}: {val['score']:5.1f}/{val['max']}  ({val.get('value', '')})")

        lines.extend([
            f"",
            f"BEHAVIORAL BREAKDOWN",
            f"{'-' * 50}",
        ])
        for key, val in behav_breakdown.items():
            if isinstance(val, dict) and "score" in val:
                lines.append(f"  {key:<18s}: {val['score']:5.1f}/{val['max']}  ({val.get('value', '')})")

        lines.extend([
            f"",
            f"AI FIT BREAKDOWN",
            f"{'-' * 50}",
        ])
        for key, val in ai_breakdown.items():
            if isinstance(val, dict) and "score" in val:
                lines.append(f"  {key:<18s}: {val['score']:5.1f}/{val['max']}")
            elif key == "reason":
                lines.append(f"  {val}")

        return "\n".join(lines)


# ── Convenience Function for n8n ─────────────────────────

def qualify_lead(enriched_lead_data: dict) -> dict:
    """
    Convenience function for n8n Code nodes.
    Accepts a dict, returns a dict.
    """
    lead = EnrichedLead(**enriched_lead_data)
    agent = QualificationAgent()
    result = agent.qualify(lead)
    return result.model_dump(mode="json")
