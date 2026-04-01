"""
logics.py  (v2 — fixed)
========================
Ten behavioral logic modules for the F&O Trader Behavioral Engine.

Fixes applied vs v1:
  L1  — Threshold kept at <= -80%. Tier labels improved.
  L5  — Denominator changed from |realized_pnl| to total_gross_losses.
  L8  — "Breakeven" redefined as trades earning less than avg charge per trade,
        NOT trades with |PnL%| < 2% (old logic wrongly flagged large ₹3k wins).
  L9  — Moneyness tiers now use premium_pct_of_strike (per-unit/strike × 100).
        Far-OTM only flagged when ALSO near-worthless, not just cheap premium.
  L10 — Replaced meaningless alphabetical split with loss clustering analysis:
        underlying concentration, directional bias, near-worthless rate, bad stocks.
  L6  — Weekly flag only fires when weeklies are actually net-losing.

Each module returns:
{
  "logic_id", "name", "severity", "triggered",
  "headline", "detail", "evidence", "recommendation",
  "impact_inr", "metrics"
}
"""

from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd


# ─── shared helpers ──────────────────────────────────────────────────────────

def _fmt(n: float, decimals: int = 2) -> str:
    sign = "-" if n < 0 else ""
    return f"{sign}₹{abs(n):,.{decimals}f}"

def _pct(n: float) -> str:
    return f"{n:.1f}%"

def _row_to_evidence(row: pd.Series, extra_fields: Optional[list] = None) -> dict:
    base = {
        "symbol":           str(row.get("Symbol", "")),
        "quantity":         int(row.get("Quantity", 0)),
        "buy_value":        float(row.get("Buy Value", 0)),
        "sell_value":       float(row.get("Sell Value", 0)),
        "realized_pnl":     float(row.get("Realized P&L", 0)),
        "realized_pnl_pct": round(float(row.get("Realized P&L Pct.", 0)), 4),
    }
    if extra_fields:
        for f in extra_fields:
            if f in row.index:
                v = row[f]
                base[f] = None if (isinstance(v, float) and np.isnan(v)) else v
    return base


# ─── L1: Near-Worthless Exit Detector ────────────────────────────────────────

def logic_L1_near_worthless_exit(trades, summary, open_positions, meta):
    """
    Flags options held until they lost >= 80% of entry value.
    Tiers: Catastrophic (<= -95%), Severe (<= -80%).
    """
    THRESHOLD_SEVERE       = -80.0
    THRESHOLD_CATASTROPHIC = -95.0

    pnl_pct      = trades["Realized P&L Pct."]
    severe       = trades[pnl_pct <= THRESHOLD_SEVERE].copy()
    catastrophic = trades[pnl_pct <= THRESHOLD_CATASTROPHIC].copy()

    triggered       = len(severe) > 0
    total_destroyed = severe["Realized P&L"].sum()

    recoverable = 0.0
    for _, row in severe.iterrows():
        bv          = row["Buy Value"]
        actual_loss = row["Realized P&L"]
        stop_thresh = -0.30 * bv
        if actual_loss < stop_thresh:
            recoverable += actual_loss - stop_thresh
    recoverable = abs(recoverable)

    evidence = [
        _row_to_evidence(r, ["underlying", "expiry_type", "strike"])
        for _, r in severe.nsmallest(10, "Realized P&L").iterrows()
    ]

    if triggered:
        n_cat    = len(catastrophic)
        severity = "critical" if n_cat >= 2 else "high"
        headline = (
            f"{len(severe)} trade(s) held until near-worthless — "
            f"{_fmt(abs(total_destroyed))} destroyed"
        )
        detail = (
            f"You held {len(severe)} option(s) until they lost >= 80% of buy value. "
            f"{n_cat} lost over 95% — effectively expired at zero. "
            f"Total capital written off: {_fmt(abs(total_destroyed))}. "
            f"A 30% stop-loss rule would have saved approximately {_fmt(recoverable)}."
        )
    else:
        severity    = "ok"
        headline    = "No near-worthless exits — losses closed before -80%."
        detail      = "Good discipline: every losing trade was exited before total write-off."
        recoverable = 0.0

    return {
        "logic_id": "L1",
        "name":     "Near-worthless exit detector",
        "severity": severity,
        "triggered": triggered,
        "headline": headline,
        "detail":   detail,
        "evidence": evidence,
        "recommendation": (
            "Set a hard stop-loss the moment you enter: exit if the option loses 30% of buy value. "
            "Never hold an OTM option into expiry hoping for a reversal — theta kills value "
            "exponentially in the final week. Use a price alert or conditional order at entry."
        ),
        "impact_inr": round(total_destroyed, 2),
        "metrics": {
            "near_worthless_count":              int(len(severe)),
            "catastrophic_above_95pct":          int(len(catastrophic)),
            "severe_80_to_95pct":                int(len(severe) - len(catastrophic)),
            "total_capital_destroyed_inr":       round(abs(total_destroyed), 2),
            "recoverable_with_30pct_stoploss":   round(recoverable, 2),
        },
    }


# ─── L2: Win/Loss Asymmetry Trap ─────────────────────────────────────────────

