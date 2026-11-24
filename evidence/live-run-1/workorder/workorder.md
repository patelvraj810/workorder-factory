# WO-001: Per-symbol position limits

**Requested by:** vraj  
**Base:** `main`  
**Branch:** `wo/WO-001`

## Intent
Max absolute net position per symbol. Reducing or closing a position is always allowed.

## Human-owned invariants for this change
- `invariants/test_position_limit_invariants.py`
