# FundOps Copilot

**A governed, human-in-the-loop agentic workflow for investment-operations reconciliation and guideline compliance.**

Every morning an asset manager's operations team reconciles each fund's internal book of record (IBOR) against the custodian, explains every break, and checks the portfolio against its investment guidelines. FundOps Copilot automates the investigation and leaves the decisions to people.

```
reconcile ──▶ triage agent (Claude + read-only tools) ──▶ compliance checks ──▶ policy gate ──▶ human review (interrupt) ──▶ report
                         │                                                         │                    │
                         └────────────── every call, tool result, decision ────────┴──── hash-chained audit log + token/cost ledger
```

## What it does

| Stage | How |
|---|---|
| **Reconcile** | Position-by-position IBOR vs custodian: quantity and market-value breaks (0.5% tolerance), positions missing on either side. |
| **Triage agent** | Claude (Opus 5.5, adaptive thinking) investigates each break with strict-schema tools (`get_pending_trades`, `get_corporate_actions`, `get_prices`, `get_fx_rate`). It then records a structured finding: root cause, confidence, evidence, and proposed action. It is **read-only** and cannot change any record. |
| **Compliance** | Deterministic checks for issuer concentration (5%), sector concentration (30%), an ESG restricted list, and minimum cash. |
| **Policy gate** | A deterministic rule, not the LLM, decides what needs a person. Only explainable causes (timing, corporate action) with high confidence and under $250K can be auto-approved. Every guideline breach is escalated. |
| **Human review** | LangGraph `interrupt()` pauses the run with its state checkpointed. It resumes only when an approver returns decisions. |
| **Audit & cost** | An append-only JSONL audit trail in which each record is hash-chained to the previous one, so tampering is detectable. It is paired with a per-run ledger of LLM calls, tokens (including prompt-cache reads) and USD cost. |
| **MCP server** | The same tools are exposed over the Model Context Protocol, so analysts can query them from Claude Code or Claude Desktop. |
| **Claude Code skill** | `.claude/skills/fundops-recon/SKILL.md` packages the daily runbook as a reusable skill. |

## Root-cause taxonomy

`TIMING` (trade-date vs settlement-date booking), `CORPORATE_ACTION` (splits), `PRICE` (pricing-vendor disagreement), `FX` (custodian rate vs the WM/Reuters 4pm fix), `MISSING_BOOKING` (custodian-only position), `QUANTITY_ERROR` (unexplained).

The synthetic data is seeded and reproducible, and it includes the cases that trip up naive rules:
- partial fills that only explain a break when summed
- pending trades booked to *another* fund that coincidentally match the gap

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (bin/ on macOS/Linux)
python -m fundops.cli --seed 7 --agent rules --approve all   # offline, no API key needed
export ANTHROPIC_API_KEY=...                                  # or: ant auth login
python -m fundops.cli --seed 7                                # Claude agent, interactive approvals
FUNDOPS_PROVIDER=bedrock AWS_REGION=us-east-1 python -m fundops.cli   # same agent on Amazon Bedrock
python -m tests.test_pipeline                                 # offline tests
claude mcp add fundops -- python -m fundops.mcp_server        # use the tools from Claude Code
```

## Evaluation

`python -m evals.run_evals --seeds 30 --agent rules` (offline) or `--agent claude` (live, prints measured cost).

| Metric | Rules baseline (30 scenarios, 240 breaks) | Claude agent |
|---|---|---|
| Root-cause accuracy | 79.6% | _run `--agent claude`_ |
| TIMING / QUANTITY_ERROR accuracy | 43.3% / 50.0% | |
| False auto-approvals (should be 0) | 14 | |
| Compliance-breach recall | 100% | |
| Cost per scenario | $0 | |

The baseline fails exactly where judgment is needed: it matches a single trade from any fund, so it misses split fills and accepts decoy trades from other funds. Those mistakes turn into unsafe auto-approvals.

## Design choices

- **The LLM proposes; deterministic policy disposes.** Approval routing, compliance limits and the audit trail never depend on model output.
- **Strict tool schemas** (`strict: true`, enum root causes) make findings machine-checkable.
- **Server-side refusal fallback** on the Claude API keeps a run from stalling on a safety decline. Prompt caching on the tool loop keeps per-turn cost down.
- **Provider-portable:** Claude API by default, Amazon Bedrock with one environment variable.

_All fund, security and issuer names are synthetic._
