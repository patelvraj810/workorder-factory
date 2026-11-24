# Live run 1: real agents, real gate catch (24 Nov 2026)

The first end-to-end run of the factory with **live Claude agents** (Claude Agent SDK 0.2.188,
my personal Claude Code login) on the synthetic order-validation service. Nothing here is scripted.
Total agent cost: **$0.70**. Lead time from work order to merge: **21 min**, including my own thinking time.

## What happened (ledger entries in brackets)

| # | Step | Result |
|---|---|---|
| 1 | Planner, try 1 (Opus) [1–2] | **Failed: hit the $1.50 session budget cap in 11 s.** The agent had inherited my personal MCP servers (hundreds of tools). Fix: `strict_mcp_config`, so agents get only their role's tools |
| 2 | Recovery [3] | Factory recorded the failure, reset the sandbox, kept leftovers in `workorder/recovered/` |
| 3 | Planner, try 2 (Sonnet) [4–5] | **Failed: my guard blocked the SDK's `StructuredOutput` tool** (its way of returning the final answer). The agent refused to work around the block and reported it. Fix: allow and log that one tool |
| 4 | Planner, try 3 [7–8] | Plan written. It flagged, unprompted, that the existing margin check blocks traders from closing positions |
| 5 | Implementer sessions 1–2 [9–12] | Done. **The guard blocked 3 tool calls** (chained shell commands, a heredoc write). The agent switched to allowed tools |
| 6 | Gate 1 [13–15] | All 8 automatic checks PASS, but the **independent reviewer (Opus) FAILED it**: an over-limit account could flip from +300 to −300 (3× the limit), because the "reducing" test only compared sizes. No test covered it |
| 7 | **Human decision** | I decided the flip rule and wrote it as invariants 5 and 6 (human-authored commit, `Change-Type: human-invariant`) |
| 8 | Fix session [16–17] | The agent fixed the code to follow my rule. 9 invariants and 18 unit tests pass |
| 9 | Gate 2 [18–20] | PASS. The reviewer also noted that an invariant changed after a failed gate needs CODEOWNER confirmation |
| 10 | CI gate (`wo ci`) | PASS on a plain checkout |
| 11 | Approval [22–24] | Self-approval refused; priya approved; merged with provenance trailers |

## Files
- `ledger.jsonl`: the full audit chain (25 entries). Verify with `python -c` or `wo ledger verify` in a repo.
- `workorder/`: plan, progress log, both gate reports, every session log, every tool call (`*.tools.jsonl`), recovered leftovers, PR text.
- `git-log.txt`: every commit with author and provenance trailers.
- `final-diff.patch`: the merged change.

## Still open (found by the agents, deliberately not fixed in this work order)
- Margin check can block a trader from closing a position (breaks AGENTS.md rule 5). Candidate for WO-002, with a human-written invariant first.
- Limit values aren't validated (NaN raises, negative blocks everything).
