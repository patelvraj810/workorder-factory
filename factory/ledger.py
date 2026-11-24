"""Append-only, hash-chained, optionally signed audit ledger.

Each entry stores the hash of the previous entry. Editing or deleting any past
entry breaks the chain, and `verify()` reports where.

If WO_LEDGER_KEY is set, each entry is also signed (HMAC-SHA256). Then an entry
can't be forged even by someone who recomputes the hash chain.

Prototype: a local JSONL file and a shared-secret HMAC. Production: the same records
shipped to write-once storage (e.g. a bucket with retention lock), signed with a
KMS-held asymmetric key or Sigstore, so nobody — including the factory — can rewrite history.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _hash(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k not in ("hash", "sig")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _sign(key: str, digest: str) -> str:
    return hmac.new(key.encode(), digest.encode(), hashlib.sha256).hexdigest()


class Ledger:
    def __init__(self, path: Path, key: str | None = None):
        self.path = path
        self.key = key if key is not None else os.environ.get("WO_LEDGER_KEY") or None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def entries(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def head(self) -> str:
        entries = self.entries()
        return entries[-1]["hash"] if entries else GENESIS

    def append(self, event: str, work_order: str | None, actor: str, **data: Any) -> dict[str, Any]:
        entries = self.entries()
        entry = {
            "seq": len(entries),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "work_order": work_order,
            "actor": actor,
            "data": data,
            "prev_hash": entries[-1]["hash"] if entries else GENESIS,
        }
        entry["hash"] = _hash(entry)
        if self.key:
            entry["sig"] = _sign(self.key, entry["hash"])
        with self.path.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def verify(self) -> tuple[bool, int | None, str]:
        prev = GENESIS
        for i, e in enumerate(self.entries()):
            if e.get("prev_hash") != prev:
                return False, i, f"entry {i}: prev_hash does not match entry {i - 1}"
            if _hash(e) != e.get("hash"):
                return False, i, f"entry {i}: content was changed after it was written"
            if self.key and not hmac.compare_digest(e.get("sig", ""), _sign(self.key, e["hash"])):
                return False, i, f"entry {i}: signature missing or invalid"
            prev = e["hash"]
        return True, None, "chain intact" + (", all signatures valid" if self.key else " (unsigned)")

    def for_work_order(self, wo_id: str) -> list[dict[str, Any]]:
        return [e for e in self.entries() if e.get("work_order") == wo_id]
