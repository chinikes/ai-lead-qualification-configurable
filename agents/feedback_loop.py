"""
Feedback Loop — the fifth and final system component.

Responsibilities:
  1. Capture deal outcomes (won/lost) and link back to original lead scores
  2. Analyze win/loss patterns across scoring dimensions
  3. Recalibrate scoring weights based on what actually predicts wins
  4. Refine the ICP based on closed-won patterns
  5. Detect scoring drift (model degradation over time)
  6. Generate reports and Slack alerts

This is NOT a separate agent in the pipeline — it's a background process
that runs periodically (daily/weekly) and feeds adjustments back into
the Qualification Agent's scoring weights and the Research Agent's ICP.

Design decisions:
  - Outcome data is stored as a list of OutcomeRecords (would be DB in production)
  - Statistical analysis uses simple correlation + percentile comparison
  - LLM is used for narrative pattern summaries, not for the math
  - Weight adjustments are bounded (max ±0.05 per cycle) to prevent wild swings
  - All changes are logged with before/after snapshots for audit
  - Drift detection uses a rolling window comparing recent accuracy to baseline
"""

import json
import logging
import math
import statistics
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum
from dataclasses import dataclass, field
from pydantic import BaseModel, Field

from config.schemas import (
    LeadScore,
    LeadTemperature,
    QualificationDecision,
    Seniority,
)
from config.settings import SCORING, ICP, ScoringWeights

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  DATA MODELS
# ══════════════════════════════════════════════════════════

class DealOutcome(str, Enum):
    WON = "closed_won"
    LOST = "closed_lost"


class OutcomeRecord(BaseModel):
    """A completed deal linked back to its original lead score."""

    deal_id: str
    lead_email: str
    company_name: Optional[str] = None
    industry: Optional[str] = None

    # Original scores at time of qualification
    firmographic_score: float
    demographic_score: float
    behavioral_score: float
    ai_fit_score: float
    composite_score: float
    temperature: str
    decision: str

    # Lead attributes for pattern analysis
    employee_count: Optional[int] = None
    seniority: Optional[str] = None
    source: Optional[str] = None
    is_decision_maker: bool = False

    # Outcome
    outcome: DealOutcome
    deal_value: Optional[float] = None
    days_to_close: Optional[int] = None
    loss_reason: Optional[str] = None

    # Timestamps
    qualified_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime = Field(default_factory=datetime.utcnow)


class WeightAdjustment(BaseModel):
    """A record of scoring weight changes."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    reason: str
    before: dict = Field(description="Weights before adjustment")
    after: dict = Field(description="Weights after adjustment")
    sample_size: int
    win_rate_before: float
    win_rate_after_predicted: float


class DriftAlert(BaseModel):
    """Alert when model performance degrades."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    alert_type: str  # "accuracy_drop", "distribution_shift", "threshold_drift"
    severity: str    # "info", "warning", "critical"
    message: str
    metric_name: str
    baseline_value: float
    current_value: float
    delta: float


class FeedbackReport(BaseModel):
    """Periodic report on system performance."""

    report_date: datetime = Field(default_factory=datetime.utcnow)
    period_days: int = 30

    # Volume
    total_outcomes: int = 0
    total_won: int = 0
    total_lost: int = 0
    win_rate: float = 0.0

    # Score accuracy
    avg_won_score: float = 0.0
    avg_lost_score: float = 0.0
    score_separation: float = 0.0  # Gap between won and lost avg scores

    # Dimension analysis
    dimension_win_correlations: dict = Field(default_factory=dict)
    strongest_predictor: Optional[str] = None
    weakest_predictor: Optional[str] = None

    # Pattern insights
    top_winning_industries: list[str] = Field(default_factory=list)
    top_winning_sources: list[str] = Field(default_factory=list)
    top_winning_seniorities: list[str] = Field(default_factory=list)
    optimal_score_threshold: Optional[float] = None

    # Adjustments made
    weight_adjustments: list[WeightAdjustment] = Field(default_factory=list)
    drift_alerts: list[DriftAlert] = Field(default_factory=list)
    icp_changes: list[str] = Field(default_factory=list)

    # Narrative
    summary: str = ""


