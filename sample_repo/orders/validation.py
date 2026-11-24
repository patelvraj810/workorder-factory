"""Pre-trade order validation.

Rules:
- Pure function: never mutate the account or the order.
- Check order matters: cheap structural checks first, risk checks last.
- Every rejection returns exactly one Reason.
"""
from __future__ import annotations

from decimal import Decimal

from orders.models import INSTRUMENTS, Account, Order, Reason, Side, ValidationResult


def _is_multiple(value: Decimal, step: Decimal) -> bool:
    return (value % step) == 0


def signed_qty(order: Order) -> Decimal:
    """Quantity with direction: BUY is +qty, SELL is -qty."""
    return order.qty if order.side is Side.BUY else -order.qty


def required_margin(order: Order) -> Decimal:
    inst = INSTRUMENTS[order.symbol]
    return order.qty * order.price * inst.margin_rate


def validate_order(order: Order, account: Account) -> ValidationResult:
    inst = INSTRUMENTS.get(order.symbol)
    if inst is None:
        return ValidationResult.reject(Reason.UNKNOWN_INSTRUMENT)
    if order.qty <= 0:
        return ValidationResult.reject(Reason.NON_POSITIVE_QTY)
    if not _is_multiple(order.qty, inst.lot_step):
        return ValidationResult.reject(Reason.BAD_LOT_STEP)
    if order.price <= 0 or not _is_multiple(order.price, inst.tick_size):
        return ValidationResult.reject(Reason.BAD_PRICE_TICK)
    if required_margin(order) > account.free_margin:
        return ValidationResult.reject(Reason.INSUFFICIENT_MARGIN)
    return ValidationResult.ok()
