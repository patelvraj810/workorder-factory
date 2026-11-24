# Work-Order Factory

A small, working slice of a software factory for a regulated trading business.

**The idea in one line:** every change is a *work order*. A human owns the intent and the
invariants. Agents do the work in a sandbox across many sessions. Independent gates check it
before any human spends time on it. Every agent step leaves a signed, hash-chained provenance
record (FINOS MI-23). A different human approves. Production deploy stays in the normal release pipeline.

## Start here

| You are… | Read this | Time |
|---|---|---|
| **Reviewing the case study** | This page → [`docs/DESIGN.md`](docs/DESIGN.md) (the 9 questions) → [`evidence/live-run-1/`](evidence/live-run-1/README.md) (a real run with live agents) → [`BUILD_LOG.md`](BUILD_LOG.md) → [`WOULD-NOT-SHIP.md`](WOULD-NOT-SHIP.md) | 20 min |
| **An engineer who wants to try it** | [Run it](#run-it) below: `./demo.sh` runs offline, no account needed. Then [`docs/MCP.md`](docs/MCP.md) for Claude Code, Cursor or Codex | 10 min |
| **Not technical, want to use it on your own project** | [`docs/USING-IT.md`](docs/USING-IT.md): one-time setup, then you just ask Claude in plain English | 10 min setup |

## What it shows

```
wo new   "Per-symbol position limits" --by vraj --invariant my_invariant_test.py
wo kill  --by sec-lead --reason "..."   # nothing can start; `wo resume` (humans only) undoes it
wo plan  WO-001     # planner agent: small features + risks (read-only tools)
wo run   WO-001     # implementer: ONE feature per session, memory = progress.md
wo run   WO-001     # next session picks up cold from the files
wo gate  WO-001     # FAIL: agent's own 11 tests pass, the human's invariant does not
wo run   WO-001     # fix session gets the gate report (no human time spent yet)
wo gate  WO-001     # PASS: 9 checks incl. an independent reviewer agent on a different model
wo pr    WO-001     # PR text with provenance — refused unless the gate passed
wo provenance WO-001                    # FINOS MI-23 record for every agent step
wo approve WO-001 --by vraj             # REFUSED: author cannot approve
wo approve WO-001 --by priya --merge    # checks gate + provenance, merges with trailers
wo ci --base main  # same gate in CI (GitHub Actions workflow in sample_repo/.github/)
wo ledger verify   # hash chain + signatures
wo stats           # flow, quality, control, cost + agent scorecard by role and model
```

The bug the gate catches is real and typical: the agent's position-limit check used
`abs(position) + qty`, so a trader **long 0.51 lots with a 1.00 limit could not sell 0.50 to
reduce risk**. The agent's own tests only covered BUY, so they passed. The human-owned
property test found the counter-example in under 2 seconds.

And when it goes wrong (`./demo-when-it-goes-wrong.sh`):
- **A session crashes halfway** (laptop dies): the next run keeps the leftovers as evidence, resets the
  sandbox to the last good commit, records the crashed step (with the tool calls it made), and continues.
- **The agent keeps failing**: after 3 failed gates in a row (or when the budget runs out) agents stop,
  the owner gets a `HANDBACK.md` summary, and no PR is opened.

## Run it

Requirements: Python 3.11+, git. Synthetic data only.

```bash
pip install -e .
./demo.sh                     # main story, replay backend — deterministic, the demo backup
./demo-when-it-goes-wrong.sh  # crash recovery + stop limit
PAUSE=1 ./demo.sh             # step through it while presenting
python -m pytest -q tests/    # 50 harness tests, including the gate evals
python evals/run_evals.py     # 10 agent failure modes the gate must catch
```

### Live agents (Claude Agent SDK)
Uses your own Claude Code login (`claude login`) or `ANTHROPIC_API_KEY`.
```bash
WO_BACKEND=claude RECORD_TO=$PWD/replays/live ./demo.sh
```
Live runs use **your own** Claude account and stop at the budgets in `factory.toml`.
Models per role: `WO_PLANNER_MODEL`, `WO_IMPLEMENTER_MODEL` (default `sonnet`), `WO_REVIEWER_MODEL`
(default `opus`, a different model from the implementer on purpose). Live agents will not reproduce
the exact red→green story every time. That's fine: the gate is the point.

### On your own project
```bash
cd /path/to/your/project
wo init --user YOUR_NAME      # adds factory.toml, AGENTS.md, invariants/, .mcp.json, a Claude skill
```
Then open the folder in Claude Code and ask: *"Open a work order to …"*. Python is set up for
you; for Node or anything else, set the test commands under `[gate]` in `factory.toml`.
Full guide, including the Claude desktop app: [`docs/USING-IT.md`](docs/USING-IT.md).

## Real vs mocked

| Part | Real | Mocked / limited |
|---|---|---|
| Agents | Claude Agent SDK backend: tool allow-list, path guard hook, budget cap, structured output. **One full live run** in `evidence/live-run-1/` (real catch, $0.70) | The demo script runs on **scripted replays** so it's fast and repeatable |
| Sandbox | One git worktree per work order; guard blocks writes outside it, to protected paths, secrets, and non-allow-listed commands | **No OS isolation.** A test file can run any Python. Production: container per session, no network, no real credentials |
| Gate | All 9 checks run for real: invariants (Hypothesis), unit tests, bandit, secret scan, diff cap, protected paths, test-config tamper, domain lint, reviewer agent | Reviewer verdicts in the demo are replays |
| Provenance & ledger | FINOS MI-23 fields per agent step, written **before** the agent starts (fail closed); hash chain; HMAC signatures | Local file + shared-secret HMAC. Production: write-once storage + KMS/Sigstore signing |
| Approval | Approver ≠ requester, gate pinned to commit, provenance checked before merge. `wo approve`, `wo resume` and adding invariants are **refused inside an AI agent session** (and the attempt is logged) | `--by` is a flag, and the agent-session check is a speed bump: an agent could unset the markers. Production: SSO identity + branch protection + required CODEOWNER review on the Git host |
| Stop controls | Kill switch (`wo kill` + `factory.toml`), 3-failed-gates stop limit, per-session and per-work-order budgets | — |
| Crash recovery | Real hard kill (`os._exit`) tested; leftovers kept as evidence; tool calls logged to disk as they happen | Crash is simulated with `WO_SIMULATE_CRASH` |
| CI gate | `wo ci` runs and is tested locally | The GitHub Actions workflow file has not been run on GitHub; install URL is a placeholder |
| Catalog | `catalog-info.yaml` (Backstage format) read for owner + risk tier on every PR | No Backstage portal |
| MCP server | Works with any MCP client; no approve/merge/resume tools | Tested in-process, not yet in every client |
| Target code | Synthetic order-validation service in Python | Not tested on a real monorepo |
| Other projects | `wo init` sets up any folder; gate test commands are set in `factory.toml`, so Node, Go etc. work. Missing tests show **WARN**, not PASS | Tested on Python and a small Node smoke test only |

## How the pieces map to the problems we heard

| Who | Problem | In this prototype |
|---|---|---|
| Senior trading engineer | Agents quietly break order-handling invariants | Human-owned property tests in `invariants/`, agent cannot write them (hook + gate), run with `--noconftest` |
| Early adopter | Huge PRs, tests written by the same agent | One feature per session, diff cap, independent reviewer agent on a different model that sees only the diff |
| Security & compliance | Who is the author? Audit? Secrets? | Requester = accountable owner, agent = drafter, approver ≠ requester; FINOS MI-23 provenance; signed ledger; secret scan; bandit; kill switch |
| Long-task engineer | Context lost between sessions | `features.json` + `progress.md` + `sessions/*.json`; each session starts cold from files; crashed sessions are recovered automatically |
| Team lead | Everyone's own setup; can't tell if faster | Conventions live in the repo (`AGENTS.md`, `factory.toml`, prompts); MCP server so any tool uses the same rules; `wo stats` |
| Platform engineer | Laptops, own creds, no record, no cost view | Worktree per work order, guard, budgets, cost per step in the ledger. *Real isolation is design-only* |
| Ops analyst | Shadow app on a laptop | *Design only*, see DESIGN.md §7 |

## Repository layout

One repo, on purpose: the factory and the sample project it works on are shown together, so one
`git clone` runs the whole story. `sample_repo/` is copied into a fresh git repo by the demos.
```
factory/            wo CLI: cli.py, agents.py (backends + guard), gate.py, ledger.py, workorder.py, policy.py, mcp_server.py
factory/prompts/    planner / implementer / reviewer prompts (the hand-off protocol)
factory/templates/  what `wo init` writes into your project (factory.toml, AGENTS.md, Claude skill, …)
sample_repo/        synthetic order-validation service (target), with AGENTS.md, factory.toml, CODEOWNERS,
                    catalog-info.yaml, invariants/, .github/workflows/factory-gate.yml
replays/WO-001/     recorded agent outputs for the demo story + the human-written invariant
evals/              gate eval suite (10 failure modes)
tests/              harness tests (50)
evidence/           live run with real agents (logs, ledger, reviewer finding)
docs/               DESIGN.md (the 9 questions), MCP.md, USING-IT.md (plain-English guide)
```

## License
MIT. See [`LICENSE`](LICENSE).
