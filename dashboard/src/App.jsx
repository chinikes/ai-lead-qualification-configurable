import { useState, useEffect, useCallback, useRef } from "react";

const API = "";  // Same origin — Vercel serves both dashboard and API

const TEMP_CONFIG = {
  hot: { color: "#EF4444", bg: "rgba(239,68,68,0.12)", label: "HOT", icon: "▲" },
  warm: { color: "#F59E0B", bg: "rgba(245,158,11,0.12)", label: "WRM", icon: "◆" },
  cool: { color: "#3B82F6", bg: "rgba(59,130,246,0.12)", label: "COL", icon: "●" },
  cold: { color: "#6B7280", bg: "rgba(107,114,128,0.12)", label: "CLD", icon: "▼" },
};

const DECISION_CONFIG = {
  qualified: { color: "#10B981", bg: "rgba(16,185,129,0.12)", label: "QUALIFIED" },
  nurture: { color: "#F59E0B", bg: "rgba(245,158,11,0.12)", label: "NURTURE" },
  needs_review: { color: "#8B5CF6", bg: "rgba(139,92,246,0.12)", label: "REVIEW" },
  disqualified: { color: "#EF4444", bg: "rgba(239,68,68,0.12)", label: "DISQUALIFIED" },
};

const STAGE_LABELS = ["Intake", "Enrichment", "AI Analysis", "Scoring", "Decision", "Routed"];

const STATUS_TO_STAGE = {
  new: 0, enriching: 1, enriched: 2, scoring: 3, scored: 4, routed: 5, engaged: 5, failed: 0,
};

function m(extra = {}) { return { fontFamily: "'JetBrains Mono', monospace", ...extra }; }

function Badge({ label, color, bg }) {
  return <span style={{ display: "inline-flex", padding: "2px 7px", borderRadius: 4, fontSize: 9, fontWeight: 600, letterSpacing: "0.05em", ...m(), color, background: bg, border: `1px solid ${color}22` }}>{label}</span>;
}

function ScoreBar({ value, color = "#3B82F6", height = 5 }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ flex: 1, height, background: "rgba(255,255,255,0.06)", borderRadius: height / 2, overflow: "hidden" }}>
        <div style={{ width: `${Math.min(value || 0, 100)}%`, height: "100%", background: color, borderRadius: height / 2, transition: "width 0.5s ease" }} />
      </div>
      <span style={{ ...m({ fontSize: 10, color: "rgba(255,255,255,0.45)", minWidth: 22, textAlign: "right" }) }}>{Math.round(value || 0)}</span>
    </div>
  );
}

