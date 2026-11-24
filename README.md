# Work-Order Factory

A small, working slice of a software factory for a regulated trading business.

**The idea in one line:** every change is a *work order* — a human owns the intent and the
invariants, agents do the work in a sandbox across many sessions, independent gates check it
before any human spends time on it, and every step lands in a hash-chained audit ledger.
A different human approves. Production deploy stays in the normal release pipeline.

Design document: [`docs/DESIGN.md`](docs/DESIGN.md) · Build log: [`BUILD_LOG.md`](BUILD_LOG.md)

## What it shows (2-minute version)

```
wo new   "Per-symbol position limits" --by vraj --invariant my_invariant_test.py
wo plan  WO-001     # planner agent: small features + risks (read-only tools)
wo run   WO-001     # implementer: ONE feature per session, memory = progress.md
wo run   WO-001     # next session picks up cold from the files
wo gate  WO-001     # FAIL: agent's own 11 tests pass, the human's invariant does not
wo run   WO-001     # fix session gets the gate report (no human time spent yet)
wo gate  WO-001     # PASS: 9 checks incl. an independent reviewer agent
wo pr    WO-001     # PR text with provenance: owner, agents, cost, gate, ledger head
wo approve WO-001 --by vraj            # REFUSED: author cannot approve
wo approve WO-001 --by priya --merge   # merged with Approved-By / Accountable-Owner trailers
wo ledger verify   # tamper-evident audit chain
wo stats           # what a team lead sees
```

The bug the gate catches is real and typical: the agent's position-limit check used
`abs(position) + qty`, so a trader **long 0.51 lots with a 1.00 limit could not sell 0.50 to
reduce risk**. The agent's own tests only covered BUY, so they passed. The human-owned
property test found the counter-example in under 2 seconds.

## Run it

Requirements: Python 3.11+, git. Synthetic data only.

```bash
pip install -e .
./demo.sh                     # offline, replay backend — deterministic, the demo backup
PAUSE=1 ./demo.sh             # step through it live while presenting
python -m pytest -q tests/    # 24 harness tests, including the gate evals
python evals/run_evals.py     # 9 agent failure modes the gate must catch
```

### Live agents (Claude Agent SDK)
Uses your own Claude Code login (`claude login`) or `ANTHROPIC_API_KEY`.
```bash
WO_BACKEND=claude RECORD_TO=$PWD/replays/live ./demo.sh
```
Models per role: `WO_PLANNER_MODEL`, `WO_IMPLEMENTER_MODEL` (default `sonnet`), `WO_REVIEWER_MODEL`
(default `opus` — a different model from the implementer on purpose). With `RECORD_TO`, every live
agent run is saved and can be replayed with `WO_REPLAY_DIR=replays/live ./demo.sh`.

> Note: live agents will not reproduce the exact red→green story every time — sometimes the
> implementer gets the direction right first time. That is fine; the gate is the point.

## How the pieces map to the problems we heard

| Who | Problem | In this prototype |
|---|---|---|
| Senior trading engineer | Agents quietly break order-handling invariants | Human-owned property tests in `invariants/`, agent cannot write them (hook + gate), run with `--noconftest` |
| Early adopter | Huge PRs, tests written by the same agent | Small features per session, diff cap, independent reviewer agent on a different model, sees only the diff |
| Security & compliance | Who is the author? Audit? Secrets? | Requester = accountable owner, agent = drafter, approver ≠ requester enforced; hash-chained ledger; secret scan; bandit; approval pinned to gated commit |
| Long-task engineer | Context lost between sessions | `features.json` + `progress.md`; each session starts cold from files |
| Team lead | Everyone's own setup; can't tell if faster | Conventions live in the repo (`AGENTS.md`, `factory.toml`, prompts); `wo stats` from the ledger |
| Platform engineer | Laptops, own creds, no record, no cost view | Per-WO git worktree, tool allow-list, path guard, per-session budget cap, cost per session in ledger. *Real isolation (container, scoped creds) is design-only.* |
| Ops analyst | Shadow app on a laptop | *Design only* — see DESIGN.md §7 |

## Repository layout
```
factory/            the wo CLI: cli.py, agents.py (backends + guard), gate.py, ledger.py, workorder.py, policy.py
factory/prompts/    planner / implementer / reviewer prompts (the hand-off protocol)
sample_repo/        synthetic order-validation service (target), with AGENTS.md, factory.toml, CODEOWNERS, invariants/
replays/WO-001/     recorded agent outputs for the demo story + the human-written invariant
evals/              gate eval suite (9 failure modes)
tests/              harness tests
docs/DESIGN.md      the design
BUILD_LOG.md        what was delegated, reviewed, hand-written, what went wrong, time
```

## Honest limits
- Replays in `replays/WO-001/` were **scripted during the build** (the build sandbox had no live API).
  The live backend is wired and tested for structure, but re-record real runs before relying on them.
- The "sandbox" is a git worktree + tool/path/command guard. It is **not** OS isolation. A malicious
  agent with Bash `python -m pytest` can run arbitrary Python via a test file. Production needs a
  container/VM per session with no network and no real credentials (see DESIGN.md).
- The ledger is a local file. Production ships the same records to write-once storage.
- `--by` is a flag. In production identity comes from SSO and the Git host's approval, not a CLI argument.
- One sample repo, one language. Not tested on a real monorepo.