# ══════════════════════════════════════════════════════════
#  OUTCOME STORE
# ══════════════════════════════════════════════════════════

class OutcomeStore:
    """
    In-memory outcome store. In production, this would be a database
    (PostgreSQL, BigQuery, etc.) with proper indexing and retention.
    """

    def __init__(self):
        self.records: list[OutcomeRecord] = []

    def add(self, record: OutcomeRecord):
        self.records.append(record)
        logger.info(f"[feedback] Recorded outcome: {record.lead_email} → {record.outcome.value}")

    def get_recent(self, days: int = 30) -> list[OutcomeRecord]:
        cutoff = datetime.utcnow() - timedelta(days=days)
        return [r for r in self.records if r.closed_at >= cutoff]

    def get_won(self, days: int = 30) -> list[OutcomeRecord]:
        return [r for r in self.get_recent(days) if r.outcome == DealOutcome.WON]

    def get_lost(self, days: int = 30) -> list[OutcomeRecord]:
        return [r for r in self.get_recent(days) if r.outcome == DealOutcome.LOST]

    @property
    def total(self) -> int:
        return len(self.records)


# ══════════════════════════════════════════════════════════
#  PATTERN ANALYZER
# ══════════════════════════════════════════════════════════

class PatternAnalyzer:
    """
    Analyzes win/loss patterns to identify what predicts success.

    Uses simple statistical methods (mean comparison, point-biserial
    correlation proxy) rather than ML models — appropriate for the
    typical sample sizes in B2B sales (dozens to low hundreds).
    """

    def analyze_dimension_correlations(
        self, outcomes: list[OutcomeRecord]
    ) -> dict[str, float]:
        """
        For each scoring dimension, compute how well it separates
        won deals from lost deals.

        Returns a dict of dimension → correlation score (-1 to 1).
        Higher = better predictor of winning.
        """
        if len(outcomes) < 5:
            return {}

        dimensions = {
            "firmographic": [r.firmographic_score for r in outcomes],
            "demographic": [r.demographic_score for r in outcomes],
            "behavioral": [r.behavioral_score for r in outcomes],
            "ai_fit": [r.ai_fit_score for r in outcomes],
        }
        labels = [1.0 if r.outcome == DealOutcome.WON else 0.0 for r in outcomes]

        correlations = {}
        for dim_name, scores in dimensions.items():
            correlations[dim_name] = self._point_biserial_proxy(scores, labels)

        return correlations

    def _point_biserial_proxy(self, scores: list[float], labels: list[float]) -> float:
        """
        Simplified point-biserial correlation.
        Measures how much a score dimension separates wins from losses.
        """
        if len(scores) < 5:
            return 0.0

        won_scores = [s for s, l in zip(scores, labels) if l == 1.0]
        lost_scores = [s for s, l in zip(scores, labels) if l == 0.0]

        if not won_scores or not lost_scores:
            return 0.0

        mean_won = statistics.mean(won_scores)
        mean_lost = statistics.mean(lost_scores)

        # Normalized separation: (mean_won - mean_lost) / pooled_std
        all_scores = scores
        if len(all_scores) < 2:
            return 0.0

        try:
            std = statistics.stdev(all_scores)
            if std == 0:
                return 0.0
            separation = (mean_won - mean_lost) / std
            # Clamp to [-1, 1]
            return max(-1.0, min(1.0, separation))
        except statistics.StatisticsError:
            return 0.0

    def find_winning_patterns(self, outcomes: list[OutcomeRecord]) -> dict:
        """Identify patterns in won deals vs lost deals."""
        won = [r for r in outcomes if r.outcome == DealOutcome.WON]
        lost = [r for r in outcomes if r.outcome == DealOutcome.LOST]

        if not won:
            return {"message": "No won deals to analyze"}

        patterns = {}

        # Industry distribution
        won_industries = {}
        for r in won:
            if r.industry:
                won_industries[r.industry] = won_industries.get(r.industry, 0) + 1
        patterns["top_industries"] = sorted(
            won_industries.items(), key=lambda x: x[1], reverse=True
        )[:5]

        # Source distribution
        won_sources = {}
        for r in won:
            if r.source:
                won_sources[r.source] = won_sources.get(r.source, 0) + 1
        patterns["top_sources"] = sorted(
            won_sources.items(), key=lambda x: x[1], reverse=True
        )[:5]

        # Seniority distribution
        won_seniority = {}
        for r in won:
            if r.seniority:
                won_seniority[r.seniority] = won_seniority.get(r.seniority, 0) + 1
        patterns["top_seniorities"] = sorted(
            won_seniority.items(), key=lambda x: x[1], reverse=True
        )[:5]

        # Employee count ranges for wins
        won_emp = [r.employee_count for r in won if r.employee_count]
        if won_emp:
            patterns["employee_range"] = {
                "min": min(won_emp),
                "max": max(won_emp),
                "median": statistics.median(won_emp),
                "mean": round(statistics.mean(won_emp)),
            }

        # Decision maker correlation
        won_dm_rate = sum(1 for r in won if r.is_decision_maker) / len(won) if won else 0
        lost_dm_rate = sum(1 for r in lost if r.is_decision_maker) / len(lost) if lost else 0
        patterns["decision_maker_win_rate_lift"] = round(won_dm_rate - lost_dm_rate, 3)

        # Avg days to close
        won_days = [r.days_to_close for r in won if r.days_to_close]
        if won_days:
            patterns["avg_days_to_close"] = round(statistics.mean(won_days))

        # Deal value
        won_values = [r.deal_value for r in won if r.deal_value]
        if won_values:
            patterns["avg_deal_value"] = round(statistics.mean(won_values))
            patterns["total_revenue"] = round(sum(won_values))

        return patterns

    def find_optimal_threshold(self, outcomes: list[OutcomeRecord]) -> Optional[float]:
        """
        Find the composite score threshold that best separates wins from losses.
        Uses a simple sweep from 30 to 90 to find the score that maximizes
        the F1-like metric (precision × recall balance).
        """
        if len(outcomes) < 10:
            return None

        best_score = None
        best_f1 = 0.0

        for threshold in range(30, 91, 5):
            # "Predicted qualified" = score >= threshold
            tp = sum(1 for r in outcomes if r.composite_score >= threshold and r.outcome == DealOutcome.WON)
            fp = sum(1 for r in outcomes if r.composite_score >= threshold and r.outcome == DealOutcome.LOST)
            fn = sum(1 for r in outcomes if r.composite_score < threshold and r.outcome == DealOutcome.WON)

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0

            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

            if f1 > best_f1:
                best_f1 = f1
                best_score = float(threshold)

        return best_score


