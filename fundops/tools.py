"""Deterministic, read-only tools. Agents investigate through these and can never change the books.

The same functions back the Claude tool definitions (TOOL_SCHEMAS) and the MCP server (mcp_server.py).
"""
from __future__ import annotations

from .data_gen import ROOT_CAUSES, Scenario

QTY_TOL = 0          # shares
MV_TOL = 0.005       # 0.5% market-value tolerance


def _mv(p: dict) -> float:
    return p["qty"] * p["price"] * p["fx"]


def reconcile_positions(s: Scenario) -> list[dict]:
    """Compare the internal book of record (IBOR) with the custodian, position by position."""
    out = []
    for sid in sorted(set(s.ibor) | set(s.custodian)):
        a, b = s.ibor.get(sid), s.custodian.get(sid)
        if a is None or b is None:
            out.append({"security_id": sid, "name": s.securities[sid]["name"], "break_type": "MISSING_IN_IBOR" if a is None else "MISSING_AT_CUSTODIAN",
                        "ibor_qty": a and a["qty"], "custodian_qty": b and b["qty"],
                        "mv_impact_usd": round(_mv(b or a), 2)})
            continue
        qty_diff = b["qty"] - a["qty"]
        mv_a, mv_b = _mv(a), _mv(b)
        if abs(qty_diff) > QTY_TOL or abs(mv_b - mv_a) > MV_TOL * mv_a:
            out.append({"security_id": sid, "name": s.securities[sid]["name"], "currency": s.securities[sid]["currency"],
                        "break_type": "QUANTITY" if qty_diff else "MARKET_VALUE",
                        "ibor_qty": a["qty"], "custodian_qty": b["qty"], "qty_diff": qty_diff,
                        "ibor_price": a["price"], "custodian_price": b["price"],
                        "ibor_fx": a["fx"], "custodian_fx": b["fx"],
                        "mv_impact_usd": round(mv_b - mv_a, 2)})
    return out


def get_pending_trades(s: Scenario, security_id: str) -> list[dict]:
    """All unsettled trades in this security, across every fund (callers must check fund_id)."""
    return [t for t in s.trades if t["security_id"] == security_id and t["status"] == "PENDING"]


def get_corporate_actions(s: Scenario, security_id: str) -> list[dict]:
    return [c for c in s.corporate_actions if c["security_id"] == security_id]


def get_prices(s: Scenario, security_id: str) -> dict:
    return s.vendor_prices.get(security_id, {})


def get_fx_rate(s: Scenario, currency: str) -> dict:
    return s.fx_rates.get(currency, {})


def check_guidelines(s: Scenario) -> list[dict]:
    """Investment-guideline checks on the IBOR: issuer and sector concentration, restricted list, cash."""
    g, nav, out = s.guidelines, s.nav, []
    sector: dict[str, float] = {}
    restricted = {r["issuer"]: r["reason"] for r in s.restricted_list}
    invested = 0.0
    for sid, p in s.ibor.items():
        mv = _mv(p); invested += mv
        sec = s.securities[sid]
        w = mv / nav
        sector[sec["sector"]] = sector.get(sec["sector"], 0) + w
        if w > g["max_issuer_weight"]:
            out.append({"rule": "MAX_ISSUER_WEIGHT", "security_id": sid, "name": sec["name"],
                        "value": round(w, 4), "limit": g["max_issuer_weight"], "severity": "HARD"})
        if sec["issuer"] in restricted:
            out.append({"rule": "RESTRICTED_LIST", "security_id": sid, "name": sec["name"],
                        "reason": restricted[sec["issuer"]], "severity": "HARD"})
    for name, w in sector.items():
        if w > g["max_sector_weight"]:
            out.append({"rule": "MAX_SECTOR_WEIGHT", "sector": name, "value": round(w, 4),
                        "limit": g["max_sector_weight"], "severity": "HARD"})
    cash_w = 1 - invested / nav
    if cash_w < g["min_cash_weight"]:
        out.append({"rule": "MIN_CASH", "value": round(cash_w, 4), "limit": g["min_cash_weight"], "severity": "SOFT"})
    return out


# ---------------------------------------------------------------- Claude tool definitions (strict schemas)
def _schema(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_SID = {"security_id": {"type": "string", "description": "Security identifier, e.g. SEC1004"}}
TOOL_SCHEMAS = [
    {"name": "get_pending_trades", "strict": True, "input_schema": _schema(_SID),
     "description": "Unsettled trades in a security across ALL funds. Each row has fund_id; only trades for the fund being reconciled can explain its break."},
    {"name": "get_corporate_actions", "strict": True, "input_schema": _schema(_SID),
     "description": "Corporate actions (splits, etc.) for a security, with ratio and ex_date."},
    {"name": "get_prices", "strict": True, "input_schema": _schema(_SID),
     "description": "Closing price from two pricing vendors (vendor_a feeds the IBOR, vendor_b feeds the custodian)."},
    {"name": "get_fx_rate", "strict": True,
     "input_schema": _schema({"currency": {"type": "string", "description": "ISO currency code, e.g. EUR"}}),
     "description": "FX rate to USD: the fund's official WM/Reuters 4pm fix and the rate the custodian applied."},
    {"name": "record_finding", "strict": True,
     "description": "Record your conclusion for ONE break. Call exactly once per break after investigating it.",
     "input_schema": _schema({
         "security_id": {"type": "string"},
         "root_cause": {"type": "string", "enum": ROOT_CAUSES},
         "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
         "evidence": {"type": "string", "description": "Specific numbers from the tools that support the conclusion."},
         "proposed_action": {"type": "string", "description": "What operations should do. You cannot execute it."},
     })},
]


def run_tool(s: Scenario, name: str, args: dict):
    if name == "get_pending_trades":
        return get_pending_trades(s, args["security_id"])
    if name == "get_corporate_actions":
        return get_corporate_actions(s, args["security_id"])
    if name == "get_prices":
        return get_prices(s, args["security_id"])
    if name == "get_fx_rate":
        return get_fx_rate(s, args["currency"])
    raise KeyError(name)
