"""Order and account models for a small, synthetic order-validation service.

All money and quantity values are Decimal. Never float.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Reason(str, Enum):
    """Every rejection must carry exactly one of these codes."""

    UNKNOWN_INSTRUMENT = "UNKNOWN_INSTRUMENT"
    NON_POSITIVE_QTY = "NON_POSITIVE_QTY"
    BAD_LOT_STEP = "BAD_LOT_STEP"
    BAD_PRICE_TICK = "BAD_PRICE_TICK"
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    lot_step: Decimal  # smallest tradable quantity increment
    tick_size: Decimal  # smallest price increment
    margin_rate: Decimal  # e.g. Decimal("0.05") = 5% margin


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Side
    qty: Decimal  # always positive; direction comes from `side`
    price: Decimal


@dataclass
class Account:
    account_id: str
    free_margin: Decimal
    # Signed net position per symbol. Positive = long, negative = short.
    positions: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason: Reason | None = None

    @staticmethod
    def ok() -> "ValidationResult":
        return ValidationResult(accepted=True)

    @staticmethod
    def reject(reason: Reason) -> "ValidationResult":
        return ValidationResult(accepted=False, reason=reason)


INSTRUMENTS: dict[str, Instrument] = {
    "EURUSD": Instrument("EURUSD", Decimal("1000"), Decimal("0.00001"), Decimal("0.0333")),
    "XAUUSD": Instrument("XAUUSD", Decimal("0.01"), Decimal("0.01"), Decimal("0.05")),
    "US500": Instrument("US500", Decimal("0.1"), Decimal("0.1"), Decimal("0.05")),
}
