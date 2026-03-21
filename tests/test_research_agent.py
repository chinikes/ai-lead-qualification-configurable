"""
Demo / Test runner for the Research Agent.

Runs the full pipeline with mock enrichment providers to demonstrate
the system architecture without requiring live API keys.

Usage:
    python -m tests.test_research_agent
"""

import asyncio
import json
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.schemas import RawLead, LeadSource
from agents.research_agent import ResearchAgent


# ── Test Leads ───────────────────────────────────────────

TEST_LEADS = [
    {
        "name": "Perfect ICP Match",
        "lead": RawLead(
            source=LeadSource.WEB_FORM,
            email="sarah.chen@techcorp.io",
            first_name="Sarah",
            last_name="Chen",
            company_name="TechCorp Solutions",
            company_domain="techcorp.io",
            job_title="VP of Sales",
            phone="+1-555-0142",
            message="We're looking to automate our lead qualification process. Currently spending 20+ hours/week on manual lead review. Interested in your AI-powered solution.",
            utm_source="google",
            utm_medium="cpc",
            utm_campaign="sales-automation-2024",
            page_url="https://yoursite.com/solutions/lead-qualification",
        ),
    },
    {
        "name": "Good Fit — Healthcare",
        "lead": RawLead(
            source=LeadSource.LINKEDIN,
            email="james.wilson@megahealth.com",
            first_name="James",
            last_name="Wilson",
            company_name="MegaHealth Systems",
            company_domain="megahealth.com",
            job_title="Director of Sales Operations",
            message="Saw your post about AI lead scoring. We're a healthtech company struggling with lead prioritization.",
        ),
    },
    {
        "name": "Free Email — Needs Review",
        "lead": RawLead(
            source=LeadSource.CHAT_WIDGET,
            email="john.doe@gmail.com",
            first_name="John",
            last_name="Doe",
            job_title="Consultant",
            message="Just exploring options for a client.",
        ),
    },
    {
        "name": "Minimal Data — Domain Only",
        "lead": RawLead(
            source=LeadSource.API,
            company_domain="stripe.com",
        ),
    },
    {
        "name": "No Identifiers — Should Fail",
        "lead": RawLead(
            source=LeadSource.EVENT,
            first_name="Anonymous",
            message="Met at the conference, interested in learning more.",
        ),
    },
]


# ── Pretty Printer ───────────────────────────────────────

def print_section(title: str, char: str = "─"):
    width = 70
    print(f"\n{char * width}")
    print(f"  {title}")
    print(f"{char * width}")


