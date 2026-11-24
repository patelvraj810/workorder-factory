# AGENTS.md — __PROJECT__

Every AI agent (and every human) reads this before changing anything here.

## What this project does
TODO (human): two or three plain sentences. Who uses it? What breaks if it goes wrong?

## Hard rules
1. **Never edit `invariants/`, `factory.toml`, `CODEOWNERS`, `.github/`, `.mcp.json` or `.claude/`.**
   They are human-owned. If an invariant looks wrong, stop and say so in your notes.
2. Keep each change small. One feature per session.
3. No new dependencies unless a human asked for them.
4. Never read or print secrets (`.env`, keys).
5. TODO (human): add the rules that matter for this project.

## How to check your work
```bash
__UNIT_TESTS_DOC__
__INVARIANTS_DOC__
```
