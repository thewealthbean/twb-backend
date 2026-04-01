"""
parser.py
=========
Parses Zerodha F&O PnL Excel statements into clean, structured DataFrames.

Zerodha statement layout (both F&O sheet and Other Debits & Credits):
  - Rows 1–11   : header / metadata (client ID, period, guide link)
  - Row 12      : "Summary" label
  - Rows 14–17  : summary block  (Charges, Other Credit & Debit, Realized P&L, Unrealized P&L)
  - Row 20      : "Charges" label
  - Row 22      : Account Head / Amount header
  - Rows 23–32  : individual charge line-items
  - Row 37      : trade table header (Symbol, ISIN, Quantity, Buy Value …)
  - Row 38+     : one row per instrument traded

The function find_header_row() scans for the 'Symbol' header dynamically,
so the engine stays robust if Zerodha shifts rows in future exports.
"""

import re
from datetime import datetime
from typing import Optional
import pandas as pd


# ─── column names we expect in the trade table ───────────────────────────────
REQUIRED_COLS = {
    "Symbol", "Quantity", "Buy Value", "Sell Value",
    "Realized P&L", "Realized P&L Pct.",
    "Open Quantity", "Open Quantity Type", "Open Value",
    "Unrealized P&L", "Unrealized P&L Pct.",
}


def _find_header_row(df_raw: pd.DataFrame) -> Optional[int]:
    """Return the 0-based row index that contains the trade table header."""
    for idx, row in df_raw.iterrows():
        if "Symbol" in str(row.values):
            return idx
    return None


def _extract_summary_block(df_raw: pd.DataFrame) -> dict:
    """
    Pull the top-level summary numbers from the raw sheet:
      Charges, Other Credit & Debit, Realized P&L, Unrealized P&L
    and individual charge line-items.
    """
    summary = {
        "charges_total": 0.0,
        "other_credit_debit": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "charges_breakdown": {},
        "period": "",
        "client_id": "",
    }

    charge_keys = {
        "Brokerage": "brokerage",
        "Exchange Transaction Charges": "exchange_txn_charges",
        "Clearing Charges": "clearing_charges",
        "Central GST": "cgst",
        "State GST": "sgst",
        "Integrated GST": "igst",
        "Securities Transaction Tax": "stt",
        "SEBI Turnover Fees": "sebi_fees",
        "Stamp Duty": "stamp_duty",
        "IPFT": "ipft",
    }

    for _, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row.values if pd.notna(v) and str(v).strip()]

        # client ID
        if "Client ID" in vals:
            idx = vals.index("Client ID")
            if idx + 1 < len(vals):
                summary["client_id"] = vals[idx + 1]

        # period string  e.g. "P&L Statement for F&O from 2025-06-01 to 2025-06-30"
        for v in vals:
            if "from" in v and "to" in v and ("F&O" in v or "FO" in v):
                summary["period"] = v
                break

        # summary line-items
        if len(vals) >= 2:
            label, *rest = vals
            try:
                amount = float(rest[0])
            except (ValueError, IndexError):
                amount = None

            if amount is not None:
                if label == "Charges":
                    summary["charges_total"] = amount
                elif label == "Other Credit & Debit":
                    summary["other_credit_debit"] = amount
                elif label == "Realized P&L":
                    summary["realized_pnl"] = amount
                elif label == "Unrealized P&L":
                    summary["unrealized_pnl"] = amount

            # charge breakdown
            for k, field in charge_keys.items():
                if label.startswith(k) and amount is not None:
                    summary["charges_breakdown"][field] = amount

    return summary


