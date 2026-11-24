# Build log

Built mainly by directing an agent (Claude, in an agent session with a shell and file tools).
Times are wall-clock. Sections marked **[Vraj]** are for me to fill in honestly after my own review.

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
| **[Vraj]** | Review of code, design doc, slides/recording | Me |

## What I delegated
- All code in `factory/`, `sample_repo/`, `evals/`, `tests/`, `demo.sh`.
- First draft of `docs/DESIGN.md` and `README.md`.
- The demo bug itself: the agent wrote both the buggy session-2 change and the fix, on purpose, to
  reproduce the failure mode the senior engineer described.

## What I reviewed  **[Vraj — fill in]**
- [ ] `factory/agents.py` guard rules — do I agree with every allow/deny?
- [ ] `factory/gate.py` — order of checks, what blocks vs warns
- [ ] `replays/WO-001/human/test_position_limit_invariants.py` — is the "reduce is always allowed" rule right for a real broker?
- [ ] DESIGN.md §5 delegate/review/own table — is this where I really draw the line?
- [ ] Live run with `WO_BACKEND=claude` — what did the real agents do differently?

## What I wrote by hand  **[Vraj — fill in]**
- The choice of definition and which problems come first (§1–2) came from my direction; the agent drafted the words.
- (add anything you edit by hand)

## What went wrong (and what it taught)

| # | Problem | Cause | Fix | Lesson |
|---|---|---|---|---|
| 1 | Live Agent SDK call failed in the build sandbox (auth error) | Cloud sandbox has no usable model credentials for nested agents | Built a **replay backend** behind the same interface and guard; recordings scripted by hand; live backend to be run on my laptop | Always have an offline path for a live demo. Also: the replays are scripted, not recorded — I say so everywhere |
| 2 | **Gate read `factory.toml` from the branch under review** | First version loaded policy from the working copy | Policy now always read from the trusted base ref (`load_policy_at`). Eval case `policy_tamper` added | Found by writing the eval, not by reading the code. Threat-model the gate like any security boundary |
| 3 | Gate output didn't show Hypothesis' failing example | Hypothesis changed its message from "Falsifying example" to "Failing test case" | Parser handles both | Don't parse tool output you don't control without a test |
| 4 | `demo.sh` stopped at step 8 | `wo pr \| head` + `set -o pipefail` → SIGPIPE exit | Write PR to file, print first lines | Run the demo end-to-end after every change |
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
| 5 | Diff-size count includes the human's invariant file | Gate counts all non-`.factory` lines | Left as is, noted | Open question: should human-owned lines count toward the agent's diff budget? |

## Time spent  **[Vraj — update]**
- Round 1 agent build: ~15 minutes wall-clock for the prototype, ~5 minutes for docs.
- Planning and research with me: ~35 minutes (22:24–23:00).
- Round 2 agent build: ~12 minutes (audit-first, MI-23, stop limits, kill switch, MCP, docs).
- Round 3: ~18 minutes choosing ideas with me, ~10 minutes agent build (crash recovery, CI gate, scorecard, catalog).
- My time: planning 5 min + **[review __ min]** + **[live run + recording __ min]** + **[slides __ min]**.
- Why: I spent the time on the gate and its evals because that is where trust is won or lost.
  I cut: real container isolation, model gateway, parallel workers, GitHub check integration,
  non-engineer paved road (all design only).

## Tools used
- Claude (agent session with shell + file tools) for planning, code, tests, docs.
- Claude Agent SDK (Python) as the agent runtime inside the factory.
- pytest, Hypothesis (property tests), bandit (SAST), git worktrees.
- No internal or confidential material from FTMO, OANDA, or any employer. All data synthetic.
