"""Break-triage agents.

ClaudeTriageAgent: Claude investigates each break with read-only tools and records a structured finding.
RulesTriageAgent:  a naive deterministic baseline, used offline and as the comparison point in evals.
"""
from __future__ import annotations

import json
import os

import anthropic

from .data_gen import Scenario
from .governance import AuditLog, CostLedger
from .tools import TOOL_SCHEMAS, run_tool

MODEL = os.getenv("FUNDOPS_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("FUNDOPS_EFFORT", "medium")

SYSTEM = """You are a reconciliation analyst agent in an asset manager's investment-operations team.
You triage breaks between the fund's internal book of record (IBOR) and the custodian statement.

For EVERY break you are given: investigate with the tools, then call record_finding exactly once.
Root causes:
- TIMING: IBOR books on trade date, custodian on settlement date; this fund's pending trades explain the quantity gap exactly (several partial fills may sum to it).
- CORPORATE_ACTION: a confirmed split/stock dividend explains the custodian quantity and price (custodian qty = IBOR qty x ratio).
- PRICE: quantities agree; the two pricing vendors disagree.
- FX: quantities and local prices agree; the custodian applied a different FX rate than the official WM/Reuters 4pm fix.
- MISSING_BOOKING: the custodian holds a position the IBOR does not have at all.
- QUANTITY_ERROR: a quantity gap that no pending trade of THIS fund or corporate action explains. Trades booked to other funds never explain a break.
Rules: cite exact numbers in evidence. Use confidence "high" only when the numbers reconcile exactly.
You cannot change any records; proposed_action is a recommendation for a human or a downstream system."""


def make_client():
    """Claude API by default; Amazon Bedrock when FUNDOPS_PROVIDER=bedrock (uses AWS credentials)."""
    if os.getenv("FUNDOPS_PROVIDER") == "bedrock":
        return anthropic.AnthropicBedrockMantle(aws_region=os.getenv("AWS_REGION", "us-east-1")), "anthropic." + MODEL
    return anthropic.Anthropic(), MODEL


def has_credentials() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("FUNDOPS_PROVIDER") == "bedrock")


class ClaudeTriageAgent:
    name = "claude"

    def __init__(self, ledger: CostLedger, audit: AuditLog, max_turns: int = 30):
        self.client, self.model = make_client()
        self.bedrock = os.getenv("FUNDOPS_PROVIDER") == "bedrock"
        self.ledger, self.audit, self.max_turns = ledger, audit, max_turns

    def _create(self, messages):
        kw = dict(model=self.model, max_tokens=16000, system=SYSTEM, tools=TOOL_SCHEMAS, messages=messages,
                  thinking={"type": "adaptive"}, output_config={"effort": EFFORT},
                  cache_control={"type": "ephemeral"})   # caches the growing tool loop prefix between turns
        if self.bedrock:   # server-side fallbacks are not offered on Bedrock
            return self.client.messages.create(**kw)
        # On a safety decline the API re-runs the request on a fallback model inside the same call.
        return self.client.beta.messages.create(**kw, betas=["server-side-fallback-2026-07-01"], fallbacks="default")

    def triage(self, s: Scenario, breaks: list[dict]) -> dict[str, dict]:
        findings: dict[str, dict] = {}
        prompt = (f"Fund {s.fund_id}, as-of {s.as_of}. Reconciliation produced {len(breaks)} breaks:\n"
                  + json.dumps(breaks, indent=1))
        messages = [{"role": "user", "content": prompt}]
        for turn in range(self.max_turns):
            try:
                resp = self._create(messages)
            except anthropic.RateLimitError as e:
                self.audit.write("llm_error", "claude", {"error": "rate_limited", "request_id": getattr(e, "request_id", None)})
                raise
            except anthropic.APIStatusError as e:
                self.audit.write("llm_error", "claude", {"status": e.status_code, "message": e.message})
                raise
            cost = self.ledger.add(self.model, resp.usage)
            self.audit.write("llm_call", "claude", {"turn": turn, "model": resp.model, "stop_reason": resp.stop_reason,
                                                    "input_tokens": resp.usage.input_tokens,
                                                    "output_tokens": resp.usage.output_tokens, "cost_usd": round(cost, 5)})
            if resp.stop_reason == "refusal":
                self.audit.write("llm_refusal", "claude", {"details": str(getattr(resp, "stop_details", None))})
                break
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                break   # end_turn (or max_tokens): the agent is done
            results = []
            for tu in tool_uses:
                args = tu.input if isinstance(tu.input, dict) else json.loads(tu.input)
                if tu.name == "record_finding":
                    findings[args["security_id"]] = args
                    self.audit.write("finding", "claude", args)
                    out, err = {"recorded": True}, False
                else:
                    try:
                        out, err = run_tool(s, tu.name, args), False
                    except Exception as e:   # surface tool failures to the model instead of crashing
                        out, err = {"error": str(e)}, True
                    self.audit.write("tool_call", "claude", {"tool": tu.name, "args": args, "result": out})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out, default=str),
                                "is_error": err})
            missing = [b["security_id"] for b in breaks if b["security_id"] not in findings]
            if not missing and all(t.name == "record_finding" for t in tool_uses):
                messages.append({"role": "user", "content": results})
                break
            messages.append({"role": "user", "content": results})
        for b in breaks:   # anything the agent did not conclude on goes to a human
            findings.setdefault(b["security_id"], {"security_id": b["security_id"], "root_cause": "QUANTITY_ERROR",
                                                   "confidence": "low", "evidence": "agent recorded no finding",
                                                   "proposed_action": "manual investigation"})
        return findings


class RulesTriageAgent:
    """Naive baseline: the checks an analyst's spreadsheet would do, one rule per cause."""
    name = "rules"

    def __init__(self, audit: AuditLog | None = None):
        self.audit = audit

    def triage(self, s: Scenario, breaks: list[dict]) -> dict[str, dict]:
        out = {}
        for b in breaks:
            sid = b["security_id"]
            if b["break_type"] == "MISSING_IN_IBOR":
                cause, ev = "MISSING_BOOKING", "position only at custodian"
            elif b.get("qty_diff"):
                trades = run_tool(s, "get_pending_trades", {"security_id": sid})
                cas = run_tool(s, "get_corporate_actions", {"security_id": sid})
                if any(abs(t["qty"]) == abs(b["qty_diff"]) for t in trades):   # single-trade match, any fund
                    cause, ev = "TIMING", "a pending trade matches the quantity gap"
                elif cas:
                    cause, ev = "CORPORATE_ACTION", f"corporate action {cas[0]['ca_id']}"
                else:
                    cause, ev = "QUANTITY_ERROR", "no matching trade or corporate action"
            elif b["ibor_fx"] != b["custodian_fx"]:
                cause, ev = "FX", "fx rates differ"
            else:
                cause, ev = "PRICE", "prices differ"
            out[sid] = {"security_id": sid, "root_cause": cause, "confidence": "high", "evidence": ev,
                        "proposed_action": "see playbook"}
            if self.audit:
                self.audit.write("finding", "rules", out[sid])
        return out
