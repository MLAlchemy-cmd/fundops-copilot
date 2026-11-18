"""MCP server exposing the read-only fund-operations tools to any MCP client (Claude Code, Claude Desktop).

  claude mcp add fundops -- python -m fundops.mcp_server
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from . import tools
from .data_gen import generate

server = MCPServer(name="fundops", instructions="Read-only investment-operations tools for one fund and business day. "
                                                 "Tools never modify records.")
_S = generate(int(os.getenv("FUNDOPS_SEED", "7")))


@server.tool(description="Reconcile IBOR vs custodian positions and list every break.")
def reconcile_positions() -> list[dict]:
    return tools.reconcile_positions(_S)


@server.tool(description="Unsettled trades in a security across all funds (check fund_id).")
def get_pending_trades(security_id: str) -> list[dict]:
    return tools.get_pending_trades(_S, security_id)


@server.tool(description="Corporate actions for a security.")
def get_corporate_actions(security_id: str) -> list[dict]:
    return tools.get_corporate_actions(_S, security_id)


@server.tool(description="Closing prices from the two pricing vendors.")
def get_prices(security_id: str) -> dict:
    return tools.get_prices(_S, security_id)


@server.tool(description="Official WM/Reuters 4pm FX fix vs the rate the custodian applied.")
def get_fx_rate(currency: str) -> dict:
    return tools.get_fx_rate(_S, currency)


@server.tool(description="Investment-guideline checks: issuer/sector concentration, restricted list, cash.")
def check_guidelines() -> list[dict]:
    return tools.check_guidelines(_S)


if __name__ == "__main__":
    server.run("stdio")
