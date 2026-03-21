"""
Demo / Test runner for the full Research → Qualification pipeline.

Runs both agents in sequence with mock enrichment to demonstrate
the complete scoring, classification, and decision logic.

Usage:
    python -m tests.test_qualification_agent
"""

import asyncio
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.schemas import RawLead, LeadSource
from agents.research_agent import ResearchAgent
from agents.qualification_agent import QualificationAgent


# ── Test Leads ───────────────────────────────────────────

TEST_LEADS = [
    {
        "name": "Dream Lead — VP Sales at SaaS",
        "lead": RawLead(
            source=LeadSource.WEB_FORM,
            email="sarah.chen@techcorp.io",
            first_name="Sarah",
            last_name="Chen",
            company_name="TechCorp Solutions",
            company_domain="techcorp.io",
            job_title="VP of Sales",
            phone="+1-555-0142",
            message="We're looking to automate our lead qualification process. Currently spending 20+ hours/week on manual lead review. What's your pricing for a team of 15 reps?",
            utm_source="google",
            utm_medium="cpc",
            utm_campaign="sales-automation-demo-2024",
            page_url="https://yoursite.com/pricing",
        ),
        "expected_temp": "hot",
        "expected_decision": "qualified",
    },
    {
        "name": "Good Fit — Director at HealthTech",
        "lead": RawLead(
            source=LeadSource.LINKEDIN,
            email="james.wilson@megahealth.com",
            first_name="James",
            last_name="Wilson",
            company_name="MegaHealth Systems",
            company_domain="megahealth.com",
            job_title="Director of Sales Operations",
            message="Saw your post about AI lead scoring. We're evaluating options to improve our pipeline visibility.",
        ),
        "expected_temp": "warm",
        "expected_decision": "qualified",
    },
    {
        "name": "Mid-Tier — Manager, Partial Fit",
        "lead": RawLead(
            source=LeadSource.CHAT_WIDGET,
            email="alex.martinez@acmewidgets.com",
            first_name="Alex",
            last_name="Martinez",
            company_domain="acmewidgets.com",
            job_title="Sales Manager",
            message="Interested in learning more about your platform.",
        ),
        "expected_temp": "cool",
        "expected_decision": "nurture",
    },
    {
        "name": "Free Email — Consultant",
        "lead": RawLead(
            source=LeadSource.CHAT_WIDGET,
            email="john.doe@gmail.com",
            first_name="John",
            last_name="Doe",
            job_title="Consultant",
            message="Just exploring options for a client.",
        ),
        "expected_temp": "cool",
        "expected_decision": "needs_review",
    },
    {
        "name": "Minimal — Domain Only",
        "lead": RawLead(
            source=LeadSource.API,
            company_domain="stripe.com",
        ),
        "expected_temp": "cold",
        "expected_decision": "needs_review",
    },
    {
        "name": "No Identifiers — Should Fail",
        "lead": RawLead(
            source=LeadSource.EVENT,
            first_name="Anonymous",
            message="Met at the conference.",
        ),
        "expected_temp": "cold",
        "expected_decision": "disqualified",
    },
]


# ── Display Helpers ──────────────────────────────────────

TEMP_ICONS = {"hot": "🔥", "warm": "🌤 ", "cool": "❄️ ", "cold": "🧊"}
DECISION_ICONS = {
    "qualified": "✅", "nurture": "🌱",
    "needs_review": "🔍", "disqualified": "❌",
}

