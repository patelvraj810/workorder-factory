# The Work-Order Factory
### A software factory design for FTMO & OANDA — AI Engineer case study

> **In one line:** a software factory is a *contract around every change*. A human owns the intent
> and the rules that must never break. Agents do the work. Independent checks judge it before a
> person spends time on it. Every step is recorded. A different person approves.

Where I went deep: **§1 Definition**, **§3 The design (with the gate as the deep dive)**,
**§5 Delegate / review / own**, **§6 Quality, security and governance**. The rest is short on purpose.

---

## 1. My definition

The brief offers a range: "tasks in, shipped changes out" at one end, "how agents talk to each other" at the other.

I land in the middle, closer to the protocol end. **The factory is the work order** — a small set of
files that travel with every change, plus the checks and the record around them:

| Part | What it is | Who writes it |
|---|---|---|
| `workorder.json` | Intent, requester, base, invariants | Human |
| `invariants/*` | Executable rules that must never break (property tests) | Human (domain owners) |
| `features.json` | The work split into small slices | Planner agent, human can edit |
| `progress.md` | Append-only log of every session: what was done, what's next, traps | Agents (via harness) |
| `gates/gate-N.json` | Check results, pinned to one commit | Factory |
| `PR.md` | Pull request text with provenance | Factory |
| Ledger entry | Hash-chained record of each step | Factory |

Why this and not a full "tasks in, prod out" pipeline:

1. **The bottleneck is trust, not typing.** Every person we heard from has the same root problem:
   they cannot tell if agent output is right, safe, or faster. More automation without trust makes it worse.
2. **Files outlive tools and sessions.** Claude Code, Cursor and Codex will change every six months.
   A work order on disk works with all of them, survives context loss, and survives people leaving.
3. **Regulators audit changes, not agents.** The unit compliance cares about is "a production change
   with an author and an approver". The work order maps 1:1 to that.

**What it is deliberately NOT:**
- Not autonomous deploy. Merge needs a human; production deploy stays in the existing release pipeline.
- Not a new IDE or a new agent. It wraps whatever agent the engineer already likes.
- Not a replacement for code review. It makes review smaller and better-aimed.
- Not a mandate. It has to win on speed and safety, or it should lose.

---

## 2. Problems and people — what we solve, in what order

| Order | Person | Problem | Why this order |
|---|---|---|---|
| 1 | Senior trading engineer | Agents break order-handling invariants | If the code that moves money isn't safe, nothing else matters. Also: he is the most respected skeptic. Win him, others follow |
| 2 | Security & compliance lead | Authorship, approval, audit, secrets | Without this, nothing agent-written can reach production in a regulated firm. It's a blocker, not a feature |
| 3 | Early adopter | Big PRs, self-graded tests, rubber-stamp reviews | Already happening today. This is live risk |
| 4 | Long-task engineer | Context loss across sessions | Cheap to fix with the same work-order files |
| 5 | Team lead | No shared setup, no measurement | Falls out of 1–4: shared config in repo, metrics from the ledger |
| 6 | Platform engineer | Laptops, own creds, no record, no cost view | Partly solved (guard, budget, ledger). Real isolation is a 30–60 day platform project |
| 7 | Ops analyst | Shadow apps on laptops with prod exports | Important, but needs 1–6 first. A paved road on an unsafe base is worse than none |

**What I would leave alone:** autocomplete and chat use. The senior engineer is happy with it and it
carries little risk. The factory is for changes that reach production, not for how people think.

---

## 3. The design

```
  Engineer (any tool: Claude Code, Cursor, CLI, PR bot)
        │  wo new  (intent + invariants)
        ▼
┌─────────────────── Work order (files on a branch) ──────────────────┐
│ workorder.json   invariants/   features.json   progress.md          │
└───────┬──────────────────┬───────────────────────┬──────────────────┘
        ▼                  ▼                       ▼
   Planner agent     Implementer agent       Reviewer agent
   read-only         1 feature / session     fresh context, other model
                     sandbox + guard         sees only diff + intent
        │                  │                       │
        └──────────► GATE (cheap → expensive) ◄────┘
                 protected paths · test-config tamper · diff size
                 secret scan · domain lint · SAST · invariants · unit tests
                 · reviewer agent        → pinned to commit SHA
                           │ pass
                           ▼
            Human review (CODEOWNERS) → approve (≠ requester) → merge
                           │
                           ▼
          Existing CI/CD release pipeline (unchanged) → production
   Every step → hash-chained ledger (write-once storage) → metrics
```

