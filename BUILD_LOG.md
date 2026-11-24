# Build log

Built mainly by directing an agent (Claude, in an agent session with a shell and file tools), then
run on my own Mac with live Claude agents. Times are wall-clock. The line marked **[Vraj]** is
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
| after the live run | Read `factory/agents.py` (guard) and `factory/gate.py` line by line | Me |
| ~01:50–02:20 | Round 5, "use it myself": I decided to make it a public repo that a non-technical person can use by talking to Claude. Chose 3 items from a list: `wo init` + Claude skill + `.mcp.json`; approval guard; public-repo prep. Agent built them, plus configurable gate commands so it works on non-Python projects | Me decided, agent built |
| **[Vraj]** | Rehearse, record the demo, final edit of the slides, push to GitHub | Me |

## What I delegated
- All code in `factory/`, `sample_repo/`, `evals/`, `tests/`, `demo.sh`.
- First draft of `docs/DESIGN.md`, `README.md` and `docs/USING-IT.md`.
- Round 5 code: `wo init` and its templates, the Claude skill, the approval guard, configurable gate commands.
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
- **Read the two files that matter most line by line:** `factory/agents.py` (the guard) and
  `factory/gate.py`. If I have to defend one thing in the interview, it's these.
- **Round 5 decisions:** make it public; make it usable without a terminal (you talk to Claude in VS Code
  or the desktop app); pick 3 of the proposed items and say no to the rest; keep **one repo, not two**,
  so one `git clone` runs the whole story (factory + the sample project it works on).

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
| 5 | Diff-size count includes the human's invariant file | Gate counts all non-`.factory` lines | Left open at first; it came true in #24 | Open questions in a log are cheap; answer them before they bite |
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
| 16 | First `wo init` draft would have committed the user's other unsaved work | The shared commit helper runs `git add -A` | `wo init` now commits **only the files it created**; a test checks an unrelated file stays uncommitted | Setup tools run in someone else's repo. Touch only what you made |
| 17 | Gate said "add the command to  in factory.toml" | The terminal UI library treats `[gate]` as a colour tag and drops it | Reworded | Small, but a non-technical user would be stuck on that line |
| 18 | Early commit messages included a link to the private build chat | Default attribution lines from the agent tool | Removed from every commit message before the repo goes public | Check commit messages too, not just files, before publishing |
| 19 | First real use through Claude Code: the plan worked, but every file write was refused | The factory's guard allowed the writes, but Claude Code's own "is this inside the project?" check refused them. Most likely cause: my test folder's name ends in a space | For file writes, the guard's decision is now final: it tells Claude Code "allow" after checking the path is inside the sandbox and not protected, secret or `.git`. The MCP server also lets the factory start its own agents from inside Claude Code | **The agent stopped and reported the block instead of working around it.** Dogfooding on a real folder found what the tests couldn't |
| 20 | Next build said "all features done" while F1 was still blocked, then the gate ran on empty work and used 1 of my 3 tries | `next_feature` only looked for "todo", so "blocked" looked finished. The gate didn't check the plan was finished | Blocked features are retried with the last session's notes; the gate refuses (and counts nothing) until every planned feature is done; a missing `tests/` folder is a WARN | Found by Claude Code while I used the factory on my own practice project. The stop limit did its job: one wasted gate, not ten |
| 21 | Folder name ending in a space: every agent file write refused, even with the guard's "allow" | The agent dropped the trailing space when writing file paths, so Claude Code saw writes to a folder that doesn't exist | New practice folder without spaces worked first time. `wo init` now refuses folder names that start or end with a space and explains why | Confirmed by testing outside Claude Code first, then a clean folder: change one thing at a time |
| 22 | Python cache files got committed with agent work, and later blocked the gate as "uncommitted changes no one owns" | Running tests leaves `__pycache__` folders; my practice project's `.gitignore` didn't exclude them | The factory now tells git to ignore tool caches for the repo and every work-order copy (`.git/info/exclude`), the gate runs tests without writing them, and `wo init` adds the right ignores per project type | Small mess, real cost: it burned a session and nearly a gate |
| 23 | The gate rejected my own rule as "changed by an agent" | I committed the rule by hand without the factory's marker line, and the check required the marker | Any commit to `invariants/` by a person now counts as human-owned (agents can't commit at all); new `wo rule WO-001 file.py --by NAME` adds a rule to an open work order properly (humans only) | The safe path has to be the easy path, or people go around it. Same lesson as the brief |
| 24 | My practice work order was handed back after the 3rd failed gate, on size alone: 344 lines against a 250 limit | 180 of those lines were **my own rules**; the agent's work was 164 lines. The cap counted human-owned files (open question #5) | The cap now counts only what the agent changed; human-owned lines are shown but not counted (they need a code-owner review instead) | The stop limit worked as designed, and exposed a wrong rule in my own factory. Also: I should have counted lines before using the last gate |

## Time spent
| Block | Time |
|---|---|
| Plan + first build (agent) | 22:06–22:21 · 15 min |
| Understanding it in plain English, choosing additions, research | 22:24–23:33 · ~70 min |
| Build rounds 2–3 (agent) | ~25 min |
| First Mac run + macOS fixes | 00:04–00:50 · ~45 min |
| First live run with real agents | 00:50–01:14 · ~25 min |
| Round 4: demo follows the flip rule, WO-002 kit, log, slide draft | 01:19–01:45 · ~25 min |
| Round 5: `wo init`, Claude skill, approval guard, public-repo prep | ~30 min |
| **Total build time** | **about 3 h 55 min, within the 4-hour timebox** |

Rehearsing, recording the demo and the final slide edit are presentation prep, not build time.

**How I used the time:** I spent it on three things, not on more features.

1. **Most important: anyone can use it, not only engineers.** I wanted a person who can't code to use
   this factory safely, and to plug it into the AI agent they already use, not a new tool. One command
   (`wo init`) sets up their project. After that they talk to their agent in plain English ("open a work
   order to…") and the agent drives the factory through the MCP server. The same server works with Claude
   Code, the Claude desktop app, Codex, Cursor, or any agent that speaks MCP, and the same rules apply in
   every one of them: same gate, same limits, same audit record. Only two things stay with a human, on
   purpose: approving a change and adding a rule that must never break. This is the problem the ops
   analyst and the team lead described: people already use AI tools their own way, so the safe path has to
   be the easy path. Plain-English guide: `docs/USING-IT.md`. (Tested: the MCP server and `wo init`, plus
   a Node project. Not yet tested live inside every agent tool.)
2. **Understanding:** I stopped the build to make sure I could defend every part.
3. **A real live run:** replays can't prove agent fluency, and the live run found 3 problems the
   replays never could (budget cap, blocked answer tool, flip loophole).

**What I cut:** real container isolation, model gateway, parallel workers, GitHub check integration,
a hosted paved road for non-engineers (design only; `wo init` + the Claude skill is the local first step).

## Tools used
- Claude (agent session with shell + file tools) for planning, code, tests, docs.
- Claude Agent SDK (Python) as the agent runtime inside the factory.
- pytest, Hypothesis (property tests), bandit (SAST), git worktrees.
- No internal or confidential material from FTMO, OANDA, or any employer. All data synthetic.