def print_enriched_lead(result, test_name: str):
    """Pretty-print the enriched lead result."""
    print_section(f"RESULT: {test_name}", "═")

    # Summary
    print(f"\n  Confidence: {result.overall_data_confidence:.1%}")
    print(f"  Flags:      {', '.join(result.flags) if result.flags else 'none'}")
    print(f"  Review:     {'YES — ' + '; '.join(result.review_reasons) if result.needs_human_review else 'No'}")
    print(f"  Duration:   {result.enrichment_duration_ms}ms")

    # Company
    if result.company:
        c = result.company
        print(f"\n  ── Company {'─' * 50}")
        print(f"  Name:       {c.legal_name or '?'}")
        print(f"  Industry:   {c.industry or '?'} / {c.sub_industry or '?'}")
        print(f"  Employees:  {c.employee_count or '?'} ({c.employee_range.value if c.employee_range else '?'})")
        print(f"  Revenue:    {c.estimated_revenue or '?'}")
        print(f"  Funding:    {c.funding_stage or '?'} ({c.total_funding or '?'})")
        print(f"  Location:   {c.headquarters_city or '?'}, {c.headquarters_state or '?'}, {c.headquarters_country or '?'}")
        print(f"  Tech:       {', '.join(c.technologies[:8]) if c.technologies else 'Unknown'}")
        print(f"  Sources:    {', '.join(c.enrichment_sources)}")
        print(f"  Confidence: {c.enrichment_confidence:.1%}")
    else:
        print("\n  ── Company: No data")

    # Contact
    if result.contact:
        ct = result.contact
        print(f"\n  ── Contact {'─' * 50}")
        print(f"  Name:       {ct.full_name or '?'}")
        print(f"  Title:      {ct.normalized_title or '?'}")
        print(f"  Seniority:  {ct.seniority.value}")
        print(f"  Department: {ct.department or '?'}")
        print(f"  Decision:   {'✓ Decision Maker' if ct.is_decision_maker else '✗ Not a decision maker'}")
        print(f"  LinkedIn:   {ct.linkedin_url or '?'}")
        print(f"  Phone:      {ct.phone_direct or '?'}")
        print(f"  Previous:   {', '.join(ct.previous_companies[:3]) if ct.previous_companies else 'None'}")
    else:
        print("\n  ── Contact: No data")

    # AI Analysis
    if result.ai_analysis:
        ai = result.ai_analysis
        print(f"\n  ── AI Analysis {'─' * 46}")
        print(f"  ICP Fit:    {ai.icp_fit_score:.0%}")
        print(f"  Urgency:    {ai.urgency_assessment}")
        print(f"  Confidence: {ai.confidence:.0%}")
        print(f"\n  Summary:")
        print(f"    {ai.company_summary[:200]}")
        print(f"\n  ICP Narrative:")
        print(f"    {ai.icp_fit_narrative[:200]}")
        if ai.buying_signals:
            print(f"\n  Buying Signals:")
            for sig in ai.buying_signals[:3]:
                print(f"    [{sig.strength.upper():8s}] {sig.signal}")
        if ai.pain_points:
            print(f"\n  Pain Points:")
            for pp in ai.pain_points[:3]:
                print(f"    [{pp.relevance_to_product.upper():6s}] {pp.pain_point}")
        if ai.recommended_talking_points:
            print(f"\n  Talking Points:")
            for tp in ai.recommended_talking_points[:3]:
                print(f"    • {tp}")
    else:
        print("\n  ── AI Analysis: Skipped (no enrichment data or API unavailable)")

    print()


# ── Main Runner ──────────────────────────────────────────

async def run_tests():
    """Run all test leads through the Research Agent."""
    print_section("AI LEAD QUALIFICATION SYSTEM — RESEARCH AGENT DEMO", "█")
    print(f"  Started: {datetime.utcnow().isoformat()}Z")
    print(f"  Mode: Mock enrichment (no live API calls)")
    print(f"  Leads: {len(TEST_LEADS)} test cases")

    # Initialize agent with mock providers
    agent = ResearchAgent(use_mock=True)

    results = []
    for test_case in TEST_LEADS:
        print_section(f"PROCESSING: {test_case['name']}")
        print(f"  Email:   {test_case['lead'].email or 'None'}")
        print(f"  Domain:  {test_case['lead'].company_domain or 'None'}")
        print(f"  Source:  {test_case['lead'].source.value}")

        result = await agent.research(test_case["lead"])
        results.append((test_case["name"], result))

        print_enriched_lead(result, test_case["name"])

    # Summary table
    print_section("SUMMARY", "█")
    print(f"  {'Test Case':<30} {'Confidence':>12} {'Flags':>20} {'Review':>8}")
    print(f"  {'─' * 30} {'─' * 12} {'─' * 20} {'─' * 8}")
    for name, result in results:
        flags_str = ','.join(result.flags[:2]) if result.flags else 'none'
        review_str = 'YES' if result.needs_human_review else 'no'
        print(f"  {name:<30} {result.overall_data_confidence:>11.1%} {flags_str:>20} {review_str:>8}")

    print(f"\n  Total processed: {len(results)}")
    print(f"  Auto-proceed:    {sum(1 for _, r in results if not r.needs_human_review)}")
    print(f"  Needs review:    {sum(1 for _, r in results if r.needs_human_review)}")
    print()


if __name__ == "__main__":
    asyncio.run(run_tests())