### 3a. One real change, idea to merge (this is the live demo)

**Change:** risk wants a maximum net position per symbol.

| Step | Who | What happens | Human time |
|---|---|---|---|
| 1 | **Vraj (engineer)** | Writes intent and a property test: *"a trader can always reduce a position, even when over the limit"*. Runs `wo new`. The test is committed **as Vraj**, before any agent runs | ~15 min |
| 2 | Planner agent | Reads code + invariant. Splits into F1 (model) and F2 (check). Flags the risk: *"abs(position)+qty would block closing"* | 0 (Vraj skims plan: 1 min) |
| 3 | Implementer, session 1 | Fresh context. Reads progress.md. Does F1. Writes notes for the next session | 0 |
| 4 | Implementer, session 2 | New session, no memory. Picks up from files. Does F2. **Writes `abs(position) + qty`** and tests only BUY. Its 11 tests pass | 0 |
| 5 | Gate 1 | **FAIL.** Hypothesis finds: long 0.51, limit 1.00, SELL 0.50 → blocked. Reviewer agent independently flags the same bug + missing SELL tests | 0 |
| 6 | Implementer, session 3 | Fix mode: gets the gate report as its task. Uses signed position. Adds 5 tests | 0 |
| 7 | Gate 2 | **PASS** on commit `a9e4…`. Reviewer: approve, with one note — "the long→short flip rule is a business decision" | 0 |
| 8 | Factory | Writes PR.md: intent, features, sessions, cost ($1.53), gate, ledger head | 0 |
| 9 | Vraj | Tries to approve his own change → **refused** and logged | — |
| 10 | **Priya (trading lead, CODEOWNER)** | Reviews a 75-line, gated diff. Confirms flip rule with trading. Approves → merge commit carries `Approved-By`, `Accountable-Owner`, gate SHA, ledger hash | ~10 min |

The bug in step 4 is the exact thing the senior engineer described: *plausible code that quietly
breaks an invariant*. No human had to find it.

### 3b. Deep dive — the gate, and why the agent that writes the code must never grade it

This is the part I find most interesting **and** most risky. An agent optimises for "checks pass".
If the checks are reachable, it will — sometimes on purpose, usually by accident — make them pass the
wrong way. So I treat the gate as a security boundary and threat-model it like one.

**Threat model: how agent output gets past checks**

| # | Escape | Real example | Mitigation | Built? |
|---|---|---|---|---|
| 1 | Agent tests its own blind spot | Tests only BUY | Human-owned **property** invariants, written before the agent runs | ✅ |
| 2 | Agent edits the invariant | Replaces an assert with `pass  # flaky` | Pre-tool hook blocks the write; gate fails if any non-human commit touches `invariants/` | ✅ both layers |
| 3 | Agent disables tests indirectly | Root `conftest.py` that skips everything | Invariants run with `--noconftest`; gate fails on agent changes to test config | ✅ |
| 4 | Agent loosens the rules | Raises `max_diff_lines` in `factory.toml` | Policy always loaded from the **base branch**, never the branch under review; file is protected | ✅ (found by my own eval — see build log) |
| 5 | Code changes after the gate | "One small fix" after green | Gate is pinned to a commit SHA; approval refused if non-factory files changed since | ✅ |
| 6 | Self-approval | Author approves own PR | Approver must differ from requester; refusal logged | ✅ |
| 7 | Reviewer shares the author's blind spots | Same model, same context, says "LGTM" | Reviewer gets a fresh session, a **different model**, the intent and the diff — not the author's reasoning | ✅ |
| 8 | Secrets / unsafe code | Hard-coded key, `shell=True` | Secret scan on added lines, bandit, domain lint (no `float` on money) | ✅ |
| 9 | Escape the sandbox | Write `../../ledger.jsonl`, read `.env`, `curl … \| sh` | Path guard (must resolve inside worktree), secret-path deny, Bash allow-list with no chaining | ✅ guard; ❌ OS isolation |
| 10 | Code that runs inside the test process | Test file that edits files on import | Needs a container per session with read-only mount of `invariants/` and no network | ❌ design only |

