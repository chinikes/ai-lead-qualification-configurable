# AI Lead Qualification & CRM Automation System

A production-grade multi-agent system that automates lead qualification, enrichment, scoring, and routing using AI agents orchestrated through n8n, integrated with HubSpot CRM.

## Quick Start

```bash
pip install -r requirements.txt
python -m tests.test_research_agent       # Phase 1: Research Agent only
python -m tests.test_qualification_agent  # Phase 1+2: Research → Qualification
python -m tests.test_full_pipeline        # Full pipeline: Research → Qualification → Routing → Engagement
python -m tests.test_feedback_loop        # Feedback loop: outcome analysis + weight recalibration
```

## Project Structure

```
ai-lead-qualification/
├── config/
│   ├── schemas.py              # Pydantic data models (full system contract)
│   └── settings.py             # ICP definition, API config, scoring weights
├── agents/
│   ├── research_agent.py       # Research Agent — enrichment orchestrator
│   ├── qualification_agent.py  # Qualification Agent — scoring & decisions
│   ├── routing_agent.py        # Routing Agent — territory, round-robin, SLA
│   ├── engagement_agent.py     # Engagement Agent — personalized sequences
│   ├── feedback_loop.py        # Feedback Loop — outcome tracking & retraining
│   ├── enrichment_providers.py # Clearbit, Apollo, Hunter + Mock providers
│   └── hubspot_integration.py  # HubSpot CRM bidirectional sync
├── workflows/
│   ├── research_agent_workflow.json       # n8n workflow — Phase 1
│   └── qualification_agent_workflow.json  # n8n workflow — Phase 2
├── dashboard/
│   └── LeadQualificationDashboard.jsx     # React ops dashboard
├── tests/
│   ├── test_research_agent.py       # Research Agent demo (5 scenarios)
│   ├── test_qualification_agent.py  # Qualification demo (6 scenarios)
│   ├── test_full_pipeline.py        # Full 4-agent pipeline (4 scenarios)
│   └── test_feedback_loop.py        # Feedback loop analysis demo
├── .env.example
├── .gitignore
└── requirements.txt
```

## Research Agent Pipeline

| Stage | Description | Implementation |
|-------|-------------|----------------|
| 1. Intake | Webhook receives raw lead JSON | n8n webhook node |
| 2. Normalize | Clean emails, extract domain, detect free emails | n8n Code node + Python |
| 3. Enrich | Parallel API calls (Clearbit, Apollo, Hunter) | asyncio.gather |
| 4. AI Analysis | Claude structured JSON: ICP fit, signals, pain points | Anthropic API |
| 5. Validate | Confidence scoring, low-quality flagging | Weighted formula |
| 6. Output | HubSpot sync, Slack alerts, qualification queue | n8n integrations |

## Key Design Decisions

- **Parallel enrichment** — All providers fire simultaneously. 500ms vs 3s sequential.
- **Provider-agnostic merge** — "First non-null wins" for scalars, union for lists.
- **Structured LLM output** — Claude returns JSON matching a Pydantic schema.
- **Confidence-gated routing** — Below 40% → human review. 40-70% → flagged. 70%+ → auto-proceed.
- **Mock provider** — Deterministic fake data for portfolio demos (same input = same output).

## Environment Variables

```bash
CLEARBIT_API_KEY=sk_...
APOLLO_API_KEY=...
HUNTER_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
HUBSPOT_ACCESS_TOKEN=pat-...
```

## n8n Workflow Import

1. Open n8n → Workflows → Import from File
2. Select `workflows/research_agent_workflow.json`
3. Configure API credentials
4. Activate and test with: `POST /webhook/lead-intake`

## Data Schemas

| Model | Purpose |
|-------|---------|
| `RawLead` | Inbound data from any source |
| `CompanyEnrichment` | Enriched company profile |
| `ContactEnrichment` | Enriched contact profile |
| `AIAnalysis` | LLM output: ICP fit, signals, pain points |
| `EnrichedLead` | Research Agent output → Qualification Agent input |
| `LeadScore` | Qualification Agent output (scores + decision) |
| `RoutingDecision` | Routing Agent output (assignment + SLA) |

## Qualification Agent (Phase 2)

### Four Scoring Dimensions

| Dimension | Weight | What It Measures | Points |
|-----------|--------|-----------------|--------|
| Firmographic | 30% | Industry, size, funding, geography, tech stack | 100 |
| Demographic | 25% | Seniority, title match, department, decision maker | 100 |
| Behavioral | 20% | Source, message intent, UTM, page, timing | 100 |
| AI Fit | 25% | ICP score, buying signals, pain points, urgency | 100 |

