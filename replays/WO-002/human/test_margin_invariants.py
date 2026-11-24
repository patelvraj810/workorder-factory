"""HUMAN-OWNED INVARIANTS for WO-002 (margin must never block reducing or closing).

Found by the agents in live run 1 (planner and reviewer both flagged it): the margin check runs on
every order, so a trader with little free margin cannot close a position. That breaks AGENTS.md rule 5.

Business rule (decided by the requester):
- Reducing or closing a position never needs margin.
- Opening new exposure needs margin for the NEW exposure only:
    same side, getting bigger -> margin for the increase
    flip through zero         -> margin for the new opposite position only
Uses the existing required_margin() rate: qty * price * margin_rate.
"""
from decimal import Decimal

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from orders.models import INSTRUMENTS, Account, Order, Reason, Side
from orders.validation import signed_qty, validate_order

SYMBOL = "XAUUSD"
PRICE = Decimal("2400.00")
RATE = INSTRUMENTS[SYMBOL].margin_rate
lot_qty = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("200"), places=2, allow_nan=False)
position = st.decimals(min_value=Decimal("-300"), max_value=Decimal("300"), places=2, allow_nan=False)
free_margin = st.decimals(min_value=Decimal("0"), max_value=Decimal("50000"), places=2, allow_nan=False)
side = st.sampled_from([Side.BUY, Side.SELL])


def opened_exposure(current: Decimal, new: Decimal) -> Decimal:
    if current != 0 and new != 0 and (current > 0) != (new > 0):
        return abs(new)                      # flip: only the new side is new exposure
    return max(abs(new) - abs(current), Decimal("0"))


@settings(max_examples=600, deadline=None)
@given(position, lot_qty, free_margin)
def test_inv_margin_1_reducing_or_closing_never_needs_margin(pos, qty, free):
    assume(pos != 0 and qty <= abs(pos))
    sd = Side.SELL if pos > 0 else Side.BUY
    r = validate_order(Order(SYMBOL, sd, qty, PRICE), Account("A1", free, {SYMBOL: pos}))
    assert r.reason is not Reason.INSUFFICIENT_MARGIN


@settings(max_examples=600, deadline=None)
@given(position, lot_qty, side, free_margin)
def test_inv_margin_2_accepted_orders_have_margin_for_their_new_exposure(pos, qty, sd, free):
    order = Order(SYMBOL, sd, qty, PRICE)
    new = pos + signed_qty(order)
    r = validate_order(order, Account("A1", free, {SYMBOL: pos}))
    if r.accepted:
        assert opened_exposure(pos, new) * PRICE * RATE <= free


def test_inv_margin_3_closing_with_zero_free_margin_is_allowed():
    r = validate_order(Order(SYMBOL, Side.SELL, Decimal("5.00"), PRICE), Account("A1", Decimal("0"), {SYMBOL: Decimal("5")}))
    assert r.accepted


def test_inv_margin_4_opening_with_zero_free_margin_is_rejected():
    r = validate_order(Order(SYMBOL, Side.BUY, Decimal("1.00"), PRICE), Account("A1", Decimal("0"), {}))
    assert r.reason is Reason.INSUFFICIENT_MARGIN
