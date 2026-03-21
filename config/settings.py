"""
System configuration — ICP definition, API settings, scoring weights.
In production, these would be environment variables and a config service.
"""

import os
from dataclasses import dataclass, field


@dataclass
class ICPProfile:
    """Ideal Customer Profile — the Research Agent matches leads against this."""

    # Firmographic criteria
    target_industries: list[str] = field(default_factory=lambda: [
        "SaaS", "Technology", "Software", "Financial Services",
        "Healthcare Technology", "E-commerce", "Digital Marketing",
        "Professional Services", "Manufacturing"
    ])
    min_employee_count: int = 20
    max_employee_count: int = 5000
    target_revenue_range: str = "$2M - $500M ARR"
    preferred_funding_stages: list[str] = field(default_factory=lambda: [
        "Series A", "Series B", "Series C", "Growth", "Public"
    ])
    target_countries: list[str] = field(default_factory=lambda: [
        "US", "CA", "UK", "AU", "DE", "FR", "NL"
    ])

    # Demographic criteria (contact-level)
    target_titles: list[str] = field(default_factory=lambda: [
        "VP of Sales", "VP Sales", "Head of Sales", "Sales Director",
        "Chief Revenue Officer", "CRO", "VP Revenue Operations",
        "Head of Revenue Operations", "Director of Sales Operations",
        "VP of Business Development", "Head of Growth",
        "Chief Operating Officer", "COO"
    ])
    target_seniorities: list[str] = field(default_factory=lambda: [
        "c_level", "vp", "director"
    ])
    target_departments: list[str] = field(default_factory=lambda: [
        "Sales", "Revenue Operations", "Business Development", "Growth"
    ])

    # Technographic signals (positive indicators)
    positive_tech_signals: list[str] = field(default_factory=lambda: [
        "Salesforce", "HubSpot", "Outreach", "Salesloft", "Gong",
        "ZoomInfo", "Apollo", "6sense", "Drift", "Intercom",
        "Marketo", "Pardot", "Zapier", "Make", "n8n"
    ])

    # Negative signals (disqualifiers)
    disqualifying_domains: list[str] = field(default_factory=lambda: [
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        "aol.com", "icloud.com", "mail.com", "protonmail.com"
    ])
    competitor_domains: list[str] = field(default_factory=lambda: [
        "competitor1.com", "competitor2.com"
    ])


@dataclass
class EnrichmentConfig:
    """API keys and settings for enrichment providers."""

    # API Keys (from environment)
    clearbit_api_key: str = field(default_factory=lambda: os.getenv("CLEARBIT_API_KEY", ""))
    apollo_api_key: str = field(default_factory=lambda: os.getenv("APOLLO_API_KEY", ""))
    hunter_api_key: str = field(default_factory=lambda: os.getenv("HUNTER_API_KEY", ""))
    builtwith_api_key: str = field(default_factory=lambda: os.getenv("BUILTWITH_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))

    # HubSpot
    hubspot_access_token: str = field(default_factory=lambda: os.getenv("HUBSPOT_ACCESS_TOKEN", ""))
    hubspot_portal_id: str = field(default_factory=lambda: os.getenv("HUBSPOT_PORTAL_ID", ""))

    # Rate limits (requests per minute)
    clearbit_rpm: int = 60
    apollo_rpm: int = 30
    hunter_rpm: int = 30
    anthropic_rpm: int = 50

    # Timeouts (seconds)
    enrichment_timeout: int = 30
    llm_timeout: int = 60

    # Confidence thresholds
    min_confidence_auto_proceed: float = 0.7
    min_confidence_with_review: float = 0.4


@dataclass
class ScoringWeights:
    """Weights for the composite lead score calculation."""

    firmographic: float = 0.30   # Company fit
    demographic: float = 0.25   # Contact fit
    behavioral: float = 0.20   # Engagement signals
    ai_fit: float = 0.25       # LLM assessment

    # Score thresholds for temperature classification
    hot_threshold: float = 80.0
    warm_threshold: float = 60.0
    cool_threshold: float = 40.0
    # Below cool = cold

    # Qualification thresholds
    auto_qualify_threshold: float = 75.0
    nurture_threshold: float = 45.0
    # Between nurture and qualify = needs_review
    # Below nurture = disqualified


@dataclass
class RoutingConfig:
    """Sales team routing rules."""

    territories: dict = field(default_factory=lambda: {
        "us_west": {
            "states": ["CA", "WA", "OR", "NV", "AZ", "CO", "UT"],
            "reps": ["rep_001", "rep_002"]
        },
        "us_east": {
            "states": ["NY", "MA", "PA", "FL", "GA", "NC", "VA", "NJ", "CT"],
            "reps": ["rep_003", "rep_004"]
        },
        "us_central": {
            "states": ["TX", "IL", "OH", "MI", "MN", "WI", "MO"],
            "reps": ["rep_005"]
        },
        "international": {
            "countries": ["UK", "CA", "AU", "DE", "FR", "NL"],
            "reps": ["rep_006"]
        }
    })

    # Round-robin state per territory (would be Redis/DB in production)
    max_leads_per_rep_per_day: int = 25

    # SLA by temperature
    sla_minutes: dict = field(default_factory=lambda: {
        "hot": 15,
        "warm": 60,
        "cool": 480,
        "cold": 1440
    })


# ── Singleton config instances ───────────────────────────

ICP = ICPProfile()
ENRICHMENT = EnrichmentConfig()
SCORING = ScoringWeights()
ROUTING = RoutingConfig()
