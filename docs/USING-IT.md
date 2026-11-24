# Using the factory on your own project (plain English)

You don't need to know the terminal to use the factory day to day. You talk to Claude, and
Claude drives the factory for you. You only need a terminal (or GitHub) for **two things that
must stay human**: approving a change and adding a rule that must never break.

---

## 1. One-time setup (about 10 minutes)

You need: Python 3.11 or newer, git, and a Claude account (Pro, Max or API key).

**Easiest way: ask Claude Code to do it.** Open your project folder in VS Code, open the
Claude Code panel, and paste:

> Install the work-order factory from https://github.com/patelvraj810/workorder-factory into
> a virtual environment in my home folder, then run `wo init --user MY_NAME` in this project.
> Show me what it created.

**Or do it yourself in a terminal:**

```bash
git clone https://github.com/patelvraj810/workorder-factory ~/workorder-factory
cd ~/workorder-factory && python3 -m venv .venv && . .venv/bin/activate && pip install -e .
cd /path/to/your/project
wo init --user YOUR_NAME
```

`wo init` only creates files that are missing. It never overwrites your own files and never
commits your other work. It adds:

| File | What it is for | Who owns it |
|---|---|---|
| `factory.toml` | The rules: limits, budgets, test commands, protected files | You |
| `AGENTS.md` / `CLAUDE.md` | What every AI agent must read first. Fill in the TODO lines. | You |
| `invariants/` | Tests for things that must **never** break. Agents can run them, never edit them. | You |
| `.mcp.json` | Connects Claude Code to the factory | You |
| `.claude/skills/work-order/` | Teaches Claude the steps, in plain English | You |

It works out your project type. Python projects get `pytest` set up. For Node or anything
else, set the test commands under `[gate]` in `factory.toml` (until you do, the gate shows
**WARN**, not PASS, for that line):

```toml
[gate]
unit_tests = "npm test"
invariants = "npx vitest run invariants/"
money_paths = ["src/billing/"]   # any float added here fails the gate
```

---

## 2. Day to day: just ask Claude

### In VS Code (Claude Code extension), the best option

1. Open your project folder. Claude Code asks once to allow the `work-order-factory` server. Say yes.
2. Ask in plain words, for example:
   - "Open a work order to add a discount code field to checkout. Don't change how tax is worked out."
   - "Plan it." → "Build the next feature." → "Run the gate." → "Write the PR."
   - "Where are we?" · "Stop everything now."
3. Claude reads back the work order before opening it, explains every gate result
   (PASS / FAIL / WARN), and tells you when it's your turn.

### In the Claude desktop app (chat or Cowork)

The desktop app can use the same factory through a local MCP server. Add this to
`claude_desktop_config.json` (on a Mac: `~/Library/Application Support/Claude/`), then restart
the app. Copy the values from the `.mcp.json` that `wo init` made in your project:

```json
{
  "mcpServers": {
    "work-order-factory": {
      "command": "/Users/YOU/workorder-factory/.venv/bin/python",
      "args": ["-m", "factory.mcp_server"],
      "env": {
        "WO_REPO": "/Users/YOU/path/to/your/project",
        "WO_USER": "YOUR_NAME",
        "WO_BACKEND": "claude",
        "PYTHONPATH": "/Users/YOU/workorder-factory"
      }
    }
  }
}
```

One server points at one project. For a second project, add a second entry with another name.

### Not in claude.ai in a web browser

The website can't reach programs on your computer, so it can't run the factory. Use VS Code
or the desktop app.

---

## 3. The two things that stay with you

| Action | Why Claude can't do it | How you do it |
|---|---|---|
| **Approve / merge** | The person who asked for the change, and the AI that made it, must not approve it. | On GitHub, approve the pull request. Or in your own terminal: `wo approve WO-001 --by REVIEWER_NAME` (a different person from the requester). |
| **Add a rule (invariant)** | The AI must never write the tests it is judged by. | Ask Claude to **draft** the rule. It saves it in `.factory/proposals/`. Read it, then in your own terminal: for an **open** work order `wo rule WO-001 .factory/proposals/test_x.py --by YOUR_NAME`; for a **new** one `wo new "Title" --intent "..." --by YOUR_NAME --invariant .factory/proposals/test_x.py` |
| **Turn the kill switch off** | Anyone (or any agent) can stop. Only a human can start again. | `wo resume --by YOUR_NAME` in your own terminal. |

If an AI agent tries any of these, the factory refuses and writes the attempt in the audit
ledger. It spots agent sessions by the markers that agent tools set (for example
`CLAUDECODE`) and by the MCP channel.

**Honest limit:** on your own laptop this check is a speed bump, not a wall. A determined
agent could unset those markers. The real wall is on the server: GitHub branch protection plus
a required review from a code owner, and the `factory-gate` CI job that re-runs every check.
Turn both on before a team relies on it.

---

**Folder names:** don't start or end a project folder's name with a space. AI agents drop that space
when they write file paths, and every write gets refused. `wo init` checks this for you.

## 4. What it costs

Every agent session uses **your own Claude account**. The defaults stop a session at
**$1.50** and a whole work order at **$5.00**; change them in `factory.toml`. After **3**
failed gates in a row, the agents stop and hand the work back to you with a summary
(`HANDBACK.md`). No pull request is opened.

## 5. Try it without spending anything

The repo ships recorded agent runs. `./demo.sh` replays a full work order offline: no
account, no cost, same result every time.