def logic_L2_winloss_asymmetry(trades, summary, open_positions, meta):
    """
    Detects when avg loss >> avg win makes long-run profitability impossible.
    Breakeven win rate = avg_loss / (avg_win + avg_loss).
    """
    if len(trades) == 0:
        return {
            "logic_id": "L2", "name": "Win/loss asymmetry trap",
            "severity": "ok", "triggered": False,
            "headline": "No trades to analyse.", "detail": "",
            "evidence": [], "recommendation": "", "impact_inr": 0.0, "metrics": {},
        }

    winners  = trades[trades["Realized P&L"] > 0]
    losers   = trades[trades["Realized P&L"] < 0]
    n_total  = len(trades)
    n_win    = len(winners)
    n_loss   = len(losers)
    n_zero   = n_total - n_win - n_loss

    win_rate = n_win / n_total * 100
    avg_win  = winners["Realized P&L"].mean() if n_win  > 0 else 0.0
    avg_loss = abs(losers["Realized P&L"].mean()) if n_loss > 0 else 0.0
    rr_ratio = (avg_win / avg_loss) if avg_loss > 0 else float("inf")

    breakeven_win_rate = (
        avg_loss / (avg_win + avg_loss) * 100
        if (avg_win + avg_loss) > 0 else 0.0
    )

    gross_wins   = winners["Realized P&L"].sum()
    gross_losses = abs(losers["Realized P&L"].sum())
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf")

    triggered = rr_ratio < 1.0

    if triggered:
        severity = "critical" if rr_ratio < 0.5 else "high"
        headline = (
            f"Win rate {_pct(win_rate)} but avg loss ({_fmt(avg_loss)}) is "
            f"{1/rr_ratio:.1f}x avg win ({_fmt(avg_win)}) — structurally unprofitable"
        )
        detail = (
            f"{n_win} wins, {n_loss} losses, {n_zero} breakeven out of {n_total} trades. "
            f"Avg winner: {_fmt(avg_win)} | Avg loser: {_fmt(avg_loss)}. "
            f"RR ratio: {rr_ratio:.2f}x (need >= 1.0). "
            f"You need a {_pct(breakeven_win_rate)} win rate to break even at this RR ratio — "
            f"your {_pct(win_rate)} is "
            + ("above that: marginally profitable." if win_rate > breakeven_win_rate
               else f"below that: losing money despite a majority win rate.")
            + f" Profit factor: {profit_factor:.2f} (need >= 1.5 for sustainability)."
        )
    else:
        severity = "ok"
        headline = f"Win/loss ratio healthy — {rr_ratio:.2f}x reward per unit of risk."
        detail   = (
            f"Win rate: {_pct(win_rate)} | Avg win: {_fmt(avg_win)} | "
            f"Avg loss: {_fmt(avg_loss)} | RR: {rr_ratio:.2f}x | PF: {profit_factor:.2f}."
        )

    top_winners = [_row_to_evidence(r) for _, r in winners.nlargest(5, "Realized P&L").iterrows()]
    top_losers  = [_row_to_evidence(r) for _, r in losers.nsmallest(5, "Realized P&L").iterrows()]

    return {
        "logic_id": "L2",
        "name":     "Win/loss asymmetry trap",
        "severity": severity,
        "triggered": triggered,
        "headline": headline,
        "detail":   detail,
        "evidence": {"top_winners": top_winners, "top_losers": top_losers},
        "recommendation": (
            "Set a minimum 1:1 reward-to-risk on every trade. "
            "Profit-book at +40% of buy value; stop-loss at -30% of buy value. "
            "Never let a loss exceed your average winning trade size."
        ),
        "impact_inr": round(summary.get("realized_pnl", 0), 2),
        "metrics": {
            "total_trades":           n_total,
            "winners":                n_win,
            "losers":                 n_loss,
            "breakeven_trades":       n_zero,
            "win_rate_pct":           round(win_rate, 2),
            "avg_winner_inr":         round(avg_win, 2),
            "avg_loser_inr":          round(avg_loss, 2),
            "rr_ratio":               round(rr_ratio, 4),
            "breakeven_win_rate_pct": round(breakeven_win_rate, 2),
            "profit_factor":          round(profit_factor, 4),
            "gross_wins_inr":         round(gross_wins, 2),
            "gross_losses_inr":       round(gross_losses, 2),
        },
    }


# ─── L3: Brokerage Drag Analyser ─────────────────────────────────────────────

