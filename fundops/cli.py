"""Run one day's reconciliation end to end.

  python -m fundops.cli --seed 7                 # Claude agent if credentials are set, else rules baseline
  python -m fundops.cli --seed 7 --agent rules   # force the offline baseline
  python -m fundops.cli --seed 7 --approve all   # non-interactive approver (demo / CI)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from langgraph.types import Command

from .agents import ClaudeTriageAgent, RulesTriageAgent, has_credentials
from .data_gen import generate
from .governance import AuditLog, CostLedger, verify_chain
from .graph import build_graph


def ask_human(pending: list[dict], mode: str) -> dict[str, str]:
    out = {}
    for item in pending:
        if mode in ("all", "none"):
            out[item["id"]] = "approve" if mode == "all" else "reject"
            continue
        print(f"\n[{item['id']}] {item['why']}")
        print(json.dumps(item.get("finding") or item.get("violation"), indent=1))
        ans = input("approve / reject? [a/r] ").strip().lower()
        out[item["id"]] = "approve" if ans.startswith("a") else "reject"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--agent", choices=["auto", "claude", "rules"], default="auto")
    ap.add_argument("--approve", choices=["ask", "all", "none"], default="ask")
    a = ap.parse_args(argv)

    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-seed{a.seed}"
    run_dir = Path("runs") / run_id
    audit, ledger = AuditLog(run_dir / "audit.jsonl"), CostLedger()
    use_claude = a.agent == "claude" or (a.agent == "auto" and has_credentials())
    agent = ClaudeTriageAgent(ledger, audit) if use_claude else RulesTriageAgent(audit)
    s = generate(a.seed)
    audit.write("run_start", "system", {"seed": a.seed, "agent": agent.name, "fund": s.fund_id, "as_of": s.as_of})

    graph = build_graph(s, agent, audit)
    cfg = {"configurable": {"thread_id": run_id}}
    state = graph.invoke({}, cfg)
    while "__interrupt__" in state:   # paused at the human-review gate
        pending = state["__interrupt__"][0].value["pending"]
        print(f"\n{len(pending)} item(s) need human approval.")
        state = graph.invoke(Command(resume=ask_human(pending, a.approve)), cfg)

    (run_dir / "report.md").write_text(state["report"], encoding="utf-8")
    audit.write("run_end", "system", {"llm_calls": ledger.calls, "input_tokens": ledger.input_tokens,
                                      "output_tokens": ledger.output_tokens, "cost_usd": ledger.usd})
    print(state["report"])
    print(f"\nagent={agent.name} | LLM calls={ledger.calls} | tokens in/out={ledger.input_tokens}/{ledger.output_tokens} "
          f"| cost=${ledger.usd} | audit chain valid={verify_chain(run_dir / 'audit.jsonl')} | {run_dir}")


if __name__ == "__main__":
    main()
