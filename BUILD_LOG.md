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
| **[Vraj]** | Review of code, design doc, live run on my laptop, re-record replays, slides/recording | Me |

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
| 5 | Diff-size count includes the human's invariant file | Gate counts all non-`.factory` lines | Left as is, noted | Open question: should human-owned lines count toward the agent's diff budget? |

## Time spent  **[Vraj — update]**
- Agent build time: ~15 minutes wall-clock for the prototype, ~5 minutes for docs.
- My time: planning 5 min + **[review __ min]** + **[live run + recording __ min]** + **[slides __ min]**.
- Why: I spent the time on the gate and its evals because that is where trust is won or lost.
  I cut: real container isolation, GitHub check integration, non-engineer paved road (design only).

## Tools used
- Claude (agent session with shell + file tools) for planning, code, tests, docs.
- Claude Agent SDK (Python) as the agent runtime inside the factory.
- pytest, Hypothesis (property tests), bandit (SAST), git worktrees.
- No internal or confidential material from FTMO, OANDA, or any employer. All data synthetic.