# ══════════════════════════════════════════════════════════
#  WEIGHT RECALIBRATOR
# ══════════════════════════════════════════════════════════

class WeightRecalibrator:
    """
    Adjusts scoring weights based on which dimensions best predict wins.

    Constraints:
      - Max adjustment per cycle: ±0.05 per dimension
      - All weights must sum to 1.0
      - No weight can go below 0.10 or above 0.50
      - Minimum sample size: 20 outcomes
    """

    MIN_SAMPLE = 20
    MAX_DELTA = 0.05
    MIN_WEIGHT = 0.10
    MAX_WEIGHT = 0.50

    def recalibrate(
        self, correlations: dict[str, float], current_weights: ScoringWeights
    ) -> Optional[WeightAdjustment]:
        """
        Compute new weights based on dimension correlations.
        Returns a WeightAdjustment record, or None if no change needed.
        """
        if not correlations:
            return None

        current = {
            "firmographic": current_weights.firmographic,
            "demographic": current_weights.demographic,
            "behavioral": current_weights.behavioral,
            "ai_fit": current_weights.ai_fit,
        }

        # Compute target weights proportional to correlation strength
        total_corr = sum(max(0.01, abs(v)) for v in correlations.values())
        if total_corr == 0:
            return None

        target = {}
        for dim, corr in correlations.items():
            # Stronger correlation → higher weight
            raw_target = max(0.01, abs(corr)) / total_corr
            # Clamp to bounds
            target[dim] = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, raw_target))

        # Normalize targets to sum to 1.0
        target_sum = sum(target.values())
        target = {k: v / target_sum for k, v in target.items()}

        # Apply bounded adjustments
        new_weights = {}
        for dim in current:
            delta = target.get(dim, current[dim]) - current[dim]
            # Clamp delta
            delta = max(-self.MAX_DELTA, min(self.MAX_DELTA, delta))
            new_weights[dim] = current[dim] + delta

        # Re-normalize to sum to 1.0
        weight_sum = sum(new_weights.values())
        new_weights = {k: round(v / weight_sum, 4) for k, v in new_weights.items()}

        # Check if change is meaningful (> 0.005 on any dimension)
        max_change = max(abs(new_weights[d] - current[d]) for d in current)
        if max_change < 0.005:
            logger.info("[feedback] Weight change too small — skipping")
            return None

        before = {k: round(v, 4) for k, v in current.items()}
        after = new_weights

        logger.info(f"[feedback] Weight adjustment: {before} → {after}")

        return WeightAdjustment(
            reason=f"Recalibrated based on outcome correlations: {json.dumps({k: round(v, 3) for k, v in correlations.items()})}",
            before=before,
            after=after,
            sample_size=0,  # Will be set by caller
            win_rate_before=0.0,
            win_rate_after_predicted=0.0,
        )

    def apply_weights(self, adjustment: WeightAdjustment, weights: ScoringWeights):
        """Apply the adjustment to the live scoring weights."""
        weights.firmographic = adjustment.after["firmographic"]
        weights.demographic = adjustment.after["demographic"]
        weights.behavioral = adjustment.after["behavioral"]
        weights.ai_fit = adjustment.after["ai_fit"]
        logger.info(f"[feedback] Applied new weights: {adjustment.after}")