### Composite Score Formula

`Composite = (Firm × 0.30 + Demo × 0.25 + Behav × 0.20 + AI × 0.25) + Bonuses − Penalties`

**Bonuses**: C-level decision maker at ICP company (+8), multiple strong signals (+5), immediate urgency (+5), sales hiring (+3)

**Penalties**: Free email (−10), low confidence (−4 to −8), no company data (−12), missing contact channels (−3)

### Decision Matrix

| Composite | Decision | Action |
|-----------|----------|--------|
| 75+ | Qualified | → Routing Agent → Sales Rep |
| 45-74 | Context-dependent | DM at ICP → Qualified, Low data → Review, else → Nurture |
| <45 | Disqualified | Low confidence → Review, else → Archive |

## Routing Agent (Phase 3)

Territory-based assignment with round-robin distribution and capacity management.

### Routing Logic

1. **Resolve territory** from company HQ or contact location (US state → territory, international → catch-all)
2. **Find available reps** in territory with remaining daily capacity
3. **Specialty bonus** — reps matching the lead's industry get priority
4. **Round-robin select** within the filtered rep pool
5. **Hot lead burst** — hot leads can override capacity limits (assigned to least-loaded rep)
6. **No rep available** — lead queued with SLA timer

### SLA by Temperature

| Temperature | Priority | SLA |
|------------|----------|-----|
| Hot | P0 Immediate | 15 min |
| Warm | P1 Today | 60 min |
| Cool | P2 This Week | 8 hours |
| Cold | P3 Queue | 24 hours |

## Engagement Agent (Phase 4)

AI-powered personalized outreach sequence generation with multi-touch cadences.

### Three Engagement Strategies

| Strategy | When Used | Cadence | Touches |
|----------|-----------|---------|---------|
| Accelerate | Hot leads, C-level decision makers | 10 days | 6 (email + LinkedIn + phone) |
| Educate | Warm/qualified, review leads | 14 days | 5 (email + LinkedIn) |
| Warm Up | Cool/nurture, cold leads | 21 days | 4 (email + LinkedIn, light touch) |

### Features

- **LLM-powered content** — Claude generates personalized emails using buying signals, pain points, and talking points from the Research Agent
- **A/B variants** — First email gets two versions with different hooks (direct-ask vs peer-proof)
- **Multi-touch types** — Email, LinkedIn connection requests, phone call scripts with objection prep
- **Template fallback** — Full template bank when LLM is unavailable, still personalized with lead data
- **Strategy upgrades** — Warm leads with C-level seniority auto-upgrade to Accelerate cadence

## Feedback Loop (Phase 5)

Closes the loop by tracking deal outcomes and using them to improve the scoring model over time.

### What It Does

1. **Outcome Capture** — Links closed-won/lost deals back to their original lead scores
2. **Pattern Analysis** — Statistical correlation analysis: which scoring dimensions predict wins?
3. **Weight Recalibration** — Adjusts the 4 dimension weights (bounded ±5% per cycle, min 10%, max 50%)
4. **Drift Detection** — Alerts when score separation, win rate, or high-score loss rate degrades
5. **ICP Refinement** — Suggests industry, employee range, and source changes based on won deals

### Drift Alerts

| Check | Warning | Critical |
|-------|---------|----------|
| Score separation | <75% of baseline | <50% of baseline |
| Qualified win rate | <80% of baseline | <60% of baseline |
| High-score losses | >30% of losses scored 80+ | — |

### Safety Constraints

- Max weight adjustment: ±5% per dimension per cycle
- Minimum sample size: 20 outcomes before recalibrating
- All weights must stay between 10%-50% and sum to 100%
- ICP changes are suggestions only — human review required

## Roadmap

All 5 phases are complete:

- [x] **Phase 1**: Research Agent (enrichment + AI analysis)
- [x] **Phase 2**: Qualification Agent (scoring + classification)
- [x] **Phase 3**: Routing Agent (territory + round-robin + SLA)
- [x] **Phase 4**: Engagement Agent (personalized multi-touch sequences)
- [x] **Phase 5**: Feedback Loop (outcome tracking + model retraining)

### Potential Extensions
- Vector store (Pinecone/Weaviate) for ICP similarity matching
- Real-time webhook listeners for HubSpot deal stage changes
- Engagement reply detection + automatic sequence adjustment
- Dashboard backend API (FastAPI) for live data
- Multi-tenant support for agency deployments
