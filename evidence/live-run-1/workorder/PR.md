# WO-001: Per-symbol position limits

**Owning team (from catalog-info.yaml):** group:trading-platform-leads · **Risk tier:** T2 — the approver should come from the owning team.

## Intent (from vraj)
Max absolute net position per symbol. Reducing or closing a position is always allowed.

## Features
| ID | Title | Status | Sessions |
|---|---|---|---|
| F1 | Add position_limits field to Account and Reason.POSITION_LIMIT (no behavior change) | done | session-1 |
| F2 | Enforce per-symbol position limit in validate_order for exposure-increasing orders only | done | session-2 |

## Gate
Gate on `142acb1340`: **PASS**

- [PASS] **protected paths**: changed only by humans: invariants/test_position_limit_invariants.py (vraj@example.com) — needs CODEOWNERS approval
- [PASS] **test config tamper**: no agent changes to conftest/pytest config
- [PASS] **diff size**: 195 changed lines in 4 files (limit 250)
- [PASS] **secret scan**: no secrets in added lines
- [PASS] **no float on money**: no float in orders/
- [PASS] **SAST (bandit)**: 2 files, 0 low-severity notes
- [PASS] **invariants (human-owned)**: 9 passed in 3.04s
- [PASS] **unit tests**: 18 passed in 0.01s
- [PASS] **independent reviewer agent**: approve: 0 blocking finding(s), 4 total

Reviewer (opus) verdict: **approve** — The position-limit logic in orders/validation.py:29-43 is correct for every case I traced: BUY and SELL, long and short, flat, exact boundaries, accounts already over their limit, flips across zero, a missing limit and a missing position. It uses Decimal only, does not change its inputs, and every rejection returns the single Reason.POSITION_LIMIT. Reducing or closing an over-limit position is never blocked by the limit check. A flip is checked only on the size of the new opposite position, which matches the human's flip rule (invariants 5 and 6). The tests cover the risky cases on both sides, not just the happy path. I found no high-severity issues. Remaining notes: the existing margin check can still block a reducing order (out of scope for this order, but it conflicts with AGENTS rule 5), bad limit values like NaN or negatives aren't handled, and the human change to the invariants file still needs CODEOWNERS approval.
  - (medium) orders/validation.py: This was not introduced by this diff, but it affects the intent. The existing INSUFFICIENT_MARGIN check (line 56) runs before the position-limit check and applies to every order, including ones that reduce or close a position. So a trader can still be blocked from closing, which conflicts with AGENTS.md rule 5 and the intent "Reducing or closing a position is always allowed". Invariant 2 only checks that the reason is not POSITION_LIMIT, so it doesn't catch this. Failing input: Account('A1', free_margin=D('10'), {'XAUUSD': D('5')}); Order('XAUUSD', SELL, D('5.00'), D('2400.00')) -> rejected INSUFFICIENT_MARGIN, even though it closes the position
  - (low) orders/validation.py: Limit values are not validated. A Decimal('NaN') limit makes `abs(new) > limit` raise decimal.InvalidOperation, so validate_order throws instead of returning a Reason. A negative limit quietly blocks every increase. A limit of 0 (flat-only) has no test. Failing input: position_limits={'XAUUSD': Decimal('NaN')}, Order('XAUUSD', BUY, D('1.00'), D('2400.00')) -> InvalidOperation exception
  - (low) tests/test_validation.py: The `_lim` helper always puts the position on 'XAUUSD' and ignores `sym`. No test has an existing position in a different symbol from the order's, or a limit on another symbol while there's an open position. The logic is right, but the coverage is thinner than it looks.
  - (low) invariants/test_position_limit_invariants.py: This protected file was added, and then changed again in d549085 ('flip rule invariant') after gate 1 failed. The gate says a human made the change, but it still needs CODEOWNERS approval. Changing an invariant after a failed gate is the pattern that most needs a human to confirm it was a deliberate business decision and not a weaker check.

## Agent sessions
| # | Feature | Agent | Status | Cost | Blocked calls | Commit |
|---|---|---|---|---|---|---|
| 1 | F1 | agent:sonnet | done | $0.11 | 2 | `549ea4a1` |
| 2 | F2 | agent:sonnet | done | $0.12 | 1 | `7e5e78f0` |
| 3 | FIX | agent:sonnet | done | $0.10 | 1 | `142acb13` |

## Change size
```
invariants/test_position_limit_invariants.py | 80 ++++++++++++++++++++++++
 orders/models.py                             |  3 +
 orders/validation.py                         | 19 ++++++
 tests/test_validation.py                     | 93 ++++++++++++++++++++++++++++
 4 files changed, 195 insertions(+)
```

## Provenance (FINOS SDLC Common Controls MI-23)
- **Agent steps recorded:** 8 (complete). Details: `wo provenance WO-001`
- **Accountable owner (author of record):** vraj
- **Code drafted by:** agents listed above (commit author `wo-agent`, trailers carry model + session)
- **Human-owned invariants:** `invariants/test_position_limit_invariants.py`
- **Approver:** pending — must be a human other than vraj
- **Total agent cost:** $0.70
- **Ledger head:** `b2915937ebf960a9` (verify with `wo ledger verify`)

## What the reviewer should look at
Read `.factory/WO-001/progress.md` for the session log, then focus on the risks the planner flagged.
