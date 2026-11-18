"""Governance layer: approval policy, append-only audit trail, and token/cost ledger."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

# USD per million tokens: (input, output, cache write, cache read). Claude Opus 5.5 list price, Claude API.
PRICING = {"claude-opus-5-5": (4.00, 20.00, 5.00, 0.20), "anthropic.claude-opus-5-5": (4.00, 20.00, 5.00, 0.20)}

AUTO_APPROVE_CAUSES = {"TIMING", "CORPORATE_ACTION"}   # explainable, self-correcting breaks
AUTO_APPROVE_MAX_USD = 250_000                           # above this, a human signs off regardless


def requires_human(finding: dict, brk: dict) -> tuple[bool, str]:
    """Human-in-the-loop policy. Returns (needs_approval, reason). Deterministic: the LLM never decides this."""
    impact = abs(brk.get("mv_impact_usd") or 0)
    if finding["root_cause"] not in AUTO_APPROVE_CAUSES:
        return True, f"root cause {finding['root_cause']} is not on the auto-approve list"
    if finding["confidence"] != "high":
        return True, f"agent confidence is {finding['confidence']}"
    if impact > AUTO_APPROVE_MAX_USD:
        return True, f"market-value impact ${impact:,.0f} exceeds ${AUTO_APPROVE_MAX_USD:,} limit"
    return False, "auto-approved by policy (explainable cause, high confidence, within limit)"


@dataclass
class CostLedger:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cache_read_tokens: int = 0
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, usage) -> float:
        i, o = usage.input_tokens or 0, usage.output_tokens or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.input_tokens += i + cw + cr; self.output_tokens += o; self.calls += 1
        self.cache_read_tokens += cr
        pin, pout, pcw, pcr = PRICING.get(model, (0.0, 0.0, 0.0, 0.0))
        cost = (i * pin + o * pout + cw * pcw + cr * pcr) / 1e6
        self.by_model[model] = self.by_model.get(model, 0.0) + cost
        return cost

    @property
    def usd(self) -> float:
        return round(sum(self.by_model.values()), 4)


class AuditLog:
    """Append-only JSONL. Each record is chained to the previous one by hash, so edits are detectable."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._prev = "0" * 16

    def write(self, step: str, actor: str, payload: dict) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step, "actor": actor, "payload": payload, "prev": self._prev}
        body = json.dumps(rec, sort_keys=True, default=str)
        self._prev = hashlib.sha256(body.encode()).hexdigest()[:16]
        rec["hash"] = self._prev
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")


def verify_chain(path: Path) -> bool:
    prev = "0" * 16
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        h = rec.pop("hash")
        if rec["prev"] != prev or hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()[:16] != h:
            return False
        prev = h
    return True