function MetricCard({ label, value, sub, accent }) {
  return (
    <div style={{ padding: "12px 16px", background: "rgba(255,255,255,0.03)", borderRadius: 8, border: "1px solid rgba(255,255,255,0.06)" }}>
      <div style={{ fontSize: 9, color: "rgba(255,255,255,0.35)", fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 5 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, ...m(), color: accent || "#fff", lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: 9, color: "rgba(255,255,255,0.25)", marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function PipelineFunnel({ leads }) {
  const stages = STAGE_LABELS.map((l, i) => ({ l, c: leads.filter(x => (STATUS_TO_STAGE[x.status] || 0) >= i).length }));
  const mx = Math.max(...stages.map(s => s.c), 1);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 56 }}>
      {stages.map((s, i) => {
        const h = Math.max((s.c / mx) * 46, 4); const op = 0.25 + (i / 5) * 0.75;
        return <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
          <span style={{ ...m({ fontSize: 10, fontWeight: 600 }), color: `rgba(59,130,246,${op + 0.2})` }}>{s.c}</span>
          <div style={{ width: "100%", height: h, background: `rgba(59,130,246,${op * 0.45})`, borderRadius: 3, transition: "height 0.3s" }} />
          <span style={{ fontSize: 7, color: "rgba(255,255,255,0.25)", textTransform: "uppercase" }}>{s.l}</span>
        </div>;
      })}
    </div>
  );
}

/* ── Transform DB lead to display format ─────────── */
function transformLead(dbLead) {
  const score = Array.isArray(dbLead.scores) ? (dbLead.scores[0] || {}) : (dbLead.scores || {});
  const route = Array.isArray(dbLead.routing) ? (dbLead.routing[0] || {}) : (dbLead.routing || {});
  const signals = (dbLead.ai_buying_signals || []).map(s => typeof s === "string" ? s : s.signal).filter(Boolean);
  const pains = (dbLead.ai_pain_points || []).map(p => typeof p === "string" ? p : p.pain_point).filter(Boolean);
  return {
    id: dbLead.id,
    email: dbLead.email,
    name: dbLead.contact_full_name || [dbLead.first_name, dbLead.last_name].filter(Boolean).join(" ") || dbLead.email || "Unknown",
    title: dbLead.contact_title || dbLead.job_title || "Unknown",
    company: dbLead.company_legal_name || dbLead.company_name || dbLead.company_domain || "Unknown",
    industry: dbLead.company_industry || "Unknown",
    employees: dbLead.company_employee_count,
    revenue: dbLead.company_revenue,
    source: dbLead.source || "web_form",
    message: dbLead.message,
    status: dbLead.status || "new",
    stage: STATUS_TO_STAGE[dbLead.status] || 0,
    scores: {
      firmographic: score.firmographic_score || 0,
      demographic: score.demographic_score || 0,
      behavioral: score.behavioral_score || 0,
      ai_fit: score.ai_fit_score || 0,
    },
    composite: score.composite_score || 0,
    temperature: score.temperature || "cold",
    decision: score.decision || "needs_review",
    signals,
    painPoints: pains,
    talkingPoints: dbLead.ai_talking_points || [],
    confidence: dbLead.overall_confidence || 0,
    flags: dbLead.flags || [],
    routing: route.assigned_rep_name ? {
      rep: route.assigned_rep_name,
      repId: route.assigned_rep_id,
      territory: route.territory,
      sla: `${route.sla_response_minutes || 60} min`,
    } : null,
    timestamp: new Date(dbLead.created_at).getTime(),
  };
}

/* ── Activity Feed ───────────────────────────────── */
function ActivityFeed({ events }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = 0; }, [events.length]);
  const icons = { intake: { i: "→", c: "#6B7280" }, enriching: { i: "◈", c: "#14B8A6" }, enriched: { i: "◈", c: "#14B8A6" }, analyzed: { i: "◈", c: "#A855F7" }, scored: { i: "▣", c: "#3B82F6" }, qualified: { i: "✓", c: "#10B981" }, nurture: { i: "~", c: "#F59E0B" }, review: { i: "?", c: "#8B5CF6" }, disqualified: { i: "✗", c: "#EF4444" }, routed: { i: "⇒", c: "#10B981" }, failed: { i: "!", c: "#EF4444" } };
  return (
    <div ref={ref} style={{ maxHeight: 280, overflowY: "auto" }}>
      {events.map((ev, i) => {
        const cfg = icons[ev.event_type] || icons.intake;
        const age = Math.round((Date.now() - new Date(ev.created_at).getTime()) / 1000);
        const ageS = age < 60 ? `${age}s` : age < 3600 ? `${Math.round(age / 60)}m` : `${Math.round(age / 3600)}h`;
        return <div key={ev.id} style={{ display: "flex", gap: 7, padding: "5px 0", borderBottom: "1px solid rgba(255,255,255,0.025)", opacity: Math.max(0.35, 1 - i * 0.03) }}>
          <span style={{ ...m({ fontSize: 10, fontWeight: 600 }), color: cfg.c, minWidth: 12, textAlign: "center" }}>{cfg.i}</span>
          <span style={{ flex: 1, fontSize: 10, color: "rgba(255,255,255,0.5)", lineHeight: 1.3 }}>{ev.message}</span>
          <span style={{ ...m({ fontSize: 8 }), color: "rgba(255,255,255,0.18)" }}>{ageS}</span>
        </div>;
      })}
      {events.length === 0 && <div style={{ color: "rgba(255,255,255,0.15)", fontSize: 10, fontStyle: "italic", padding: 10 }}>No activity yet — submit a lead to get started</div>}
    </div>
  );
}

