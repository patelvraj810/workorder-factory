# Using the factory from Claude Code, Cursor or Codex (MCP)

Engineers keep the tool they like. The factory's rules still apply, because every MCP tool runs
the same `wo` command a human would run: same guard, same gate, same ledger, same limits.

## Tools

| Tool | Does | Notes |
|---|---|---|
| `wo_new` | Open a work order | Requester comes from `WO_USER` in the config. The agent can't pass another name. No invariants via MCP |
| `wo_plan` | Planner agent | |
| `wo_run` | One implementer session | Refused when handed back, over budget, or kill switch on |
| `wo_gate` | Checks + reviewer agent | |
| `wo_pr` | PR text with provenance | Refused unless the latest gate passed |
| `wo_status`, `wo_provenance`, `wo_stats`, `wo_ledger_verify` | Read-only views | |
| `wo_kill` | Stop all agent sessions | Stopping is always allowed |

**Not exposed, on purpose:** approve, merge, resume, adding invariants. Those stay with humans in a terminal.
Ledger entries made through MCP are marked `channel: mcp`.

## Config

Install once: `pip install -e /path/to/workorder-factory` (this gives you the `wo-mcp` command).

**Claude Code** (`.mcp.json` in the target repo) and **Cursor** (`.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "work-order-factory": {
      "command": "wo-mcp",
      "env": { "WO_REPO": "/abs/path/to/target/repo", "WO_USER": "vraj", "WO_BACKEND": "claude" }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.work-order-factory]
command = "wo-mcp"
env = { WO_REPO = "/abs/path/to/target/repo", WO_USER = "vraj", WO_BACKEND = "claude" }
```

Then ask your tool: *"Open a work order to add per-symbol position limits, plan it, and run the
sessions until the gate passes. Stop and tell me if anything is refused."*

## Limits
- Identity is whatever `WO_USER` says. Production: the server gets identity from SSO, not an env variable.
- Tested in-process (`tests/test_harness.py::test_mcp_server_exposes_no_approval_tools`), not yet in every client.
