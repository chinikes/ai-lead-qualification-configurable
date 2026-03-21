-- ═══════════════════════════════════════════════════
-- AI Lead Qualification — Supabase Schema
-- Run this in Supabase SQL Editor to create all tables
-- ═══════════════════════════════════════════════════

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Leads Table (raw + enriched) ──────────────────
CREATE TABLE leads (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  
  -- Raw lead data
  email TEXT,
  first_name TEXT,
  last_name TEXT,
  company_name TEXT,
  company_domain TEXT,
  job_title TEXT,
  phone TEXT,
  message TEXT,
  source TEXT DEFAULT 'web_form',
  utm_source TEXT,
  utm_medium TEXT,
  utm_campaign TEXT,
  page_url TEXT,
  
  -- Enriched company data
  company_legal_name TEXT,
  company_industry TEXT,
  company_sub_industry TEXT,
  company_employee_count INTEGER,
  company_revenue TEXT,
  company_funding_stage TEXT,
  company_total_funding TEXT,
  company_year_founded INTEGER,
  company_hq_city TEXT,
  company_hq_state TEXT,
  company_hq_country TEXT,
  company_description TEXT,
  company_linkedin_url TEXT,
  company_technologies TEXT[] DEFAULT '{}',
  company_keywords TEXT[] DEFAULT '{}',
  company_hiring_signals TEXT[] DEFAULT '{}',
  company_recent_news TEXT[] DEFAULT '{}',
  
  -- Enriched contact data
  contact_full_name TEXT,
  contact_title TEXT,
  contact_seniority TEXT DEFAULT 'unknown',
  contact_department TEXT,
  contact_linkedin_url TEXT,
  contact_phone_direct TEXT,
  contact_location_city TEXT,
  contact_location_country TEXT,
  contact_previous_companies TEXT[] DEFAULT '{}',
  contact_is_decision_maker BOOLEAN DEFAULT FALSE,
  
  -- AI analysis
  ai_company_summary TEXT,
  ai_icp_fit_narrative TEXT,
  ai_icp_fit_score REAL,
  ai_buying_signals JSONB DEFAULT '[]',
  ai_pain_points JSONB DEFAULT '[]',
  ai_talking_points TEXT[] DEFAULT '{}',
  ai_urgency TEXT DEFAULT 'exploratory',
  ai_confidence REAL,
  ai_reasoning TEXT,
  
  -- Processing metadata
  status TEXT DEFAULT 'new' CHECK (status IN ('new', 'enriching', 'enriched', 'scoring', 'scored', 'routed', 'engaged', 'failed')),
  enrichment_sources TEXT[] DEFAULT '{}',
  overall_confidence REAL DEFAULT 0,
  flags TEXT[] DEFAULT '{}',
  needs_review BOOLEAN DEFAULT FALSE,
  review_reasons TEXT[] DEFAULT '{}',
  
  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  enriched_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Scores Table ──────────────────────────────────
CREATE TABLE scores (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
  
  -- Dimension scores (0-100)
  firmographic_score REAL DEFAULT 0,
  demographic_score REAL DEFAULT 0,
  behavioral_score REAL DEFAULT 0,
  ai_fit_score REAL DEFAULT 0,
  composite_score REAL DEFAULT 0,
  
  -- Breakdown details
  firmographic_breakdown JSONB DEFAULT '{}',
  demographic_breakdown JSONB DEFAULT '{}',
  behavioral_breakdown JSONB DEFAULT '{}',
  ai_fit_breakdown JSONB DEFAULT '{}',
  
  -- Bonuses and penalties
  bonus_applied REAL DEFAULT 0,
  penalty_applied REAL DEFAULT 0,
  
  -- Classification
  temperature TEXT DEFAULT 'cold' CHECK (temperature IN ('hot', 'warm', 'cool', 'cold')),
  decision TEXT DEFAULT 'needs_review' CHECK (decision IN ('qualified', 'nurture', 'needs_review', 'disqualified')),
  decision_reasoning TEXT,
  
  -- Metadata
  scoring_model_version TEXT DEFAULT 'v1.0',
  scored_at TIMESTAMPTZ DEFAULT NOW(),
  
  UNIQUE(lead_id)
);

-- ── Routing Table ─────────────────────────────────
CREATE TABLE routing (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
  score_id UUID REFERENCES scores(id) ON DELETE CASCADE,
  
  assigned_rep_id TEXT,
  assigned_rep_name TEXT,
  assigned_rep_email TEXT,
  territory TEXT,
  routing_reason TEXT,
  priority TEXT DEFAULT 'p3_queue',
  sla_response_minutes INTEGER DEFAULT 1440,
  
  routed_at TIMESTAMPTZ DEFAULT NOW(),
  
  UNIQUE(lead_id)
);

-- ── Engagement Plans ──────────────────────────────
CREATE TABLE engagement_plans (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
  
  strategy TEXT CHECK (strategy IN ('accelerate', 'educate', 'warm_up')),
  strategy_reasoning TEXT,
  total_touches INTEGER DEFAULT 0,
  sequence_duration_days INTEGER DEFAULT 0,
  
  -- Sequence steps stored as JSONB array
  sequence_steps JSONB DEFAULT '[]',
  personalization_inputs JSONB DEFAULT '{}',
  
  created_at TIMESTAMPTZ DEFAULT NOW(),
  
  UNIQUE(lead_id)
);

-- ── Outcomes (Feedback Loop) ──────────────────────
CREATE TABLE outcomes (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
  
  outcome TEXT CHECK (outcome IN ('closed_won', 'closed_lost')),
  deal_value REAL,
  days_to_close INTEGER,
  loss_reason TEXT,
  
  -- Snapshot of scores at time of qualification (for analysis)
  firmographic_score_snapshot REAL,
  demographic_score_snapshot REAL,
  behavioral_score_snapshot REAL,
  ai_fit_score_snapshot REAL,
  composite_score_snapshot REAL,
  
  closed_at TIMESTAMPTZ DEFAULT NOW(),
  
  UNIQUE(lead_id)
);

-- ── Activity Log ──────────────────────────────────
CREATE TABLE activity_log (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
  
  event_type TEXT NOT NULL,  -- 'intake', 'enriched', 'scored', 'qualified', 'routed', etc.
  message TEXT NOT NULL,
  metadata JSONB DEFAULT '{}',
  
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Scoring Weights (configurable) ────────────────
CREATE TABLE scoring_config (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  
  firmographic_weight REAL DEFAULT 0.30,
  demographic_weight REAL DEFAULT 0.25,
  behavioral_weight REAL DEFAULT 0.20,
  ai_fit_weight REAL DEFAULT 0.25,
  
  hot_threshold REAL DEFAULT 80,
  warm_threshold REAL DEFAULT 60,
  cool_threshold REAL DEFAULT 40,
  auto_qualify_threshold REAL DEFAULT 75,
  nurture_threshold REAL DEFAULT 45,
  
  is_active BOOLEAN DEFAULT TRUE,
  version TEXT DEFAULT 'v1.0',
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  updated_by TEXT DEFAULT 'system'
);

-- Insert default scoring config
INSERT INTO scoring_config (
  firmographic_weight, demographic_weight, behavioral_weight, ai_fit_weight,
  hot_threshold, warm_threshold, cool_threshold,
  auto_qualify_threshold, nurture_threshold,
  is_active, version
) VALUES (
  0.30, 0.25, 0.20, 0.25,
  80, 60, 40,
  75, 45,
  TRUE, 'v1.0'
);

-- ── Indexes ───────────────────────────────────────
CREATE INDEX idx_leads_email ON leads(email);
CREATE INDEX idx_leads_status ON leads(status);
CREATE INDEX idx_leads_created ON leads(created_at DESC);
CREATE INDEX idx_leads_domain ON leads(company_domain);
CREATE INDEX idx_scores_lead ON scores(lead_id);
CREATE INDEX idx_scores_decision ON scores(decision);
CREATE INDEX idx_scores_temperature ON scores(temperature);
CREATE INDEX idx_scores_composite ON scores(composite_score DESC);
CREATE INDEX idx_routing_lead ON routing(lead_id);
CREATE INDEX idx_routing_rep ON routing(assigned_rep_id);
CREATE INDEX idx_outcomes_lead ON outcomes(lead_id);
CREATE INDEX idx_outcomes_outcome ON outcomes(outcome);
CREATE INDEX idx_activity_created ON activity_log(created_at DESC);
CREATE INDEX idx_activity_lead ON activity_log(lead_id);
CREATE INDEX idx_activity_type ON activity_log(event_type);

-- ── Row Level Security (optional but recommended) ─
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE routing ENABLE ROW LEVEL SECURITY;
ALTER TABLE engagement_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE scoring_config ENABLE ROW LEVEL SECURITY;

-- Allow all operations via service role key (used by API)
CREATE POLICY "Service role full access" ON leads FOR ALL USING (TRUE);
CREATE POLICY "Service role full access" ON scores FOR ALL USING (TRUE);
CREATE POLICY "Service role full access" ON routing FOR ALL USING (TRUE);
CREATE POLICY "Service role full access" ON engagement_plans FOR ALL USING (TRUE);
CREATE POLICY "Service role full access" ON outcomes FOR ALL USING (TRUE);
CREATE POLICY "Service role full access" ON activity_log FOR ALL USING (TRUE);
CREATE POLICY "Service role full access" ON scoring_config FOR ALL USING (TRUE);