/* ── New Lead Form ───────────────────────────────── */
function NewLeadForm({ onSubmit, isProcessing }) {
  const [form, setForm] = useState({ email: "", first_name: "", last_name: "", company_domain: "", job_title: "", message: "", source: "web_form" });
  const set = (k, v) => setForm(p => ({ ...p, [k]: v }));

  const handleSubmit = () => {
    if (!form.email && !form.company_domain) return;
    onSubmit(form);
    setForm({ email: "", first_name: "", last_name: "", company_domain: "", job_title: "", message: "", source: "web_form" });
  };

  const inputStyle = {
    width: "100%", padding: "8px 10px", background: "rgba(255,255,255,0.04)",
    border: "1px solid rgba(255,255,255,0.08)", borderRadius: 6, color: "#fff",
    fontSize: 12, fontFamily: "'DM Sans', sans-serif", outline: "none",
  };
  const labelStyle = { fontSize: 9, color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 3, display: "block" };

  return (
    <div style={{ padding: 16 }}>
      <div style={{ fontSize: 10, color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 12 }}>Submit New Lead</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
        <div><label style={labelStyle}>Email</label><input style={inputStyle} value={form.email} onChange={e => set("email", e.target.value)} placeholder="sarah@company.com" /></div>
        <div><label style={labelStyle}>Domain</label><input style={inputStyle} value={form.company_domain} onChange={e => set("company_domain", e.target.value)} placeholder="company.com" /></div>
        <div><label style={labelStyle}>First Name</label><input style={inputStyle} value={form.first_name} onChange={e => set("first_name", e.target.value)} /></div>
        <div><label style={labelStyle}>Last Name</label><input style={inputStyle} value={form.last_name} onChange={e => set("last_name", e.target.value)} /></div>
        <div><label style={labelStyle}>Job Title</label><input style={inputStyle} value={form.job_title} onChange={e => set("job_title", e.target.value)} placeholder="VP of Sales" /></div>
        <div>
          <label style={labelStyle}>Source</label>
          <select style={{ ...inputStyle, appearance: "none" }} value={form.source} onChange={e => set("source", e.target.value)}>
            {["web_form", "referral", "linkedin", "chat_widget", "email", "event", "api"].map(s => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
          </select>
        </div>
      </div>
      <div style={{ marginBottom: 10 }}><label style={labelStyle}>Message</label><textarea style={{ ...inputStyle, minHeight: 50, resize: "vertical" }} value={form.message} onChange={e => set("message", e.target.value)} placeholder="What brought them in..." /></div>
      <button
        onClick={handleSubmit}
        disabled={isProcessing || (!form.email && !form.company_domain)}
        style={{
          width: "100%", padding: "10px", borderRadius: 6, cursor: isProcessing ? "wait" : "pointer",
          ...m({ fontSize: 11, fontWeight: 600, letterSpacing: "0.04em" }),
          background: isProcessing ? "rgba(59,130,246,0.08)" : "rgba(59,130,246,0.15)",
          color: "#3B82F6", border: "1px solid rgba(59,130,246,0.25)",
          opacity: (!form.email && !form.company_domain) ? 0.4 : 1,
        }}
      >
        {isProcessing ? "PROCESSING..." : "SUBMIT & QUALIFY LEAD"}
      </button>
    </div>
  );
}

/* ── Lead Row ────────────────────────────────────── */
function StageTimeline({ lead }) {
  return <div style={{ display: "flex", alignItems: "center", padding: "5px 14px 1px" }}>
    {STAGE_LABELS.map((_, i) => {
      const a = i <= lead.stage, c = i === lead.stage && lead.stage < 5;
      return <div key={i} style={{ display: "flex", alignItems: "center", flex: 1 }}>
        <div style={{ width: 5, height: 5, borderRadius: "50%", background: a ? (c ? "#3B82F6" : "rgba(59,130,246,0.45)") : "rgba(255,255,255,0.06)", boxShadow: c ? "0 0 5px rgba(59,130,246,0.4)" : "none", transition: "all 0.25s" }} />
        {i < 5 && <div style={{ flex: 1, height: 1, background: a ? "rgba(59,130,246,0.2)" : "rgba(255,255,255,0.03)", transition: "all 0.25s" }} />}
      </div>;
    })}
  </div>;
}

function LeadRow({ lead, isSelected, onClick }) {
  const t = TEMP_CONFIG[lead.temperature] || TEMP_CONFIG.cold;
  const d = DECISION_CONFIG[lead.decision] || DECISION_CONFIG.needs_review;
  const age = Math.round((Date.now() - lead.timestamp) / 60000);
  return (
    <div onClick={onClick} style={{ display: "grid", gridTemplateColumns: "1fr 80px 55px 85px 60px 38px", alignItems: "center", padding: "9px 14px", cursor: "pointer", background: isSelected ? "rgba(59,130,246,0.07)" : "transparent", borderLeft: isSelected ? "2px solid #3B82F6" : "2px solid transparent", borderBottom: "1px solid rgba(255,255,255,0.025)", transition: "all 0.1s" }}
      onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = "rgba(255,255,255,0.015)"; }}
      onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}>
      <div><div style={{ fontSize: 12, fontWeight: 600, color: "#fff", marginBottom: 1 }}>{lead.name}</div><div style={{ fontSize: 9, color: "rgba(255,255,255,0.3)" }}>{lead.title} · {lead.company}</div></div>
      <div style={{ fontSize: 9, color: "rgba(255,255,255,0.3)" }}>{(lead.source || "").replace("_", " ")}</div>
      <span style={{ ...m({ fontSize: 12, fontWeight: 600 }), color: t.color }}>{(lead.composite || 0).toFixed(1)}</span>
      <Badge label={d.label} color={d.color} bg={d.bg} />
      <Badge label={`${t.icon} ${t.label}`} color={t.color} bg={t.bg} />
      <span style={{ ...m({ fontSize: 8 }), color: "rgba(255,255,255,0.2)" }}>{age < 60 ? `${age}m` : `${Math.round(age / 60)}h`}</span>
    </div>
  );
}