def logic_L3_brokerage_drag(trades, summary, open_positions, meta):
    """
    Flags when charges consume a disproportionate share of gross PnL.
    """
    charges      = summary.get("charges_total", 0.0)
    realized_pnl = summary.get("realized_pnl", 0.0)
    breakdown    = summary.get("charges_breakdown", {})
    n_trades     = max(len(trades), 1)

    cost_per_trade = charges / n_trades
    saved_30pct    = charges * 0.30

    if realized_pnl > 0:
        charges_to_pnl_pct = charges / realized_pnl * 100
        triggered = charges_to_pnl_pct > 15.0
    else:
        charges_to_pnl_pct = float("inf")
        triggered = charges > 3000.0

    biggest_comp  = max(breakdown, key=breakdown.get) if breakdown else "unknown"
    biggest_val   = breakdown.get(biggest_comp, 0.0)

    if triggered:
        severity = "critical" if (charges_to_pnl_pct != float("inf") and charges_to_pnl_pct > 40) else "high"
        if realized_pnl > 0:
            headline = (
                f"Charges consumed {_pct(charges_to_pnl_pct)} of gross profit "
                f"({_fmt(charges)} out of {_fmt(realized_pnl)})"
            )
            detail = (
                f"Paid {_fmt(charges)} in charges across {n_trades} trades "
                f"(avg {_fmt(cost_per_trade)}/trade). "
                f"That consumed {_pct(charges_to_pnl_pct)} of gross profit. "
                f"Largest component: {biggest_comp.replace('_',' ').title()} = {_fmt(biggest_val)}. "
                f"30% fewer trades would save {_fmt(saved_30pct)}/period."
            )
        else:
            headline = (
                f"Already at a loss — plus {_fmt(charges)} in charges "
                f"(total damage: {_fmt(abs(realized_pnl) + charges)})"
            )
            detail = (
                f"Gross loss of {_fmt(abs(realized_pnl))} compounded by "
                f"{_fmt(charges)} in charges. Every additional trade you "
                f"place while losing adds to the damage."
            )
    else:
        severity = "ok"
        headline = f"Charges in acceptable range ({_pct(charges_to_pnl_pct)} of gross PnL)."
        detail   = f"Charges: {_fmt(charges)} | Gross PnL: {_fmt(realized_pnl)} | Ratio: {_pct(charges_to_pnl_pct)}."

    return {
        "logic_id": "L3",
        "name":     "Brokerage drag analyser",
        "severity": severity,
        "triggered": triggered,
        "headline": headline,
        "detail":   detail,
        "evidence": [{"component": k, "amount_inr": round(v, 2)} for k, v in breakdown.items()],
        "recommendation": (
            "Keep charges < 10% of gross PnL. "
            "Fewer, higher-conviction trades reduce charge drag significantly. "
            "Index options carry lower STT than stock options. "
            "Minimum profit per trade target = charge cost x 3."
        ),
        "impact_inr": -round(charges, 2),
        "metrics": {
            "total_charges_inr":            round(charges, 2),
            "charges_to_gross_pnl_pct":     round(charges_to_pnl_pct, 2) if charges_to_pnl_pct != float("inf") else 999.0,
            "cost_per_trade_inr":           round(cost_per_trade, 2),
            "gross_realized_pnl_inr":       round(realized_pnl, 2),
            "biggest_charge_component":     biggest_comp,
            "biggest_charge_amount_inr":    round(biggest_val, 2),
            "saving_if_30pct_fewer_trades": round(saved_30pct, 2),
            "charges_breakdown":            {k: round(v, 2) for k, v in breakdown.items()},
        },
    }


# ─── L4: Overtrading Detector ────────────────────────────────────────────────

def logic_L4_overtrading(trades, summary, open_positions, meta):
    """Flags excessive instruments and same-underlying strike scatter."""
    n = len(trades)

    if   n <= 15: level, severity, triggered = "focused",  "ok",       False
    elif n <= 30: level, severity, triggered = "moderate", "medium",   True
    elif n <= 50: level, severity, triggered = "high",     "high",     True
    else:         level, severity, triggered = "severe",   "critical", True

    scatter = []
    if "underlying" in trades.columns and "expiry_str" in trades.columns:
        grp = (
            trades[trades["underlying"].notna()]
            .groupby(["underlying", "expiry_str"])
            .agg(strike_count=("Symbol","count"), pnl=("Realized P&L","sum"))
            .reset_index()
        )
        scatter = grp[grp["strike_count"] >= 4].to_dict("records")

    underlying_counts = {}
    if "underlying" in trades.columns:
        underlying_counts = trades["underlying"].value_counts().head(10).to_dict()

    INDEX = ["NIFTY","BANKNIFTY","FINNIFTY","SENSEX","MIDCPNIFTY"]
    index_trades = trades[trades["underlying"].isin(INDEX)] if "underlying" in trades.columns else pd.DataFrame()
    index_pct = len(index_trades) / n * 100 if n > 0 else 0.0

    if triggered:
        headline = f"{n} instruments traded — {level.upper()} overtrading (focused = 10-20)"
        detail   = (
            f"You traded {n} different option contracts this period. "
            f"Focused F&O traders manage 10-20 positions maximum. "
            f"At {n} positions you cannot properly monitor stop-losses or news. "
            + (f"Strike scatter on {len(scatter)} underlying(s): buying 4+ strikes "
               f"on the same stock in the same expiry = direction confusion. " if scatter else "")
        )
    else:
        headline = f"Trade count healthy — {n} instruments (within focused range)."
        detail   = f"{n} instruments. No overtrading detected."

    return {
        "logic_id": "L4", "name": "Overtrading detector",
        "severity": severity, "triggered": triggered,
        "headline": headline, "detail": detail,
        "evidence": {"strike_scatter_groups": scatter[:10], "top_underlyings": underlying_counts},
        "recommendation": (
            "Cap positions at 15-20/month. Know 8-10 stocks deeply; only trade those. "
            "Before opening a new trade, close one that is at target or stop. "
            "On any one underlying, hold at most 1 CE and 1 PE."
        ),
        "impact_inr": 0.0,
        "metrics": {
            "total_instruments":          n,
            "overtrading_level":          level,
            "strike_scatter_underlyings": len(scatter),
            "index_trades":               int(len(index_trades)),
            "stock_trades":               int(n - len(index_trades)),
            "index_pct":                  round(index_pct, 2),
        },
    }


# ─── L5: Single-Trade Capital Concentration Risk ──────────────────────────────