def print_section(title, char="─"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def print_score_bar(label, score, max_score=100, width=30):
    """Print a visual score bar."""
    filled = int((score / max_score) * width)
    bar = "█" * filled + "░" * (width - filled)
    print(f"  {label:<18s} {bar} {score:5.1f}/{max_score}")


def print_result(name, score, expected_temp, expected_decision):
    """Pretty-print qualification result."""
    temp_icon = TEMP_ICONS.get(score.temperature.value, "?")
    dec_icon = DECISION_ICONS.get(score.decision.value, "?")

    temp_match = "✓" if score.temperature.value == expected_temp else "✗"
    dec_match = "✓" if score.decision.value == expected_decision else "✗"

    print_section(f"RESULT: {name}", "═")

    # Contact & Company
    lead = score.enriched_lead
    contact_name = lead.contact.full_name if lead.contact else "Unknown"
    contact_title = lead.contact.normalized_title if lead.contact else "Unknown"
    company_name = lead.company.legal_name if lead.company else "Unknown"

    print(f"\n  Contact:  {contact_name} — {contact_title}")
    print(f"  Company:  {company_name}")
    print(f"  Source:   {lead.raw_lead.source.value}")
    print()

    # Score bars
    print_score_bar("Firmographic", score.firmographic_score)
    print_score_bar("Demographic", score.demographic_score)
    print_score_bar("Behavioral", score.behavioral_score)
    print_score_bar("AI Fit", score.ai_fit_score)
    print()
    print_score_bar("COMPOSITE", score.composite_score)
    print()

    # Temperature & Decision
    print(f"  Temperature:  {temp_icon} {score.temperature.value.upper():<8s}  (expected: {expected_temp}) [{temp_match}]")
    print(f"  Decision:     {dec_icon} {score.decision.value:<16s}  (expected: {expected_decision}) [{dec_match}]")
    print(f"  Reason:       {score.decision_reasoning.split(chr(10))[4] if chr(10) in score.decision_reasoning else score.decision_reasoning}")
    print()


# ── Main Runner ──────────────────────────────────────────

async def run_tests():
    print_section("AI LEAD QUALIFICATION — FULL PIPELINE DEMO", "█")
    print(f"  Pipeline: Raw Lead → Research Agent → Qualification Agent")
    print(f"  Mode:     Mock enrichment (no live API calls)")
    print(f"  Leads:    {len(TEST_LEADS)} test scenarios")
    print(f"  Started:  {datetime.utcnow().isoformat()}Z")

    research = ResearchAgent(use_mock=True)
    qualification = QualificationAgent()

    results = []

    for test in TEST_LEADS:
        print_section(f"PROCESSING: {test['name']}")
        print(f"  Email:  {test['lead'].email or 'None'}")
        print(f"  Domain: {test['lead'].company_domain or 'None'}")

        # Phase 1: Research
        enriched = await research.research(test["lead"])

        # Phase 2: Qualification
        scored = qualification.qualify(enriched)

        print_result(test["name"], scored, test["expected_temp"], test["expected_decision"])
        results.append((test["name"], scored, test["expected_temp"], test["expected_decision"]))

    # ── Summary Table ────────────────────────────────────
    print_section("SUMMARY", "█")
    print(f"  {'Lead':<35s} {'Score':>6s} {'Temp':>6s} {'Decision':<16s} {'Match':>5s}")
    print(f"  {'─' * 35} {'─' * 6} {'─' * 6} {'─' * 16} {'─' * 5}")

    correct = 0
    total = len(results)

    for name, scored, exp_temp, exp_dec in results:
        temp_ok = scored.temperature.value == exp_temp
        dec_ok = scored.decision.value == exp_dec
        match_str = "✓ ✓" if (temp_ok and dec_ok) else ("✓ ✗" if temp_ok else ("✗ ✓" if dec_ok else "✗ ✗"))
        if temp_ok and dec_ok:
            correct += 1

        icon = TEMP_ICONS.get(scored.temperature.value, " ")
        print(
            f"  {name:<35s} "
            f"{scored.composite_score:5.1f} "
            f"{icon}{scored.temperature.value:<5s} "
            f"{scored.decision.value:<16s} "
            f"{match_str:>5s}"
        )

    print(f"\n  Accuracy: {correct}/{total} expected outcomes matched")
    print(f"  Qualified: {sum(1 for _, s, _, _ in results if s.decision.value == 'qualified')}")
    print(f"  Nurture:   {sum(1 for _, s, _, _ in results if s.decision.value == 'nurture')}")
    print(f"  Review:    {sum(1 for _, s, _, _ in results if s.decision.value == 'needs_review')}")
    print(f"  Disqual:   {sum(1 for _, s, _, _ in results if s.decision.value == 'disqualified')}")
    print()


if __name__ == "__main__":
    asyncio.run(run_tests())
