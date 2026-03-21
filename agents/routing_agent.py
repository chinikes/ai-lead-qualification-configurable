"""
Routing Agent — the third agent in the pipeline.

Responsibilities:
  1. Determine territory based on lead geography
  2. Select rep via round-robin within territory (respecting capacity)
  3. Set priority and SLA based on lead temperature
  4. Handle overflow (all reps at capacity → queue or escalate)
  5. Output a RoutingDecision ready for CRM sync and notifications

Design decisions:
  - Round-robin state would be Redis/DB in production; here it's in-memory
  - Capacity limits prevent rep overload (configurable per-day max)
  - Hot leads can "burst" past capacity limits with a flag
  - Unroutable leads (no territory match) go to a catch-all queue
  - SLA timers are set but enforcement happens in a separate n8n workflow
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from config.schemas import (
    LeadScore,
    LeadTemperature,
    QualificationDecision,
    RoutingDecision,
)
from config.settings import ROUTING

logger = logging.getLogger(__name__)


# ── Sales Rep Registry ───────────────────────────────────

class SalesRep:
    """Represents a sales rep with capacity tracking."""

    def __init__(
        self,
        rep_id: str,
        name: str,
        email: str,
        territories: list[str],
        max_daily_leads: int = 25,
        specialties: list[str] | None = None,
        is_active: bool = True,
    ):
        self.rep_id = rep_id
        self.name = name
        self.email = email
        self.territories = territories
        self.max_daily_leads = max_daily_leads
        self.specialties = specialties or []
        self.is_active = is_active
        self.assigned_today: int = 0
        self.last_assigned_at: Optional[datetime] = None

    @property
    def has_capacity(self) -> bool:
        return self.assigned_today < self.max_daily_leads

    @property
    def utilization(self) -> float:
        return self.assigned_today / self.max_daily_leads if self.max_daily_leads > 0 else 1.0

    def assign(self):
        """Record a lead assignment."""
        self.assigned_today += 1
        self.last_assigned_at = datetime.utcnow()

    def reset_daily(self):
        """Reset daily counters (called by a scheduled job)."""
        self.assigned_today = 0


# ── Default Rep Roster ───────────────────────────────────

def build_default_roster() -> list[SalesRep]:
    """Build the default sales team roster from config."""
    return [
        SalesRep(
            rep_id="rep_001",
            name="Jessica Torres",
            email="jessica.torres@company.com",
            territories=["us_west"],
            specialties=["saas", "technology"],
            max_daily_leads=25,
        ),
        SalesRep(
            rep_id="rep_002",
            name="Marcus Johnson",
            email="marcus.johnson@company.com",
            territories=["us_west"],
            specialties=["healthcare", "fintech"],
            max_daily_leads=25,
        ),
        SalesRep(
            rep_id="rep_003",
            name="Aisha Patel",
            email="aisha.patel@company.com",
            territories=["us_east"],
            specialties=["e-commerce", "professional services"],
            max_daily_leads=20,
        ),
        SalesRep(
            rep_id="rep_004",
            name="David Kim",
            email="david.kim@company.com",
            territories=["us_east"],
            specialties=["saas", "technology"],
            max_daily_leads=20,
        ),
        SalesRep(
            rep_id="rep_005",
            name="Rachel Green",
            email="rachel.green@company.com",
            territories=["us_central"],
            specialties=["manufacturing", "professional services"],
            max_daily_leads=25,
        ),
        SalesRep(
            rep_id="rep_006",
            name="Olivier Dubois",
            email="olivier.dubois@company.com",
            territories=["international"],
            specialties=["saas", "technology", "financial services"],
            max_daily_leads=20,
        ),
    ]


# ── Territory Resolver ───────────────────────────────────

US_STATE_TERRITORY = {}
for territory, config in ROUTING.territories.items():
    for state in config.get("states", []):
        US_STATE_TERRITORY[state.upper()] = territory


def resolve_territory(
    country: Optional[str],
    state: Optional[str],
    city: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve a lead's geography to a territory name.

    Priority:
      1. US state → specific territory
      2. International country → 'international'
      3. None → unresolved
    """
    if not country:
        return None

    country_upper = country.upper()

    # US leads: resolve by state
    if country_upper in ("US", "USA", "UNITED STATES"):
        if state:
            state_upper = state.upper()
            # Direct state mapping
            territory = US_STATE_TERRITORY.get(state_upper)
            if territory:
                return territory
            # Fallback: try 2-letter abbreviation
            if len(state_upper) == 2:
                territory = US_STATE_TERRITORY.get(state_upper)
                if territory:
                    return territory
        # US but unknown state → round-robin across US territories
        return "us_central"  # Default US fallback

    # International
    intl_countries = ROUTING.territories.get("international", {}).get("countries", [])
    if country_upper in [c.upper() for c in intl_countries]:
        return "international"

    # Unknown country → international catch-all
    return "international"


