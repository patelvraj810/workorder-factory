# Progress log — WO-001: Per-symbol position limits

Opened by vraj. No sessions yet.

## Recovery
Session step(s) 1 did not finish. Half-done changes were discarded and kept as evidence in `.factory/WO-001/recovered/after-step-1.patch`. Continue from the last good commit.

## Recovery
Session step(s) 4 did not finish. Half-done changes were discarded and kept as evidence in `.factory/WO-001/recovered/after-step-4.patch`. Continue from the last good commit.

## Plan (sonnet, $0.05)
- **F1** Add position_limits field to Account and Reason.POSITION_LIMIT (no behavior change)
- **F2** Enforce per-symbol position limit in validate_order for exposure-increasing orders only

**Risks flagged by planner:**
- Using abs(new) > limit alone (without the abs(new) > abs(current) guard) blocks reducing orders when already over the limit, which violates invariant 2 and AGENTS.md rule 5.
- Checking the limit against order.qty instead of the resulting net position (current + signed_qty) gives wrong results for shorts and for BUY vs SELL; sign errors on SELL are easy to make.
- Flip orders (long to short through zero) are neither plain reductions nor plain increases. Judge them by the final abs(new) vs abs(current), not by side alone.
- Existing margin check runs on all orders, including reducing ones, so a reducing order can still be rejected for INSUFFICIENT_MARGIN. This is a pre-existing behavior that conflicts with AGENTS.md rule 5. The invariants use RICH margin so they will not catch it. Do not silently change it. Flag it in the notes for a human.
- Check ordering: if the limit check runs before the margin check, an order failing both reports POSITION_LIMIT instead of INSUFFICIENT_MARGIN. Keep it with the risk checks and keep exactly one Reason per rejection.
- A missing key must mean no limit. Do not use `.get(symbol, 0)` for the limit, because that would reject everything. Treat missing positions as 0 but missing limits as None.
- Never convert to float or build Decimals from floats. Use Decimal(0), not 0.0. Mixing int literals is fine, float is not.
- Adding the Account field in the wrong position, or without default_factory, breaks existing Account(...) callers and the shared mutable default.
- Do not mutate account.positions or position_limits, for example by using setdefault. validate_order must stay pure.
- Invariant files, factory.toml, CODEOWNERS and .github/ are human-owned. If an invariant looks wrong, stop and note it rather than editing.

## Session 1 — F1: Add position_limits field to Account and Reason.POSITION_LIMIT (no behavior change) (sonnet, $0.11)
**Status:** done

**Summary:** Added Account.position_limits (dict[str, Decimal], default_factory=dict, declared after positions) and Reason.POSITION_LIMIT. validate_order is unchanged. Added 4 unit tests. tests/ passes (10) and invariants/test_order_invariants.py passes (3).

**Tests added:** test_account_position_limits_default_empty_and_not_shared, test_account_positional_and_keyword_limits, test_position_limit_reason_code, test_limits_do_not_change_behavior_yet

**Notes for next session:** Done in F1: orders/models.py now has Account.position_limits (after positions, default_factory=dict) and Reason.POSITION_LIMIT. Four tests were added to tests/test_validation.py. They cover default and non-shared dict, positional plus keyword construction, the reason code, and BUY/SELL on long/short positions still accepted (no behavior change).

Left for F2: enforce the limit in orders/validation.py.
- Compute new = positions.get(symbol, Decimal(0)) + signed qty.
- A missing limit (None) means no limit. Do not use .get(symbol, 0) for limits.
- Reject with POSITION_LIMIT only if abs(new) > limit AND abs(new) > abs(current). This keeps reducing orders allowed, and flips are judged by the final abs.
- Keep exactly one Reason per rejection, and do not mutate the account.

TRAPS:
1. invariants/test_position_limit_invariants.py already exists and currently FAILS (test_inv_limit_1: pos=0, BUY 1.01, limit 1.00 is accepted). This is expected until F2 is implemented, not a bug in F1. F2 must make it pass. I did not edit it.
2. The existing margin check runs on reducing orders too. This conflicts with AGENTS.md rule 5, and the invariants use RICH margin so they won't catch it. I did not change it. A human should decide.
3. I could not run compound shell commands (heredocs, `;` chains) because the allow-list hook blocked them. Use single commands and the Edit tool.

**Concerns:** invariants/test_position_limit_invariants.py fails now because enforcement is F2. Running all of invariants/ will show 1 failure until F2 lands.; Pre-existing: margin check rejects reducing orders (AGENTS.md rule 5). Needs a human decision.

**Blocked tool calls:**
- Bash `ls; ls tests; grep -rn "class Account" -A12 --include=*.py . | grep -v invariants | head -40; grep -rn "class Reason" -A12 --include=*.py . | head -30` — command not on the allow-list: 'ls; ls tests; grep -rn "class Account" -A12 --include=*.py . | grep -v invariants | head -40; grep -rn "class Reason" -A12 --include=*.py . | head -30'
- Bash `cat >> tests/test_validation.py <<'EOF'


def test_account_position_limits_default_empty_and_not_shared():
    a = Account("A1", D("1"))
    b = Account("A2", D("1"))
    assert a.position_limits == {` — command not on the allow-list: 'cat >> tests/test_validation.py <<\'EOF\'\n\n\ndef test_account_position_limits_default_empty_and_not_shared():\n    a = Account("A1", D("1"))\n    b = Account("A2", D("1"))\n    assert a.position_limits == {}\n    assert a.position_limits is not b.position_limits\n    a.position_limits["XAUUSD"] = D("5")\n    assert b.position_limits == {}\n\n\ndef test_account_positional_and_keyword_limits():\n    a = Account("A1", D("1"), {"XAUUSD": D("-2")}, position_limits={"XAUUSD": D("5")})\n    assert a.positions == {"XAUUSD": D("-2")}\n    assert a.position_limits == {"XAUUSD": D("5")}\n\n\ndef test_position_limit_reason_code():\n    assert Reason.POSITION_LIMIT.value == "POSITION_LIMIT"\n\n\ndef test_limits_do_not_change_behavior_yet():\n    # F1 adds the field only; BUY and SELL, long and short, still accepted.\n    for side, pos in [(Side.BUY, "3"), (Side.SELL, "3"), (Side.BUY, "-3"), (Side.SELL, "-3")]:\n        a = Account("A1", D("100000"), {"XAUUSD": D(pos)}, position_limits={"XAUUSD": D("1")})\n        assert validate_order(Order("XAUUSD", side, D("1.00"), D("2400.00")), a).accepted\nEOF\npython -m pytest -q tests/ ; python -m pytest -q invariants/'

