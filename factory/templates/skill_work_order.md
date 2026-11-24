---
name: work-order
description: Use when the user wants a change made to this project through the work-order factory (open, plan, build, check, or see the status of a work order), or asks what the factory can do. Explains each step in plain English.
---

# Work-order factory: how to help the user

The user may not be technical. Use short, plain sentences. No jargon.
You drive the factory through the `work-order-factory` MCP tools. Never edit factory files yourself.

## The steps, in order
1. **Open** — `wo_new(title, intent)`. Turn what the user said into one short title and a clear
   intent (what should change, and what must NOT change). Read it back to them before you call it.
2. **Plan** — `wo_plan(id)`. Show the feature list in plain words.
3. **Build** — `wo_run(id)`. One feature per call. After each call, say what was done and what is next.
   Call again until all features are done.
4. **Check** — `wo_gate(id)`. Explain each result: PASS, FAIL (what broke) or WARN (not set up yet).
   If it fails, call `wo_run(id)` again: the next session fixes the problems.
5. **Pull request** — `wo_pr(id)` once the gate passes. Tell the user where the PR text is.
6. **Approval** — you can NEVER approve or merge. Tell the user: a different person must approve,
   either on GitHub or by running `wo approve` in their own terminal.

Use `wo_status` any time the user asks "where are we?".

## Rules you must follow
- **Never** approve, merge, resume after a stop, or add/change files in `invariants/`. The factory
  refuses these when an agent tries, and logs the attempt. Do not try to work around it.
- If a tool says **stopped**, **budget**, **stop limit** or **hand-back**: tell the user plainly what
  happened and what they can do. Do not retry around it.
- If the user wants something to stop now: `wo_kill(reason)`.
- Do not change code directly in this chat while a work order is open. The factory's agents do the work
  inside their own copy, so everything is checked and recorded.

## Helping the user write a rule (invariant)
If the user describes something that must always be true ("a refund can never be more than the payment"):
1. Draft a test for it and save it in `.factory/proposals/` (never in `invariants/`).
2. Explain the test in one or two plain sentences.
3. Tell them to read it and add it themselves from their own terminal:
   - to a work order that is already open: `wo rule WO-00N .factory/proposals/<file> --by THEIR_NAME`
   - to a new work order: `wo new "..." --intent "..." --by THEIR_NAME --invariant .factory/proposals/<file>`
4. Rules live in the work order's own copy (`.factory/worktrees/WO-00N/invariants/`), not the main folder.

## If something is not set up
- No MCP tools named `wo_*`? Tell the user to run `wo init` once in this folder, then restart Claude.
- Gate shows WARN for tests: the project has no test command yet. Ask the user (or a developer)
  to set `[gate]` in `factory.toml`.