def logic_L5_capital_concentration(trades, summary, open_positions, meta):
    """
    Flags single trades that dominate the loss pool.

    FIX: Denominator is now total_gross_losses (sum of all losing trades),
    NOT |realized_pnl|. Using net PnL as denominator produced absurd ratios
    (263% in Dec where net PnL was small but one trade lost 40k).
    """
    LOSS_PCT_THRESHOLD     = 20.0   # loss > 20% of total gross losses
    BUY_PCT_THRESHOLD      = 10.0   # buy value > 10% of all capital deployed

    losers         = trades[trades["Realized P&L"] < 0].copy()
    total_buy      = trades["Buy Value"].sum()
    total_gross_loss = abs(losers["Realized P&L"].sum()) if len(losers) > 0 else 1.0

    losers["loss_pct_of_gross"]   = losers["Realized P&L"].abs() / total_gross_loss * 100
    losers["buy_pct_of_deployed"] = losers["Buy Value"] / max(total_buy, 1) * 100

    flagged = losers[
        (losers["loss_pct_of_gross"]   > LOSS_PCT_THRESHOLD) |
        (losers["buy_pct_of_deployed"] > BUY_PCT_THRESHOLD)
    ]

    triggered = len(flagged) > 0

    worst_trade = None
    if len(losers) > 0:
        wr = losers.loc[losers["Realized P&L"].idxmin()]
        worst_trade = {
            "symbol":               str(wr["Symbol"]),
            "realized_pnl":         float(wr["Realized P&L"]),
            "buy_value":            float(wr["Buy Value"]),
            "loss_pct_of_all_losses": round(float(wr["loss_pct_of_gross"]), 2),
            "buy_pct_of_deployed":  round(float(wr["buy_pct_of_deployed"]), 2),
        }

    evidence = []
    for _, row in flagged.nsmallest(10, "Realized P&L").iterrows():
        ev = _row_to_evidence(row)
        ev["loss_pct_of_all_losses"]  = round(float(row["loss_pct_of_gross"]), 2)
        ev["buy_pct_of_total_deployed"] = round(float(row["buy_pct_of_deployed"]), 2)
        evidence.append(ev)

    if triggered:
        severity = "critical" if len(flagged) >= 3 else "high"
        headline = (
            f"{len(flagged)} trade(s) each represent >20% of all losses — "
            f"position sizing is inconsistent"
        )
        detail = (
            f"Total deployed: {_fmt(total_buy)} | Total gross losses: {_fmt(total_gross_loss)}. "
            f"{len(flagged)} trades each contributed >20% of the total loss pool."
            + (f" Worst: {worst_trade['symbol']} lost "
               f"{_fmt(abs(worst_trade['realized_pnl']))} = "
               f"{_pct(worst_trade['loss_pct_of_all_losses'])} of all losses." if worst_trade else "")
        )
    else:
        severity = "ok"
        headline = "Loss distribution balanced — no single trade dominates the loss pool."
        detail   = f"Largest single loss: {_fmt(abs(losers['Realized P&L'].min()))} ({_pct(worst_trade['loss_pct_of_all_losses'])} of gross losses)." if worst_trade else "No losses."

    return {
        "logic_id": "L5", "name": "Single-trade capital concentration risk",
        "severity": severity, "triggered": triggered,
        "headline": headline, "detail": detail, "evidence": evidence,
        "recommendation": (
            "5% rule: no single trade should risk more than 5% of monthly capital. "
            "Compute max_loss = buy_value × 0.30 before entering. "
            "If max_loss > 5% of capital, reduce lot size. "
            "One trade must never define your month."
        ),
        "impact_inr": round(flagged["Realized P&L"].sum(), 2) if len(flagged) > 0 else 0.0,
        "metrics": {
            "flagged_trades_count":        int(len(flagged)),
            "total_capital_deployed_inr":  round(total_buy, 2),
            "total_gross_losses_inr":      round(total_gross_loss, 2),
            "worst_single_loss_inr":       round(abs(losers["Realized P&L"].min()), 2) if len(losers) > 0 else 0.0,
            "worst_trade":                 worst_trade,
        },
    }


# ─── L6: Option Buying Bias + Theta Decay Flag ───────────────────────────────

