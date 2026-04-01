"""
analyzer.py
===========
Orchestrates the full behavioral analysis pipeline.

Usage — single file:
    from behavioral_engine.analyzer import BehavioralEngine
    engine = BehavioralEngine()
    report = engine.analyse("path/to/pnl.xlsx")

Usage — multiple files (cross-period trend):
    report = engine.analyse_multiple(["jun.xlsx", "aug_dec.xlsx", "dec.xlsx"])

The returned report dict is JSON-serialisable and ready for an API response,
a PDF renderer, or a frontend dashboard.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from typing import Optional

import pandas as pd

from parser import parse_pnl_file
from logics import (
    logic_L1_near_worthless_exit,
    logic_L2_winloss_asymmetry,
    logic_L3_brokerage_drag,
    logic_L4_overtrading,
    logic_L5_capital_concentration,
    logic_L6_option_buyer_bias,
    logic_L7_open_position_hemorrhage,
    logic_L8_breakeven_waste,
    logic_L9_strike_selection,
    logic_L10_monthly_trend,
)


# ─── ordered list of all logic runners ───────────────────────────────────────
ALL_LOGICS = [
    logic_L1_near_worthless_exit,
    logic_L2_winloss_asymmetry,
    logic_L3_brokerage_drag,
    logic_L4_overtrading,
    logic_L5_capital_concentration,
    logic_L6_option_buyer_bias,
    logic_L7_open_position_hemorrhage,
    logic_L8_breakeven_waste,
    logic_L9_strike_selection,
    logic_L10_monthly_trend,
]

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "ok": 0}


def _severity_score(result: dict) -> int:
    return SEVERITY_RANK.get(result.get("severity", "ok"), 0)


def _compute_overall_health(results: list[dict], summary: dict = None) -> dict:
    """
    Derive an overall health score (0–100) and grade (A–F).

    Score = Behavioral score (0-70) + Profitability score (0-30)

    Behavioral score (70 pts max):
      Deduct per triggered logic: critical=15, high=8, medium=4
      Minimum behavioral score = 10 (so a profitable trader is never 0)

    Profitability score (30 pts):
      Net PnL > 0 and charges < 20% of gross PnL  → 30
      Net PnL > 0 and charges >= 20%               → 20
      Net PnL = 0 or near-zero                     → 10
      Net PnL < 0                                  →  0

    This prevents the score from hitting 0 just because many mistakes are
    triggered — if the trader is actually profitable, the score reflects that.
    """
    triggered  = [r for r in results if r.get("triggered")]
    n_critical = sum(1 for r in triggered if r["severity"] == "critical")
    n_high     = sum(1 for r in triggered if r["severity"] == "high")
    n_medium   = sum(1 for r in triggered if r["severity"] == "medium")

    deductions      = (n_critical * 15) + (n_high * 8) + (n_medium * 4)
    behavioral_score = max(10, 70 - deductions)   # floor at 10

    # Profitability component
    profit_score = 0
    if summary:
        net_pnl      = summary.get("net_pnl", 0.0) or 0.0
        realized_pnl = summary.get("realized_pnl", 0.0) or 0.0
        charges      = summary.get("charges_total", 0.0) or 0.0
        charge_pct   = (charges / realized_pnl * 100) if realized_pnl > 0 else 999.0

        if net_pnl > 0 and charge_pct < 20:
            profit_score = 30
        elif net_pnl > 0:
            profit_score = 20
        elif abs(net_pnl) < 1000:
            profit_score = 10
        else:
            profit_score = 0

    score = behavioral_score + profit_score

    if score >= 85:
        grade, label = "A", "Excellent"
    elif score >= 70:
        grade, label = "B", "Good"
    elif score >= 55:
        grade, label = "C", "Needs Improvement"
    elif score >= 40:
        grade, label = "D", "Poor"
    else:
        grade, label = "F", "Critical Risk"

    return {
        "score":             score,
        "grade":             grade,
        "label":             label,
        "behavioral_score":  behavioral_score,
        "profitability_score": profit_score,
        "triggered_count":   len(triggered),
        "critical_count":    n_critical,
        "high_count":        n_high,
        "medium_count":      n_medium,
    }


def _cross_period_trend(parsed_list: list[dict]) -> dict:
    """
    Compute a cross-period trend from multiple parsed PnL files.
    Returns a timeline list and trend flags.
    """
    timeline = []
    for p in parsed_list:
        trades = p["trades"]
        summary = p["summary"]
        meta = p["meta"]

        n = len(trades)
        winners = trades[trades["Realized P&L"] > 0]
        losers = trades[trades["Realized P&L"] < 0]
        win_rate = len(winners) / n * 100 if n > 0 else 0.0
        avg_win = winners["Realized P&L"].mean() if len(winners) > 0 else 0.0
        avg_loss = abs(losers["Realized P&L"].mean()) if len(losers) > 0 else 0.0
        rr_ratio = (avg_win / avg_loss) if avg_loss > 0 else float("inf")
        near_worthless = int((trades["Realized P&L Pct."] <= -80).sum())
        charges = summary.get("charges_total", 0.0)
        realized_pnl = summary.get("realized_pnl", 0.0)
        charge_drag = (charges / abs(realized_pnl) * 100) if realized_pnl != 0 else 0.0

        timeline.append({
            "period": meta.get("period", ""),
            "start_date": meta.get("start_date", ""),
            "end_date": meta.get("end_date", ""),
            "instruments_traded": n,
            "win_rate_pct": round(win_rate, 2),
            "avg_winner_inr": round(avg_win, 2),
            "avg_loser_inr": round(avg_loss, 2),
            "rr_ratio": round(rr_ratio, 4),
            "realized_pnl_inr": round(realized_pnl, 2),
            "charges_inr": round(charges, 2),
            "charge_drag_pct": round(charge_drag, 2),
            "near_worthless_exits": near_worthless,
            "net_pnl_inr": round(summary.get("net_pnl", 0.0), 2),
        })

    # trend flags
    if len(timeline) < 2:
        trends = {}
    else:
        first, last = timeline[0], timeline[-1]
        trends = {
            "win_rate_direction": (
                "improving" if last["win_rate_pct"] > first["win_rate_pct"]
                else "declining" if last["win_rate_pct"] < first["win_rate_pct"]
                else "stable"
            ),
            "rr_ratio_direction": (
                "improving" if last["rr_ratio"] > first["rr_ratio"]
                else "declining" if last["rr_ratio"] < first["rr_ratio"]
                else "stable"
            ),
            "overtrading_direction": (
                "worsening" if last["instruments_traded"] > first["instruments_traded"]
                else "improving"
            ),
            "near_worthless_persistent": all(
                t["near_worthless_exits"] > 0 for t in timeline
            ),
            "consecutive_losing_periods": sum(
                1 for t in timeline if t["realized_pnl_inr"] < 0
            ),
        }

    return {"timeline": timeline, "trends": trends}


class BehavioralEngine:
    """
    Main engine class. Instantiate once, call analyse() or analyse_multiple().
    """

    def __init__(self, enabled_logics: Optional[list[str]] = None):
        """
        enabled_logics: optional list of logic IDs to run (e.g. ["L1","L2"]).
                        If None, all 10 are run.
        """
        if enabled_logics:
            enabled_set = set(enabled_logics)
            self._logics = [fn for fn in ALL_LOGICS
                            if fn.__name__.split("_")[1] in enabled_set]
        else:
            self._logics = ALL_LOGICS

    # ── single file ───────────────────────────────────────────────────────────
    def analyse(self, file_path: str) -> dict:
        """
        Parse one Zerodha PnL Excel file and run all behavioral logic modules.

        Returns a complete report dict ready for JSON serialisation.
        """
        parsed = parse_pnl_file(file_path)
        return self._build_report([parsed], cross_period=False)

    # ── multiple files ────────────────────────────────────────────────────────
    def analyse_multiple(self, file_paths: list[str]) -> dict:
        """
        Parse multiple PnL files and analyse each period individually,
        plus compute cross-period trends.
        """
        parsed_list = []
        errors = []
        for path in file_paths:
            try:
                parsed_list.append(parse_pnl_file(path))
            except Exception as e:
                errors.append({"file": path, "error": str(e)})

        if not parsed_list:
            return {
                "status": "error",
                "errors": errors,
                "message": "Could not parse any of the provided files.",
            }

        report = self._build_report(parsed_list, cross_period=(len(parsed_list) > 1))
        if errors:
            report["parse_errors"] = errors
        return report

    # ── internal builder ──────────────────────────────────────────────────────
    def _build_report(self, parsed_list: list[dict], cross_period: bool) -> dict:
        """Run all logics per period and assemble the final report."""

        period_reports = []

        for parsed in parsed_list:
            trades = parsed["trades"]
            summary = parsed["summary"]
            open_pos = parsed["open_positions"]
            meta = parsed["meta"]

            logic_results = []
            for fn in self._logics:
                try:
                    result = fn(trades, summary, open_pos, meta)
                except Exception:
                    result = {
                        "logic_id": fn.__name__,
                        "name": fn.__name__,
                        "severity": "ok",
                        "triggered": False,
                        "headline": "Error running this logic.",
                        "detail": traceback.format_exc(),
                        "evidence": [],
                        "recommendation": "",
                        "impact_inr": 0.0,
                        "metrics": {},
                    }
                logic_results.append(result)

            # sort by severity (critical first)
            logic_results.sort(key=_severity_score, reverse=True)

            health = _compute_overall_health(logic_results, summary=summary)
            total_impact = sum(
                r.get("impact_inr", 0.0) for r in logic_results if r.get("triggered")
            )

            # top mistakes (triggered, sorted by severity then impact)
            top_mistakes = [
                {
                    "logic_id": r["logic_id"],
                    "severity": r["severity"],
                    "headline": r["headline"],
                    "recommendation": r["recommendation"],
                    "impact_inr": r.get("impact_inr", 0.0),
                }
                for r in logic_results if r.get("triggered")
            ]

            period_reports.append({
                "meta": meta,
                "summary": summary,
                "health": health,
                "top_mistakes": top_mistakes,
                "total_estimated_impact_inr": round(total_impact, 2),
                "logic_results": logic_results,
            })

        # cross-period trend
        cross_period_data = {}
        if cross_period and len(parsed_list) > 1:
            cross_period_data = _cross_period_trend(parsed_list)

        # if single period, unwrap for convenience
        if len(period_reports) == 1:
            report = period_reports[0]
            report["generated_at"] = datetime.now().isoformat()
            report["engine_version"] = "1.0.0"
            return report

        return {
            "generated_at": datetime.now().isoformat(),
            "engine_version": "1.0.0",
            "periods_analysed": len(period_reports),
            "period_reports": period_reports,
            "cross_period_trend": cross_period_data,
        }


# ─── convenience function ─────────────────────────────────────────────────────
def analyse_file(file_path: str) -> dict:
    """One-shot function. Analyse a single Zerodha PnL Excel file."""
    return BehavioralEngine().analyse(file_path)


def analyse_files(file_paths: list[str]) -> dict:
    """One-shot function. Analyse multiple Zerodha PnL Excel files."""
    return BehavioralEngine().analyse_multiple(file_paths)