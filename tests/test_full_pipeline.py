"""
Full 4-agent pipeline: Research → Qualification → Routing → Engagement.

Usage:
    python -m tests.test_full_pipeline
"""

import asyncio
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.schemas import RawLead, LeadSource
from agents.research_agent import ResearchAgent
from agents.qualification_agent import QualificationAgent
from agents.routing_agent import RoutingAgent
from agents.engagement_agent import EngagementAgent

TEMP_ICONS = {"hot": "🔥", "warm": "🌤 ", "cool": "❄️ ", "cold": "🧊"}
DEC_ICONS = {"qualified": "✅", "nurture": "🌱", "needs_review": "🔍", "disqualified": "❌"}

TEST_LEADS = [
    RawLead(source=LeadSource.WEB_FORM, email="sarah.chen@techcorp.io", first_name="Sarah", last_name="Chen", company_domain="techcorp.io", job_title="VP of Sales", message="Looking to automate lead qual. Spending 20+ hrs/week. What's pricing for 15 reps?", utm_source="google", utm_medium="cpc", utm_campaign="sales-automation-demo-2024", page_url="https://site.com/pricing"),
    RawLead(source=LeadSource.REFERRAL, email="priya.patel@finova.io", first_name="Priya", last_name="Patel", company_domain="finova.io", job_title="CRO", message="Our board is pushing us to improve sales efficiency. Need a demo this week."),
    RawLead(source=LeadSource.LINKEDIN, email="james.wilson@megahealth.com", first_name="James", last_name="Wilson", company_domain="megahealth.com", job_title="Director of Sales Ops", message="Evaluating AI lead scoring. Need better pipeline visibility."),
    RawLead(source=LeadSource.CHAT_WIDGET, email="alex.martinez@acmewidgets.com", first_name="Alex", last_name="Martinez", company_domain="acmewidgets.com", job_title="Sales Manager", message="Interested in learning more."),
]


def bar(val, w=25):
    f = int((val / 100) * w)
    return "█" * f + "░" * (w - f)


def sec(title, char="─"):
    print(f"\n{char * 76}")
    print(f"  {title}")
    print(f"{char * 76}")


def print_step(step):
    icons = {"email": "📧", "linkedin": "🔗", "phone": "📞"}
    icon = icons.get(step.touch_type.value, "•")
    vtag = f" [Variant {step.variant}]" if step.variant != "A" else ""
    print(f"    {icon} Day {step.day} — {step.touch_type.value.upper()}{vtag}")
    if step.subject:
        print(f"       Subject: {step.subject}")
    lines = step.body.strip().split("\n")
    for line in lines[:6]:
        print(f"       {line}")
    if len(lines) > 6:
        print(f"       ... ({len(lines) - 6} more lines)")
    if step.cta:
        print(f"       CTA: {step.cta}")
    if step.internal_notes:
        print(f"       📝 Note: {step.internal_notes}")
    print()


async def main():
    sec("AI LEAD QUALIFICATION — FULL 4-AGENT PIPELINE", "█")
    print(f"  Pipeline:  Raw Lead → Research → Qualification → Routing → Engagement")
    print(f"  Mode:      Mock enrichment + template content (no live APIs)")
    print(f"  Leads:     {len(TEST_LEADS)} test scenarios")

    research = ResearchAgent(use_mock=True)
    qualification = QualificationAgent()
    routing = RoutingAgent()
    engagement = EngagementAgent()

    results = []

    for i, lead in enumerate(TEST_LEADS):
        sec(f"LEAD {i+1}/{len(TEST_LEADS)}: {lead.email}")

        enriched = await research.research(lead)
        scored = qualification.qualify(enriched)
        routed = routing.route(scored)
        plan = await engagement.engage(routed)

        name = enriched.contact.full_name if enriched.contact else "Unknown"
        company = enriched.company.legal_name if enriched.company else "Unknown"
        t = scored.temperature.value
        d = scored.decision.value

        print(f"\n  {name} — {enriched.contact.normalized_title if enriched.contact else '?'}")
        print(f"  {company} ({enriched.company.industry if enriched.company else '?'})")
        print(f"  Score: {scored.composite_score:.1f} | {TEMP_ICONS.get(t)} {t.upper()} | {DEC_ICONS.get(d)} {d.upper()}")

        if routed.assigned_rep_name:
            print(f"  Routed → {routed.assigned_rep_name} ({routed.territory}) | SLA: {routed.sla_response_minutes}min")
        else:
            print(f"  Routing: {routed.routing_reason}")

        print(f"\n  ── Engagement Plan {'─' * 48}")
        print(f"  Strategy:  {plan.strategy.value.upper()} — {plan.strategy_reasoning}")
        print(f"  Touches:   {plan.total_touches} over {plan.sequence_duration_days} days")
        ab = len([s for s in plan.sequence_steps if s.variant == "B"])
        print(f"  A/B tests: {ab}")
        print()

        for step in plan.sequence_steps:
            print_step(step)

        results.append({
            "name": name, "company": company, "score": scored.composite_score,
            "temp": t, "decision": d, "strategy": plan.strategy.value,
            "touches": plan.total_touches, "days": plan.sequence_duration_days,
            "rep": routed.assigned_rep_name,
        })

    sec("PIPELINE SUMMARY", "█")
    print(f"  {'Lead':<20s} {'Company':<18s} {'Score':>5s} {'Temp':>5s} {'Decision':<12s} {'Strategy':<12s} {'Steps':>5s} {'Rep':<16s}")
    print(f"  {'─'*20} {'─'*18} {'─'*5} {'─'*5} {'─'*12} {'─'*12} {'─'*5} {'─'*16}")
    for r in results:
        print(f"  {r['name']:<20s} {r['company']:<18s} {r['score']:5.1f} {r['temp']:>5s} {r['decision']:<12s} {r['strategy']:<12s} {r['touches']:>5d} {(r['rep'] or '—'):<16s}")

    sec("REP WORKLOAD", "═")
    for stat in routing.get_rep_stats():
        load = "█" * stat["assigned_today"] + "░" * (stat["max_daily"] - stat["assigned_today"])
        print(f"  {stat['name']:<20s} [{load}] {stat['assigned_today']}/{stat['max_daily']}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