def logic_L6_option_buyer_bias(trades, summary, open_positions, meta):
    """
    100% option buyer flag + weekly concentration flag (only when losing on weeklies).
    FIX: Weekly flag now only fires when weekly PnL is actually negative.
    """
    n = len(trades)
    if n == 0:
        return {
            "logic_id": "L6", "name": "Option buying bias + theta decay flag",
            "severity": "ok", "triggered": False,
            "headline": "No trades.", "detail": "",
            "evidence": [], "recommendation": "", "impact_inr": 0.0, "metrics": {},
        }

    option_trades = trades[trades["option_type"].isin(["CE","PE"])] if "option_type" in trades.columns else trades
    pct_options   = len(option_trades) / n * 100

    weekly  = trades[trades["expiry_type"] == "weekly"]  if "expiry_type" in trades.columns else pd.DataFrame()
    monthly = trades[trades["expiry_type"] == "monthly"] if "expiry_type" in trades.columns else pd.DataFrame()
    weekly_pct  = len(weekly) / n * 100
    weekly_pnl  = weekly["Realized P&L"].sum()  if len(weekly)  > 0 else 0.0
    monthly_pnl = monthly["Realized P&L"].sum() if len(monthly) > 0 else 0.0

    ce = trades[trades["option_type"] == "CE"] if "option_type" in trades.columns else pd.DataFrame()
    pe = trades[trades["option_type"] == "PE"] if "option_type" in trades.columns else pd.DataFrame()
    ce_pnl = ce["Realized P&L"].sum() if len(ce) > 0 else 0.0
    pe_pnl = pe["Realized P&L"].sum() if len(pe) > 0 else 0.0

    pure_buyer     = pct_options >= 95.0
    weekly_losing  = weekly_pct > 50.0 and weekly_pnl < 0   # FIX: only if actually losing

    triggered = pure_buyer or weekly_losing
    parts, headline_parts = [], []

    if pure_buyer:
        parts.append(
            "100% option buyer — theta decays every position daily. "
            "No premium-selling trades offset the time-value bleed."
        )
        headline_parts.append("100% option buyer — theta works against every position")
    if weekly_losing:
        parts.append(
            f"Weekly expiry concentration: {_pct(weekly_pct)} of trades are weeklies "
            f"with net loss of {_fmt(abs(weekly_pnl))}."
        )
        headline_parts.append(f"Weekly expiry losing {_fmt(abs(weekly_pnl))}")

    if triggered:
        severity = "high" if pure_buyer else "medium"
        headline = " | ".join(headline_parts)
        detail   = " ".join(parts) + (
            f" Weekly PnL: {_fmt(weekly_pnl)} | Monthly PnL: {_fmt(monthly_pnl)}. "
            f"CE PnL: {_fmt(ce_pnl)} | PE PnL: {_fmt(pe_pnl)}."
        )
        impact = weekly_pnl if weekly_losing else 0.0
    else:
        severity = "ok"
        headline = f"Option mix acceptable — weekly: {_fmt(weekly_pnl)}, monthly: {_fmt(monthly_pnl)}."
        detail   = (
            f"Weekly: {len(weekly)} trades ({_pct(weekly_pct)}) PnL {_fmt(weekly_pnl)}. "
            f"Monthly: {len(monthly)} trades PnL {_fmt(monthly_pnl)}. "
            f"CE PnL: {_fmt(ce_pnl)} | PE PnL: {_fmt(pe_pnl)}."
        )
        impact = 0.0

    return {
        "logic_id": "L6", "name": "Option buying bias + theta decay flag",
        "severity": severity, "triggered": triggered,
        "headline": headline, "detail": detail, "evidence": [],
        "recommendation": (
            "Balance with 20-30% premium selling (credit spreads, short puts). "
            "For weeklies: only enter when expecting a sharp 2-day move. "
            "Sellers receive theta daily — time works for them."
        ),
        "impact_inr": round(impact, 2),
        "metrics": {
            "pct_option_trades": round(pct_options, 2),
            "pure_buyer":        pure_buyer,
            "weekly_trades":     int(len(weekly)),
            "monthly_trades":    int(len(monthly)),
            "weekly_pct":        round(weekly_pct, 2),
            "weekly_pnl_inr":    round(weekly_pnl, 2),
            "monthly_pnl_inr":   round(monthly_pnl, 2),
            "ce_pnl_inr":        round(ce_pnl, 2),
            "pe_pnl_inr":        round(pe_pnl, 2),
        },
    }


# ─── L7: Open Position Hemorrhage Tracker ────────────────────────────────────

def logic_L7_open_position_hemorrhage(trades, summary, open_positions, meta):
    """Detects open positions carried into next period with >10% unrealised loss."""
    LOSS_THRESHOLD_PCT = 10.0

    if len(open_positions) == 0:
        return {
            "logic_id": "L7", "name": "Open position hemorrhage tracker",
            "severity": "ok", "triggered": False,
            "headline": "No open positions at period end.", "detail": "",
            "evidence": [], "recommendation": "", "impact_inr": 0.0, "metrics": {},
        }

    losing_open = open_positions[open_positions["Unrealized P&L"] < 0].copy()
    losing_open["unrealised_loss_pct"] = (
        losing_open["Unrealized P&L"].abs()
        / losing_open["Open Value"].replace(0, np.nan) * 100
    ).fillna(0.0)

    severe            = losing_open[losing_open["unrealised_loss_pct"] > LOSS_THRESHOLD_PCT]
    total_open_value  = open_positions["Open Value"].sum()
    total_unrealised  = open_positions["Unrealized P&L"].sum()
    total_unreal_pct  = abs(total_unrealised) / total_open_value * 100 if total_open_value > 0 else 0.0
    triggered         = len(severe) > 0

    evidence = [
        {
            "symbol":             str(r["Symbol"]),
            "open_quantity":      int(r["Open Quantity"]),
            "open_value_inr":     float(r["Open Value"]),
            "unrealised_pnl_inr": float(r["Unrealized P&L"]),
            "unrealised_pnl_pct": round(float(r.get("Unrealized P&L Pct.", 0)), 2),
            "previous_close":     float(r.get("Previous Closing Price", 0)),
        }
        for _, r in open_positions.sort_values("Unrealized P&L").iterrows()
    ]

    if triggered:
        severity = "high" if abs(total_unrealised) > 20000 else "medium"
        headline = (
            f"{len(severe)} open position(s) bleeding >10% — "
            f"{_fmt(abs(total_unrealised))} unrealised loss carried forward"
        )
        detail = (
            f"{len(open_positions)} position(s) open at period end. "
            f"Total open value: {_fmt(total_open_value)}. "
            f"Unrealised loss: {_fmt(abs(total_unrealised))} ({_pct(total_unreal_pct)}). "
            f"{len(severe)} positions lost >10% of open value and are still held — "
            f"these crystallise next period if not reversed."
        )
    else:
        severity = "ok"
        headline = (
            f"Open positions in profit {_fmt(total_unrealised)}."
            if total_unrealised >= 0
            else f"Minor unrealised loss {_fmt(abs(total_unrealised))} within threshold."
        )
        detail = f"Open: {len(open_positions)} | Value: {_fmt(total_open_value)} | Unrealised: {_fmt(total_unrealised)}."

    return {
        "logic_id": "L7", "name": "Open position hemorrhage tracker",
        "severity": severity, "triggered": triggered,
        "headline": headline, "detail": detail, "evidence": evidence,
        "recommendation": (
            "Never carry an option down >15% of open value into the next week without a clear exit plan. "
            "Options bleed fastest in the final week before expiry."
        ),
        "impact_inr": round(total_unrealised, 2),
        "metrics": {
            "open_positions_count":      int(len(open_positions)),
            "bleeding_positions_count":  int(len(losing_open)),
            "severe_bleeders_count":     int(len(severe)),
            "total_open_value_inr":      round(total_open_value, 2),
            "total_unrealised_pnl_inr":  round(total_unrealised, 2),
            "total_unrealised_loss_pct": round(total_unreal_pct, 2),
        },
    }


