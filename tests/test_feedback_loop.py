"""
Feedback Loop Demo — shows outcome analysis, weight recalibration,
drift detection, and ICP refinement working on mock deal data.

Usage:
    python -m tests.test_feedback_loop
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.feedback_loop import (
    FeedbackLoop,
    generate_mock_outcomes,
    DealOutcome,
)
from config.settings import SCORING


def sec(title, char="─"):
    print(f"\n{char * 72}")
    print(f"  {title}")
    print(f"{char * 72}")


def main():
    sec("AI LEAD QUALIFICATION — FEEDBACK LOOP DEMO", "█")
    print(f"  Component:  Feedback Loop (Phase 5)")
    print(f"  Mode:       Mock outcome data (50 deals)")
    print(f"  Actions:    Pattern analysis, weight recalibration, drift detection")

    # ── Generate mock outcomes ────────────────────────────
    sec("STEP 1: Generate Mock Deal Outcomes")
    outcomes = generate_mock_outcomes(50)
    won = [r for r in outcomes if r.outcome == DealOutcome.WON]
    lost = [r for r in outcomes if r.outcome == DealOutcome.LOST]
    print(f"  Generated {len(outcomes)} outcomes: {len(won)} won, {len(lost)} lost")
    print(f"  Win rate: {len(won)/len(outcomes):.0%}")

    # Show a few sample outcomes
    print(f"\n  Sample outcomes:")
    print(f"  {'Email':<28s} {'Score':>6s} {'Temp':>5s} {'Decision':<12s} {'Outcome':<10s} {'Value':>10s}")
    print(f"  {'─'*28} {'─'*6} {'─'*5} {'─'*12} {'─'*10} {'─'*10}")
    for r in outcomes[:8]:
        val = f"${r.deal_value:,.0f}" if r.deal_value else "—"
        print(f"  {r.lead_email:<28s} {r.composite_score:5.1f} {r.temperature:>5s} {r.decision:<12s} {r.outcome.value:<10s} {val:>10s}")

    # ── Initialize feedback loop ─────────────────────────
    sec("STEP 2: Load Outcomes into Feedback Loop")
    loop = FeedbackLoop()
    for outcome in outcomes:
        loop.record_outcome(outcome)
    print(f"  Loaded {loop.store.total} outcomes into the store")

    # ── Show current weights ─────────────────────────────
    sec("STEP 3: Current Scoring Weights (Before)")
    print(f"  Firmographic:  {SCORING.firmographic:.2%}")
    print(f"  Demographic:   {SCORING.demographic:.2%}")
    print(f"  Behavioral:    {SCORING.behavioral:.2%}")
    print(f"  AI Fit:        {SCORING.ai_fit:.2%}")

    # ── Run analysis ─────────────────────────────────────
    sec("STEP 4: Run Feedback Analysis")
    report = loop.run_analysis(days=90, auto_apply=True)

    # Print the full report
    print()
    print(report.summary)

    # ── Show updated weights ─────────────────────────────
    if report.weight_adjustments:
        sec("STEP 5: Updated Scoring Weights (After)")
        print(f"  Firmographic:  {SCORING.firmographic:.2%}")
        print(f"  Demographic:   {SCORING.demographic:.2%}")
        print(f"  Behavioral:    {SCORING.behavioral:.2%}")
        print(f"  AI Fit:        {SCORING.ai_fit:.2%}")

        adj = report.weight_adjustments[0]
        print(f"\n  Changes applied:")
        for dim in ["firmographic", "demographic", "behavioral", "ai_fit"]:
            before = adj.before[dim]
            after = adj.after[dim]
            delta = after - before
            direction = "↑" if delta > 0 else "↓" if delta < 0 else "="
            print(f"    {dim:<14s}: {before:.2%} → {after:.2%}  ({direction} {abs(delta):.2%})")
    else:
        sec("STEP 5: No Weight Adjustments Needed")
        print("  Current weights are performing well — no changes required.")

    # ── Drift alerts ─────────────────────────────────────
    if report.drift_alerts:
        sec("STEP 6: Drift Alerts")
        for alert in report.drift_alerts:
            severity_icon = {"info": "ℹ️ ", "warning": "⚠️ ", "critical": "🚨"}
            print(f"  {severity_icon.get(alert.severity, '•')} [{alert.severity.upper()}] {alert.alert_type}")
            print(f"     {alert.message}")
            print(f"     Metric: {alert.metric_name} | Baseline: {alert.baseline_value} | Current: {alert.current_value}")
            print()
    else:
        sec("STEP 6: No Drift Detected")
        print("  Model performance is within normal bounds.")

    # ── ICP suggestions ──────────────────────────────────
    if report.icp_changes:
        sec("STEP 7: ICP Refinement Suggestions")
        for i, suggestion in enumerate(report.icp_changes, 1):
            print(f"  {i}. {suggestion}")
    else:
        sec("STEP 7: No ICP Changes Suggested")

    # ── Final summary ────────────────────────────────────
    sec("SYSTEM STATUS", "█")
    print(f"  Outcomes tracked:     {report.total_outcomes}")
    print(f"  Win rate:             {report.win_rate:.0%}")
    print(f"  Score separation:     {report.score_separation:.1f} pts  {'✓' if report.score_separation > 15 else '⚠ Action needed'}")
    print(f"  Strongest predictor:  {report.strongest_predictor}")
    print(f"  Weakest predictor:    {report.weakest_predictor}")
    print(f"  Optimal threshold:    {report.optimal_score_threshold}")
    print(f"  Weight adjustments:   {len(report.weight_adjustments)}")
    print(f"  Drift alerts:         {len(report.drift_alerts)}")
    print(f"  ICP suggestions:      {len(report.icp_changes)}")
    print()

    # ── Simulate a second analysis after more data ───────
    sec("BONUS: Second Analysis (Simulating Time Passing)")
    more_outcomes = generate_mock_outcomes(30)
    for o in more_outcomes:
        loop.record_outcome(o)
    print(f"  Added {len(more_outcomes)} more outcomes (total: {loop.store.total})")

    report2 = loop.run_analysis(days=90, auto_apply=True)
    print(f"  Win rate:        {report2.win_rate:.0%}")
    print(f"  Separation:      {report2.score_separation:.1f}")
    print(f"  New adjustments: {len(report2.weight_adjustments)}")
    print(f"  New alerts:      {len(report2.drift_alerts)}")
    print(f"\n  Final weights:")
    print(f"    Firmographic: {SCORING.firmographic:.2%}")
    print(f"    Demographic:  {SCORING.demographic:.2%}")
    print(f"    Behavioral:   {SCORING.behavioral:.2%}")
    print(f"    AI Fit:       {SCORING.ai_fit:.2%}")
    print()


if __name__ == "__main__":
    main()