/* ── Lead Detail Panel ───────────────────────────── */
function LeadDetail({ lead }) {
  if (!lead) return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "rgba(255,255,255,0.12)", fontSize: 11, fontStyle: "italic" }}>Select a lead to inspect</div>;
  const t = TEMP_CONFIG[lead.temperature] || TEMP_CONFIG.cold;
  const d = DECISION_CONFIG[lead.decision] || DECISION_CONFIG.needs_review;
  const dims = [{ k: "firmographic", l: "Firmographic", c: "#14B8A6" }, { k: "demographic", l: "Demographic", c: "#F97316" }, { k: "behavioral", l: "Behavioral", c: "#3B82F6" }, { k: "ai_fit", l: "AI Fit", c: "#A855F7" }];
  const Sec = ({ title, children }) => <div style={{ marginBottom: 14 }}><div style={{ fontSize: 8, color: "rgba(255,255,255,0.28)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6, fontWeight: 600 }}>{title}</div>{children}</div>;

  return (
    <div style={{ padding: "14px 16px", overflowY: "auto", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#fff" }}>{lead.name}</h3>
          <div style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", marginTop: 1 }}>{lead.title}</div>
          <div style={{ fontSize: 10, color: "rgba(255,255,255,0.25)", marginTop: 1 }}>{lead.company} · {lead.industry}{lead.employees ? ` · ${lead.employees} emp` : ""}</div>
          {lead.email && <div style={{ fontSize: 10, color: "rgba(255,255,255,0.2)", marginTop: 2 }}>{lead.email}</div>}
        </div>
        <div style={{ display: "flex", gap: 4, alignItems: "flex-start" }}>
          <Badge label={`${t.icon} ${t.label}`} color={t.color} bg={t.bg} />
          <Badge label={d.label} color={d.color} bg={d.bg} />
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: 12, background: "rgba(255,255,255,0.025)", borderRadius: 8, marginBottom: 14, border: "1px solid rgba(255,255,255,0.05)" }}>
        <div style={{ ...m({ fontSize: 30, fontWeight: 700 }), color: t.color, lineHeight: 1 }}>{(lead.composite || 0).toFixed(1)}</div>
        <div><div style={{ fontSize: 9, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Composite</div><div style={{ fontSize: 9, color: "rgba(255,255,255,0.2)", marginTop: 2 }}>Confidence: {Math.round((lead.confidence || 0) * 100)}%</div></div>
      </div>
      <Sec title="Score Breakdown">{dims.map(d => <div key={d.k} style={{ marginBottom: 5 }}><div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}><span style={{ fontSize: 10, color: "rgba(255,255,255,0.4)" }}>{d.l}</span><span style={{ ...m({ fontSize: 10 }), color: d.c }}>{Math.round(lead.scores?.[d.k] || 0)}</span></div><ScoreBar value={lead.scores?.[d.k] || 0} color={d.c} height={4} /></div>)}</Sec>
      {lead.message && <Sec title="Message"><div style={{ fontSize: 11, color: "rgba(255,255,255,0.45)", lineHeight: 1.5, padding: 9, background: "rgba(255,255,255,0.02)", borderRadius: 6, borderLeft: "2px solid rgba(59,130,246,0.2)" }}>"{lead.message}"</div></Sec>}
      {lead.signals?.length > 0 && <Sec title="Buying Signals">{lead.signals.map((s, i) => <div key={i} style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 3, fontSize: 10, color: "rgba(255,255,255,0.45)" }}><span style={{ color: "#10B981", fontSize: 6 }}>●</span>{s}</div>)}</Sec>}
      {lead.painPoints?.length > 0 && <Sec title="Pain Points">{lead.painPoints.map((p, i) => <div key={i} style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 3, fontSize: 10, color: "rgba(255,255,255,0.45)" }}><span style={{ color: "#F59E0B", fontSize: 6 }}>●</span>{p}</div>)}</Sec>}
      {lead.talkingPoints?.length > 0 && <Sec title="Talking Points">{lead.talkingPoints.map((tp, i) => <div key={i} style={{ display: "flex", gap: 5, marginBottom: 3, fontSize: 10, color: "rgba(255,255,255,0.45)" }}><span style={{ color: "#3B82F6", fontSize: 9 }}>→</span>{tp}</div>)}</Sec>}
      {lead.flags?.length > 0 && <Sec title="Flags"><div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>{lead.flags.map((f, i) => <Badge key={i} label={f.replace(/_/g, " ").toUpperCase()} color="#F59E0B" bg="rgba(245,158,11,0.08)" />)}</div></Sec>}
      {lead.routing && <div style={{ padding: 9, background: "rgba(16,185,129,0.04)", borderRadius: 6, border: "1px solid rgba(16,185,129,0.1)" }}><div style={{ fontSize: 8, color: "#10B981", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5, fontWeight: 600 }}>Routing</div><div style={{ fontSize: 10, color: "rgba(255,255,255,0.45)" }}>Rep: {lead.routing.rep} · Territory: {lead.routing.territory} · SLA: {lead.routing.sla}</div></div>}
    </div>
  );
}