# ══════════════════════════════════════════════════════════
#  ROUTING AGENT
# ══════════════════════════════════════════════════════════


class RoutingAgent:
    """
    Routes qualified leads to the appropriate sales rep.

    Features:
      - Territory-based assignment
      - Round-robin within territory
      - Capacity limits per rep
      - Specialty matching (bonus, not requirement)
      - Hot lead priority override
      - Overflow handling

    Usage:
        agent = RoutingAgent()
        routing = agent.route(lead_score)
    """

    def __init__(self, roster: list[SalesRep] | None = None):
        self.roster = roster or build_default_roster()
        # Round-robin index per territory
        self._rr_index: dict[str, int] = defaultdict(int)

    def route(self, lead_score: LeadScore) -> RoutingDecision:
        """
        Route a scored lead to a sales rep.

        Only routes leads with decision = qualified.
        Nurture/review/disqualified leads get no assignment.
        """
        enriched = lead_score.enriched_lead

        # ── Non-qualified leads don't get routed ─────────
        if lead_score.decision not in (
            QualificationDecision.QUALIFIED,
            QualificationDecision.REVIEW,
        ):
            logger.info(f"[routing] Skipping {enriched.raw_lead.email} — {lead_score.decision.value}")
            return RoutingDecision(
                lead_score=lead_score,
                routing_reason=f"Not routed — decision is {lead_score.decision.value}",
                priority=self._determine_priority(lead_score.temperature),
                sla_response_minutes=self._determine_sla(lead_score.temperature),
                routed_at=datetime.utcnow(),
            )

        # ── Step 1: Resolve territory ────────────────────
        territory = self._resolve_lead_territory(enriched)

        # ── Step 2: Find available reps in territory ─────
        available_reps = self._get_territory_reps(territory)

        # ── Step 3: Apply specialty bonus scoring ────────
        if available_reps and enriched.company and enriched.company.industry:
            available_reps = self._sort_by_specialty(
                available_reps, enriched.company.industry
            )

        # ── Step 4: Round-robin selection ────────────────
        selected_rep = self._round_robin_select(available_reps, territory)

        # ── Step 5: Handle hot lead burst ────────────────
        if not selected_rep and lead_score.temperature == LeadTemperature.HOT:
            selected_rep = self._burst_select(territory)
            if selected_rep:
                logger.warning(
                    f"[routing] Hot lead burst: {selected_rep.name} over capacity"
                )

        # ── Step 6: Handle no available rep ──────────────
        if not selected_rep:
            logger.warning(f"[routing] No rep available for territory={territory}")
            return RoutingDecision(
                lead_score=lead_score,
                territory=territory,
                routing_reason=f"No available rep in {territory} — queued for next available",
                priority=self._determine_priority(lead_score.temperature),
                sla_response_minutes=self._determine_sla(lead_score.temperature),
                routed_at=datetime.utcnow(),
            )

        # ── Step 7: Assign and record ────────────────────
        selected_rep.assign()
        priority = self._determine_priority(lead_score.temperature)
        sla = self._determine_sla(lead_score.temperature)

        logger.info(
            f"[routing] {enriched.raw_lead.email} → {selected_rep.name} "
            f"(territory={territory}, priority={priority}, sla={sla}min)"
        )

        return RoutingDecision(
            lead_score=lead_score,
            assigned_rep_id=selected_rep.rep_id,
            assigned_rep_name=selected_rep.name,
            assigned_rep_email=selected_rep.email,
            territory=territory,
            routing_reason=self._build_routing_reason(
                selected_rep, territory, lead_score
            ),
            priority=priority,
            sla_response_minutes=sla,
            routed_at=datetime.utcnow(),
        )

    # ── Territory Resolution ─────────────────────────────

    def _resolve_lead_territory(self, enriched) -> Optional[str]:
        """Determine territory from company or contact geography."""
        # Try company HQ first
        if enriched.company:
            territory = resolve_territory(
                enriched.company.headquarters_country,
                enriched.company.headquarters_state,
                enriched.company.headquarters_city,
            )
            if territory:
                return territory

        # Try contact location
        if enriched.contact:
            territory = resolve_territory(
                enriched.contact.location_country,
                enriched.contact.location_state,
                enriched.contact.location_city,
            )
            if territory:
                return territory

        return None

    # ── Rep Selection ────────────────────────────────────

    def _get_territory_reps(self, territory: Optional[str]) -> list[SalesRep]:
        """Get active reps with capacity in the given territory."""
        if not territory:
            # No territory → all active reps are candidates
            return [r for r in self.roster if r.is_active and r.has_capacity]

        return [
            r for r in self.roster
            if r.is_active and r.has_capacity and territory in r.territories
        ]

    def _sort_by_specialty(
        self, reps: list[SalesRep], industry: str
    ) -> list[SalesRep]:
        """
        Sort reps by specialty match (matching reps first).
        Within each group, preserve existing order for round-robin fairness.
        """
        industry_lower = industry.lower()
        matching = [r for r in reps if any(s in industry_lower or industry_lower in s for s in r.specialties)]
        non_matching = [r for r in reps if r not in matching]
        return matching + non_matching

    def _round_robin_select(
        self, reps: list[SalesRep], territory: Optional[str]
    ) -> Optional[SalesRep]:
        """Select next rep via round-robin within territory."""
        if not reps:
            return None

        key = territory or "_global"
        idx = self._rr_index[key] % len(reps)
        selected = reps[idx]
        self._rr_index[key] = idx + 1

        return selected

    def _burst_select(self, territory: Optional[str]) -> Optional[SalesRep]:
        """
        For hot leads: select least-loaded rep even if at capacity.
        Only used when no rep has capacity.
        """
        if territory:
            candidates = [
                r for r in self.roster
                if r.is_active and territory in r.territories
            ]
        else:
            candidates = [r for r in self.roster if r.is_active]

        if not candidates:
            return None

        # Pick the rep with lowest utilization
        return min(candidates, key=lambda r: r.utilization)

    # ── Priority & SLA ───────────────────────────────────

    def _determine_priority(self, temperature: LeadTemperature) -> str:
        """Map temperature to priority level."""
        priority_map = {
            LeadTemperature.HOT: "p0_immediate",
            LeadTemperature.WARM: "p1_today",
            LeadTemperature.COOL: "p2_this_week",
            LeadTemperature.COLD: "p3_queue",
        }
        return priority_map.get(temperature, "p3_queue")

    def _determine_sla(self, temperature: LeadTemperature) -> int:
        """Map temperature to SLA in minutes."""
        return ROUTING.sla_minutes.get(temperature.value, 1440)

    # ── Reasoning ────────────────────────────────────────

    def _build_routing_reason(
        self, rep: SalesRep, territory: Optional[str], score: LeadScore
    ) -> str:
        """Build human-readable routing explanation."""
        parts = []
        parts.append(f"Assigned to {rep.name} ({rep.email})")

        if territory:
            parts.append(f"Territory: {territory}")

        industry = score.enriched_lead.company.industry if score.enriched_lead.company else None
        if industry and any(s in industry.lower() for s in rep.specialties):
            parts.append(f"Specialty match: {industry}")

        parts.append(f"Rep utilization: {rep.assigned_today}/{rep.max_daily_leads}")
        parts.append(f"Priority: {self._determine_priority(score.temperature)}")
        parts.append(f"SLA: {self._determine_sla(score.temperature)} minutes")

        return " | ".join(parts)

    # ── Admin Methods ────────────────────────────────────

    def get_rep_stats(self) -> list[dict]:
        """Get current stats for all reps (for dashboard)."""
        return [
            {
                "rep_id": r.rep_id,
                "name": r.name,
                "territories": r.territories,
                "assigned_today": r.assigned_today,
                "max_daily": r.max_daily_leads,
                "utilization": round(r.utilization * 100, 1),
                "has_capacity": r.has_capacity,
                "is_active": r.is_active,
                "last_assigned": r.last_assigned_at.isoformat() if r.last_assigned_at else None,
            }
            for r in self.roster
        ]

    def reset_all_daily_counts(self):
        """Reset all rep daily counters. Called by scheduler at midnight."""
        for rep in self.roster:
            rep.reset_daily()
        self._rr_index.clear()
        logger.info("[routing] Daily counters reset for all reps")


# ── Convenience Function for n8n ─────────────────────────

def route_lead(lead_score_data: dict) -> dict:
    """
    Convenience function for n8n Code nodes.
    Accepts a dict, returns a dict.
    """
    score = LeadScore(**lead_score_data)
    agent = RoutingAgent()
    result = agent.route(score)
    return result.model_dump(mode="json")
