# Build log

Built mainly by directing an agent (Claude, in an agent session with a shell and file tools), then
run on my own Mac with live Claude agents. Times are wall-clock. The one line marked **[Vraj]** is
what I still do before submitting.

## Timeline

| Time (ET, 23 Nov 2026) | What | Who |
|---|---|---|
| 22:06 | Read the brief + job post. Decide the definition ("contract around every change") and the prototype slice | Me + agent |
| 22:08 | Plan agreed: problems in order, prototype = work-order CLI + gate + ledger, 4-hour timebox | Agent proposed, me approved |
| 22:08–22:10 | Synthetic order-validation service, AGENTS.md, factory.toml, CODEOWNERS, human-owned invariants | Agent |
| 22:10–22:14 | Harness: policy, ledger, git ops, work-order files, prompts, agent backends + guard, gate, CLI | Agent |
| 22:14–22:16 | Demo recordings (3 sessions, plan, 2 reviews) + demo.sh, first full run | Agent |
| 22:16–22:19 | Gate evals (9 cases), harness tests (24) | Agent |
| 22:19–22:21 | README, DESIGN.md, this log | Agent |
| 22:24–22:47 | Stopped building. Agent explained the assignment and plan in plain English; I compared ideas and chose what to add | Me + agent |
| 22:47–23:00 | Research: how other governed agent-delivery products and open standards work. Found the FINOS SDLC Common Controls catalog (RI-13 / MI-23 cover exactly our problem). I picked the additions | Me + agent |
| 23:00–23:12 | Round 2: audit-first (fail closed), FINOS MI-23 provenance + `wo provenance`, signed ledger, stop limit + hand-back, work-order budget, kill switch, MCP server, delivery-health stats, WOULD-NOT-SHIP.md, README real-vs-mocked, DESIGN.md control map | Agent, from my list |
| 23:15–23:33 | Looked at four well-known open-source projects (Paperclip, Backstage, Dagger, Temporal) for one small idea each that fits the assignment | Me + agent |
| 23:33–23:43 | Round 3: crash recovery (durable work orders), `wo ci` + GitHub Actions workflow, agent scorecard, `catalog-info.yaml` owner + risk tier, docs | Agent, from my list |
| 00:04–00:50 (24 Nov) | First run on my Mac: install, tests, both demos. Fixed 4 macOS issues | Me + agent |
| 00:50–01:14 | **First live run with real agents**: 2 failed planner tries (budget cap, blocked output tool), then a full work order; reviewer caught a flip loophole; I wrote the flip-rule invariant; merged. Evidence in `evidence/live-run-1/` | Me + live agents |
| 01:19–01:45 | Round 4: scripted demo updated to the flip rule (+ `flip_loophole` eval), WO-002 kit for the margin bug the agents found, this log, slide draft | Agent, from my list |
| **[Vraj]** | Read the guard and gate code, rehearse, record the demo, final edit of the slides | Me |

## What I delegated
- All code in `factory/`, `sample_repo/`, `evals/`, `tests/`, `demo.sh`.
- First draft of `docs/DESIGN.md` and `README.md`.
- The demo bug itself: the agent wrote both the buggy session-2 change and the fix, on purpose, to
  reproduce the failure mode the senior engineer described.

## What I reviewed and decided
- **I stopped the build after the first 15 minutes** because I didn't understand what had been built.
  I made the agent explain the assignment, the plan and every part in plain English before going on.
  Lesson: fast agent output is worthless if the owner can't defend it.
- **Where the design sits:** I compared it against four common meanings of "software factory"
  (agent factory, CI/CD, developer platform, scaffolding) and placed it as the control layer of an agent factory.
- **What to add, what to cut:** for each research round I chose from a short list and said no to
  the rest (e.g. no extra prompts file; no ideas presented as mine that came from other companies' products;
  credit borrowed ideas by name: Temporal, Dagger, Paperclip, Backstage, FINOS).
- **Ran everything on my own Mac** and fed back every failure (4 macOS issues).
- **Live run:** I read the reviewer's flip finding, **decided the business rule myself**, and committed
  the invariant under my name before the agent fixed the code.
- **WO-002:** the agent drafted the margin invariant from my rule; I read it and own it before it runs.
- Still to read line by line before the interview **[Vraj]**: `factory/agents.py` (guard) and `factory/gate.py`.

## What I wrote by hand
- Almost no code. My work was direction, decisions, review and running it for real.
- The decisions that matter most are mine: the definition, the order of problems, the flip rule,
  what stays out of scope, and what I would never hand to an agent (`WOULD-NOT-SHIP.md`).
- Invariant code was drafted by the agent from rules I stated; I committed it as the accountable owner.

## What went wrong (and what it taught)

