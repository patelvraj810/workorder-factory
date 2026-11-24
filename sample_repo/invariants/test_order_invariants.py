"""HUMAN-OWNED INVARIANTS. Agents must not edit this folder.

Owner: trading-platform team (see CODEOWNERS).
These are property-based tests (Hypothesis): they generate thousands of orders
and check rules that must hold for every one of them, not just a few examples.
"""
from copy import deepcopy
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from orders.models import INSTRUMENTS, Account, Order, Reason, Side
from orders.validation import validate_order

symbols = st.sampled_from(sorted(INSTRUMENTS) + ["UNKNOWN"])
sides = st.sampled_from([Side.BUY, Side.SELL])
qtys = st.decimals(min_value=Decimal("-10"), max_value=Decimal("5000"), places=2, allow_nan=False)
prices = st.decimals(min_value=Decimal("-1"), max_value=Decimal("5000"), places=5, allow_nan=False)
margins = st.decimals(min_value=Decimal("0"), max_value=Decimal("1000000"), places=2, allow_nan=False)
positions = st.decimals(min_value=Decimal("-500"), max_value=Decimal("500"), places=2, allow_nan=False)


@st.composite
def accounts(draw):
    pos = {s: draw(positions) for s in draw(st.sets(st.sampled_from(sorted(INSTRUMENTS))))}
    return Account("A1", draw(margins), pos)


orders = st.builds(Order, symbols, sides, qtys, prices)


@settings(max_examples=400, deadline=None)
@given(orders, accounts())
def test_inv1_accepted_orders_are_well_formed(order, account):
    """An accepted order always has a known symbol, qty > 0 and valid lot/tick."""
    r = validate_order(order, account)
    if r.accepted:
        inst = INSTRUMENTS[order.symbol]
        assert order.qty > 0
        assert order.qty % inst.lot_step == 0
        assert order.price > 0 and order.price % inst.tick_size == 0


@settings(max_examples=400, deadline=None)
@given(orders, accounts())
def test_inv2_rejections_always_have_a_reason(order, account):
    r = validate_order(order, account)
    assert r.accepted == (r.reason is None)
    if not r.accepted:
        assert isinstance(r.reason, Reason)


@settings(max_examples=200, deadline=None)
@given(orders, accounts())
def test_inv3_validation_is_pure(order, account):
    """Validation never mutates the account and is deterministic."""
    before = deepcopy(account)
    r1 = validate_order(order, account)
    r2 = validate_order(order, account)
    assert account == before
    assert r1 == r2