# ══════════════════════════════════════════════════════════
#  DRIFT DETECTOR
# ══════════════════════════════════════════════════════════

class DriftDetector:
    """
    Detects when the scoring model's performance degrades.

    Checks:
      - Score separation between won/lost is shrinking
      - Win rate for "qualified" leads is dropping
      - Score distribution is shifting
    """

    def __init__(self, baseline_separation: float = 20.0, baseline_win_rate: float = 0.40):
        self.baseline_separation = baseline_separation
        self.baseline_win_rate = baseline_win_rate

    def check_for_drift(self, outcomes: list[OutcomeRecord]) -> list[DriftAlert]:
        """Run all drift checks. Returns a list of alerts."""
        alerts = []

        if len(outcomes) < 10:
            return alerts

        won = [r for r in outcomes if r.outcome == DealOutcome.WON]
        lost = [r for r in outcomes if r.outcome == DealOutcome.LOST]

        if not won or not lost:
            return alerts

        # Check 1: Score separation
        avg_won = statistics.mean([r.composite_score for r in won])
        avg_lost = statistics.mean([r.composite_score for r in lost])
        separation = avg_won - avg_lost

        if separation < self.baseline_separation * 0.5:
            alerts.append(DriftAlert(
                alert_type="accuracy_drop",
                severity="critical" if separation < self.baseline_separation * 0.25 else "warning",
                message=f"Score separation between won/lost deals has narrowed to {separation:.1f} (baseline: {self.baseline_separation:.1f}). The model is losing predictive power.",
                metric_name="score_separation",
                baseline_value=self.baseline_separation,
                current_value=round(separation, 1),
                delta=round(separation - self.baseline_separation, 1),
            ))
        elif separation < self.baseline_separation * 0.75:
            alerts.append(DriftAlert(
                alert_type="accuracy_drop",
                severity="info",
                message=f"Score separation trending down: {separation:.1f} vs baseline {self.baseline_separation:.1f}",
                metric_name="score_separation",
                baseline_value=self.baseline_separation,
                current_value=round(separation, 1),
                delta=round(separation - self.baseline_separation, 1),
            ))

        # Check 2: Qualified win rate
        qualified_outcomes = [r for r in outcomes if r.decision == "qualified"]
        if len(qualified_outcomes) >= 5:
            qualified_wins = sum(1 for r in qualified_outcomes if r.outcome == DealOutcome.WON)
            qualified_win_rate = qualified_wins / len(qualified_outcomes)

            if qualified_win_rate < self.baseline_win_rate * 0.6:
                alerts.append(DriftAlert(
                    alert_type="threshold_drift",
                    severity="critical",
                    message=f"Win rate for qualified leads dropped to {qualified_win_rate:.0%} (baseline: {self.baseline_win_rate:.0%}). Qualification threshold may need raising.",
                    metric_name="qualified_win_rate",
                    baseline_value=self.baseline_win_rate,
                    current_value=round(qualified_win_rate, 3),
                    delta=round(qualified_win_rate - self.baseline_win_rate, 3),
                ))
            elif qualified_win_rate < self.baseline_win_rate * 0.8:
                alerts.append(DriftAlert(
                    alert_type="threshold_drift",
                    severity="warning",
                    message=f"Qualified lead win rate declining: {qualified_win_rate:.0%} vs baseline {self.baseline_win_rate:.0%}",
                    metric_name="qualified_win_rate",
                    baseline_value=self.baseline_win_rate,
                    current_value=round(qualified_win_rate, 3),
                    delta=round(qualified_win_rate - self.baseline_win_rate, 3),
                ))

        # Check 3: High-scoring losses (score > 80 but lost)
        high_score_losses = [r for r in lost if r.composite_score >= 80]
        if len(high_score_losses) >= 3:
            pct = len(high_score_losses) / len(lost)
            if pct > 0.3:
                alerts.append(DriftAlert(
                    alert_type="distribution_shift",
                    severity="warning",
                    message=f"{len(high_score_losses)} deals scored 80+ but were lost ({pct:.0%} of all losses). High-score calibration may be off.",
                    metric_name="high_score_loss_rate",
                    baseline_value=0.1,
                    current_value=round(pct, 3),
                    delta=round(pct - 0.1, 3),
                ))

        return alerts