# ─── L8: Charge-Negative Trade Detector ──────────────────────────────────────

def logic_L8_breakeven_waste(trades, summary, open_positions, meta):
    """
    Identifies trades that earned less than their charge cost.

    FIX vs v1: Old logic used |PnL%| < 2% which wrongly flagged large profitable
    trades (e.g. +₹3,082 at 1.59% = real money, not waste). New logic uses the
    ABSOLUTE charge cost per trade as the threshold — if you earned less than the
    fees you paid, the trade was charge-negative.
    """
    charges    = summary.get("charges_total", 0.0)
    n_trades   = max(len(trades), 1)
    avg_charge = charges / n_trades

    exact_zero       = trades[trades["Realized P&L"] == 0.0]
    charge_neg_wins  = trades[(trades["Realized P&L"] > 0) & (trades["Realized P&L"] < avg_charge)]
    near_zero_losses = trades[(trades["Realized P&L"] < 0) & (trades["Realized P&L"] > -avg_charge)]

    all_wasted = pd.concat([exact_zero, charge_neg_wins, near_zero_losses]).drop_duplicates()
    n_wasted   = len(all_wasted)
    fees_wasted = n_wasted * avg_charge

    triggered = n_wasted > 3 or (n_wasted / n_trades > 0.08)

    if triggered:
        severity = "medium"
        headline = (
            f"{n_wasted} trades earned less than their charge cost "
            f"({_fmt(avg_charge)}/trade avg) — {_fmt(fees_wasted)} in fees wasted"
        )
        detail = (
            f"With {_fmt(charges)} total charges across {n_trades} trades, "
            f"each trade costs ~{_fmt(avg_charge)} in brokerage + taxes. "
            f"{len(exact_zero)} trades closed at exactly ₹0. "
            f"{len(charge_neg_wins)} trades earned a profit but less than {_fmt(avg_charge)} — "
            f"charge-negative after fees. "
            f"{len(near_zero_losses)} trades closed at a tiny loss smaller than one trade's charge. "
            f"Estimated fees wasted on these {n_wasted} trades: {_fmt(fees_wasted)}."
        )
    else:
        severity = "ok"
        headline = f"Charge-negative trades minimal — only {n_wasted} below fee threshold."
        detail   = (
            f"Avg charge/trade: {_fmt(avg_charge)}. "
            f"Exact-zero: {len(exact_zero)}. Charge-negative wins: {len(charge_neg_wins)}."
        )

    return {
        "logic_id": "L8", "name": "Charge-negative trade detector",
        "severity": severity, "triggered": triggered,
        "headline": headline, "detail": detail,
        "evidence": [_row_to_evidence(r) for _, r in all_wasted.iterrows()][:15],
        "recommendation": (
            f"Minimum profit target per trade = charge cost x 3 = {_fmt(avg_charge * 3)}. "
            "If you cannot see a path to that profit at entry, skip the trade. "
            "Zero-PnL trades and tiny wins still cost you in fees."
        ),
        "impact_inr": -round(fees_wasted, 2),
        "metrics": {
            "avg_charge_per_trade_inr":     round(avg_charge, 2),
            "exact_zero_pnl_trades":        int(len(exact_zero)),
            "charge_negative_wins":         int(len(charge_neg_wins)),
            "near_zero_loss_trades":        int(len(near_zero_losses)),
            "total_wasted_trades":          n_wasted,
            "wasted_pct_of_total":          round(n_wasted / n_trades * 100, 2),
            "estimated_fees_wasted_inr":    round(fees_wasted, 2),
        },
    }


# ─── L9: OTM Strike Selection Risk ───────────────────────────────────────────