/* ── Settings Panel ───────────────────────────────── */
function SettingsPanel() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/config`).then(r => r.json()).then(data => {
      setConfig(data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      await fetch(`${API}/api/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) { console.error(e); }
    setSaving(false);
  };

  const updateIcp = (key, value) => setConfig(prev => ({ ...prev, icp: { ...prev.icp, [key]: value } }));
  const updateScoring = (key, value) => setConfig(prev => ({ ...prev, scoring: { ...prev.scoring, [key]: value } }));

  const inputStyle = { width: "100%", padding: "8px 10px", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 6, color: "#fff", fontSize: 12, fontFamily: "'DM Sans', sans-serif", outline: "none" };
  const labelStyle = { fontSize: 9, color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 4, display: "block" };
  const sectionStyle = { padding: 16, background: "rgba(255,255,255,0.025)", borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)", marginBottom: 14 };
  const sectionTitle = { fontSize: 11, color: "rgba(255,255,255,0.5)", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", marginBottom: 12 };

  const TagEditor = ({ label, value, onChange }) => {
    const [input, setInput] = useState("");
    const items = value || [];
    const add = () => { if (input.trim() && !items.includes(input.trim().toLowerCase())) { onChange([...items, input.trim().toLowerCase()]); setInput(""); } };
    const remove = (i) => onChange(items.filter((_, idx) => idx !== i));
    return (
      <div style={{ marginBottom: 12 }}>
        <label style={labelStyle}>{label}</label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
          {items.map((item, i) => (
            <span key={i} onClick={() => remove(i)} style={{ display: "inline-flex", alignItems: "center", gap: 3, padding: "2px 7px", borderRadius: 4, fontSize: 10, background: "rgba(59,130,246,0.1)", color: "#3B82F6", border: "1px solid rgba(59,130,246,0.15)", cursor: "pointer" }}>
              {item} <span style={{ fontSize: 8, opacity: 0.5 }}>✕</span>
            </span>
          ))}
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          <input style={{ ...inputStyle, flex: 1 }} value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === "Enter" && add()} placeholder={`Add ${label.toLowerCase()}...`} />
          <button onClick={add} style={{ padding: "8px 12px", borderRadius: 6, background: "rgba(59,130,246,0.1)", color: "#3B82F6", border: "1px solid rgba(59,130,246,0.15)", cursor: "pointer", fontSize: 10, fontWeight: 600 }}>Add</button>
        </div>
      </div>
    );
  };

  if (loading) return <div style={{ padding: 40, textAlign: "center", color: "rgba(255,255,255,0.2)" }}>Loading configuration...</div>;
  if (!config) return <div style={{ padding: 40, textAlign: "center", color: "rgba(239,68,68,0.5)" }}>Failed to load configuration</div>;

  const icp = config.icp || {};
  const scoring = config.scoring || {};

  return (
    <div style={{ flex: 1, padding: "14px 18px", overflowY: "auto", borderTop: "1px solid rgba(255,255,255,0.05)", maxWidth: 900 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "#fff" }}>ICP & Scoring Configuration</h2>
          <p style={{ margin: "4px 0 0", fontSize: 11, color: "rgba(255,255,255,0.3)" }}>Changes apply to all new leads processed after saving</p>
        </div>
        <button onClick={handleSave} disabled={saving} style={{ ...m({ fontSize: 10, fontWeight: 600, letterSpacing: "0.04em" }), padding: "8px 20px", borderRadius: 6, background: saved ? "rgba(16,185,129,0.15)" : "rgba(59,130,246,0.15)", color: saved ? "#10B981" : "#3B82F6", border: `1px solid ${saved ? "rgba(16,185,129,0.25)" : "rgba(59,130,246,0.25)"}`, cursor: saving ? "wait" : "pointer" }}>
          {saving ? "SAVING..." : saved ? "SAVED ✓" : "SAVE CHANGES"}
        </button>
      </div>

      {/* Company Description */}
      <div style={sectionStyle}>
        <div style={sectionTitle}>Company Profile (used in AI prompt)</div>
        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Company Description</label>
          <textarea style={{ ...inputStyle, minHeight: 60, resize: "vertical" }} value={icp.company_description || ""} onChange={e => updateIcp("company_description", e.target.value)} />
        </div>
        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Service Lines</label>
          <textarea style={{ ...inputStyle, minHeight: 60, resize: "vertical" }} value={icp.service_lines || ""} onChange={e => updateIcp("service_lines", e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Ideal Client Description</label>
          <textarea style={{ ...inputStyle, minHeight: 60, resize: "vertical" }} value={icp.ideal_client_description || ""} onChange={e => updateIcp("ideal_client_description", e.target.value)} />
        </div>
      </div>

      {/* Company Targeting */}
      <div style={sectionStyle}>
        <div style={sectionTitle}>Company Targeting</div>
        <TagEditor label="Target Industries" value={icp.target_industries} onChange={v => updateIcp("target_industries", v)} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
          <div>
            <label style={labelStyle}>Min Employees</label>
            <input type="number" style={inputStyle} value={icp.min_employee_count || 20} onChange={e => updateIcp("min_employee_count", parseInt(e.target.value) || 0)} />
          </div>
          <div>
            <label style={labelStyle}>Max Employees</label>
            <input type="number" style={inputStyle} value={icp.max_employee_count || 2000} onChange={e => updateIcp("max_employee_count", parseInt(e.target.value) || 0)} />
          </div>
        </div>
        <TagEditor label="Target Countries" value={icp.target_countries} onChange={v => updateIcp("target_countries", v)} />
      </div>

      {/* Contact Targeting */}
      <div style={sectionStyle}>
        <div style={sectionTitle}>Contact Targeting</div>
        <TagEditor label="Target Job Titles" value={icp.target_titles} onChange={v => updateIcp("target_titles", v)} />
        <TagEditor label="Target Departments" value={icp.target_departments} onChange={v => updateIcp("target_departments", v)} />
      </div>

      {/* Signal Detection */}
      <div style={sectionStyle}>
        <div style={sectionTitle}>Signal Detection</div>
        <TagEditor label="Positive Tech Stack Signals" value={icp.positive_tech} onChange={v => updateIcp("positive_tech", v)} />
        <TagEditor label="High Intent Keywords" value={icp.high_intent_keywords} onChange={v => updateIcp("high_intent_keywords", v)} />
        <TagEditor label="Medium Intent Keywords" value={icp.med_intent_keywords} onChange={v => updateIcp("med_intent_keywords", v)} />
      </div>

      {/* Scoring Weights */}
      <div style={sectionStyle}>
        <div style={sectionTitle}>Scoring Weights & Thresholds</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12, marginBottom: 14 }}>
          {[["firmographic_weight", "Firmographic"], ["demographic_weight", "Demographic"], ["behavioral_weight", "Behavioral"], ["ai_fit_weight", "AI Fit"]].map(([key, label]) => (
            <div key={key}>
              <label style={labelStyle}>{label} Weight</label>
              <input type="number" step="0.05" min="0.05" max="0.60" style={inputStyle} value={scoring[key] || 0.25} onChange={e => updateScoring(key, parseFloat(e.target.value) || 0.25)} />
            </div>
          ))}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
          {[["hot_threshold", "Hot Threshold"], ["warm_threshold", "Warm Threshold"], ["cool_threshold", "Cool Threshold"], ["auto_qualify_threshold", "Auto-Qualify"], ["nurture_threshold", "Nurture Threshold"]].map(([key, label]) => (
            <div key={key}>
              <label style={labelStyle}>{label}</label>
              <input type="number" step="5" min="0" max="100" style={inputStyle} value={scoring[key] || 50} onChange={e => updateScoring(key, parseFloat(e.target.value) || 50)} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── Main Dashboard ──────────────────────────────── */
export default function Dashboard() {
  const [leads, setLeads] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [filter, setFilter] = useState("all");
  const [events, setEvents] = useState([]);
  const [view, setView] = useState("leads");
  const [pulse, setPulse] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => { const t = setInterval(() => setPulse(p => !p), 1200); return () => clearInterval(t); }, []);

  // ── Fetch leads ───────────────────────────────────
  const fetchLeads = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/leads?limit=100`);
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      setLeads((data.leads || []).map(transformLead));
      setError(null);
    } catch (e) {
      console.error("Fetch leads error:", e);
      setError("Cannot reach API — check Vercel deployment");
    }
  }, []);

  // ── Fetch activity ────────────────────────────────
  const fetchActivity = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/activity?limit=40`);
      if (!res.ok) return;
      const data = await res.json();
      setEvents(data.events || []);
    } catch (e) { /* silent */ }
  }, []);

  // ── Initial load + polling ────────────────────────
  useEffect(() => {
    fetchLeads();
    fetchActivity();
    pollRef.current = setInterval(() => {
      fetchLeads();
      fetchActivity();
    }, 5000);
    return () => clearInterval(pollRef.current);
  }, [fetchLeads, fetchActivity]);

  // ── Submit new lead ───────────────────────────────
  const handleSubmitLead = async (formData) => {
    setIsProcessing(true);
    setError(null);
    try {
      // Step 1: Create the lead
      const createRes = await fetch(`${API}/api/leads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });
      if (!createRes.ok) throw new Error("Failed to create lead");
      const { lead } = await createRes.json();

      // Immediately refresh to show the new lead
      await fetchLeads();
      setShowForm(false);

      // Step 2: Process it (enrichment → AI → scoring)
      const processRes = await fetch(`${API}/api/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lead_id: lead.id }),
      });
      if (!processRes.ok) throw new Error("Pipeline processing failed");

      // Refresh again to show results
      await fetchLeads();
      await fetchActivity();

    } catch (e) {
      setError(e.message);
    } finally {
      setIsProcessing(false);
    }
  };

  const sel = leads.find(l => l.id === selectedId);
  const scored = leads.filter(l => l.stage >= 4);
  const filt = filter === "all" ? leads : leads.filter(l => l.decision === filter);

  const st = {
    total: leads.length,
    qual: scored.filter(l => l.decision === "qualified").length,
    avg: scored.length > 0 ? (scored.reduce((s, l) => s + l.composite, 0) / scored.length).toFixed(1) : "0",
    hot: scored.filter(l => l.temperature === "hot").length,
  };
  const qr = scored.length > 0 ? Math.round((st.qual / scored.length) * 100) : 0;

  return (
    <div style={{ fontFamily: "'DM Sans', sans-serif", background: "#0A0B0E", color: "#fff", minHeight: "100vh", display: "flex", flexDirection: "column" }}>

      {/* Header */}
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "11px 18px", borderBottom: "1px solid rgba(255,255,255,0.05)", background: "rgba(255,255,255,0.012)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: pulse ? "#10B981" : "rgba(16,185,129,0.25)", boxShadow: pulse ? "0 0 6px rgba(16,185,129,0.35)" : "none", transition: "all 0.3s" }} />
          <span style={{ ...m({ fontSize: 11, fontWeight: 600, letterSpacing: "0.04em" }), color: "rgba(255,255,255,0.6)" }}>LEAD QUALIFICATION OPS</span>
          <span style={{ ...m({ fontSize: 8 }), color: "rgba(255,255,255,0.15)" }}>LIVE</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {["leads", "analytics", "settings"].map(v => <button key={v} onClick={() => setView(v)} style={{ ...m({ fontSize: 9, fontWeight: 500 }), padding: "4px 10px", borderRadius: 4, cursor: "pointer", background: view === v ? "rgba(255,255,255,0.07)" : "transparent", color: view === v ? "#fff" : "rgba(255,255,255,0.3)", border: view === v ? "1px solid rgba(255,255,255,0.1)" : "1px solid transparent", textTransform: "uppercase", letterSpacing: "0.05em" }}>{v}</button>)}
          <button onClick={() => setShowForm(p => !p)} style={{ ...m({ fontSize: 9, fontWeight: 600, letterSpacing: "0.04em" }), padding: "5px 12px", borderRadius: 4, background: showForm ? "rgba(239,68,68,0.12)" : "rgba(59,130,246,0.1)", color: showForm ? "#EF4444" : "#3B82F6", border: `1px solid ${showForm ? "rgba(239,68,68,0.2)" : "rgba(59,130,246,0.2)"}`, cursor: "pointer" }}>
            {showForm ? "CANCEL" : "+ NEW LEAD"}
          </button>
        </div>
      </header>

      {/* Error banner */}
      {error && <div style={{ padding: "8px 18px", background: "rgba(239,68,68,0.1)", color: "#EF4444", fontSize: 11, borderBottom: "1px solid rgba(239,68,68,0.15)" }}>{error}</div>}

      {/* Processing indicator */}
      {isProcessing && <div style={{ padding: "6px 18px", background: "rgba(59,130,246,0.08)", color: "#3B82F6", fontSize: 10, borderBottom: "1px solid rgba(59,130,246,0.1)", ...m() }}>Processing lead through pipeline... enrichment → AI analysis → scoring</div>}

      {/* New Lead Form (collapsible) */}
      {showForm && <div style={{ borderBottom: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.015)" }}><NewLeadForm onSubmit={handleSubmitLead} isProcessing={isProcessing} /></div>}

      {/* Metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, padding: "12px 18px" }}>
        <MetricCard label="Total Leads" value={st.total} />
        <MetricCard label="Qualified" value={st.qual} accent="#10B981" sub={`${qr}% rate`} />
        <MetricCard label="Avg Score" value={st.avg} accent="#3B82F6" />
        <MetricCard label="Hot Leads" value={st.hot} accent="#EF4444" sub="15 min SLA" />
        <div style={{ padding: "10px 16px", background: "rgba(255,255,255,0.03)", borderRadius: 8, border: "1px solid rgba(255,255,255,0.06)" }}>
          <div style={{ fontSize: 9, color: "rgba(255,255,255,0.35)", fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 5 }}>Pipeline</div>
          <PipelineFunnel leads={leads} />
        </div>
      </div>

      {/* Main Content */}
      {view === "leads" ? (
        <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 320px", borderTop: "1px solid rgba(255,255,255,0.05)", overflow: "hidden" }}>
          <div style={{ display: "flex", flexDirection: "column", borderRight: "1px solid rgba(255,255,255,0.05)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "7px 14px", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
              {["all", "qualified", "nurture", "needs_review", "disqualified"].map(f => {
                const cnt = f === "all" ? leads.length : leads.filter(l => l.decision === f).length;
                const lbl = f === "all" ? "ALL" : f === "needs_review" ? "REV" : f.slice(0, 4).toUpperCase();
                return <button key={f} onClick={() => setFilter(f)} style={{ ...m({ fontSize: 8, fontWeight: 500 }), padding: "3px 7px", borderRadius: 3, cursor: "pointer", background: filter === f ? "rgba(255,255,255,0.07)" : "transparent", color: filter === f ? "#fff" : "rgba(255,255,255,0.25)", border: filter === f ? "1px solid rgba(255,255,255,0.1)" : "1px solid transparent", textTransform: "uppercase", letterSpacing: "0.04em" }}>{lbl} {cnt}</button>;
              })}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 80px 55px 85px 60px 38px", padding: "5px 14px", borderBottom: "1px solid rgba(255,255,255,0.03)", fontSize: 7, color: "rgba(255,255,255,0.2)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 600 }}>
              <span>Lead</span><span>Source</span><span>Score</span><span>Decision</span><span>Temp</span><span>Age</span>
            </div>
            <div style={{ flex: 1, overflowY: "auto" }}>
              {filt.map(l => <div key={l.id}><StageTimeline lead={l} /><LeadRow lead={l} isSelected={selectedId === l.id} onClick={() => setSelectedId(l.id)} /></div>)}
              {filt.length === 0 && <div style={{ padding: 28, textAlign: "center", color: "rgba(255,255,255,0.12)", fontSize: 11, fontStyle: "italic" }}>{leads.length === 0 ? "No leads yet — click + NEW LEAD to get started" : "No leads match filter"}</div>}
            </div>
          </div>
          <div style={{ background: "rgba(255,255,255,0.008)", overflowY: "auto" }}><LeadDetail lead={sel} /></div>
        </div>
      ) : view === "analytics" ? (
        <div style={{ flex: 1, padding: "14px 18px", overflowY: "auto", borderTop: "1px solid rgba(255,255,255,0.05)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
            {/* Decision Distribution */}
            <div style={{ padding: 16, background: "rgba(255,255,255,0.025)", borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)" }}>
              <div style={{ fontSize: 9, color: "rgba(255,255,255,0.35)", fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 10 }}>Decision Distribution</div>
              {Object.entries(DECISION_CONFIG).map(([k, cfg]) => {
                const cnt = scored.filter(l => l.decision === k).length;
                const pct = scored.length > 0 ? (cnt / scored.length) * 100 : 0;
                return <div key={k} style={{ marginBottom: 7 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}><span style={{ fontSize: 10, color: cfg.color }}>{cfg.label}</span><span style={{ ...m({ fontSize: 10 }), color: "rgba(255,255,255,0.4)" }}>{cnt} ({Math.round(pct)}%)</span></div>
                  <div style={{ height: 5, background: "rgba(255,255,255,0.04)", borderRadius: 3, overflow: "hidden" }}><div style={{ width: `${pct}%`, height: "100%", background: cfg.color, borderRadius: 3, opacity: 0.6, transition: "width 0.3s" }} /></div>
                </div>;
              })}
            </div>

            {/* Score by Source */}
            <div style={{ padding: 16, background: "rgba(255,255,255,0.025)", borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)" }}>
              <div style={{ fontSize: 9, color: "rgba(255,255,255,0.35)", fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 10 }}>Avg Score by Source</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
                {["web_form", "referral", "linkedin", "chat_widget"].map(src => {
                  const sl = scored.filter(l => l.source === src);
                  const avg = sl.length > 0 ? sl.reduce((s, l) => s + l.composite, 0) / sl.length : 0;
                  const h = Math.max((avg / 100) * 50, 3);
                  const clr = avg >= 75 ? "#10B981" : avg >= 50 ? "#F59E0B" : avg >= 25 ? "#3B82F6" : "#6B7280";
                  return <div key={src} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
                    <span style={{ ...m({ fontSize: 11, fontWeight: 600 }), color: avg > 0 ? clr : "rgba(255,255,255,0.1)" }}>{avg > 0 ? Math.round(avg) : "—"}</span>
                    <div style={{ width: "65%", height: h, background: clr, borderRadius: 2, opacity: 0.5 }} />
                    <span style={{ fontSize: 7, color: "rgba(255,255,255,0.25)", textTransform: "uppercase" }}>{src.replace("_", " ")}</span>
                    <span style={{ ...m({ fontSize: 8 }), color: "rgba(255,255,255,0.15)" }}>{sl.length}</span>
                  </div>;
                })}
              </div>
            </div>
          </div>

          {/* Activity Feed */}
          <div style={{ padding: 16, background: "rgba(255,255,255,0.025)", borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <div style={{ fontSize: 9, color: "rgba(255,255,255,0.35)", fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase" }}>Live Activity</div>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <div style={{ width: 4, height: 4, borderRadius: "50%", background: pulse ? "#10B981" : "rgba(16,185,129,0.2)", transition: "all 0.3s" }} />
                <span style={{ ...m({ fontSize: 8 }), color: "rgba(255,255,255,0.2)" }}>{events.length} events · polling 5s</span>
              </div>
            </div>
            <ActivityFeed events={events} />
          </div>
        </div>
      ) : view === "settings" ? (
        <SettingsPanel />
      ) : null}
    </div>
  );
}
