"""Supabase client for all database operations."""

import os
import json
from datetime import datetime
from supabase import create_client, Client

def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return create_client(url, key)


# ── Lead Operations ──────────────────────────────────

def create_lead(db: Client, data: dict) -> dict:
    """Insert a new raw lead. Returns the created record."""
    result = db.table("leads").insert(data).execute()
    return result.data[0] if result.data else {}


def update_lead(db: Client, lead_id: str, data: dict) -> dict:
    """Update a lead record."""
    data["updated_at"] = datetime.utcnow().isoformat()
    result = db.table("leads").update(data).eq("id", lead_id).execute()
    return result.data[0] if result.data else {}


def get_lead(db: Client, lead_id: str) -> dict | None:
    result = db.table("leads").select("*").eq("id", lead_id).execute()
    return result.data[0] if result.data else None


def get_lead_by_email(db: Client, email: str) -> dict | None:
    result = db.table("leads").select("*").eq("email", email).execute()
    return result.data[0] if result.data else None


def list_leads(db: Client, limit: int = 50, offset: int = 0, status: str = None, decision: str = None) -> list[dict]:
    query = db.table("leads").select(
        "*, scores(*), routing(*)"
    ).order("created_at", desc=True).limit(limit).offset(offset)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return result.data or []


# ── Score Operations ─────────────────────────────────

def upsert_score(db: Client, lead_id: str, data: dict) -> dict:
    data["lead_id"] = lead_id
    result = db.table("scores").upsert(data, on_conflict="lead_id").execute()
    return result.data[0] if result.data else {}


def get_score(db: Client, lead_id: str) -> dict | None:
    result = db.table("scores").select("*").eq("lead_id", lead_id).execute()
    return result.data[0] if result.data else None


# ── Routing Operations ───────────────────────────────

def upsert_routing(db: Client, lead_id: str, score_id: str, data: dict) -> dict:
    data["lead_id"] = lead_id
    data["score_id"] = score_id
    result = db.table("routing").upsert(data, on_conflict="lead_id").execute()
    return result.data[0] if result.data else {}


# ── Engagement Operations ────────────────────────────

def upsert_engagement(db: Client, lead_id: str, data: dict) -> dict:
    data["lead_id"] = lead_id
    result = db.table("engagement_plans").upsert(data, on_conflict="lead_id").execute()
    return result.data[0] if result.data else {}


# ── Outcome Operations ───────────────────────────────

def record_outcome(db: Client, lead_id: str, data: dict) -> dict:
    data["lead_id"] = lead_id
    result = db.table("outcomes").upsert(data, on_conflict="lead_id").execute()
    return result.data[0] if result.data else {}


# ── Activity Log ─────────────────────────────────────

def log_activity(db: Client, event_type: str, message: str, lead_id: str = None, metadata: dict = None):
    data = {
        "event_type": event_type,
        "message": message,
        "lead_id": lead_id,
        "metadata": json.dumps(metadata or {}),
    }
    db.table("activity_log").insert(data).execute()


def get_activity(db: Client, limit: int = 30) -> list[dict]:
    result = (
        db.table("activity_log")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ── Analytics ────────────────────────────────────────

def get_analytics(db: Client) -> dict:
    """Compute dashboard analytics from DB."""
    all_leads = db.table("leads").select("id, status, created_at").execute().data or []
    all_scores = db.table("scores").select("*").execute().data or []
    all_routing = db.table("routing").select("*").execute().data or []

    total = len(all_leads)
    qualified = sum(1 for s in all_scores if s.get("decision") == "qualified")
    hot = sum(1 for s in all_scores if s.get("temperature") == "hot")
    avg_score = (
        round(sum(s.get("composite_score", 0) for s in all_scores) / len(all_scores), 1)
        if all_scores else 0
    )
    qual_rate = round((qualified / total) * 100) if total > 0 else 0

    # Decision distribution
    decisions = {}
    for s in all_scores:
        d = s.get("decision", "unknown")
        decisions[d] = decisions.get(d, 0) + 1

    # Temperature distribution
    temps = {}
    for s in all_scores:
        t = s.get("temperature", "unknown")
        temps[t] = temps.get(t, 0) + 1

    # Rep workload
    rep_loads = {}
    for r in all_routing:
        name = r.get("assigned_rep_name")
        if name:
            rep_loads[name] = rep_loads.get(name, 0) + 1

    # Source distribution
    sources = {}
    for l in all_leads:
        src = l.get("source", "unknown") if isinstance(l, dict) else "unknown"

    return {
        "total_leads": total,
        "qualified": qualified,
        "hot_leads": hot,
        "avg_score": avg_score,
        "qual_rate": qual_rate,
        "decisions": decisions,
        "temperatures": temps,
        "rep_workload": rep_loads,
    }


# ── Scoring Config ───────────────────────────────────

def get_scoring_config(db: Client) -> dict:
    result = db.table("scoring_config").select("*").eq("is_active", True).limit(1).execute()
    return result.data[0] if result.data else {
        "firmographic_weight": 0.30,
        "demographic_weight": 0.25,
        "behavioral_weight": 0.20,
        "ai_fit_weight": 0.25,
        "hot_threshold": 80,
        "warm_threshold": 60,
        "cool_threshold": 40,
        "auto_qualify_threshold": 75,
        "nurture_threshold": 45,
    }
