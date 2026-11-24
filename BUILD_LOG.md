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
| after the timebox | Used the factory on my own practice project (a tip calculator) from Claude Code. It caught real money bugs in the AI's code, and showed me bugs in the factory itself (#9–12 below), each fixed | Me + Claude Code + the factory |
| after the timebox | Dashboard `wo ui`: see every work order live and make the human-only decisions with buttons; project rule presets; accept a reviewer finding with a written reason | Me decided, agent built |
| **[Vraj]** | Rehearse, record the demo, final edit of the slides | Me |

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
- **Using it myself changed the design.** The terminal was too hard for a non-technical person, and even
  for me it hid what was going on. I decided against a chat UI (Claude already is one) and for a
  **dashboard**: people talk to their AI, and see and decide in one page.
- **A human must be able to overrule the AI reviewer**, with a written reason on record. Before this, the
  reviewer agent had the last word, which is backwards. Only its opinions can be overruled: failed tests
  and broken rules never can.
- **Strictness per project:** practice / normal / money-critical presets. A tip calculator and order
  handling should not have the same rules.
- **Integrations stay design-only for now** (Jira, Slack, Git host, SSO, incident tools; DESIGN.md §8b),
  with one rule: they can start work and inform people, never approve.

## What I wrote by hand
- Almost no code. My work was direction, decisions, review and running it for real.
- The decisions that matter most are mine: the definition, the order of problems, the flip rule,
  what stays out of scope, and what I would never hand to an agent (`WOULD-NOT-SHIP.md`).
- Invariant code was drafted by the agent from rules I stated; I committed it as the accountable owner.

## What went wrong (and what it taught)

The problems worth remembering. Small ones (library renames, exit codes) are left out.

| # | Problem | Cause | Fix | Lesson |
|---|---|---|---|---|
| 1 | Live agents couldn't run in the build sandbox | No model login there | A **replay backend** behind the same guard; live runs on my Mac | Always have an offline path for a live demo, and label replays as scripted |
| 2 | **The gate read its rules from the branch it was checking** | First version loaded `factory.toml` from the working copy | Rules always come from the trusted base branch; eval `policy_tamper` added | Found by writing an eval, not by reading code. Treat the gate like any security boundary |
| 3 | The reviewer agent wasn't in the audit record | It ran inside the gate, outside the audited path | The reviewer runs as its own recorded session | "Every agent step" means every one, including the checker |
| 4 | A crash erased what the agent had touched | The tool log only lived in memory | Every tool call is written to disk the moment it happens | Recording has to survive the crash, not only the happy path |
| 5 | Demos broke on my Mac | `\| head` with `pipefail` (twice), old bash, macOS `sed`, a skipped editable install | Fixed in the scripts | Test on the machine you demo on |
| 6 | First live planner hit the $1.50 budget in 11 seconds | It inherited my personal tools | Agents get only their role's tools | Least privilege is also a cost control |
| 7 | The guard blocked the SDK's own answer tool | Replays never used it | Allow and log that one tool | **The agent reported the block instead of working around it** |
| 8 | **The reviewer failed a change that passed every test** | An account over its limit could flip +300 to −300. My own scripted answer had the same flaw | I decided the rule and wrote it as a test; the fix followed it; eval `flip_loophole` added | The independent reviewer earns its cost. Evidence in `evidence/live-run-1/` |
| 9 | Using it on my own project, every file write was refused | The folder name ended in a space; the agent dropped it when writing paths | New folder worked first time; `wo init` now refuses such names | Change one thing at a time to find the real cause |
| 10 | A blocked step looked finished, and a check ran on empty work | Only "todo" counted as unfinished | Blocked steps are retried; checks wait until the plan is built | Small state bugs waste real money and tries |
| 11 | The factory rejected **my own** rule as agent work, and later counted my rule lines against the agent's size limit | Both checks assumed every change in the work order was the agent's | Changes by a person count as human; the size limit counts only the agent's lines; `wo rule` adds a rule properly | The safe path must be the easy path, or people go around it |
| 12 | The terminal hid what was happening, even from me | A CLI is an engine, not a product | The `wo ui` dashboard: see everything, decide with buttons | Use your own tool before you ask anyone else to |

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


| Extra, after the timebox | Time |
|---|---|
| Dashboard (`wo ui`), presets, accepting reviewer findings | ~30 min |

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
integrations with Jira / Slack / SSO, a hosted paved road for non-engineers (design only; `wo init`, the
Claude skill and the local dashboard are the first step).

## Tools used
- Claude (agent session with shell + file tools) for planning, code, tests, docs.
- Claude Agent SDK (Python) as the agent runtime inside the factory.
- pytest, Hypothesis (property tests), bandit (SAST), git worktrees.
- No internal or confidential material from FTMO, OANDA, or any employer. All data synthetic.
