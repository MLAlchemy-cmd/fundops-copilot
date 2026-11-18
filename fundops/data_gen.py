"""Synthetic investment-operations data for one fund on one business day.

Every scenario is generated from a seed, so runs are reproducible, and every injected reconciliation
break carries a ground-truth root cause so the agent can be evaluated. All names are fictional.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

ROOT_CAUSES = ["TIMING", "CORPORATE_ACTION", "PRICE", "FX", "MISSING_BOOKING", "QUANTITY_ERROR"]

SECTORS = ["Technology", "Healthcare", "Financials", "Industrials", "Consumer", "Energy", "Utilities", "Materials"]
_WORDS = ["Apex", "Birch", "Cobalt", "Delta", "Ember", "Fjord", "Granite", "Harbor", "Iris", "Juniper", "Kestrel",
          "Lumen", "Meridian", "Nimbus", "Orchid", "Pioneer", "Quarry", "Ridge", "Summit", "Tidal", "Umbra",
          "Vertex", "Willow", "Xenon", "Yarrow", "Zephyr", "Atlas", "Beacon", "Cinder", "Dune", "Echo", "Falcon"]
_SUFFIX = ["Systems", "Holdings", "Labs", "Energy", "Health", "Capital", "Industries", "Materials", "Networks"]
CCY = {"USD": 1.0, "EUR": 1.085, "GBP": 1.27, "JPY": 0.0066}


@dataclass
class Scenario:
    seed: int
    as_of: str
    fund_id: str
    nav: float
    securities: dict          # security_id -> {name, sector, currency, issuer}
    ibor: dict                # security_id -> {qty, price, fx}   (internal book of record)
    custodian: dict           # security_id -> {qty, price, fx}   (custodian statement)
    trades: list              # pending / settled trades
    corporate_actions: list
    vendor_prices: dict       # security_id -> {vendor_a, vendor_b}
    fx_rates: dict            # ccy -> {wm_reuters_4pm, custodian_rate}
    restricted_list: list
    guidelines: dict
    truth: dict = field(default_factory=dict)   # security_id -> root cause (ground truth, never shown to agents)


def generate(seed: int = 7, n_positions: int = 40, n_breaks: int = 8, hard: bool = True) -> Scenario:
    rng = random.Random(seed)
    as_of = date(2026, 11, 17)
    fund_id = "ESG-EQ-01"
    names = rng.sample([f"{w} {s}" for w in _WORDS for s in _SUFFIX], n_positions + 4)

    securities, ibor = {}, {}
    for i in range(n_positions):
        sid = f"SEC{1000 + i}"
        ccy = rng.choices(list(CCY), weights=[70, 15, 10, 5])[0]
        price_local = round(rng.uniform(15, 480) / (CCY[ccy] if ccy == "JPY" else 1), 2)
        securities[sid] = {"name": names[i], "sector": rng.choice(SECTORS), "currency": ccy, "issuer": names[i]}
        ibor[sid] = {"qty": rng.randrange(2_000, 60_000, 100), "price": price_local, "fx": CCY[ccy]}

    custodian = {k: dict(v) for k, v in ibor.items()}
    vendor_prices = {k: {"vendor_a": v["price"], "vendor_b": v["price"]} for k, v in ibor.items()}
    fx_rates = {c: {"wm_reuters_4pm": r, "custodian_rate": r} for c, r in CCY.items()}
    trades, cas, truth = [], [], {}
    settle_next = (as_of + timedelta(days=1)).isoformat()

    # Decoy pending trades in *another* fund, which must not be used to explain a break.
    for sid in rng.sample(list(ibor), 3):
        trades.append({"trade_id": f"T{rng.randrange(10**5, 10**6)}", "fund_id": "GLB-BAL-02", "security_id": sid,
                       "side": "BUY", "qty": rng.randrange(500, 5_000, 100), "trade_date": as_of.isoformat(),
                       "settle_date": settle_next, "status": "PENDING"})

    usd = [s for s in ibor if securities[s]["currency"] == "USD"]
    non_usd = [s for s in ibor if securities[s]["currency"] != "USD"]
    plan = (ROOT_CAUSES * 3)[:n_breaks]
    rng.shuffle(plan)
    used: set[str] = set()

    def pick(pool):
        s = rng.choice([x for x in pool if x not in used])
        used.add(s)
        return s

    for cause in plan:
        if cause == "TIMING":
            sid = pick(usd)
            q = ibor[sid]["qty"]
            if hard and rng.random() < 0.5:   # two partial executions that only explain the break together
                a = rng.randrange(300, 2_000, 100); b = rng.randrange(300, 2_000, 100)
                legs = [a, b]
            else:
                legs = [rng.randrange(500, 4_000, 100)]
            for leg in legs:
                trades.append({"trade_id": f"T{rng.randrange(10**5, 10**6)}", "fund_id": fund_id, "security_id": sid,
                               "side": "BUY", "qty": leg, "trade_date": as_of.isoformat(),
                               "settle_date": settle_next, "status": "PENDING"})
            custodian[sid]["qty"] = q - sum(legs)    # IBOR books on trade date, custodian on settlement
        elif cause == "CORPORATE_ACTION":
            sid = pick(usd)
            ratio = rng.choice([2, 3, 1.5])
            cas.append({"ca_id": f"CA{rng.randrange(1000, 9999)}", "security_id": sid, "type": "STOCK_SPLIT",
                        "ratio": ratio, "ex_date": as_of.isoformat(), "status": "CONFIRMED"})
            custodian[sid]["qty"] = int(ibor[sid]["qty"] * ratio)
            custodian[sid]["price"] = round(ibor[sid]["price"] / ratio, 4)
        elif cause == "PRICE":
            sid = pick(usd)
            bump = 1 + rng.choice([-1, 1]) * rng.uniform(0.006, 0.03)
            custodian[sid]["price"] = round(ibor[sid]["price"] * bump, 2)
            vendor_prices[sid]["vendor_b"] = custodian[sid]["price"]
        elif cause == "FX":
            sid = pick(non_usd)
            ccy = securities[sid]["currency"]
            stale = round(CCY[ccy] * (1 + rng.choice([-1, 1]) * rng.uniform(0.006, 0.015)), 6)
            custodian[sid]["fx"] = stale
            fx_rates[ccy]["custodian_rate"] = stale
        elif cause == "MISSING_BOOKING":
            k = sum(1 for v in truth.values() if v == "MISSING_BOOKING")
            sid = f"SEC{1900 + k}"
            used.add(sid)
            securities[sid] = {"name": names[n_positions + k], "sector": rng.choice(SECTORS),
                               "currency": "USD", "issuer": names[n_positions + k]}
            custodian[sid] = {"qty": rng.randrange(1_000, 9_000, 100), "price": round(rng.uniform(20, 200), 2), "fx": 1.0}
            vendor_prices[sid] = {"vendor_a": custodian[sid]["price"], "vendor_b": custodian[sid]["price"]}
        elif cause == "QUANTITY_ERROR":
            sid = pick(usd)
            custodian[sid]["qty"] = ibor[sid]["qty"] + rng.choice([-1, 1]) * rng.choice([10, 90, 900, 1_000])
            if hard and rng.random() < 0.5:   # a decoy trade in the same security but a different fund
                trades.append({"trade_id": f"T{rng.randrange(10**5, 10**6)}", "fund_id": "GLB-BAL-02",
                               "security_id": sid, "side": "BUY", "qty": abs(custodian[sid]["qty"] - ibor[sid]["qty"]),
                               "trade_date": as_of.isoformat(), "settle_date": settle_next, "status": "PENDING"})
        truth[sid] = cause

    # Guideline breaches the compliance check must catch.
    big = rng.choice([s for s in usd if s not in used])
    ibor[big]["qty"] = int(ibor[big]["qty"] * 4)
    custodian[big]["qty"] = ibor[big]["qty"]
    restricted_sid = rng.choice([s for s in ibor if s not in used and s != big])
    restricted = [{"issuer": securities[restricted_sid]["issuer"], "reason": "Thermal coal revenue > 10% (ESG exclusion)"},
                  {"issuer": "Northwind Tobacco", "reason": "Tobacco producer (ESG exclusion)"}]

    nav = sum(p["qty"] * p["price"] * p["fx"] for p in ibor.values()) / 0.98   # 2% cash
    return Scenario(seed=seed, as_of=as_of.isoformat(), fund_id=fund_id, nav=round(nav, 2), securities=securities,
                    ibor=ibor, custodian=custodian, trades=trades, corporate_actions=cas, vendor_prices=vendor_prices,
                    fx_rates=fx_rates, restricted_list=restricted,
                    guidelines={"max_issuer_weight": 0.05, "max_sector_weight": 0.30, "min_cash_weight": 0.01},
                    truth=truth)
