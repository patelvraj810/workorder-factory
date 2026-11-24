#!/usr/bin/env bash
# End-to-end demo of one real change, from idea to merge.
#
#   ./demo.sh                       replay backend (no network, deterministic) — the backup
#   WO_BACKEND=claude ./demo.sh     live agents via the Claude Agent SDK
#   PAUSE=1 ./demo.sh               wait for Enter between steps (for presenting)
#   ./demo-when-it-goes-wrong.sh    crash recovery + stop limit
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"   # find the factory code even if the editable install is skipped
DEMO="${DEMO_DIR:-/tmp/wo-demo}"
export WO_BACKEND="${WO_BACKEND:-replay}"
export WO_REPLAY_DIR="${WO_REPLAY_DIR:-$HERE/replays/WO-001}"
export WO_LEDGER_KEY="${WO_LEDGER_KEY:-demo-signing-key-not-secret}"   # signs every ledger entry
RECORD_ARGS=()
if [[ "$WO_BACKEND" == "claude" && -n "${RECORD_TO:-}" ]]; then RECORD_ARGS=(--record "$RECORD_TO"); fi

step() { echo; echo -e "\033[1;35m━━ $* ━━\033[0m"; if [[ "${PAUSE:-0}" == "1" ]]; then read -r -p "(enter)"; fi; }

step "0. Fresh copy of the sample service (synthetic data), committed to main"
rm -rf "$DEMO" && cp -r "$HERE/sample_repo" "$DEMO" && cd "$DEMO"
git init -q -b main && git add -A
git -c user.name=trading-lead -c user.email=trading-lead@example.com commit -qm "Initial order-validation service"
python -m pytest -q 2>&1 | tail -1

step "1. HUMAN (vraj) opens a work order with intent + an invariant test he wrote"
wo new "Per-symbol position limits" --by vraj \
  --intent "Risk wants a max absolute net position per symbol. Orders that would push the position past the limit must be rejected with a clear reason. Traders must always be able to reduce or close a position." \
  --invariant "$HERE/replays/WO-001/human/test_position_limit_invariants.py"

step "2. KILL SWITCH: security pulls the plug — no agent can start"
wo kill --by sec-lead --reason "checking an unusual tool call"
wo plan WO-001 || true
wo resume --by sec-lead --note "checked, fine"

step "3. PLANNER agent splits the work into small features (audit record written FIRST)"
wo plan WO-001 ${RECORD_ARGS[@]+"${RECORD_ARGS[@]}"}

step "4. IMPLEMENTER session 1 — F1 (fresh context, reads progress.md)"
wo run WO-001 ${RECORD_ARGS[@]+"${RECORD_ARGS[@]}"}

step "5. IMPLEMENTER session 2 — F2 (new session, no memory except the files)"
wo run WO-001 ${RECORD_ARGS[@]+"${RECORD_ARGS[@]}"}

step "6. GATE — the agent's own tests pass... do the human-owned invariants?"
wo gate WO-001 ${RECORD_ARGS[@]+"${RECORD_ARGS[@]}"} || true

step "7. IMPLEMENTER session 3 — fix mode: gets the gate report, not a human's time"
wo run WO-001 ${RECORD_ARGS[@]+"${RECORD_ARGS[@]}"}

step "8. GATE again"
wo gate WO-001 ${RECORD_ARGS[@]+"${RECORD_ARGS[@]}"}

step "8b. Same gate in CI: GitHub Actions runs this on the PR (no model calls, no secrets)"
(cd .factory/worktrees/WO-001 && wo --repo . ci --base main)

step "9. PR text with provenance"
wo pr WO-001 > /dev/null && sed -n "1,25p" .factory/worktrees/WO-001/.factory/WO-001/PR.md

step "10. PROVENANCE — FINOS MI-23 record for every agent step"
wo provenance WO-001

step "11. vraj tries to approve his own change"
wo approve WO-001 --by vraj || true

step "12. A different human (priya, trading lead) approves and merges"
wo approve WO-001 --by priya --merge --note "Confirmed flip rule with trading team"
git log --oneline -1 && git log -1 --format=%B | tail -6

step "13. Audit: ledger, stats, and what happens if someone edits history"
wo ledger show
wo stats
wo ledger verify
cp .factory/ledger.jsonl /tmp/ledger.bak
sed -i 's/"actor": "priya"/"actor": "vraj"/' .factory/ledger.jsonl
wo ledger verify || echo "(tamper detected — restoring)"
cp /tmp/ledger.bak .factory/ledger.jsonl
