"""
Data schemas for the AI Lead Qualification system.
These Pydantic models define the contract between all agents.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
from datetime import datetime
from enum import Enum
import re


# ── Enums ────────────────────────────────────────────────

class LeadSource(str, Enum):
    WEB_FORM = "web_form"
    EMAIL = "email"
    LINKEDIN = "linkedin"
    CHAT_WIDGET = "chat_widget"
    API = "api"
    REFERRAL = "referral"
    EVENT = "event"


class CompanySize(str, Enum):
    SOLO = "1"
    MICRO = "2-10"
    SMALL = "11-50"
    MEDIUM = "51-200"
    LARGE = "201-1000"
    ENTERPRISE = "1001-5000"
    MEGA = "5000+"


class Seniority(str, Enum):
    C_LEVEL = "c_level"
    VP = "vp"
    DIRECTOR = "director"
    MANAGER = "manager"
    SENIOR_IC = "senior_ic"
    IC = "ic"
    UNKNOWN = "unknown"


class LeadTemperature(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COOL = "cool"
    COLD = "cold"


class QualificationDecision(str, Enum):
    QUALIFIED = "qualified"
    NURTURE = "nurture"
    DISQUALIFIED = "disqualified"
    REVIEW = "needs_review"


# ── Raw Lead (Input from any source) ────────────────────

class RawLead(BaseModel):
    """What arrives from inbound sources — minimal, messy, incomplete."""

    source: LeadSource
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company_name: Optional[str] = None
    company_domain: Optional[str] = None
    job_title: Optional[str] = None
    phone: Optional[str] = None
    message: Optional[str] = None
    form_data: Optional[dict] = Field(default=None, description="Raw key-value pairs from form submission")
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    page_url: Optional[str] = None
    ip_address: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", v):
            return None
        return v

    @field_validator("company_domain")
    @classmethod
    def normalize_domain(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        v = re.sub(r"^(https?://)?(www\.)?", "", v)
        v = v.rstrip("/")
        return v


# ── Company Enrichment ───────────────────────────────────

class CompanyEnrichment(BaseModel):
    """Enriched company profile from Clearbit, Apollo, and web scraping."""

    domain: str
    legal_name: Optional[str] = None
    industry: Optional[str] = None
    sub_industry: Optional[str] = None
    employee_count: Optional[int] = None
    employee_range: Optional[CompanySize] = None
    estimated_revenue: Optional[str] = None
    funding_stage: Optional[str] = None
    total_funding: Optional[str] = None
    year_founded: Optional[int] = None
    headquarters_city: Optional[str] = None
    headquarters_state: Optional[str] = None
    headquarters_country: Optional[str] = None
    description: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    technologies: list[str] = Field(default_factory=list, description="Detected tech stack")
    keywords: list[str] = Field(default_factory=list, description="Industry/product keywords")
    recent_news: list[str] = Field(default_factory=list, description="Recent headlines or press releases")
    hiring_signals: list[str] = Field(default_factory=list, description="Open roles indicating growth/needs")
    enrichment_sources: list[str] = Field(default_factory=list)
    enrichment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ── Contact Enrichment ───────────────────────────────────

class ContactEnrichment(BaseModel):
    """Enriched contact profile."""

    email: str
    full_name: Optional[str] = None
    normalized_title: Optional[str] = None
    seniority: Seniority = Seniority.UNKNOWN
    department: Optional[str] = None
    linkedin_url: Optional[str] = None
    phone_direct: Optional[str] = None
    phone_mobile: Optional[str] = None
    location_city: Optional[str] = None
    location_state: Optional[str] = None
    location_country: Optional[str] = None
    previous_companies: list[str] = Field(default_factory=list)
    is_decision_maker: bool = False
    enrichment_sources: list[str] = Field(default_factory=list)
    enrichment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ── AI Analysis (LLM Output) ────────────────────────────

class BuyingSignal(BaseModel):
    """An individual buying signal detected by the LLM."""

    signal: str = Field(description="Description of the signal")
    source: str = Field(description="Where this signal was detected")
    strength: Literal["strong", "moderate", "weak"] = "moderate"


class PainPoint(BaseModel):
    """An inferred pain point."""

    pain_point: str
    evidence: str
    relevance_to_product: Literal["high", "medium", "low"] = "medium"


class AIAnalysis(BaseModel):
    """Structured LLM analysis of the enriched lead."""

    company_summary: str = Field(description="2-3 sentence company overview")
    icp_fit_narrative: str = Field(description="Why this lead does/doesn't match the ICP")
    icp_fit_score: float = Field(ge=0.0, le=1.0, description="0.0 = terrible fit, 1.0 = perfect fit")
    buying_signals: list[BuyingSignal] = Field(default_factory=list)
    pain_points: list[PainPoint] = Field(default_factory=list)
    recommended_talking_points: list[str] = Field(default_factory=list)
    urgency_assessment: Literal["immediate", "near_term", "exploratory", "not_ready"] = "exploratory"
    confidence: float = Field(ge=0.0, le=1.0, description="LLM's self-assessed confidence")
    reasoning: str = Field(description="Chain-of-thought explanation for the assessment")


# ── Enriched Lead (Research Agent Output) ────────────────

class EnrichedLead(BaseModel):
    """Complete output of the Research Agent. Input to the Qualification Agent."""

    # Original data
    raw_lead: RawLead

    # Enrichment results
    company: Optional[CompanyEnrichment] = None
    contact: Optional[ContactEnrichment] = None
    ai_analysis: Optional[AIAnalysis] = None

    # Metadata
    enrichment_started_at: datetime = Field(default_factory=datetime.utcnow)
    enrichment_completed_at: Optional[datetime] = None
    enrichment_duration_ms: Optional[int] = None
    overall_data_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list, description="Warnings: missing_email, free_email, low_confidence, etc.")
    needs_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


# ── Lead Score (Qualification Agent Output) ──────────────

class LeadScore(BaseModel):
    """Output of the Qualification Agent."""

    enriched_lead: EnrichedLead

    # Scoring breakdown
    firmographic_score: float = Field(ge=0.0, le=100.0)
    demographic_score: float = Field(ge=0.0, le=100.0)
    behavioral_score: float = Field(ge=0.0, le=100.0)
    ai_fit_score: float = Field(ge=0.0, le=100.0)
    composite_score: float = Field(ge=0.0, le=100.0)

    # Classification
    temperature: LeadTemperature = LeadTemperature.COLD
    decision: QualificationDecision = QualificationDecision.REVIEW
    decision_reasoning: str = ""

    # Scoring metadata
    scoring_model_version: str = "v1.0"
    scored_at: datetime = Field(default_factory=datetime.utcnow)


# ── Routing Decision ────────────────────────────────────

class RoutingDecision(BaseModel):
    """Output of the Routing Agent."""

    lead_score: LeadScore
    assigned_rep_id: Optional[str] = None
    assigned_rep_name: Optional[str] = None
    assigned_rep_email: Optional[str] = None
    territory: Optional[str] = None
    routing_reason: str = ""
    priority: Literal["p0_immediate", "p1_today", "p2_this_week", "p3_queue"] = "p3_queue"
    sla_response_minutes: int = 1440  # Default 24h
    routed_at: datetime = Field(default_factory=datetime.utcnow)
