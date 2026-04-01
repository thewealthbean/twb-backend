"""
cli.py
======
Command-line interface for the F&O Behavioral Engine.

Usage:
  python -m behavioral_engine.cli path/to/pnl.xlsx
  python -m behavioral_engine.cli jun.xlsx aug_dec.xlsx dec.xlsx
  python -m behavioral_engine.cli pnl.xlsx --logics L1,L2,L3
  python -m behavioral_engine.cli pnl.xlsx --output report.json
  python -m behavioral_engine.cli pnl.xlsx --summary-only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from .analyzer import BehavioralEngine
from .api import _make_serialisable

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🔵",
    "ok":       "🟢",
}

SEP = "─" * 72


def _print_report(report: dict, summary_only: bool = False):
    """Pretty-print a behavioral report to stdout."""

    def p(s=""): print(s)

    # ── handle multi-period vs single ────────────────────────────────────────
    if "period_reports" in report:
        p(SEP)
        p(f"  MULTI-PERIOD BEHAVIORAL ANALYSIS")
        p(f"  Periods analysed: {report['periods_analysed']}")
        p(SEP)

        # cross-period trend
        ct = report.get("cross_period_trend", {})
        trends = ct.get("trends", {})
        timeline = ct.get("timeline", [])

        if timeline:
            p("\n  CROSS-PERIOD TIMELINE:")
            hdr = f"  {'Period':<35} {'Instruments':>11} {'Win%':>6} {'RR':>6} {'Realized PnL':>14} {'NW Exits':>9}"
            p(hdr)
            p("  " + "-" * 68)
            for t in timeline:
                period_label = t.get("start_date", "") + " → " + t.get("end_date", "")
                p(
                    f"  {period_label:<35}"
                    f" {t['instruments_traded']:>11}"
                    f" {t['win_rate_pct']:>5.1f}%"
                    f" {t['rr_ratio']:>6.2f}"
                    f" ₹{t['realized_pnl_inr']:>13,.2f}"
                    f" {t['near_worthless_exits']:>9}"
                )

        if trends:
            p("\n  TREND FLAGS:")
            for k, v in trends.items():
                p(f"    {k.replace('_', ' ').title()}: {v}")

        p()
        for i, pr in enumerate(report["period_reports"], 1):
            p(SEP)
            p(f"  PERIOD {i}: {pr['meta'].get('period', '')}")
            p(SEP)
            _print_single_period(pr, summary_only)
            p()
        return

    # single period
    p(SEP)
    p(f"  F&O BEHAVIORAL ANALYSIS — {report['meta'].get('period', 'Unknown period')}")
    p(f"  Client: {report['meta'].get('client_id', 'N/A')}")
    p(f"  Generated: {report.get('generated_at', '')}")
    p(SEP)
    _print_single_period(report, summary_only)


def _print_single_period(report: dict, summary_only: bool):
    def p(s=""): print(s)

    health = report.get("health", {})
    summary = report.get("summary", {})

    # health score
    grade = health.get("grade", "?")
    score = health.get("score", 0)
    label = health.get("label", "")
    p(f"\n  HEALTH SCORE: {score}/100  [{grade}]  {label}")

    # summary numbers
    p(f"\n  SUMMARY:")
    p(f"    Realized PnL      : ₹{summary.get('realized_pnl', 0):>12,.2f}")
    p(f"    Charges           : ₹{summary.get('charges_total', 0):>12,.2f}")
    p(f"    Net PnL           : ₹{summary.get('net_pnl', 0):>12,.2f}")
    p(f"    Unrealized PnL    : ₹{summary.get('unrealized_pnl', 0):>12,.2f}")

    mistakes = report.get("top_mistakes", [])
    p(f"\n  MISTAKES FOUND ({len(mistakes)}):")
    if not mistakes:
        p("    ✅  No behavioral mistakes detected.")
    else:
        for m in mistakes:
            icon = SEVERITY_EMOJI.get(m["severity"], "⚪")
            p(f"\n    {icon}  [{m['logic_id']}] {m['headline']}")
            if m.get("impact_inr"):
                p(f"         Impact: ₹{m['impact_inr']:,.2f}")
            p(f"         Fix   : {m['recommendation'][:120]}{'…' if len(m['recommendation'])>120 else ''}")

    if summary_only:
        return

    # detailed logic results
    p(f"\n{'─'*72}")
    p("  DETAILED LOGIC RESULTS:")
    for result in report.get("logic_results", []):
        icon = SEVERITY_EMOJI.get(result.get("severity", "ok"), "⚪")
        triggered = "TRIGGERED" if result.get("triggered") else "ok"
        p(f"\n  {icon} [{result['logic_id']}] {result['name']} — {triggered}")
        p(f"     {result.get('headline', '')}")
        if result.get("triggered"):
            p(f"     Detail: {result.get('detail', '')[:200]}{'…' if len(result.get('detail',''))>200 else ''}")
            if result.get("impact_inr"):
                p(f"     Impact: ₹{result['impact_inr']:,.2f}")

    p(f"\n  Total estimated impact: ₹{report.get('total_estimated_impact_inr', 0):,.2f}")


def main():
    parser = argparse.ArgumentParser(
        prog="behavioral_engine",
        description="F&O Trader Behavioral Engine — analyse Zerodha PnL statements",
    )
    parser.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="One or more Zerodha F&O PnL Excel files (.xlsx)",
    )
    parser.add_argument(
        "--logics",
        default=None,
        metavar="L1,L2",
        help="Comma-separated logic IDs to run (default: all)",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Save JSON report to this file path",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only the summary and top mistakes (no detailed per-logic output)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON report to stdout instead of formatted text",
    )

    args = parser.parse_args()

    logic_ids = None
    if args.logics:
        logic_ids = [x.strip().upper() for x in args.logics.split(",")]

    engine = BehavioralEngine(enabled_logics=logic_ids)

    if len(args.files) == 1:
        report = engine.analyse(args.files[0])
    else:
        report = engine.analyse_multiple(args.files)

    report = _make_serialisable(report)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_report(report, summary_only=args.summary_only)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n  Report saved to: {args.output}")


if __name__ == "__main__":
    main()
