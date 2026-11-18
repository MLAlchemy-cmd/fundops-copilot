"""Offline tests: tools, policy, audit chain, the LangGraph interrupt, and the Claude tool loop (fake client).

  python -m tests.test_pipeline
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langgraph.types import Command                          # noqa: E402

from fundops.agents import ClaudeTriageAgent, RulesTriageAgent  # noqa: E402
from fundops.data_gen import generate                         # noqa: E402
from fundops.governance import AuditLog, CostLedger, requires_human, verify_chain  # noqa: E402
from fundops.graph import build_graph                         # noqa: E402
from fundops.tools import check_guidelines, reconcile_positions  # noqa: E402


def test_reconcile_finds_every_injected_break():
    for seed in range(1, 31):
        s = generate(seed)
        found = {b["security_id"] for b in reconcile_positions(s)}
        assert set(s.truth) <= found, (seed, set(s.truth) - found)


def test_compliance_catches_seeded_breaches():
    rules = {v["rule"] for v in check_guidelines(generate(3))}
    assert {"MAX_ISSUER_WEIGHT", "RESTRICTED_LIST"} <= rules


def test_policy_never_auto_approves_unexplained_breaks():
    need, _ = requires_human({"root_cause": "QUANTITY_ERROR", "confidence": "high"}, {"mv_impact_usd": 10})
    assert need
    need, _ = requires_human({"root_cause": "TIMING", "confidence": "high"}, {"mv_impact_usd": 10_000_000})
    assert need


def test_audit_chain_detects_tampering():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.jsonl"
        log = AuditLog(p)
        log.write("x", "system", {"n": 1}); log.write("y", "human", {"n": 2})
        assert verify_chain(p)
        p.write_text(p.read_text().replace('"n": 2', '"n": 3'))
        assert not verify_chain(p)


def test_graph_pauses_for_human_and_resumes():
    with tempfile.TemporaryDirectory() as d:
        s, audit = generate(7), AuditLog(Path(d) / "a.jsonl")
        g = build_graph(s, RulesTriageAgent(), audit)
        cfg = {"configurable": {"thread_id": "t"}}
        st = g.invoke({}, cfg)
        pending = st["__interrupt__"][0].value["pending"]
        assert pending and "report" not in st
        st = g.invoke(Command(resume={p["id"]: "reject" for p in pending}), cfg)
        assert "reject" in st["report"]


def _usage():
    return NS(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def test_claude_tool_loop_with_fake_client():
    s = generate(7)
    breaks = reconcile_positions(s)
    sid = breaks[0]["security_id"]
    script = [  # turn 1: investigate; turn 2: record findings for all breaks; turn 3: done
        [NS(type="tool_use", id="t1", name="get_pending_trades", input={"security_id": sid})],
        [NS(type="tool_use", id=f"f{i}", name="record_finding",
            input={"security_id": b["security_id"], "root_cause": "PRICE", "confidence": "low",
                   "evidence": "test", "proposed_action": "test"}) for i, b in enumerate(breaks)],
        [NS(type="text", text="done")],
    ]
    calls = []

    def fake_create(messages):
        calls.append(len(messages))
        content = script[len(calls) - 1]
        stop = "tool_use" if content[0].type == "tool_use" else "end_turn"
        return NS(content=content, stop_reason=stop, usage=_usage(), model="claude-opus-5-5")

    with tempfile.TemporaryDirectory() as d:
        ledger = CostLedger()
        agent = ClaudeTriageAgent.__new__(ClaudeTriageAgent)
        agent.ledger, agent.audit, agent.max_turns, agent.model = ledger, AuditLog(Path(d) / "a.jsonl"), 10, "claude-opus-5-5"
        agent._create = fake_create
        out = agent.triage(s, breaks)
    assert set(out) == {b["security_id"] for b in breaks}
    assert ledger.calls == 2 and ledger.usd > 0          # stops once every break has a finding
    assert calls == [1, 3]                               # assistant + tool_result appended each turn


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fns:
        fn(); print("PASS", fn.__name__)
    print(f"{len(fns)} passed")