**Measured:** `evals/run_evals.py` runs 9 of these as seeded agent changes. 9/9 caught, and the correct
implementation passes (no false positive). Every new escape we find becomes an eval case first.

**Order matters:** cheap deterministic checks first, the reviewer agent last. Deterministic checks are
free, repeatable, and can't be argued with. The reviewer is for what rules can't express
("this flip rule is a business decision").

**Honest gap:** the gate is only as good as the invariants. If nobody writes the invariant that matters,
the gate passes bad code with a green tick — which is worse than no tick. That is why §8 puts so much
weight on getting domain owners to write invariants.

---

## 4. The people using it, day to day

| Person | Starts work | Steers | Reviews | Why they'd trust it | When it goes wrong |
|---|---|---|---|---|---|
| Engineer | `wo new` in their terminal, or a GitHub issue label | Edits `features.json`, adds a note to `progress.md`, re-runs a session | Only sees gated, small PRs with a session log | They wrote the invariants; they can read every step | Gate red → agent fixes first. 3 red gates in a row → hand back to human with a summary |
| Reviewer (CODEOWNER) | PR notification | Comments go into the next session's input | PR.md shows what was checked and what wasn't; planner's risks tell them where to look | Approval is pinned to the exact commit that passed | Can reject; rejection reason is logged and becomes an eval candidate |
| Team lead | `wo stats` / dashboard | Owns `AGENTS.md` + prompts in the repo, reviewed like code | — | Numbers come from the ledger, not from surveys | Bad trend (first-gate pass rate falls) shows up weekly |
| Compliance | Ledger export | Owns `factory.toml` with platform | Samples work orders; verifies chain | Tamper-evident, write-once storage | Broken chain = incident |

**How I'd know it works for them** (measured from the ledger + Git host, reported weekly, failures included):
- Human review minutes per merged PR (target: down) and PR size (target: down)
- **First-gate pass rate** (are agents + AGENTS.md getting better?)
- Escaped defects: incidents traced back to a work order (target: not higher than human-written)
- Reviewer-agent precision: how often humans agree with its blocking findings (is it worth the money?)
- Cost per merged work order
- Voluntary repeat use: % of engineers who used it again within 2 weeks

---

## 5. Delegate, review, own

The line: **agents can do anything that is checkable; humans must do anything that is a judgment about
risk, money, or what "correct" means.**

| Delegate to agents | A human must review | A human must own (never delegated) |
|---|---|---|
| Splitting work into small features | The plan, quickly (1 min) — it shapes everything after | **Intent**: what we are changing and why |
| Writing code and unit tests | Every diff that reaches `main` (a CODEOWNER, not the requester) | **Invariants**: what must never break. If an agent writes the rule *and* the code, nothing was checked |
| Fixing gate failures | Reviewer-agent findings marked "business decision" | **Policy**: `factory.toml`, protected paths, budgets, who can approve |
| First-pass review (reviewer agent) | Any change to invariants, policy, CI, auth, or money movement | **Approval and merge** |
| Writing PR text, release notes, progress logs | Agent-proposed new dependencies | **Production deploy decision** and incident response |
| Explaining code, finding call sites | — | **Accountability**: the requester is the author of record |

Why the line sits there: an agent can be right most of the time, but in order handling "most of the
time" is a loss event. Checkable work can be delegated because the check catches the miss.
Unwritten rules can't be checked, so they stay with people — and the factory's job is to turn as many
unwritten rules as possible into written invariants.

---

## 6. Quality, security and governance

**Quality beyond "it looks right"**
1. **Property-based invariants** owned by domain teams (Hypothesis). They test thousands of generated
   cases, not the three the author thought of. In the demo it found the bug in < 2 seconds.