## Session 2 — F2: Enforce per-symbol position limit in validate_order for exposure-increasing orders only (sonnet, $0.12)
**Status:** done

**Summary:** Added _breaches_position_limit(order, account) in orders/validation.py. It breaches only if abs(new) > abs(current) and abs(new) > limit. A missing limit means no limit, and a missing position counts as Decimal(0). validate_order calls it after the margin check and returns Reason.POSITION_LIMIT. It is pure and uses only Decimal. I added 7 tests and rewrote one obsolete F1 test. tests/ passes (17) and invariants/ passes (7), including test_position_limit_invariants.py.

**Tests added:** test_limit_flat_buy_and_sell_at_and_over, test_limit_long_and_short_increase, test_limit_reducing_while_over_limit_allowed, test_limit_flip_across_zero, test_limit_flip_reducing_abs_while_over_limit_allowed, test_no_limit_or_other_symbol_limit, test_limit_does_not_mutate, test_over_limit_account_reducing_accepted_increasing_rejected (replaces F1 test_limits_do_not_change_behavior_yet)

**Notes for next session:** F2 is done. The change is in orders/validation.py: the helper _breaches_position_limit, plus one check after the margin check. Both test suites pass.

TRAPS and decisions:
1. The F1 test test_limits_do_not_change_behavior_yet asserted that limits were ignored. It failed once enforcement landed, so I replaced it with test_over_limit_account_reducing_accepted_increasing_rejected.
2. I put the limit check AFTER the margin check. An order that fails both reports INSUFFICIENT_MARGIN. The acceptance criteria don't specify the order, so a human may want to confirm it.
3. A flip that ends with a smaller abs position than it started with is allowed even when the account is over the limit (300 -> -200 with limit 100). This follows the specified rule, abs(new) > abs(current), but a human may want to confirm it.
4. Unresolved since F1: the margin check still rejects reducing and closing orders with INSUFFICIENT_MARGIN when free margin is low. That conflicts with AGENTS.md rule 5. I did not change it, and the invariants use RICH margin so they don't catch it. A human needs to decide.
5. The shell allow-list hook blocks compound commands and some plain ones such as head. Use the Read and Edit tools, and run single commands.

**Concerns:** Pre-existing: the margin check rejects reducing and closing orders, which conflicts with AGENTS.md rule 5. It needs a human decision.; Check order (margin before position limit) and the over-limit flip that shrinks abs position are my reading of the spec, so a human should confirm them.

**Blocked tool calls:**
- Bash `head -30 tests/test_validation.py` — command not on the allow-list: 'head -30 tests/test_validation.py'

## Session 3 — FIX: Fix gate 1 failures (sonnet, $0.10)
**Status:** done

**Summary:** Fixed the reviewer's high finding in orders/validation.py. An order that crosses zero is now judged on the new opposite position alone, which must be within the limit. Non-crossing orders keep the old rule: breach only if abs(new) > abs(current) and abs(new) > limit. I replaced the test that allowed an over-limit flip with tests for rejected over-limit flips and for flips landing exactly on the limit. tests/ passes (18) and invariants/ passes (9), including inv 5 and 6 on flips.

**Tests added:** test_limit_flip_into_over_limit_rejected (replaces test_limit_flip_reducing_abs_while_over_limit_allowed), test_limit_flip_landing_exactly_on_limit_allowed

**Notes for next session:** What I did: in _breaches_position_limit (orders/validation.py), a flip through zero (current * new < 0) now breaches if abs(new) > limit, whatever happens to the abs size. Non-flip orders are judged as before. This matches invariants inv 5 and 6, which were already in the repo, so I did not edit them. In tests/test_validation.py I replaced the obsolete test that allowed an over-limit flip with test_limit_flip_into_over_limit_rejected. I also added test_limit_flip_landing_exactly_on_limit_allowed, which covers BUY and SELL, long and short, and an exact close. Both suites pass (18 and 9). Left for a human: (1) The margin check rejects reducing and closing orders, which conflicts with AGENTS.md rule 5. The invariants use RICH margin so they don't catch it. I left the behavior alone. (2) Limit values are not validated, so a negative limit blocks every increasing order. (3) The limit check runs after the margin check. Trap: the shell allow-list hook blocks git log and compound commands, so use single commands plus the Read and Edit tools.

**Concerns:** Pre-existing: the margin check runs on reducing and closing orders and can reject them with INSUFFICIENT_MARGIN. This conflicts with AGENTS.md rule 5 and is still unfixed. I did not change it, so a human needs to decide.; Limit values are not validated. A negative limit blocks every increasing order. The contract doesn't cover this, so I left it as is.; The limit check runs after the margin check, so an order that fails both reports INSUFFICIENT_MARGIN.

**Blocked tool calls:**
- Bash `git log --stat -3 --format=%h\ %s` — command not on the allow-list: 'git log --stat -3 --format=%h\\ %s'

