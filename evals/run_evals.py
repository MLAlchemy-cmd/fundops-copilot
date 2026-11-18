"""Evaluate break triage against ground truth across many seeded scenarios.

  python -m evals.run_evals --seeds 20 --agent rules     # free, offline
  python -m evals.run_evals --seeds 5  --agent claude    # live; prints measured cost

Metrics: root-cause accuracy (per cause), escalation recall (breaks that truly need a human and were
routed to one), false auto-approvals (should be 0), compliance-breach recall, and cost per run.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fundops.agents import ClaudeTriageAgent, RulesTriageAgent       # noqa: E402
from fundops.data_gen import generate                                 # noqa: E402
from fundops.governance import AUTO_APPROVE_CAUSES, AuditLog, CostLedger, requires_human  # noqa: E402
from fundops.tools import check_guidelines, reconcile_positions       # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--agent", choices=["rules", "claude"], default="rules")
    a = ap.parse_args(argv)
    out_dir = Path("runs") / f"eval-{a.agent}"
    ledger = CostLedger()
    audit = AuditLog(out_dir / "audit.jsonl")
    agent = ClaudeTriageAgent(ledger, audit) if a.agent == "claude" else RulesTriageAgent()

    correct, total, per = 0, 0, Counter()
    per_total = Counter()
    need_human = routed = false_auto = 0
    cmp_found = cmp_expected = 0
    rows = []
    for seed in range(1, a.seeds + 1):
        s = generate(seed)
        breaks = reconcile_positions(s)
        found = agent.triage(s, breaks)
        by_id = {b["security_id"]: b for b in breaks}
        for sid, truth in s.truth.items():
            pred = found.get(sid, {}).get("root_cause")
            total += 1; per_total[truth] += 1
            ok = pred == truth
            correct += ok; per[truth] += ok
            human, _ = requires_human(found[sid], by_id[sid]) if sid in found else (True, "")
            truly_needs = truth not in AUTO_APPROVE_CAUSES
            need_human += truly_needs; routed += truly_needs and human
            false_auto += truly_needs and not human
            rows.append({"seed": seed, "security_id": sid, "truth": truth, "pred": pred, "ok": ok})
        rules = {v["rule"] for v in check_guidelines(s)}
        cmp_expected += 2; cmp_found += len(rules & {"MAX_ISSUER_WEIGHT", "RESTRICTED_LIST"})

    summary = {
        "agent": a.agent, "scenarios": a.seeds, "breaks": total,
        "root_cause_accuracy": round(correct / total, 3),
        "per_cause_accuracy": {k: round(per[k] / per_total[k], 3) for k in sorted(per_total)},
        "escalation_recall": round(routed / need_human, 3) if need_human else None,
        "false_auto_approvals": false_auto,
        "compliance_recall": round(cmp_found / cmp_expected, 3),
        "llm_calls": ledger.calls, "input_tokens": ledger.input_tokens, "output_tokens": ledger.output_tokens,
        "cost_usd_total": ledger.usd, "cost_usd_per_scenario": round(ledger.usd / a.seeds, 4),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