2. **Independence**: the author never grades itself — separate reviewer, separate model, separate context.
3. **Small changes**: one feature per session, diff cap per work order.
4. **Measured over time**: first-gate pass rate, escaped defects, reviewer precision.
5. Next: mutation testing on invariants (do they actually fail when code is wrong?).

**Authorship, approval, audit — the compliance lead's question answered**

| Question | Answer in this design |
|---|---|
| Who is the author? | The **requester** is the author of record and is accountable. The agent is a tool that *drafted* it — recorded as commit author `wo-agent` with model, session ID and work order in trailers. Same as a compiler or a code generator: tools don't carry accountability |
| Who approves? | A CODEOWNER who is **not** the requester. Enforced by the factory and (in production) by branch protection on the Git host |
| How do I know what happened? | Hash-chained ledger: every plan, session (files changed, tool calls, **blocked** calls, cost), gate result, refusal, approval and merge. Editing or deleting any entry breaks the chain |
| How do I know it didn't add a vulnerability or leak a secret? | Secret scan, SAST, dependency rule, protected paths, sandbox guard — all before review, all recorded. Plus a human review of a small diff |
| Can I reproduce it? | Gate pinned to commit SHA, deterministic seed for property tests, prompts versioned in the repo |

**Production version:** ledger records shipped to write-once storage (e.g. GCS bucket with retention
lock, queryable in BigQuery); identity from SSO, not a `--by` flag; agent sessions in isolated
runners (container/gVisor, no network except the model API, short-lived scoped credentials via
workload identity — never the engineer's own creds); per-team model routing and budgets.

---

## 7. Apps built by non-engineers (short)

The ops analyst did something valuable. The answer is a **paved road**, not a ban:

- **Amnesty + registry**: register existing tools, no blame. The reconciliation tool is pilot #1.
- **Risk tiers**: T0 personal, no prod data → free. T1 team tool, read-only data → the paved road.
  T2 writes data or moves money → normal engineering path with an engineering owner.
- **Paved road for T1**: a template (small web app), hosted on the platform behind SSO (not a laptop),
  data from governed **read-only views** (not raw exports), same work order + gate.
- **Invariants in plain language**: the analyst writes examples — "given these two files, these 3 rows
  must be flagged" — the agent turns them into tests, the analyst confirms. That is the analyst's
  equivalent of owning invariants.
- **Engineering sponsor** approves (approver ≠ requester still holds). The analyst never becomes an engineer.

---

## 8. Adoption without a mandate (short)

**The senior engineer** — don't ask him to use agents. Ask him to **own the invariants** for order
handling. That is valuable to him even with zero agents: it protects his code from the early adopter's
agent PRs. The factory helps him write property tests faster. Once his invariants gate everyone else's
changes, he has a reason to care — and the demo above is his exact complaint, caught automatically.
Then offer agents for the work he hates (test scaffolding, migrations), not for order logic.

**The early adopter** — make the factory the **fast path**: gated PRs get a review SLA; ungated large
PRs go to the back of the queue. Show them their own first-gate pass rate. Their speed stays; their
habits come up to standard because the gate does the nagging, not a person.

**The team lead** — setup lives in the repo, so it doesn't leave with people. `wo stats` answers "are we faster?" with data.

General rule: start with one team, publish real numbers including failures, and let the next team ask.

---

## 9. First 90 days (short)

| Days | Build | Expect to learn |
|---|---|---|
| 0–30 | Work order + gate as a **GitHub check** (not only a CLI). Ledger to write-once storage. One pilot product team + the trading platform's invariants. Baseline metrics before any change | How many invariants a team will actually write. Baseline review time and PR size |
| 31–60 | Isolated runners (no laptop creds), model routing, cost dashboard. Compliance sign-off on the authorship model. Second team | Real cost per merged change. Is the reviewer agent worth it (precision)? |
| 61–90 | Paved road pilot with the ops analyst's tool. Long-running multi-session tasks at scale. First honest report to the AI Lead | Can a non-engineer ship safely? Does first-gate pass rate improve as AGENTS.md improves? |

**Biggest risk I'd watch:** a green gate on weak invariants creates false confidence. Mitigation:
mutation-test the invariants, and report escaped defects next to every speed number.
