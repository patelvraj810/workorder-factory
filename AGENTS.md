# AGENTS.md — building the work-order factory itself

Instructions for any coding agent working on THIS repository (the factory, not the sample service).

## Layout
- `factory/` — the `wo` CLI and harness. `cli.py` (commands), `agents.py` (backends + guard),
  `gate.py` (checks), `ledger.py` (signed audit chain), `mcp_server.py` (MCP tools), `workorder.py` (hand-off files), `policy.py`.
- `factory/prompts/` — role prompts. Changing a prompt is a behaviour change: run the evals.
- `sample_repo/` — the synthetic target service. Its own `AGENTS.md` governs agents working there.
- `replays/` — recorded agent runs used by tests, evals and the offline demo.
- `evals/run_evals.py` — failure modes the gate must catch. Add a case for every new escape you find.
- `tests/` — tests of the harness.

## Rules
1. The guard (`agents.guard`) and the gate are security controls. Every change to them needs a
   new test in `tests/test_harness.py` or a new eval case. Never relax a check to make a test pass.
2. Policy is always read from the trusted base ref (`load_policy_at`), never from the branch under review.
3. The ledger is append-only. Do not add any code path that rewrites or deletes entries.
4. Agents never commit. The harness commits, with provenance trailers.
5. Every agent run goes through `Ctx.agent_session` (audit record first, fail closed). No other path may call a backend.
6. The MCP server must never expose approve, merge, resume, or a way to add invariants.
7. Synthetic data only. No real broker, client or employer data anywhere in this repo.

## Before you finish
```bash
python -m pytest -q tests/          # harness tests (includes the evals)
python evals/run_evals.py           # gate eval table
./demo.sh && ./demo-stop-limit.sh   # both stories, replay backend
```
