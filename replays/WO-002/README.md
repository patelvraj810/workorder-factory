# WO-002 kit: margin must never block reducing or closing

**Why this exists:** in live run 1, the planner and the reviewer agent both flagged, on their own,
that the existing margin check rejects orders that *close* a position. That breaks AGENTS.md rule 5.
They were right not to fix it inside WO-001 (out of scope). This is the follow-up work order.

**The human part:** `human/test_margin_invariants.py` states the business rule. The agent drafted the test code;
**I read it and agree with the rule before running `wo new`**, which commits it under my name. The rule:
- reducing or closing never needs margin;
- opening new exposure needs margin for the new exposure only (a flip: only the new side).

Checked before handing it to agents: it **fails** on today's code (2 tests) and **passes** with a
correct fix, together with all older invariants (13/13 over 4 seeds). The reference fix was thrown
away on purpose: the agent writes the real one.

## Run it live (about 10 minutes, under $1)

```bash
cd ~/Desktop/workorder-factory && source .venv/bin/activate
export PYTHONPATH=$HOME/Desktop/workorder-factory WO_BACKEND=claude
unset ANTHROPIC_API_KEY
cd /tmp/wo-live
wo new "Margin never blocks reducing or closing" --by vraj --intent "A trader must always be able to reduce or close a position, even with no free margin. Opening new exposure still needs margin for the new exposure only." --invariant ~/Desktop/workorder-factory/replays/WO-002/human/test_margin_invariants.py
wo plan WO-002
wo run WO-002
```
Repeat `wo run WO-002` until it says `All features are done`. Then:
```bash
wo gate WO-002
```
If the gate fails: `wo run WO-002`, then `wo gate WO-002` again. When it passes:
```bash
wo pr WO-002
wo approve WO-002 --by priya --merge --note "Margin rule confirmed with risk"
wo stats
mkdir -p ~/Desktop/workorder-factory/evidence/live-run-2
cp .factory/ledger.jsonl ~/Desktop/workorder-factory/evidence/live-run-2/
cp -R .factory/worktrees/WO-002/.factory/WO-002 ~/Desktop/workorder-factory/evidence/live-run-2/workorder
git diff HEAD^1 HEAD -- orders tests invariants > ~/Desktop/workorder-factory/evidence/live-run-2/final-diff.patch
```

If `/tmp/wo-live` is gone (it's wiped when the Mac restarts), make a fresh one first:
```bash
cd /tmp && rm -rf wo-live && cp -r ~/Desktop/workorder-factory/sample_repo wo-live && cd wo-live
git init -q -b main && git add -A && git -c user.name=lead -c user.email=lead@example.com commit -qm init
```
WO-002 does not depend on WO-001, so it works in a fresh repo too.