# ══════════════════════════════════════════════════════════
#  ICP REFINER
# ══════════════════════════════════════════════════════════

class ICPRefiner:
    """
    Suggests ICP changes based on closed-won patterns.

    Does NOT auto-apply changes — generates recommendations
    for human review. ICP changes are high-impact and should
    be deliberate.
    """

    def suggest_refinements(self, patterns: dict, current_icp=ICP) -> list[str]:
        """Generate ICP refinement suggestions."""
        suggestions = []

        # Industry suggestions
        top_industries = patterns.get("top_industries", [])
        if top_industries:
            winning_industries = [ind for ind, count in top_industries if count >= 2]
            current_target = set(i.lower() for i in current_icp.target_industries)
            for ind in winning_industries:
                if ind.lower() not in current_target:
                    suggestions.append(
                        f"ADD INDUSTRY: '{ind}' appeared in {dict(top_industries).get(ind, 0)} won deals but isn't in the ICP target list"
                    )

        # Employee range suggestions
        emp_range = patterns.get("employee_range")
        if emp_range:
            if emp_range["min"] < current_icp.min_employee_count:
                suggestions.append(
                    f"LOWER MIN EMPLOYEES: Won deals include companies as small as {emp_range['min']} (current min: {current_icp.min_employee_count})"
                )
            if emp_range["max"] > current_icp.max_employee_count:
                suggestions.append(
                    f"RAISE MAX EMPLOYEES: Won deals include companies as large as {emp_range['max']} (current max: {current_icp.max_employee_count})"
                )

        # Source quality
        top_sources = patterns.get("top_sources", [])
        if top_sources:
            best_source = top_sources[0][0] if top_sources else None
            if best_source:
                suggestions.append(
                    f"INVEST IN SOURCE: '{best_source}' is the top-performing lead source for closed-won deals"
                )

        # Decision maker impact
        dm_lift = patterns.get("decision_maker_win_rate_lift", 0)
        if dm_lift > 0.15:
            suggestions.append(
                f"PRIORITIZE DECISION MAKERS: Decision makers have a {dm_lift:.0%} higher win rate — consider adding a DM bonus to scoring"
            )

        return suggestions