| # | Problem | Cause | Fix | Lesson |
|---|---|---|---|---|
| 1 | Live Agent SDK call failed in the build sandbox (auth error) | Cloud sandbox has no usable model credentials for nested agents | Built a **replay backend** behind the same interface and guard; recordings scripted by hand; live backend to be run on my laptop | Always have an offline path for a live demo. Also: the replays are scripted, not recorded — I say so everywhere |
| 2 | **Gate read `factory.toml` from the branch under review** | First version loaded policy from the working copy | Policy now always read from the trusted base ref (`load_policy_at`). Eval case `policy_tamper` added | Found by writing the eval, not by reading the code. Threat-model the gate like any security boundary |
| 3 | Gate output didn't show Hypothesis' failing example | Hypothesis changed its message from "Falsifying example" to "Failing test case" | Parser handles both | Don't parse tool output you don't control without a test |
| 4 | `demo.sh` stopped at step 8 | `wo pr \| head` + `set -o pipefail` → SIGPIPE exit | Write PR to file, print first lines | Run the demo end-to-end after every change |
| 5 | Diff-size count includes the human's invariant file | Gate counts all non-`.factory` lines | Left as is, noted | Open question: should human-owned lines count toward the agent's diff budget? |
| 6 | MCP server import failed | The installed `mcp` package is 2.x, which renamed `FastMCP` to `MCPServer` | Import tries 2.x first, falls back to 1.x | Library APIs move fast; pin or support both |
| 7 | Old tests broke after round 2 | `wo gate` now exits non-zero on a failed gate (so scripts and CI can stop) | Tests updated to expect it; demo uses `\|\| true` on purpose | Exit codes are part of the interface |
| 8 | First provenance design missed the reviewer agent | The reviewer ran inside the gate, outside the audited session path | Split the gate: deterministic checks, then the reviewer as its own audited session | "Every agent step" means every one, including the checker |
| 9 | A crashed session's tool calls were lost | The tool log was only kept in memory, so a hard kill erased it | Every tool call is written to `sessions/<key>.tools.jsonl` the moment it happens (fsync) | "Record what the agent touched" must survive the crash, not only the happy path |
| 10 | The gate could test files that weren't committed | pytest runs on the working files, but the report names a commit | The gate refuses to run while uncommitted changes exist that no agent owns | A report must describe exactly the commit it names |
| 11 | Failure demo stopped early again | `\| head` + `pipefail` (same as #4) | `sed -n` instead of `head` | The same mistake twice: I should have added a lint for it |
| 12 | Four macOS-only problems on my first Mac run | Old bash 3.2 (empty list under `set -u`), macOS `sed -i`, a lost "can run" permission after an edit, and the editable install being skipped | Fixed in the demo scripts; demos set `PYTHONPATH` | Test on the machine you demo on. The Linux tests passed every time |
| 13 | First live planner hit the $1.50 budget cap in 11 s | The agent inherited my personal MCP servers (hundreds of tools in every request) | `strict_mcp_config=True`: agents get only their role's tools | The cost cap and failure record worked on the first real run |
| 14 | Guard blocked the SDK's own answer tool | Structured output is delivered through a `StructuredOutput` tool call; replays never used it | Allow and log that one tool | **The agent refused to work around the block and reported it.** Replays can't test everything |
| 15 | Live reviewer failed a change that passed every test | Over-limit account could flip +300 → −300; my invariants only covered pure reductions. **My own scripted "correct" answer had the same flaw** | I decided the flip rule and wrote invariants 5–6; the agent fixed the code. Then I added the same invariants to the scripted demo, rewrote its fix, and added a `flip_loophole` eval case (the new invariant also catches my old scripted answer: `pos=1.01, sell 2.02, limit 1.00`) | The independent reviewer earns its cost. See `evidence/live-run-1/` |

## Time spent
| Block | Time |
|---|---|
| Plan + first build (agent) | 22:06–22:21 · 15 min |
| Understanding it in plain English, choosing additions, research | 22:24–23:33 · ~70 min |
| Build rounds 2–3 (agent) | ~25 min |
| First Mac run + macOS fixes | 00:04–00:50 · ~45 min |
| First live run with real agents | 00:50–01:14 · ~25 min |
| Round 4: demo follows the flip rule, WO-002 kit, log, slide draft | 01:19–01:45 · ~25 min |
| **Total so far** | **about 3 h 25 min** |
| Rehearsal, recording, final slides **[Vraj]** | planned ~1 h 30 min |

**Why over the suggested 4 hours (about 5 h in total):** I chose to spend the extra time on two things,
not on more features. First, understanding: I stopped the build to make sure I could defend every part.
Second, a real live run: replays can't prove agent fluency, and the live run found 3 problems the
replays never could (budget cap, blocked answer tool, flip loophole).
**What I cut:** real container isolation, model gateway, parallel workers, GitHub check integration,
the non-engineer paved road (all design only).

## Tools used
- Claude (agent session with shell + file tools) for planning, code, tests, docs.
- Claude Agent SDK (Python) as the agent runtime inside the factory.
- pytest, Hypothesis (property tests), bandit (SAST), git worktrees.
- No internal or confidential material from FTMO, OANDA, or any employer. All data synthetic.
