#!/usr/bin/env bash
# "What happens when it goes wrong": the agent keeps failing the gate.
# After 3 failed gates in a row the factory stops the agents, hands the work order back to its
# owner with a summary, and refuses to open a pull request. No reviewer time is wasted.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEMO="${DEMO_DIR:-/tmp/wo-demo-stop}"
export WO_BACKEND=replay WO_REPLAY_DIR="$HERE/replays/WO-001"
step() { echo; echo -e "\033[1;35m━━ $* ━━\033[0m"; if [[ "${PAUSE:-0}" == "1" ]]; then read -r -p "(enter)"; fi; }

rm -rf "$DEMO" && cp -r "$HERE/sample_repo" "$DEMO" && cd "$DEMO"
git init -q -b main && git add -A
git -c user.name=trading-lead -c user.email=trading-lead@example.com commit -qm "Initial order-validation service"

step "1. Same work order, same buggy session 2"
wo new "Per-symbol position limits" --by vraj --intent "Position limits; reducing is always allowed." \
  --invariant "$HERE/replays/WO-001/human/test_position_limit_invariants.py" > /dev/null
wo plan WO-001 > /dev/null && wo run WO-001 > /dev/null && wo run WO-001 > /dev/null && echo "planned + 2 sessions done"

step "2. Gate fails three times in a row (deterministic checks only, to keep it short)"
for i in 1 2 3; do wo gate WO-001 --no-review 2>&1 | grep -E "GATE FAIL|STOP LIMIT|Handed|No PR" || true; done

step "3. Agents are stopped, and there is no PR"
wo run WO-001 || true
wo pr WO-001 || true

step "4. What the owner gets instead"
cat .factory/worktrees/WO-001/.factory/WO-001/HANDBACK.md
wo status