# ══════════════════════════════════════════════════════════
#  FEEDBACK LOOP ORCHESTRATOR
# ══════════════════════════════════════════════════════════

class FeedbackLoop:
    """
    Orchestrates the full feedback loop.

    Usage:
        loop = FeedbackLoop()
        loop.record_outcome(outcome_record)     # Called when deals close
        report = loop.run_analysis(days=30)      # Called periodically (daily/weekly)
    """

    def __init__(self):
        self.store = OutcomeStore()
        self.analyzer = PatternAnalyzer()
        self.recalibrator = WeightRecalibrator()
        self.drift_detector = DriftDetector()
        self.icp_refiner = ICPRefiner()
        self.adjustment_history: list[WeightAdjustment] = []

    def record_outcome(self, record: OutcomeRecord):
        """Record a deal outcome."""
        self.store.add(record)

    def run_analysis(self, days: int = 30, auto_apply: bool = False) -> FeedbackReport:
        """
        Run the full feedback analysis pipeline.

        Args:
            days: Look-back period for analysis
            auto_apply: If True, automatically apply weight adjustments.
                        If False (default), just report recommendations.
        """
        logger.info(f"[feedback] Running analysis for last {days} days")

        outcomes = self.store.get_recent(days)
        won = [r for r in outcomes if r.outcome == DealOutcome.WON]
        lost = [r for r in outcomes if r.outcome == DealOutcome.LOST]

        report = FeedbackReport(period_days=days)
        report.total_outcomes = len(outcomes)
        report.total_won = len(won)
        report.total_lost = len(lost)
        report.win_rate = len(won) / len(outcomes) if outcomes else 0.0

        if len(outcomes) < 5:
            report.summary = f"Insufficient data for analysis ({len(outcomes)} outcomes, minimum 5 needed)"
            return report

        # ── Score Analysis ───────────────────────────────
        report.avg_won_score = round(statistics.mean([r.composite_score for r in won]), 1) if won else 0
        report.avg_lost_score = round(statistics.mean([r.composite_score for r in lost]), 1) if lost else 0
        report.score_separation = round(report.avg_won_score - report.avg_lost_score, 1)

        # ── Dimension Correlations ───────────────────────
        correlations = self.analyzer.analyze_dimension_correlations(outcomes)
        report.dimension_win_correlations = {k: round(v, 3) for k, v in correlations.items()}

        if correlations:
            sorted_dims = sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True)
            report.strongest_predictor = sorted_dims[0][0]
            report.weakest_predictor = sorted_dims[-1][0]

        # ── Pattern Analysis ─────────────────────────────
        patterns = self.analyzer.find_winning_patterns(outcomes)
        report.top_winning_industries = [ind for ind, _ in patterns.get("top_industries", [])[:3]]
        report.top_winning_sources = [src for src, _ in patterns.get("top_sources", [])[:3]]
        report.top_winning_seniorities = [sen for sen, _ in patterns.get("top_seniorities", [])[:3]]

        # ── Optimal Threshold ────────────────────────────
        report.optimal_score_threshold = self.analyzer.find_optimal_threshold(outcomes)

        # ── Weight Recalibration ─────────────────────────
        if len(outcomes) >= self.recalibrator.MIN_SAMPLE:
            adjustment = self.recalibrator.recalibrate(correlations, SCORING)
            if adjustment:
                adjustment.sample_size = len(outcomes)
                adjustment.win_rate_before = report.win_rate
                report.weight_adjustments.append(adjustment)
                self.adjustment_history.append(adjustment)

                if auto_apply:
                    self.recalibrator.apply_weights(adjustment, SCORING)
                    logger.info("[feedback] Auto-applied weight adjustment")

        # ── Drift Detection ──────────────────────────────
        drift_alerts = self.drift_detector.check_for_drift(outcomes)
        report.drift_alerts = drift_alerts

        # ── ICP Refinement Suggestions ───────────────────
        icp_suggestions = self.icp_refiner.suggest_refinements(patterns)
        report.icp_changes = icp_suggestions

        # ── Build Summary ────────────────────────────────
        report.summary = self._build_summary(report, patterns)

        logger.info(
            f"[feedback] Analysis complete: {report.total_outcomes} outcomes, "
            f"win rate={report.win_rate:.0%}, separation={report.score_separation:.1f}"
        )

        return report

    def _build_summary(self, report: FeedbackReport, patterns: dict) -> str:
        """Build a human-readable summary of the feedback analysis."""
        lines = [
            f"FEEDBACK LOOP REPORT — Last {report.period_days} Days",
            f"{'=' * 55}",
            f"",
            f"OUTCOMES",
            f"  Total: {report.total_outcomes}  |  Won: {report.total_won}  |  Lost: {report.total_lost}  |  Win Rate: {report.win_rate:.0%}",
            f"",
            f"SCORING ACCURACY",
            f"  Avg score (won):  {report.avg_won_score}",
            f"  Avg score (lost): {report.avg_lost_score}",
            f"  Separation:       {report.score_separation} pts  {'✓ Good' if report.score_separation > 15 else '⚠ Low — model may be losing predictive power'}",
            f"",
            f"DIMENSION EFFECTIVENESS",
        ]

        for dim, corr in sorted(report.dimension_win_correlations.items(), key=lambda x: abs(x[1]), reverse=True):
            bar = "█" * int(abs(corr) * 20) + "░" * (20 - int(abs(corr) * 20))
            indicator = "↑ predicts wins" if corr > 0.1 else "↓ predicts losses" if corr < -0.1 else "~ neutral"
            lines.append(f"  {dim:<14s} [{bar}] {corr:+.3f}  {indicator}")

        if report.strongest_predictor:
            lines.append(f"\n  Strongest predictor: {report.strongest_predictor}")
            lines.append(f"  Weakest predictor:  {report.weakest_predictor}")

        if report.optimal_score_threshold:
            lines.append(f"\n  Optimal qualification threshold: {report.optimal_score_threshold:.0f}")
            lines.append(f"  Current threshold:               {SCORING.auto_qualify_threshold:.0f}")

        if report.top_winning_industries:
            lines.append(f"\nWINNING PATTERNS")
            lines.append(f"  Top industries:  {', '.join(report.top_winning_industries)}")
        if report.top_winning_sources:
            lines.append(f"  Top sources:     {', '.join(report.top_winning_sources)}")
        if report.top_winning_seniorities:
            lines.append(f"  Top seniorities: {', '.join(report.top_winning_seniorities)}")

        if patterns.get("avg_deal_value"):
            lines.append(f"  Avg deal value:  ${patterns['avg_deal_value']:,.0f}")
        if patterns.get("avg_days_to_close"):
            lines.append(f"  Avg days to close: {patterns['avg_days_to_close']}d")

        if report.weight_adjustments:
            lines.append(f"\nWEIGHT ADJUSTMENTS")
            for adj in report.weight_adjustments:
                lines.append(f"  Before: {adj.before}")
                lines.append(f"  After:  {adj.after}")
                lines.append(f"  Reason: {adj.reason}")

        if report.drift_alerts:
            lines.append(f"\nDRIFT ALERTS ({len(report.drift_alerts)})")
            for alert in report.drift_alerts:
                lines.append(f"  [{alert.severity.upper()}] {alert.message}")

        if report.icp_changes:
            lines.append(f"\nICP REFINEMENT SUGGESTIONS")
            for suggestion in report.icp_changes:
                lines.append(f"  • {suggestion}")

        return "\n".join(lines)