def logic_L9_strike_selection(trades, summary, open_positions, meta):
    """
    Analyses moneyness using premium_pct_of_strike = (per_unit_price / strike) x 100.

    FIX vs v1: Old tiers used raw per-unit price (e.g. >100 = ATM) which is
    meaningless across underlyings (BEL ₹400 vs NIFTY ₹26000 have very different
    absolute premiums). The percentage of strike is comparable across all underlyings.

    Tiers (calibrated to Indian F&O market):
      > 3.5%   ATM / near-ATM
      1.5-3.5% Slight OTM
      0.5-1.5% OTM (meaningful directional move required)
      < 0.5%   Far OTM / lottery

    KEY FIX: Only flag if Far OTM options ALSO expired near-worthless.
    A cheap NIFTY weekly that gives 72% return is NOT a problem.
    """
    if ("buy_price_per_unit" not in trades.columns or
            "strike" not in trades.columns):
        return {
            "logic_id": "L9", "name": "OTM strike selection risk",
            "severity": "low", "triggered": False,
            "headline": "Cannot compute moneyness — strike data missing.",
            "detail": "", "evidence": [], "recommendation": "", "impact_inr": 0.0, "metrics": {},
        }

    df = trades[
        trades["buy_price_per_unit"].notna() &
        trades["strike"].notna() &
        (trades["strike"] > 0)
    ].copy()

    if len(df) == 0:
        return {
            "logic_id": "L9", "name": "OTM strike selection risk",
            "severity": "ok", "triggered": False,
            "headline": "No analysable trades.", "detail": "",
            "evidence": [], "recommendation": "", "impact_inr": 0.0, "metrics": {},
        }

    df["premium_pct"] = df["buy_price_per_unit"] / df["strike"] * 100

    TIERS = [
        ("atm",       3.5, float("inf"), "ATM / near-ATM  (> 3.5% of strike)"),
        ("slight_otm",1.5, 3.5,          "Slight OTM      (1.5-3.5%)"),
        ("otm",       0.5, 1.5,          "OTM             (0.5-1.5%)"),
        ("far_otm",   0.0, 0.5,          "Far OTM/lottery (< 0.5%)"),
    ]

    tier_stats = {}
    for key, lo, hi, label in TIERS:
        sub = df[(df["premium_pct"] >= lo) & (df["premium_pct"] < hi)]
        wr  = (sub["Realized P&L"] > 0).sum() / len(sub) * 100 if len(sub) > 0 else 0.0
        nw  = (sub["Realized P&L Pct."] <= -80).sum()
        tier_stats[key] = {
            "label":                label,
            "count":                int(len(sub)),
            "pct_of_total":         round(len(sub) / len(df) * 100, 2),
            "win_rate_pct":         round(wr, 2),
            "avg_pnl_inr":          round(sub["Realized P&L"].mean(), 2) if len(sub) > 0 else 0.0,
            "near_worthless_exits": int(nw),
            "total_pnl_inr":        round(sub["Realized P&L"].sum(), 2),
        }

    far_otm_df       = df[df["premium_pct"] < 0.5]
    far_otm_worthless = far_otm_df[far_otm_df["Realized P&L Pct."] <= -80]
    far_otm_pnl      = far_otm_df["Realized P&L"].sum()

    # Only trigger if far-OTM options are ALSO expiring near-worthless (pattern, not one-off)
    triggered = len(far_otm_worthless) >= 2 and far_otm_pnl < 0

    evidence = [
        _row_to_evidence(r, ["premium_pct", "strike", "underlying"])
        for _, r in far_otm_worthless.nsmallest(8, "Realized P&L").iterrows()
    ]

    if triggered:
        severity = "high" if len(far_otm_worthless) >= 3 else "medium"
        headline = (
            f"{len(far_otm_worthless)} Far OTM options (< 0.5% of strike) expired "
            f"near-worthless — {_fmt(abs(far_otm_pnl))} net loss from this tier"
        )
        detail = (
            f"{len(far_otm_df)} trades had a premium below 0.5% of the strike price (Far OTM). "
            f"{len(far_otm_worthless)} of these lost >80% — lottery tickets that didn't pay. "
            f"Far OTM win rate: {_pct(tier_stats['far_otm']['win_rate_pct'])}. "
            f"Net PnL from Far OTM tier: {_fmt(far_otm_pnl)}."
        )
    else:
        severity = "ok"
        headline = (
            f"Strike selection acceptable — "
            f"Far OTM near-worthless: {len(far_otm_worthless)} trade(s)."
        )
        detail = f"Far OTM trades: {len(far_otm_df)} (win rate {_pct(tier_stats['far_otm']['win_rate_pct'])})."

    return {
        "logic_id": "L9", "name": "OTM strike selection risk",
        "severity": severity, "triggered": triggered,
        "headline": headline, "detail": detail, "evidence": evidence,
        "recommendation": (
            "Keep > 60% of option buys in Slight OTM or ATM tier (premium > 1.5% of strike). "
            "Far OTM (<0.5%) should be max 10-15% of book and only for event-driven trades. "
            "For NIFTY: premium below ₹50/unit on a weekly = Far OTM, needs 200+ point move."
        ),
        "impact_inr": round(far_otm_pnl, 2) if triggered else 0.0,
        "metrics": {
            "total_analysed":          int(len(df)),
            "far_otm_count":           int(len(far_otm_df)),
            "far_otm_worthless_count": int(len(far_otm_worthless)),
            "far_otm_total_pnl_inr":   round(far_otm_pnl, 2),
            "tier_breakdown":          tier_stats,
        },
    }


# ─── L10: Loss Clustering & Underlying Concentration ─────────────────────────

