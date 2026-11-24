#!/usr/bin/env bash
# "What happens when it goes wrong":
#   A. the laptop dies in the middle of an agent session  -> durable work order: recover and continue
#   B. the agent keeps failing the gate                   -> stop limit: hand back, no PR
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEMO="${DEMO_DIR:-/tmp/wo-demo-wrong}"
export WO_BACKEND=replay WO_REPLAY_DIR="$HERE/replays/WO-001"
step() { echo; echo -e "\033[1;35m━━ $* ━━\033[0m"; if [[ "${PAUSE:-0}" == "1" ]]; then read -r -p "(enter)"; fi; }

rm -rf "$DEMO" && cp -r "$HERE/sample_repo" "$DEMO" && cd "$DEMO"
git init -q -b main && git add -A
git -c user.name=trading-lead -c user.email=trading-lead@example.com commit -qm "Initial order-validation service"
wo new "Per-symbol position limits" --by vraj --intent "Position limits; reducing is always allowed." \
  --invariant "$HERE/replays/WO-001/human/test_position_limit_invariants.py" > /dev/null
wo plan WO-001 > /dev/null && wo run WO-001 > /dev/null && echo "work order opened, planned, session 1 done"

step "A1. Session 2 starts... and the process is killed halfway (like a laptop losing power)"
WO_SIMULATE_CRASH=session-2 wo run WO-001 || echo "(process died with exit code $?)"
echo "Half-written files left in the sandbox:"; git -C .factory/worktrees/WO-001 status --short

step "A2. The factory knows a session never finished"
wo status WO-001 | sed -n "1,3p"

step "A3. Next run: keep the leftovers as evidence, reset to the last good commit, continue"
wo run WO-001

step "A4. Provenance still complete: the crashed step is on record, with the tool calls it made before dying"
wo provenance WO-001 | tail -4

step "B1. Now the gate fails three times in a row (deterministic checks only, to keep it short)"
for i in 1 2 3; do wo gate WO-001 --no-review 2>&1 | grep -E "GATE FAIL|STOP LIMIT|Handed|No PR" || true; done

step "B2. Agents are stopped, and there is no PR"
wo run WO-001 || true
wo pr WO-001 || true

step "B3. What the owner gets instead"
cat .factory/worktrees/WO-001/.factory/WO-001/HANDBACK.md
wo status