# ── Mock Outcome Generator (for demos) ───────────────────

def generate_mock_outcomes(n: int = 50) -> list[OutcomeRecord]:
    """
    Generate realistic mock outcomes for testing the feedback loop.
    Higher-scored leads have higher win probability (with noise).
    """
    import random
    random.seed(42)

    industries = ["SaaS", "FinTech", "Healthcare Tech", "E-commerce", "Manufacturing", "Professional Services"]
    sources = ["web_form", "referral", "linkedin", "chat_widget", "email", "event"]
    seniorities = ["c_level", "vp", "director", "manager", "senior_ic", "ic"]
    loss_reasons = ["Budget", "Timing", "Competitor", "No decision", "Internal solution", "Champion left"]

    outcomes = []
    for i in range(n):
        # Generate correlated scores
        firm = random.gauss(60, 20)
        demo = random.gauss(55, 25)
        behav = random.gauss(50, 22)
        ai = random.gauss(52, 18)

        firm = max(0, min(100, firm))
        demo = max(0, min(100, demo))
        behav = max(0, min(100, behav))
        ai = max(0, min(100, ai))

        composite = firm * 0.30 + demo * 0.25 + behav * 0.20 + ai * 0.25

        # Win probability correlates with composite score
        win_prob = 1 / (1 + math.exp(-(composite - 55) / 12))
        won = random.random() < win_prob

        # High seniority wins more
        seniority = random.choice(seniorities)
        if seniority in ["c_level", "vp"]:
            won = won or random.random() < 0.3

        emp = random.choice([25, 50, 120, 300, 800, 2000, 5000])
        temp = "hot" if composite >= 80 else "warm" if composite >= 60 else "cool" if composite >= 40 else "cold"
        decision = "qualified" if composite >= 75 else "nurture" if composite >= 45 else "disqualified"

        outcomes.append(OutcomeRecord(
            deal_id=f"deal_{i:04d}",
            lead_email=f"lead_{i}@company{i}.com",
            company_name=f"Company {i}",
            industry=random.choice(industries),
            firmographic_score=round(firm, 1),
            demographic_score=round(demo, 1),
            behavioral_score=round(behav, 1),
            ai_fit_score=round(ai, 1),
            composite_score=round(composite, 1),
            temperature=temp,
            decision=decision,
            employee_count=emp,
            seniority=seniority,
            source=random.choice(sources),
            is_decision_maker=seniority in ["c_level", "vp", "director"],
            outcome=DealOutcome.WON if won else DealOutcome.LOST,
            deal_value=random.randint(5000, 150000) if won else None,
            days_to_close=random.randint(14, 120),
            loss_reason=None if won else random.choice(loss_reasons),
            qualified_at=datetime.utcnow() - timedelta(days=random.randint(30, 90)),
            closed_at=datetime.utcnow() - timedelta(days=random.randint(0, 30)),
        ))

    return outcomes
