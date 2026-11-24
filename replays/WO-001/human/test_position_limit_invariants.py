"""HUMAN-OWNED INVARIANTS for work order WO-001 (position limits).

Written by the requesting engineer BEFORE any agent runs. Agents must not edit.

Contract the agent must implement:
- Account.position_limits: dict[str, Decimal]  (max absolute net position per symbol;
  a missing symbol means "no limit")
- Reason.POSITION_LIMIT
"""
from decimal import Decimal

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from orders.models import Account, Order, Reason, Side
from orders.validation import signed_qty, validate_order

SYMBOL = "XAUUSD"  # lot step 0.01, tick 0.01
lot_qty = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("200"), places=2, allow_nan=False)
position = st.decimals(min_value=Decimal("-300"), max_value=Decimal("300"), places=2, allow_nan=False)
limit = st.decimals(min_value=Decimal("1"), max_value=Decimal("250"), places=2, allow_nan=False)
side = st.sampled_from([Side.BUY, Side.SELL])
RICH = Decimal("1000000000")  # enough margin that margin never interferes
PRICE = Decimal("2400.00")


def make(pos, lim):
    return Account("A1", RICH, {SYMBOL: pos}, position_limits={SYMBOL: lim})


@settings(max_examples=500, deadline=None)
@given(position, lot_qty, side, limit)
def test_inv_limit_1_accepted_orders_never_end_beyond_limit_when_increasing(pos, qty, sd, lim):
    """If an accepted order increases exposure, the new position is within the limit."""
    order = Order(SYMBOL, sd, qty, PRICE)
    new_pos = pos + signed_qty(order)
    r = validate_order(order, make(pos, lim))
    if r.accepted and abs(new_pos) > abs(pos):
        assert abs(new_pos) <= lim


@settings(max_examples=500, deadline=None)
@given(position, lot_qty, limit)
def test_inv_limit_2_reducing_orders_are_never_blocked_by_the_limit(pos, qty, lim):
    """A trader must ALWAYS be able to reduce or close a position, even when over the limit."""
    assume(pos != 0)
    assume(qty <= abs(pos))
    sd = Side.SELL if pos > 0 else Side.BUY  # opposite side = reducing
    r = validate_order(Order(SYMBOL, sd, qty, PRICE), make(pos, lim))
    assert r.reason is not Reason.POSITION_LIMIT


@settings(max_examples=300, deadline=None)
@given(position, lot_qty, side)
def test_inv_limit_3_no_limit_configured_means_no_limit_rejections(pos, qty, sd):
    acct = Account("A1", RICH, {SYMBOL: pos})
    r = validate_order(Order(SYMBOL, sd, qty, PRICE), acct)
    assert r.reason is not Reason.POSITION_LIMIT


def test_inv_limit_4_exactly_at_limit_is_allowed():
    r = validate_order(Order(SYMBOL, Side.BUY, Decimal("0.20"), PRICE), make(Decimal("0.10"), Decimal("0.30")))
    assert r.accepted