def logic_L10_monthly_trend(trades, summary, open_positions, meta):
    """
    FIX vs v1: Old logic split trades alphabetically (meaningless since Zerodha
    sorts symbols alphabetically, not chronologically).

    New approach — Loss Clustering Analysis:
      1. Underlying concentration: one stock > 40% of all losses = over-exposed
      2. Directional bias: CE or PE > 70% of losses = consistently wrong on direction
      3. Near-worthless rate: > 8% of trades expired near-worthless = systemic holding issue
      4. Bad underlyings: stocks where loss rate > 70% across >= 3 trades
    """
    n = len(trades)
    if n < 4:
        return {
            "logic_id": "L10", "name": "Loss clustering & concentration",
            "severity": "ok", "triggered": False,
            "headline": "Too few trades for clustering analysis.",
            "detail": "Need >= 4 trades.", "evidence": [],
            "recommendation": "", "impact_inr": 0.0, "metrics": {},
        }

    losers     = trades[trades["Realized P&L"] < 0]
    total_loss = abs(losers["Realized P&L"].sum()) if len(losers) > 0 else 1.0
    flags      = []

    # 1. Underlying concentration in losses
    underlying_concentration = {}
    if "underlying" in trades.columns and len(losers) > 0:
        loss_by_u = losers.groupby("underlying")["Realized P&L"].sum().abs().sort_values(ascending=False)
        for u, lv in loss_by_u.items():
            pct = lv / loss_by_u.sum() * 100
            underlying_concentration[str(u)] = {"loss_inr": round(float(lv),2), "pct_of_total_losses": round(float(pct),2)}
        top_u, top_d = list(underlying_concentration.items())[0] if underlying_concentration else (None,{})
        if top_d and top_d.get("pct_of_total_losses",0) > 40:
            flags.append(
                f"Loss concentration: {top_u} accounts for {_pct(top_d['pct_of_total_losses'])} "
                f"of all losses ({_fmt(top_d['loss_inr'])}). Over-exposed to a single view."
            )

    # 2. Directional bias
    direction_bias = {}
    if "option_type" in trades.columns and len(losers) > 0:
        lb_type = losers.groupby("option_type")["Realized P&L"].sum().abs()
        tot_dir = lb_type.sum() or 1.0
        for ot, lv in lb_type.items():
            direction_bias[str(ot)] = {"loss_inr": round(float(lv),2), "pct_of_total_losses": round(float(lv/tot_dir*100),2)}
        for ot, d in direction_bias.items():
            if d["pct_of_total_losses"] > 70:
                label = "BULLISH (call buying)" if ot == "CE" else "BEARISH (put buying)"
                flags.append(
                    f"Directional bias: {_pct(d['pct_of_total_losses'])} of losses on {ot} ({label}). "
                    f"Your market bias is consistently wrong on that side."
                )

    # 3. Near-worthless exit rate
    near_worthless = trades[trades["Realized P&L Pct."] <= -80]
    nw_rate = len(near_worthless) / n * 100
    nw_expiry_dist = {}
    if "expiry_type" in near_worthless.columns and len(near_worthless) > 0:
        nw_expiry_dist = near_worthless["expiry_type"].value_counts().to_dict()
    if nw_rate > 8:
        flags.append(
            f"Near-worthless exit rate: {_pct(nw_rate)} ({len(near_worthless)}/{n} trades). "
            f"This is a systemic stop-loss discipline failure, not bad luck."
        )

    # 4. Consistently losing underlyings
    bad_underlyings = []
    if "underlying" in trades.columns:
        for ug, grp in trades.groupby("underlying"):
            if len(grp) < 3: continue
            lr = (grp["Realized P&L"] < 0).sum() / len(grp) * 100
            if lr > 70:
                bad_underlyings.append({
                    "underlying":    str(ug),
                    "trades":        int(len(grp)),
                    "loss_rate_pct": round(float(lr),2),
                    "total_pnl_inr": round(float(grp["Realized P&L"].sum()),2),
                })
        if bad_underlyings:
            names = ", ".join(b["underlying"] for b in bad_underlyings[:3])
            flags.append(
                f"Consistently losing on: {names} (loss rate >70%). "
                f"Remove these from your watchlist until you understand why."
            )

    triggered = len(flags) > 0
    severity  = "high" if len(flags) >= 2 else ("medium" if flags else "ok")

    headline = (
        f"{len(flags)} concentration/clustering problem(s) detected"
        if triggered else
        "No loss clustering — losses distributed across underlyings and directions."
    )
    detail = " | ".join(flags) if triggered else (
        f"Near-worthless rate: {_pct(nw_rate)}. No single underlying dominates losses."
    )

    return {
        "logic_id": "L10", "name": "Loss clustering & concentration",
        "severity": severity, "triggered": triggered,
        "headline": headline, "detail": detail,
        "evidence": [_row_to_evidence(r) for _,r in near_worthless.nsmallest(5,"Realized P&L").iterrows()],
        "recommendation": (
            "If one underlying generates losses consistently, remove it for 60 days. "
            "If > 70% of losses are directional (all CE or all PE), paper-trade the opposite "
            "for 2 weeks to recalibrate. Near-worthless rate > 8% = mandatory stop-loss rule."
        ),
        "impact_inr": -round(total_loss, 2) if triggered else 0.0,
        "metrics": {
            "total_trades":                  n,
            "near_worthless_count":          int(len(near_worthless)),
            "near_worthless_rate_pct":       round(nw_rate, 2),
            "nw_expiry_distribution":        {str(k):int(v) for k,v in nw_expiry_dist.items()},
            "underlying_loss_concentration": underlying_concentration,
            "directional_bias":              direction_bias,
            "bad_underlyings":               bad_underlyings,
            "clustering_flags_count":        len(flags),
        },
    }