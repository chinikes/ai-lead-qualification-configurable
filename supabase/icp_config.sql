-- ═══════════════════════════════════════════════════
-- ICP Configuration Table — stores editable criteria
-- Run this in Supabase SQL Editor
-- ═══════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS icp_config (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  
  -- Company targeting
  target_industries TEXT[] DEFAULT '{}',
  min_employee_count INTEGER DEFAULT 20,
  max_employee_count INTEGER DEFAULT 2000,
  target_countries TEXT[] DEFAULT '{"US","CA","UK","AU","DE","FR","NL"}',
  
  -- Contact targeting
  target_titles TEXT[] DEFAULT '{}',
  target_departments TEXT[] DEFAULT '{}',
  
  -- Tech & intent signals
  positive_tech TEXT[] DEFAULT '{}',
  high_intent_keywords TEXT[] DEFAULT '{}',
  med_intent_keywords TEXT[] DEFAULT '{}',
  
  -- AI prompt context
  company_description TEXT DEFAULT '',
  service_lines TEXT DEFAULT '',
  ideal_client_description TEXT DEFAULT '',
  
  -- Metadata
  is_active BOOLEAN DEFAULT TRUE,
  version TEXT DEFAULT 'v1.0',
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  updated_by TEXT DEFAULT 'system'
);

-- Enable RLS
ALTER TABLE icp_config ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON icp_config FOR ALL USING (TRUE);

-- Insert default AgileDevs config
INSERT INTO icp_config (
  target_industries,
  min_employee_count,
  max_employee_count,
  target_countries,
  target_titles,
  target_departments,
  positive_tech,
  high_intent_keywords,
  med_intent_keywords,
  company_description,
  service_lines,
  ideal_client_description,
  is_active,
  version
) VALUES (
  ARRAY['saas','technology','software','financial services','fintech','healthcare','healthcare technology','e-commerce','professional services','manufacturing','information technology','managed services','telecommunications','media','education','nonprofit','construction','real estate','logistics','energy'],
  20,
  2000,
  ARRAY['US','CA','UK','AU','DE','FR','NL'],
  ARRAY['cto','cio','coo','vp of engineering','vp engineering','vp of operations','vp operations','head of engineering','director of engineering','director of it','director of operations','head of it','head of operations','it director','it manager','engineering manager','product manager','program manager','project manager','head of product','vp of product','director of product','chief digital officer','digital transformation lead','operations manager','business operations manager','director of technology','head of technology'],
  ARRAY['engineering','it','operations','product','technology','digital transformation','business operations','project management'],
  ARRAY['jira','confluence','atlassian','jira service management','jsm','salesforce','hubspot','netsuite','dynamics 365','microsoft dynamics','n8n','make','make.com','zapier','airtable','slack','asana','monday.com','trello','linear','github','gitlab','bitbucket','azure devops','vercel','aws','gcp','azure','heroku','notion','clickup'],
  ARRAY['consulting','implementation','migration','automate','automation','integrate','integration','jira setup','workflow','project management','erp','crm','help with','need someone','looking for a consultant','budget','timeline','proposal','rfp','sow','engagement','outsource','contractor','freelancer','agency'],
  ARRAY['interested','learn more','information','evaluate','streamline','improve','optimize','scale','growing','challenges','pain','struggling','manual process','spreadsheet','too many tools','disorganized'],
  'AgileDevs Consulting provides Project Management consulting, Workflow Automation (n8n, Make.com), and Atlassian Administration (Jira, Confluence, JSM). We also handle ERP/CRM implementations (Salesforce, NetSuite, Dynamics 365) and SaaS/PaaS delivery.',
  '1. Project Management Consulting — Agile delivery, program management, ERP/CRM implementations\n2. Workflow Automation — n8n, Make.com, Zapier; integrating business tools\n3. Atlassian Administration — Jira, Confluence, JSM setup, configuration, migration',
  'Mid-market companies (20-2000 employees) in technology, SaaS, fintech, healthcare, professional services, or manufacturing who need help with digital transformation, tool consolidation, process automation, or scaling operations. Decision makers: CTOs, VPs of Engineering, IT Directors, Operations leaders.',
  TRUE,
  'v1.0'
);
