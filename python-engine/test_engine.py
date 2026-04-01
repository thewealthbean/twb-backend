"""
tests/test_engine.py
====================
Unit and integration tests for the F&O Behavioral Engine.

Run with:
  pytest tests/test_engine.py -v
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pandas as pd
import numpy as np

from behavioral_engine.parser import parse_pnl_file, _parse_symbol
from behavioral_engine.analyzer import BehavioralEngine, _compute_overall_health
from behavioral_engine.logics import (
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


# ─── fixtures ────────────────────────────────────────────────────────────────

def _make_trades(**kwargs) -> pd.DataFrame:
    """Build a minimal trades DataFrame for unit testing."""
    defaults = {
        "Symbol": ["NIFTY25JUN25000CE"],
        "Quantity": [75],
        "Buy Value": [10000.0],
        "Sell Value": [8000.0],
        "Realized P&L": [-2000.0],
        "Realized P&L Pct.": [-20.0],
        "Open Quantity": [0],
        "Open Quantity Type": [None],
        "Open Value": [0.0],
        "Unrealized P&L": [0.0],
        "Unrealized P&L Pct.": [0.0],
        "underlying": ["NIFTY"],
        "option_type": ["CE"],
        "strike": [25000],
        "expiry_str": ["JUN-2025"],
        "expiry_type": ["monthly"],
        "buy_price_per_unit": [133.33],
    }
    defaults.update(kwargs)
    return pd.DataFrame(defaults)


BLANK_SUMMARY = {
    "charges_total": 0.0,
    "other_credit_debit": 0.0,
    "realized_pnl": -2000.0,
    "unrealized_pnl": 0.0,
    "charges_breakdown": {},
    "net_pnl": -2000.0,
}

BLANK_OPEN = pd.DataFrame(columns=[
    "Symbol", "Open Quantity", "Open Quantity Type",
    "Open Value", "Unrealized P&L", "Unrealized P&L Pct.",
    "Previous Closing Price",
])

BLANK_META = {"client_id": "TEST001", "period": "Test period", "start_date": None, "end_date": None}


# ─── parser tests ─────────────────────────────────────────────────────────────

class TestSymbolParser:
    def test_monthly_ce(self):
        r = _parse_symbol("NIFTY25JUN25000CE")
        assert r["underlying"] == "NIFTY"
        assert r["option_type"] == "CE"
        assert r["strike"] == 25000
        assert r["expiry_type"] == "monthly"

    def test_monthly_pe(self):
        r = _parse_symbol("TATACHEM25JUN980CE")
        assert r["underlying"] == "TATACHEM"
        assert r["strike"] == 980

    def test_weekly_nifty(self):
        r = _parse_symbol("NIFTY2561225300CE")
        assert r["underlying"] == "NIFTY"
        assert r["strike"] == 25300
        assert r["expiry_type"] == "weekly"

    def test_mam_ampersand(self):
        r = _parse_symbol("M&M25JUN3100CE")
        assert r["underlying"] == "M&M"
        assert r["strike"] == 3100

    def test_non_option(self):
        r = _parse_symbol("NIFTY25JUNFUT")
        assert r["option_type"] is None

    def test_empty_string(self):
        r = _parse_symbol("")
        assert r["option_type"] is None


# ─── L1 tests ─────────────────────────────────────────────────────────────────

class TestL1NearWorthless:
    def test_catastrophic_loss_triggers(self):
        trades = _make_trades(
            Symbol=["NIFTY25JUN25300CE"],
            Buy_Value=[10000.0],
            **{
                "Realized P&L": [-9800.0],
                "Realized P&L Pct.": [-98.0],
                "Buy Value": [10000.0],
                "Sell Value": [200.0],
            }
        )
        result = logic_L1_near_worthless_exit(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is True
        assert result["severity"] in ("critical", "high")
        assert result["metrics"]["trades_above_80pct_loss"] == 1

    def test_small_loss_no_trigger(self):
        trades = _make_trades(
            **{
                "Realized P&L": [-2000.0],
                "Realized P&L Pct.": [-20.0],
                "Buy Value": [10000.0],
                "Sell Value": [8000.0],
            }
        )
        result = logic_L1_near_worthless_exit(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is False
        assert result["severity"] == "ok"

    def test_multiple_catastrophic(self):
        trades = pd.DataFrame({
            "Symbol": ["A", "B", "C"],
            "Quantity": [75, 75, 75],
            "Buy Value": [10000, 20000, 5000],
            "Sell Value": [100, 200, 50],
            "Realized P&L": [-9900, -19800, -4950],
            "Realized P&L Pct.": [-99.0, -99.0, -99.0],
            "Open Quantity": [0, 0, 0], "Open Value": [0, 0, 0],
            "Unrealized P&L": [0, 0, 0], "Unrealized P&L Pct.": [0, 0, 0],
            "underlying": ["NIFTY", "BEL", "NIFTY"],
            "option_type": ["CE", "CE", "PE"],
            "strike": [25000, 400, 24000],
            "expiry_str": ["JUN-2025"] * 3,
            "expiry_type": ["monthly"] * 3,
            "buy_price_per_unit": [133, 133, 66],
        })
        result = logic_L1_near_worthless_exit(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is True
        assert result["severity"] == "critical"
        assert result["metrics"]["trades_above_95pct_loss"] == 3


# ─── L2 tests ─────────────────────────────────────────────────────────────────

class TestL2WinLossAsymmetry:
    def test_bad_rr_triggers(self):
        """High win rate but avg loss >> avg win → should trigger."""
        rows = [
            {"Realized P&L": 1000, "Realized P&L Pct.": 10},   # win
            {"Realized P&L": 1200, "Realized P&L Pct.": 12},   # win
            {"Realized P&L": 800,  "Realized P&L Pct.": 8},    # win
            {"Realized P&L": -8000,"Realized P&L Pct.": -80},  # loss
        ]
        base = {k: [] for k in _make_trades().columns}
        trades = pd.DataFrame([{**{c: 0 for c in base}, "Realized P&L": r["Realized P&L"],
                                  "Realized P&L Pct.": r["Realized P&L Pct."]} for r in rows])
        result = logic_L2_winloss_asymmetry(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is True

    def test_good_rr_no_trigger(self):
        rows = [
            {"Realized P&L": 5000, "Realized P&L Pct.": 50},
            {"Realized P&L": 4000, "Realized P&L Pct.": 40},
            {"Realized P&L": -1000,"Realized P&L Pct.": -10},
        ]
        trades = pd.DataFrame([{"Realized P&L": r["Realized P&L"],
                                  "Realized P&L Pct.": r["Realized P&L Pct."]} for r in rows])
        result = logic_L2_winloss_asymmetry(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is False

    def test_empty_trades(self):
        result = logic_L2_winloss_asymmetry(pd.DataFrame(), BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is False


# ─── L3 tests ─────────────────────────────────────────────────────────────────

class TestL3BrokerageDrag:
    def test_high_charge_pct_triggers(self):
        summary = {**BLANK_SUMMARY, "realized_pnl": 10000.0, "charges_total": 3000.0}
        result = logic_L3_brokerage_drag(pd.DataFrame(), summary, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is True
        assert result["metrics"]["charges_to_gross_pnl_pct"] == 30.0

    def test_loss_with_charges_triggers(self):
        summary = {**BLANK_SUMMARY, "realized_pnl": -50000.0, "charges_total": 10000.0}
        result = logic_L3_brokerage_drag(pd.DataFrame(), summary, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is True
        assert result["severity"] == "critical"

    def test_low_charge_pct_no_trigger(self):
        summary = {**BLANK_SUMMARY, "realized_pnl": 100000.0, "charges_total": 5000.0}
        result = logic_L3_brokerage_drag(pd.DataFrame(), summary, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is False


# ─── L4 tests ─────────────────────────────────────────────────────────────────

class TestL4Overtrading:
    def test_severe_overtrading(self):
        n = 80
        trades = pd.DataFrame({
            "Symbol": [f"NIFTY25JUN{25000+i*50}CE" for i in range(n)],
            "Quantity": [75] * n,
            "Buy Value": [1000.0] * n,
            "Sell Value": [1100.0] * n,
            "Realized P&L": [100.0] * n,
            "Realized P&L Pct.": [10.0] * n,
            "underlying": ["NIFTY"] * n,
            "expiry_str": ["JUN-2025"] * n,
            "expiry_type": ["monthly"] * n,
            "option_type": ["CE"] * n,
            "strike": [25000 + i * 50 for i in range(n)],
            "buy_price_per_unit": [13.33] * n,
        })
        result = logic_L4_overtrading(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is True
        assert result["metrics"]["overtrading_level"] == "severe"

    def test_focused_trading_no_trigger(self):
        n = 10
        trades = pd.DataFrame({
            "Symbol": [f"NIFTY25JUN{25000+i*50}CE" for i in range(n)],
            "Quantity": [75] * n,
            "Buy Value": [1000.0] * n,
            "Sell Value": [1100.0] * n,
            "Realized P&L": [100.0] * n,
            "Realized P&L Pct.": [10.0] * n,
            "underlying": ["NIFTY"] * n,
            "expiry_str": ["JUN-2025"] * n,
            "expiry_type": ["monthly"] * n,
            "option_type": ["CE"] * n,
            "strike": [25000 + i * 50 for i in range(n)],
            "buy_price_per_unit": [13.33] * n,
        })
        result = logic_L4_overtrading(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is False


# ─── L7 tests ─────────────────────────────────────────────────────────────────

class TestL7OpenPositions:
    def test_bleeding_position_triggers(self):
        open_pos = pd.DataFrame({
            "Symbol": ["PGEL26JAN580CE"],
            "Open Quantity": [1900],
            "Open Quantity Type": ["buy"],
            "Open Value": [43700.0],
            "Unrealized P&L": [-6175.0],
            "Unrealized P&L Pct.": [-14.13],
            "Previous Closing Price": [19.75],
        })
        result = logic_L7_open_position_hemorrhage(
            pd.DataFrame(), BLANK_SUMMARY, open_pos, BLANK_META
        )
        assert result["triggered"] is True

    def test_no_open_positions(self):
        result = logic_L7_open_position_hemorrhage(
            pd.DataFrame(), BLANK_SUMMARY, BLANK_OPEN, BLANK_META
        )
        assert result["triggered"] is False
        assert result["severity"] == "ok"


# ─── L8 tests ─────────────────────────────────────────────────────────────────

class TestL8BreakevenWaste:
    def test_many_breakeven_triggers(self):
        n = 20
        trades = pd.DataFrame({
            "Symbol": [f"NIFTY25JUN25{i:03d}CE" for i in range(n)],
            "Quantity": [75] * n,
            "Buy Value": [1000.0] * n,
            "Sell Value": [1000.0] * n,
            "Realized P&L": [0.0] * n,
            "Realized P&L Pct.": [0.0] * n,
            "underlying": ["NIFTY"] * n,
            "option_type": ["CE"] * n,
            "strike": [25000] * n,
            "expiry_str": ["JUN-2025"] * n,
            "expiry_type": ["monthly"] * n,
            "buy_price_per_unit": [13.33] * n,
        })
        result = logic_L8_breakeven_waste(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is True
        assert result["metrics"]["exact_zero_pnl_trades"] == n


# ─── L9 tests ─────────────────────────────────────────────────────────────────

class TestL9StrikeSelection:
    def test_deep_otm_triggers(self):
        n = 10
        trades = pd.DataFrame({
            "Symbol": [f"NIFTY25JUN{25000+i*50}CE" for i in range(n)],
            "Quantity": [75] * n,
            "Buy Value": [100.0] * n,     # ₹100 total = ₹1.33/unit (deep OTM)
            "Sell Value": [10.0] * n,
            "Realized P&L": [-90.0] * n,
            "Realized P&L Pct.": [-90.0] * n,
            "underlying": ["NIFTY"] * n,
            "option_type": ["CE"] * n,
            "strike": [25000 + i * 50 for i in range(n)],
            "expiry_str": ["JUN-2025"] * n,
            "expiry_type": ["monthly"] * n,
            "buy_price_per_unit": [1.33] * n,   # < ₹5 = Far OTM
        })
        result = logic_L9_strike_selection(trades, BLANK_SUMMARY, BLANK_OPEN, BLANK_META)
        assert result["triggered"] is True
        assert result["metrics"]["deep_far_otm_count"] == n


# ─── health score tests ───────────────────────────────────────────────────────

class TestHealthScore:
    def test_all_ok_is_high_score(self):
        results = [
            {"severity": "ok", "triggered": False} for _ in range(10)
        ]
        h = _compute_overall_health(results)
        assert h["score"] == 100
        assert h["grade"] == "A"

    def test_all_critical_is_zero(self):
        results = [
            {"severity": "critical", "triggered": True} for _ in range(5)
        ]
        h = _compute_overall_health(results)
        assert h["score"] == 0
        assert h["grade"] == "F"

    def test_mixed_severity(self):
        results = [
            {"severity": "critical", "triggered": True},
            {"severity": "high", "triggered": True},
            {"severity": "ok", "triggered": False},
        ]
        h = _compute_overall_health(results)
        # deductions: 20 (critical) + 10 (high) = 30
        assert h["score"] == 70
        assert h["grade"] == "B"


# ─── integration test: real files ────────────────────────────────────────────

REAL_FILES = {
    "jun": "/mnt/user-data/uploads/jun.xlsx",
    "multi": "/mnt/user-data/uploads/multi.xlsx",
    "dec": "/mnt/user-data/uploads/dec.xlsx",
}

@pytest.mark.skipif(
    not os.path.exists(REAL_FILES["jun"]),
    reason="Real PnL files not present"
)
class TestRealFiles:
    def test_jun_parses(self):
        parsed = parse_pnl_file(REAL_FILES["jun"])
        assert len(parsed["trades"]) > 0
        assert parsed["summary"]["charges_total"] > 0

    def test_jun_full_analysis(self):
        engine = BehavioralEngine()
        report = engine.analyse(REAL_FILES["jun"])
        assert report["health"]["score"] is not None
        assert len(report["logic_results"]) == 10
        # known: L1, L2, L3, L4 should all trigger for this trader
        triggered_ids = {r["logic_id"] for r in report["logic_results"] if r["triggered"]}
        assert "L1" in triggered_ids
        assert "L2" in triggered_ids
        assert "L4" in triggered_ids

    def test_multi_period_analysis(self):
        engine = BehavioralEngine()
        report = engine.analyse_multiple(list(REAL_FILES.values()))
        assert report["periods_analysed"] == 3
        assert "cross_period_trend" in report
        assert report["cross_period_trend"]["trends"]["near_worthless_persistent"] is True

    def test_json_serialisable(self):
        import json
        from behavioral_engine.api import _make_serialisable
        engine = BehavioralEngine()
        report = engine.analyse(REAL_FILES["dec"])
        report = _make_serialisable(report)
        # should not raise
        j = json.dumps(report)
        assert len(j) > 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