def _extract_period_dates(period_str: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse 'P&L Statement for F&O from 2025-06-01 to 2025-06-30'
    into (start_date, end_date) strings.
    """
    match = re.search(r"from (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", period_str)
    if match:
        return match.group(1), match.group(2)
    return None, None


def _parse_symbol(symbol: str) -> dict:
    """
    Decode a Zerodha F&O symbol into its components.

    Zerodha symbol formats:
      Index options  : NIFTY2561225300CE  → underlying=NIFTY  expiry=25-Jun-12  strike=25300  type=CE
      Monthly options: NIFTY25JUN25000CE  → underlying=NIFTY  expiry=Jun-2025   strike=25000  type=CE
      Stock options  : TATACHEM25JUN980CE → underlying=TATACHEM expiry=Jun-2025 strike=980    type=CE
      Weekly Index   : NIFTY2570325200CE  → underlying=NIFTY  expiry=25-Jul-03  strike=25200  type=CE
    """
    info = {
        "raw_symbol": symbol,
        "underlying": None,
        "expiry_str": None,
        "strike": None,
        "option_type": None,   # CE or PE
        "expiry_type": None,   # weekly or monthly
        "days_to_expiry": None,
    }

    if not isinstance(symbol, str):
        return info

    symbol = symbol.strip().upper()

    # option type
    if symbol.endswith("CE"):
        info["option_type"] = "CE"
    elif symbol.endswith("PE"):
        info["option_type"] = "PE"
    else:
        return info  # futures or unknown

    # ── Pattern 1: Monthly expiry  e.g. NIFTY25JUN25000CE ──────────────────
    m = re.match(r"^([A-Z&]+)(\d{2})([A-Z]{3})(\d+)(CE|PE)$", symbol)
    if m:
        info["underlying"] = m.group(1)
        info["expiry_str"] = f"{m.group(3)}-20{m.group(2)}"
        info["expiry_type"] = "monthly"
        info["strike"] = int(m.group(4))
        return info

    # ── Pattern 2: Weekly expiry   e.g. NIFTY2561225300CE ──────────────────
    # Format: UNDERLYING + YY + MM-digit + DD + STRIKE + TYPE
    # YY=25, M=6, DD=12, STRIKE=25300 → 256 12 25300
    m = re.match(r"^([A-Z&]+)(\d{2})(\d)(\d{2})(\d+)(CE|PE)$", symbol)
    if m:
        info["underlying"] = m.group(1)
        yy = m.group(2)
        mo = m.group(3).zfill(2)
        dd = m.group(4)
        info["expiry_str"] = f"20{yy}-{mo}-{dd}"
        info["expiry_type"] = "weekly"
        info["strike"] = int(m.group(5))
        return info

    # ── Pattern 3: SENSEX/BANKNIFTY weekly  e.g. SENSEX2580580000PE ────────
    m = re.match(r"^([A-Z]+)(\d{2})([A-Z]\d{2})(\d+)(CE|PE)$", symbol)
    if m:
        info["underlying"] = m.group(1)
        info["expiry_str"] = m.group(2) + m.group(3)
        info["expiry_type"] = "weekly"
        info["strike"] = int(m.group(4))
        return info

    # fallback: just capture underlying
    m = re.match(r"^([A-Z&]+)\d", symbol)
    if m:
        info["underlying"] = m.group(1)
    return info


def _infer_buy_price_per_unit(row: pd.Series) -> Optional[float]:
    """
    Estimate per-unit buy price from Buy Value / Quantity.
    Used as a moneyness proxy when market data is unavailable.
    """
    try:
        qty = float(row["Quantity"])
        bv = float(row["Buy Value"])
        if qty > 0:
            return round(bv / qty, 4)
    except Exception:
        pass
    return None


def parse_pnl_file(file_path: str) -> dict:
    """
    Main entry point. Reads a Zerodha F&O PnL Excel file and returns:

    {
      "meta": {
          "client_id": str,
          "period": str,
          "start_date": str,
          "end_date": str,
          "file_path": str,
      },
      "summary": {
          "charges_total": float,
          "other_credit_debit": float,
          "realized_pnl": float,
          "unrealized_pnl": float,
          "charges_breakdown": dict,
          "net_pnl": float,
      },
      "trades": pd.DataFrame,          # closed trades (Quantity > 0)
      "open_positions": pd.DataFrame,  # open positions (Open Quantity > 0)
      "all_rows": pd.DataFrame,        # every row in the trade table
    }

    Raises ValueError if the file cannot be parsed as a valid Zerodha statement.
    """

    # ── load raw sheet ───────────────────────────────────────────────────────
    raw = pd.read_excel(file_path, sheet_name="F&O", header=None)

    summary = _extract_summary_block(raw)

    header_row = _find_header_row(raw)
    if header_row is None:
        raise ValueError(
            f"Could not locate the trade table header ('Symbol') in {file_path}. "
            "Ensure this is a Zerodha F&O PnL statement."
        )

    # ── read trade table ─────────────────────────────────────────────────────
    df = pd.read_excel(file_path, sheet_name="F&O", header=header_row)

    # drop fully-null cols and rows
    df = df.dropna(axis=1, how="all")
    df = df.dropna(subset=["Symbol"])

    # drop the ISIN column if present (not useful for analysis)
    df = df.drop(columns=["ISIN"], errors="ignore")

    # ── coerce numeric columns ────────────────────────────────────────────────
    numeric_cols = [
        "Quantity", "Buy Value", "Sell Value",
        "Realized P&L", "Realized P&L Pct.",
        "Previous Closing Price",
        "Open Quantity", "Open Value",
        "Unrealized P&L", "Unrealized P&L Pct.",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # ── enrich with parsed symbol data ───────────────────────────────────────
    parsed = df["Symbol"].apply(_parse_symbol)
    df["underlying"] = parsed.apply(lambda x: x.get("underlying"))
    df["option_type"] = parsed.apply(lambda x: x.get("option_type"))
    df["strike"] = parsed.apply(lambda x: x.get("strike"))
    df["expiry_str"] = parsed.apply(lambda x: x.get("expiry_str"))
    df["expiry_type"] = parsed.apply(lambda x: x.get("expiry_type"))

    # ── per-unit buy price (moneyness proxy) ─────────────────────────────────
    df["buy_price_per_unit"] = df.apply(_infer_buy_price_per_unit, axis=1)

    # ── split into closed vs open ─────────────────────────────────────────────
    closed = df[df["Quantity"] > 0].copy().reset_index(drop=True)
    open_pos = df[df["Open Quantity"] > 0].copy().reset_index(drop=True)

    # ── derive period dates ───────────────────────────────────────────────────
    start_date, end_date = _extract_period_dates(summary.get("period", ""))

    # ── net PnL ───────────────────────────────────────────────────────────────
    net_pnl = (
        summary["realized_pnl"]
        + summary["other_credit_debit"]
        - summary["charges_total"]
    )
    summary["net_pnl"] = round(net_pnl, 2)

    return {
        "meta": {
            "client_id": summary.pop("client_id"),
            "period": summary.pop("period"),
            "start_date": start_date,
            "end_date": end_date,
            "file_path": file_path,
        },
        "summary": summary,
        "trades": closed,
        "open_positions": open_pos,
        "all_rows": df,
    }
