"""MCP server for the work-order factory.

Lets any MCP-capable coding tool (Claude Code, Cursor, Codex, ...) drive the factory, so
engineers keep the tool they like and the factory's rules still apply. Every tool here runs
the exact same `wo` command a human would run, so the same guard, gate, ledger and limits apply.

Deliberately NOT exposed as tools (humans only, from a terminal):
  - approve / merge       an agent must never approve or merge a change
  - resume                an agent must never turn the kill switch back off
  - adding invariants     an agent must never author the rules it is judged by

Identity: the requester comes from WO_USER, set once in the server config by the human.
The agent cannot pass a different name, so it cannot open work orders as someone else.

Config (Claude Code `.mcp.json`, Cursor `.cursor/mcp.json`):
  {"mcpServers": {"work-order-factory": {"command": "wo-mcp",
     "env": {"WO_REPO": "/abs/path/to/repo", "WO_USER": "vraj", "WO_BACKEND": "claude"}}}}
"""
from __future__ import annotations

import os
import subprocess
import sys

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

INSTRUCTIONS = (
    "Work-order factory. Use wo_new to open a work order, wo_plan to split it, wo_run for one "
    "implementation session at a time, wo_gate to check it, wo_pr once the gate passes. "
    "You cannot approve or merge; tell the human to run `wo approve` with a different reviewer. "
    "If a call is refused (kill switch, stop limit, budget), report the reason to the human; do not retry around it."
)

server = _Server(name="work-order-factory", instructions=INSTRUCTIONS)


def _repo() -> str:
    repo = os.environ.get("WO_REPO")
    if not repo:
        raise RuntimeError("WO_REPO is not set in the MCP server config")
    return repo


def _user() -> str:
    user = os.environ.get("WO_USER")
    if not user:
        raise RuntimeError("WO_USER is not set in the MCP server config (the human this server acts for)")
    return user


def _wo(*args: str) -> dict:
    env = {**os.environ, "NO_COLOR": "1", "COLUMNS": "110", "TERM": "dumb", "WO_CHANNEL": "mcp"}
    # The factory starts its own Claude agents. Claude Code refuses to start inside another Claude
    # Code session, so drop its marker. Human-only commands still refuse: WO_CHANNEL=mcp marks this path.
    env.pop("CLAUDECODE", None)
    p = subprocess.run([sys.executable, "-m", "factory.cli", "--repo", _repo(), *args],
                       capture_output=True, text=True, env=env, timeout=3600)
    out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return {"ok": p.returncode == 0, "exit_code": p.returncode, "output": out[-12000:]}


@server.tool()
def wo_new(title: str, intent: str) -> dict:
    """Open a work order for the configured human (WO_USER). Agents cannot add invariants:
    the human adds those with `wo new --invariant` in a terminal or through a reviewed PR."""
    return _wo("new", title, "--intent", intent, "--by", _user())


@server.tool()
def wo_plan(work_order: str) -> dict:
    """Run the planner agent: split the work order into small features and list risks."""
    return _wo("plan", work_order)


@server.tool()
def wo_run(work_order: str) -> dict:
    """Run ONE implementer session: the next feature, or a fix session after a failed gate."""
    return _wo("run", work_order)


@server.tool()
def wo_gate(work_order: str) -> dict:
    """Run the gate: deterministic checks, human-owned invariants, then the independent reviewer agent."""
    return _wo("gate", work_order)


@server.tool()
def wo_pr(work_order: str) -> dict:
    """Write the pull request text with provenance. Refused unless the latest gate passed."""
    return _wo("pr", work_order)


@server.tool()
def wo_status(work_order: str = "") -> dict:
    """Status of one work order, or of all work orders if none is given."""
    return _wo("status", *([work_order] if work_order else []))


@server.tool()
def wo_provenance(work_order: str) -> dict:
    """FINOS MI-23 provenance record for every agent step of a work order."""
    return _wo("provenance", work_order)


@server.tool()
def wo_stats() -> dict:
    """Flow, quality, control and cost metrics from the ledger."""
    return _wo("stats")


@server.tool()
def wo_ledger_verify() -> dict:
    """Check the audit ledger's hash chain (and signatures, if a key is configured)."""
    return _wo("ledger", "verify")


@server.tool()
def wo_kill(reason: str) -> dict:
    """Stop all agent sessions now. Stopping is always allowed; only a human can resume."""
    return _wo("kill", "--by", f"{_user()} (via agent)", "--reason", reason)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
