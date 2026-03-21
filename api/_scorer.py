import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
Scoring and qualification logic.
Adapted from the original QualificationAgent for serverless use.
"""

import re

# ── ICP constants for AgileDevs Consulting ────────
# AgileDevs offers: Project Management consulting, Workflow Automation
# (n8n, Make.com), Atlassian Administration (Jira, Confluence, JSM),
# ERP/CRM implementations (Salesforce, NetSuite, Dynamics 365),
# and SaaS/PaaS delivery.

TARGET_INDUSTRIES = [
    "saas", "technology", "software", "financial services", "fintech",
    "healthcare", "healthcare technology", "e-commerce", "professional services",
    "manufacturing", "information technology", "managed services",
    "telecommunications", "media", "education", "nonprofit",
    "construction", "real estate", "logistics", "energy",
]
TARGET_TITLES = [
    "cto", "cio", "coo", "vp of engineering", "vp engineering",
    "vp of operations", "vp operations", "head of engineering",
    "director of engineering", "director of it", "director of operations",
    "head of it", "head of operations", "it director", "it manager",
    "engineering manager", "product manager", "program manager",
    "project manager", "head of product", "vp of product",
    "director of product", "chief digital officer", "digital transformation lead",
    "operations manager", "business operations manager",
    "director of technology", "head of technology",
]
TARGET_DEPTS = [
    "engineering", "it", "operations", "product", "technology",
    "digital transformation", "business operations", "project management",
]
POSITIVE_TECH = [
    "jira", "confluence", "atlassian", "jira service management", "jsm",
    "salesforce", "hubspot", "netsuite", "dynamics 365", "microsoft dynamics",
    "n8n", "make", "make.com", "zapier", "airtable",
    "slack", "asana", "monday.com", "trello", "linear",
    "github", "gitlab", "bitbucket", "azure devops",
    "vercel", "aws", "gcp", "azure", "heroku",
    "notion", "clickup",
]
HIGH_INTENT = [
    "consulting", "implementation", "migration", "automate", "automation",
    "integrate", "integration", "jira setup", "workflow", "project management",
    "erp", "crm", "help with", "need someone", "looking for a consultant",
    "budget", "timeline", "proposal", "rfp", "sow", "engagement",
    "outsource", "contractor", "freelancer", "agency",
]
MED_INTENT = [
    "interested", "learn more", "information", "evaluate",
    "streamline", "improve", "optimize", "scale", "growing",
    "challenges", "pain", "struggling", "manual process",
    "spreadsheet", "too many tools", "disorganized",
]
SOURCE_SCORES = {
    "referral": 25, "web_form": 20, "chat_widget": 18,
    "linkedin": 15, "email": 12, "event": 10, "api": 8,
}


def score_lead(lead: dict, config: dict, icp_config: dict = None) -> dict:
    """
    Score a lead across 4 dimensions and produce a qualification decision.
    If icp_config is provided, uses those criteria instead of hardcoded defaults.
    Returns a dict ready to upsert into the scores table.
    """
    # Override constants if ICP config from DB is provided
    if icp_config:
        global TARGET_INDUSTRIES, TARGET_TITLES, TARGET_DEPTS, POSITIVE_TECH, HIGH_INTENT, MED_INTENT
        TARGET_INDUSTRIES = [i.lower() for i in (icp_config.get("target_industries") or TARGET_INDUSTRIES)]
        TARGET_TITLES = [t.lower() for t in (icp_config.get("target_titles") or TARGET_TITLES)]
        TARGET_DEPTS = [d.lower() for d in (icp_config.get("target_departments") or TARGET_DEPTS)]
        POSITIVE_TECH = [t.lower() for t in (icp_config.get("positive_tech") or POSITIVE_TECH)]
        HIGH_INTENT = [k.lower() for k in (icp_config.get("high_intent_keywords") or HIGH_INTENT)]
        MED_INTENT = [k.lower() for k in (icp_config.get("med_intent_keywords") or MED_INTENT)]

    firm, firm_bd = _score_firmographic(lead)
    demo, demo_bd = _score_demographic(lead)
    behav, behav_bd = _score_behavioral(lead)
    ai, ai_bd = _score_ai_fit(lead)

    w = config
    base = (
        firm * w.get("firmographic_weight", 0.30)
        + demo * w.get("demographic_weight", 0.25)
        + behav * w.get("behavioral_weight", 0.20)
        + ai * w.get("ai_fit_weight", 0.25)
    )

    bonus = _compute_bonus(lead, firm)
    penalty = _compute_penalty(lead)
    composite = max(0, min(100, base + bonus - penalty))

    temp = _classify_temperature(composite, w)
    decision, reasoning = _make_decision(composite, lead, firm_bd, w)

    return {
        "firmographic_score": round(firm, 1),
        "demographic_score": round(demo, 1),
        "behavioral_score": round(behav, 1),
        "ai_fit_score": round(ai, 1),
        "composite_score": round(composite, 1),
        "firmographic_breakdown": firm_bd,
        "demographic_breakdown": demo_bd,
        "behavioral_breakdown": behav_bd,
        "ai_fit_breakdown": ai_bd,
        "bonus_applied": round(bonus, 1),
        "penalty_applied": round(penalty, 1),
        "temperature": temp,
        "decision": decision,
        "decision_reasoning": reasoning,
    }


# ── Firmographic (0-100) ─────────────────────────────

def _score_firmographic(lead: dict) -> tuple[float, dict]:
    bd = {}
    score = 0.0

    # Industry (0-30)
    ind = (lead.get("company_industry") or "").lower()
    ind_score = 30.0 if any(t in ind or ind in t for t in TARGET_INDUSTRIES) else 0.0
    if ind_score == 0 and lead.get("company_sub_industry"):
        sub = lead["company_sub_industry"].lower()
        if any(t in sub for t in TARGET_INDUSTRIES):
            ind_score = 20.0
    bd["industry"] = ind_score
    score += ind_score

    # Employees (0-25)
    emp = lead.get("company_employee_count")
    emp_score = 0.0
    if emp:
        if 20 <= emp <= 5000:
            sweet = 2510
            dist = abs(emp - sweet) / sweet
            emp_score = 25.0 * max(0, 1 - dist * 0.5)
        elif emp < 20:
            emp_score = 25.0 * (emp / 20) * 0.5 if emp > 6 else 0.0
        else:
            emp_score = 12.0
    bd["employees"] = round(emp_score, 1)
    score += emp_score

    # Funding (0-15)
    fund = (lead.get("company_funding_stage") or "").lower()
    preferred = ["series a", "series b", "series c", "growth", "public"]
    fund_score = 15.0 if any(f in fund for f in preferred) else (5.0 if fund else 0.0)
    bd["funding"] = fund_score
    score += fund_score

    # Geography (0-15)
    country = (lead.get("company_hq_country") or "").upper()
    target_countries = ["US", "CA", "UK", "AU", "DE", "FR", "NL"]
    geo_score = 15.0 if country in target_countries else (5.0 if country else 0.0)
    bd["geography"] = geo_score
    score += geo_score

    # Tech stack (0-15)
    techs = lead.get("company_technologies") or []
    tech_lower = {t.lower() for t in techs}
    matches = tech_lower & {t.lower() for t in POSITIVE_TECH}
    tech_score = min(15.0, 5.0 + (len(matches) - 1) * 3.5) if matches else 0.0
    bd["tech_stack"] = round(tech_score, 1)
    bd["tech_matches"] = list(matches)
    score += tech_score

    return min(score, 100.0), bd


# ── Demographic (0-100) ──────────────────────────────

def _score_demographic(lead: dict) -> tuple[float, dict]:
    bd = {}
    score = 0.0

    sen = lead.get("contact_seniority", "unknown")
    sen_map = {"c_level": 30, "vp": 28, "director": 22, "manager": 14, "senior_ic": 8, "ic": 4, "unknown": 0}
    bd["seniority"] = sen_map.get(sen, 0)
    score += bd["seniority"]

    title = (lead.get("contact_title") or lead.get("job_title") or "").lower()
    title_score = 0.0
    if title:
        if any(t == title for t in TARGET_TITLES):
            title_score = 25.0
        else:
            words = set(title.split())
            stop = {"of", "the", "and", "a", "in", "for"}
            best = 0
            for t in TARGET_TITLES:
                meaningful = words & set(t.split()) - stop
                best = max(best, len(meaningful))
            title_score = 20.0 if best >= 2 else (15.0 if best >= 1 else 0.0)
    bd["title"] = title_score
    score += title_score

    dept = (lead.get("contact_department") or "").lower()
    dept_score = 20.0 if any(d in dept or dept in d for d in TARGET_DEPTS) else 0.0
    if dept_score == 0 and any(a in dept for a in ["marketing", "operations", "strategy"]):
        dept_score = 8.0
    bd["department"] = dept_score
    score += dept_score

    dm = lead.get("contact_is_decision_maker", False)
    bd["decision_maker"] = 15.0 if dm else 0.0
    score += bd["decision_maker"]

    fields = ["contact_full_name", "contact_title", "contact_department",
              "contact_linkedin_url", "contact_phone_direct", "contact_location_city"]
    filled = sum(1 for f in fields if lead.get(f))
    bd["completeness"] = round((filled / len(fields)) * 10, 1)
    score += bd["completeness"]

    return min(score, 100.0), bd


# ── Behavioral (0-100) ───────────────────────────────

def _score_behavioral(lead: dict) -> tuple[float, dict]:
    bd = {}
    score = 0.0

    src = lead.get("source", "api")
    bd["source"] = SOURCE_SCORES.get(src, 5)
    score += bd["source"]

    msg = (lead.get("message") or "").lower()
    msg_score = 0.0
    if msg:
        high = [k for k in HIGH_INTENT if k in msg]
        med = [k for k in MED_INTENT if k in msg]
        if high:
            msg_score = min(30, 20 + len(high) * 3)
        elif med:
            msg_score = min(20, 10 + len(med) * 3)
        elif len(msg) > 50:
            msg_score = 8
        else:
            msg_score = 3
    bd["message_intent"] = round(msg_score, 1)
    score += msg_score

    utm_score = 0.0
    if lead.get("utm_source"):
        medium = lead.get("utm_medium", "")
        if medium in ("cpc", "paid", "ppc"):
            utm_score += 12
        elif medium in ("email", "social"):
            utm_score += 8
        else:
            utm_score += 4
        campaign = (lead.get("utm_campaign") or "").lower()
        if any(k in campaign for k in ["demo", "trial", "pricing"]):
            utm_score += 8
        elif any(k in campaign for k in ["solution", "product"]):
            utm_score += 5
    bd["utm"] = min(round(utm_score, 1), 20)
    score += bd["utm"]

    page = (lead.get("page_url") or "").lower()
    high_pages = ["/pricing", "/demo", "/trial", "/contact-sales", "/request-demo"]
    med_pages = ["/solutions", "/product", "/features", "/case-studies"]
    page_score = 15 if any(p in page for p in high_pages) else (10 if any(p in page for p in med_pages) else (3 if page else 0))
    bd["page_intent"] = page_score
    score += page_score

    bd["timing"] = 5
    score += 5

    return min(score, 100.0), bd


# ── AI Fit (0-100) ───────────────────────────────────

def _score_ai_fit(lead: dict) -> tuple[float, dict]:
    bd = {}
    score = 0.0

    icp = lead.get("ai_icp_fit_score")
    if icp is None:
        confidence = lead.get("overall_confidence", 0) or 0
        neutral = confidence * 40
        return round(neutral, 1), {"note": "No AI analysis — using confidence as proxy"}

    bd["icp_fit"] = round((icp or 0) * 40, 1)
    score += bd["icp_fit"]

    signals = lead.get("ai_buying_signals") or []
    strength_map = {"strong": 10, "moderate": 5, "weak": 2}
    sig_score = sum(strength_map.get(s.get("strength", "weak"), 2) for s in signals[:5])
    bd["buying_signals"] = min(round(sig_score, 1), 25)
    score += bd["buying_signals"]

    pains = lead.get("ai_pain_points") or []
    rel_map = {"high": 8, "medium": 4, "low": 1.5}
    pain_score = sum(rel_map.get(p.get("relevance_to_product", "low"), 1.5) for p in pains[:5])
    bd["pain_points"] = min(round(pain_score, 1), 20)
    score += bd["pain_points"]

    urgency_map = {"immediate": 15, "near_term": 10, "exploratory": 5, "not_ready": 1}
    bd["urgency"] = urgency_map.get(lead.get("ai_urgency", ""), 3)
    score += bd["urgency"]

    return min(score, 100.0), bd


# ── Composite helpers ────────────────────────────────

def _compute_bonus(lead: dict, firm_score: float) -> float:
    bonus = 0.0
    sen = lead.get("contact_seniority")
    dm = lead.get("contact_is_decision_maker")
    if dm and sen in ("c_level", "vp") and firm_score >= 60:
        bonus += 8
    signals = lead.get("ai_buying_signals") or []
    if sum(1 for s in signals if s.get("strength") == "strong") >= 2:
        bonus += 5
    if lead.get("ai_urgency") == "immediate":
        bonus += 5
    hiring = lead.get("company_hiring_signals") or []
    if any(re.search(r"sales|revenue|growth", h, re.I) for h in hiring):
        bonus += 3
    return bonus


def _compute_penalty(lead: dict) -> float:
    penalty = 0.0
    flags = lead.get("flags") or []
    if "free_email" in flags:
        penalty += 10
    conf = lead.get("overall_confidence") or 0
    if conf < 0.3:
        penalty += 8
    elif conf < 0.5:
        penalty += 4
    if not lead.get("company_legal_name") and not lead.get("company_industry"):
        penalty += 12
    if not lead.get("contact_phone_direct") and not lead.get("contact_linkedin_url"):
        penalty += 3
    return penalty


def _classify_temperature(composite: float, config: dict) -> str:
    if composite >= config.get("hot_threshold", 80):
        return "hot"
    if composite >= config.get("warm_threshold", 60):
        return "warm"
    if composite >= config.get("cool_threshold", 40):
        return "cool"
    return "cold"


def _make_decision(composite: float, lead: dict, firm_bd: dict, config: dict) -> tuple[str, str]:
    flags = lead.get("flags") or []
    if "no_identifiers" in flags:
        return "disqualified", "No email or domain"
    emp = lead.get("company_employee_count")
    if emp and emp < 5 and not lead.get("company_funding_stage"):
        return "disqualified", f"Company too small ({emp} employees)"

    if composite >= config.get("auto_qualify_threshold", 75):
        return "qualified", "High composite score — auto-qualified"

    if composite >= config.get("nurture_threshold", 45):
        sen = lead.get("contact_seniority")
        dm = lead.get("contact_is_decision_maker")
        if dm and sen in ("c_level", "vp") and firm_bd.get("industry", 0) >= 20:
            return "qualified", "Decision maker at ICP company — upgraded"
        if lead.get("needs_review") or (lead.get("overall_confidence") or 0) < 0.5:
            return "needs_review", "Mid-range score with incomplete data"
        return "nurture", "Score in nurture range"

    if (lead.get("overall_confidence") or 0) < 0.3 and "no_identifiers" not in flags:
        return "needs_review", "Low score + low confidence — review before discarding"
    return "disqualified", "Below qualification threshold"
