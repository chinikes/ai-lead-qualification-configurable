import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
Pipeline orchestrator — chains enrichment → AI analysis → scoring.
Called by the /api/process endpoint.
"""

import asyncio
import re
from _db import get_client, update_lead, upsert_score, log_activity, get_scoring_config
from _enrich import enrich_lead
from _ai import analyze_lead
from _scorer import score_lead

FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "mail.com", "protonmail.com",
}


async def run_pipeline(lead: dict) -> dict:
    """
    Run the full enrichment → analysis → scoring pipeline on a lead.
    Updates the DB at each stage. Returns the final scored lead.
    """
    db = get_client()
    lead_id = lead["id"]
    email = lead.get("email")
    domain = lead.get("company_domain")

    # Extract domain from email if not provided
    if not domain and email and "@" in email:
        domain = email.split("@")[1].lower()

    # Detect flags
    flags = []
    if not email and not domain:
        flags.append("no_identifiers")
        update_lead(db, lead_id, {
            "status": "failed",
            "flags": flags,
            "needs_review": True,
            "review_reasons": ["No email or domain available"],
        })
        log_activity(db, "failed", f"Lead {email or 'unknown'} — no identifiers", lead_id)
        return lead

    if email and email.split("@")[1].lower() in FREE_EMAIL_DOMAINS:
        flags.append("free_email")

    # ── Stage 1: Enrichment ──────────────────────────
    update_lead(db, lead_id, {"status": "enriching"})
    log_activity(db, "enriching", f"Enriching {lead.get('first_name', '')} {lead.get('last_name', '')} — {domain or email}", lead_id)

    enrichment = await enrich_lead(domain, email)

    # Use lead data as fallback for contact fields
    if not enrichment.get("contact_full_name"):
        name_parts = [lead.get("first_name", ""), lead.get("last_name", "")]
        name = " ".join(p for p in name_parts if p).strip()
        if name:
            enrichment["contact_full_name"] = name
    if not enrichment.get("contact_title") and lead.get("job_title"):
        enrichment["contact_title"] = lead["job_title"]
        enrichment["contact_seniority"] = _detect_seniority(lead["job_title"])

    # Compute enrichment confidence
    company_fields = ["company_legal_name", "company_industry", "company_employee_count",
                      "company_revenue", "company_funding_stage", "company_hq_city", "company_description"]
    contact_fields = ["contact_full_name", "contact_title", "contact_department",
                      "contact_linkedin_url", "contact_phone_direct", "contact_location_city"]
    comp_filled = sum(1 for f in company_fields if enrichment.get(f))
    cont_filled = sum(1 for f in contact_fields if enrichment.get(f))
    confidence = (comp_filled / len(company_fields)) * 0.5 + (cont_filled / len(contact_fields)) * 0.5

    if "free_email" in flags:
        confidence *= 0.8

    enrichment["overall_confidence"] = round(confidence, 3)
    enrichment["flags"] = flags
    enrichment["status"] = "enriched"

    update_lead(db, lead_id, enrichment)
    log_activity(db, "enriched",
        f"Enriched {enrichment.get('company_legal_name', domain or '?')} — "
        f"{len(enrichment.get('enrichment_sources', []))} sources, "
        f"{round(confidence * 100)}% confidence",
        lead_id)

    # ── Fetch ICP config from DB (used by AI + scorer) ──
    icp_config = None
    try:
        icp_result = db.table("icp_config").select("*").eq("is_active", True).limit(1).execute()
        if icp_result.data:
            icp_config = icp_result.data[0]
    except Exception:
        pass  # Fall back to hardcoded defaults

    # ── Stage 2: AI Analysis ─────────────────────────
    # Merge lead + enrichment for the AI prompt
    merged = {**lead, **enrichment}
    ai_result = await analyze_lead(merged, icp_config=icp_config)

    if ai_result:
        update_lead(db, lead_id, {**ai_result, "status": "enriched"})
        merged.update(ai_result)
        # Update confidence with AI
        ai_conf = ai_result.get("ai_confidence", 0)
        overall = confidence * 0.65 + ai_conf * 0.35
        if "free_email" in flags:
            overall *= 0.8
        update_lead(db, lead_id, {"overall_confidence": round(overall, 3)})
        merged["overall_confidence"] = round(overall, 3)
        log_activity(db, "analyzed",
            f"AI analysis: ICP fit {round(ai_result.get('ai_icp_fit_score', 0) * 100)}%, "
            f"urgency={ai_result.get('ai_urgency', '?')}",
            lead_id)
    else:
        log_activity(db, "analyzed", "AI analysis skipped (no API key or error)", lead_id)

    # ── Stage 3: Scoring ─────────────────────────────
    update_lead(db, lead_id, {"status": "scoring"})
    config = get_scoring_config(db)
    score_result = score_lead(merged, config, icp_config=icp_config)

    upsert_score(db, lead_id, score_result)
    update_lead(db, lead_id, {"status": "scored"})

    log_activity(db, "scored",
        f"Score: {score_result['composite_score']}/100 → "
        f"{score_result['temperature'].upper()} → {score_result['decision'].upper()}",
        lead_id)

    # ── Stage 4: Final status ────────────────────────
    final_status = "routed" if score_result["decision"] == "qualified" else "scored"
    needs_review = score_result["decision"] == "needs_review"
    review_reasons = [score_result["decision_reasoning"]] if needs_review else []

    update_lead(db, lead_id, {
        "status": final_status,
        "needs_review": needs_review,
        "review_reasons": review_reasons,
    })

    decision_events = {
        "qualified": ("qualified", "QUALIFIED"),
        "nurture": ("nurture", "NURTURE"),
        "needs_review": ("review", "NEEDS REVIEW"),
        "disqualified": ("disqualified", "DISQUALIFIED"),
    }
    evt, label = decision_events.get(score_result["decision"], ("scored", "SCORED"))
    contact_name = merged.get("contact_full_name", email or "Unknown")
    company_name = merged.get("company_legal_name", domain or "Unknown")
    log_activity(db, evt, f"{contact_name} ({company_name}) → {label}", lead_id)

    # ── Stage 5: Notify n8n (HubSpot + Slack) ────────
    final_data = {**merged, **score_result, "lead_id": lead_id}
    await _notify_n8n(final_data)

    return final_data


async def _notify_n8n(data: dict):
    """Fire webhook to n8n for HubSpot sync + Slack alerts."""
    import os
    import httpx
    webhook_url = os.environ.get("N8N_WEBHOOK_URL", "")
    if not webhook_url:
        return  # n8n not configured — skip silently
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook_url, json=data)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"[n8n] Webhook failed: {e}")


def _detect_seniority(title: str) -> str:
    import re
    t = title.lower()
    if re.search(r"\b(ceo|cto|cfo|coo|cro|cmo|chief|founder)\b", t):
        return "c_level"
    if re.search(r"\b(vp|vice president|svp|evp)\b", t):
        return "vp"
    if re.search(r"\b(director|head of)\b", t):
        return "director"
    if re.search(r"\b(manager|lead|team lead)\b", t):
        return "manager"
    if re.search(r"\b(senior|sr\.|principal)\b", t):
        return "senior_ic"
    return "ic"
# force redeploy
