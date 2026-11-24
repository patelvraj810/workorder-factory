"""Example-based unit tests. Agents may add to and edit these."""
from decimal import Decimal as D

from orders.models import Account, Order, Reason, Side
from orders.validation import validate_order


def acct(margin="100000", positions=None):
    return Account("A1", D(margin), positions or {})


def test_valid_order_accepted():
    r = validate_order(Order("XAUUSD", Side.BUY, D("1.00"), D("2400.00")), acct())
    assert r.accepted


def test_unknown_instrument():
    r = validate_order(Order("DOGE", Side.BUY, D("1"), D("1")), acct())
    assert r.reason is Reason.UNKNOWN_INSTRUMENT


def test_zero_qty_rejected():
    r = validate_order(Order("XAUUSD", Side.BUY, D("0"), D("2400.00")), acct())
    assert r.reason is Reason.NON_POSITIVE_QTY


def test_bad_lot_step():
    r = validate_order(Order("EURUSD", Side.BUY, D("1500"), D("1.08000")), acct())
    assert r.reason is Reason.BAD_LOT_STEP


def test_bad_tick():
    r = validate_order(Order("XAUUSD", Side.BUY, D("1.00"), D("2400.005")), acct())
    assert r.reason is Reason.BAD_PRICE_TICK


def test_insufficient_margin():
    r = validate_order(Order("XAUUSD", Side.BUY, D("100.00"), D("2400.00")), acct("10"))
    assert r.reason is Reason.INSUFFICIENT_MARGIN
