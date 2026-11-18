"""LangGraph workflow: reconcile -> triage (agent) -> compliance -> policy gate -> human review -> report.

The human-review node uses LangGraph's interrupt(): the run pauses, its state is checkpointed, and it
resumes only when an approver returns decisions. Nothing is posted to the books without that step.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .data_gen import Scenario
from .governance import AuditLog, requires_human
from .tools import check_guidelines, reconcile_positions


class RunState(TypedDict, total=False):
    breaks: list[dict]
    findings: dict[str, dict]
    compliance: list[dict]
    queue: list[dict]          # items awaiting a human
    decisions: dict[str, str]  # item id -> approve | reject
    report: str


def build_graph(scenario: Scenario, agent: Any, audit: AuditLog):
    def reconcile(state: RunState) -> RunState:
        breaks = reconcile_positions(scenario)
        audit.write("reconcile", "system", {"breaks": len(breaks)})
        return {"breaks": breaks}

    def triage(state: RunState) -> RunState:
        return {"findings": agent.triage(scenario, state["breaks"])}

    def compliance(state: RunState) -> RunState:
        hits = check_guidelines(scenario)
        audit.write("compliance", "system", {"violations": hits})
        return {"compliance": hits}

    def policy_gate(state: RunState) -> RunState:
        by_id = {b["security_id"]: b for b in state["breaks"]}
        queue, decisions = [], {}
        for sid, f in state["findings"].items():
            need, why = requires_human(f, by_id[sid])
            item = {"id": f"BRK-{sid}", "kind": "break", "finding": f, "mv_impact_usd": by_id[sid]["mv_impact_usd"], "why": why}
            if need:
                queue.append(item)
            else:
                decisions[item["id"]] = "auto-approve"
            audit.write("policy", "rule", {"id": item["id"], "needs_human": need, "why": why})
        for v in state["compliance"]:   # every hard guideline breach is escalated, never auto-cleared
            queue.append({"id": f"CMP-{v['rule']}-{v.get('security_id') or v.get('sector', '')}", "kind": "compliance",
                          "violation": v, "why": f"{v['severity']} guideline breach"})
        return {"queue": queue, "decisions": decisions}

    def human_review(state: RunState) -> RunState:
        if not state["queue"]:
            return {}
        answers = interrupt({"pending": state["queue"]})   # pauses the run until resumed
        audit.write("human_review", "human", {"decisions": answers})
        return {"decisions": {**state["decisions"], **answers}}

    def report(state: RunState) -> RunState:
        f, d = state["findings"], state["decisions"]
        lines = [f"# Reconciliation & compliance report: {scenario.fund_id}, {scenario.as_of}", "",
                 f"Breaks: {len(state['breaks'])} | escalated to human: {sum(1 for q in state['queue'] if q['kind']=='break')} | "
                 f"compliance breaches: {len(state['compliance'])}", "",
                 "| Security | Root cause | Conf. | MV impact | Decision | Evidence |", "|---|---|---|---|---|---|"]
        for b in state["breaks"]:
            x = f[b["security_id"]]
            lines.append(f"| {b['security_id']} {b['name']} | {x['root_cause']} | {x['confidence']} | "
                         f"${b['mv_impact_usd']:,.0f} | {d.get('BRK-' + b['security_id'], 'pending')} | {x['evidence'][:120]} |")
        lines += ["", "## Guideline breaches", ""]
        for v in state["compliance"]:
            key = f"CMP-{v['rule']}-{v.get('security_id') or v.get('sector', '')}"
            lines.append(f"- **{v['rule']}** {v.get('name', v.get('sector', ''))}: "
                         f"{v.get('value', v.get('reason'))} (limit {v.get('limit', 'n/a')}) - decision: {d.get(key, 'pending')}")
        text = "\n".join(lines)
        audit.write("report", "system", {"chars": len(text)})
        return {"report": text}

    g = StateGraph(RunState)
    for name, fn in [("reconcile", reconcile), ("triage", triage), ("compliance", compliance),
                     ("policy_gate", policy_gate), ("human_review", human_review), ("report", report)]:
        g.add_node(name, fn)
    g.add_edge(START, "reconcile"); g.add_edge("reconcile", "triage"); g.add_edge("triage", "compliance")
    g.add_edge("compliance", "policy_gate"); g.add_edge("policy_gate", "human_review")
    g.add_edge("human_review", "report"); g.add_edge("report", END)
    return g.compile(checkpointer=InMemorySaver())
