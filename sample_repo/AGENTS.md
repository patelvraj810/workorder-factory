# AGENTS.md — order-validation service

Read this before you change anything. It applies to every agent and every human.

## What this service does
Pre-trade validation for orders. If it accepts a bad order, real money is at risk.
If it rejects a good order, a trader cannot trade (or cannot close a position).

## Hard rules
1. **Money and quantity are `Decimal`. Never `float`.** Not even for a comparison.
2. `validate_order` is a **pure function**. Do not mutate `account` or `order`.
3. Every rejection returns exactly one `Reason`. Add a new `Reason` if you need one.
4. **Never edit `invariants/`, `factory.toml`, `CODEOWNERS` or `.github/`.** These are
   human-owned. If an invariant looks wrong, stop and say so in your notes.
5. A trader must always be able to **reduce or close** a position. Risk checks only block
   orders that *increase* exposure.
6. Keep each change small. One feature per session.

## How to check your work
```bash
python -m pytest -q tests/        # unit tests (you may add tests here)
python -m pytest -q invariants/   # human-owned property tests (you may run, not edit)
```
Write tests for **both sides** (BUY and SELL) and for **long and short** positions.

## Style
- Small functions, type hints, no new dependencies without a human asking for them.
- Reason codes are UPPER_SNAKE_CASE strings.
