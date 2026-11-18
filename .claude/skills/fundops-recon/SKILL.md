---
name: fundops-recon
description: Run and explain the daily fund reconciliation and guideline-compliance check with the fundops MCP tools. Use when asked about position breaks, custodian vs IBOR differences, or guideline breaches for a fund.
---

# Daily reconciliation runbook

1. Call `reconcile_positions` and list the breaks with their market-value impact.
2. For each break, investigate before concluding:
   - quantity gap → `get_pending_trades` (only trades whose `fund_id` is this fund count; partial fills may sum to the gap) and `get_corporate_actions`
   - market-value-only gap → `get_prices` (vendor disagreement) or `get_fx_rate` (custodian rate vs the WM/Reuters 4pm fix)
3. Call `check_guidelines` and report every HARD breach.
4. Present a table: security, root cause, evidence with numbers, proposed action.
   - State clearly that these are recommendations: you cannot change records.
   - Unexplained breaks, low-confidence findings, items over $250K, and all guideline breaches need human sign-off.
