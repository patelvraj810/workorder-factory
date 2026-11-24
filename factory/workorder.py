"""Work order: the unit of work and the hand-off protocol between humans and agents.

A work order lives on its own branch, in its own worktree, with these files:

  .factory/<WO>/workorder.json   who asked, what, why, which invariants (human)
  .factory/<WO>/features.json    small slices of work + status (planner, then harness)
  .factory/<WO>/progress.md      append-only session notes (every session reads it first)
  .factory/<WO>/gates/gate-N.json  gate results, pinned to a commit
  .factory/<WO>/PR.md            the pull request text with provenance

Any agent (or human) can pick up a work order cold by reading these files.
That is what makes multi-session work survive context loss.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Feature:
    id: str
    title: str
    acceptance: list[str]
    status: str = "todo"  # todo | done | blocked
    sessions: list[str] = field(default_factory=list)


@dataclass
class WorkOrder:
    id: str
    title: str
    intent: str
    requester: str
    base_branch: str
    branch: str
    invariants: list[str] = field(default_factory=list)
    status: str = "open"  # open | planned | in_progress | gate_failed | gate_passed | approved | merged
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    approver: str | None = None


class WorkOrderStore:
    """Reads and writes work-order files inside a worktree."""

    def __init__(self, worktree: Path, wo_id: str):
        self.worktree = worktree
        self.dir = worktree / ".factory" / wo_id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "gates").mkdir(exist_ok=True)

    # -- work order --------------------------------------------------------
    def save_wo(self, wo: WorkOrder) -> None:
        (self.dir / "workorder.json").write_text(json.dumps(asdict(wo), indent=2) + "\n")
        inv = "\n".join(f"- `{p}`" for p in wo.invariants) or "- (none added)"
        (self.dir / "workorder.md").write_text(
            f"# {wo.id}: {wo.title}\n\n"
            f"**Requested by:** {wo.requester}  \n**Base:** `{wo.base_branch}`  \n**Branch:** `{wo.branch}`\n\n"
            f"## Intent\n{wo.intent}\n\n## Human-owned invariants for this change\n{inv}\n"
        )

    def load_wo(self) -> WorkOrder:
        return WorkOrder(**json.loads((self.dir / "workorder.json").read_text()))

    # -- features ----------------------------------------------------------
    def save_features(self, features: list[Feature]) -> None:
        (self.dir / "features.json").write_text(json.dumps([asdict(f) for f in features], indent=2) + "\n")

    def load_features(self) -> list[Feature]:
        p = self.dir / "features.json"
        return [Feature(**f) for f in json.loads(p.read_text())] if p.exists() else []

    def next_feature(self) -> Feature | None:
        """The next unfinished feature. A blocked feature is unfinished: the next session retries it
        with the notes the last one left (in progress.md)."""
        return next((f for f in self.load_features() if f.status in ("todo", "blocked")), None)

    # -- progress ----------------------------------------------------------
    def progress(self) -> str:
        p = self.dir / "progress.md"
        return p.read_text() if p.exists() else ""

    def append_progress(self, text: str) -> None:
        with (self.dir / "progress.md").open("a") as f:
            f.write(text.rstrip() + "\n\n")

    # -- gates -------------------------------------------------------------
    def gate_reports(self) -> list[dict[str, Any]]:
        files = sorted(self.dir.glob("gates/gate-*.json"), key=lambda p: int(p.stem.split("-")[1]))
        return [json.loads(p.read_text()) for p in files]

    def last_gate(self) -> dict[str, Any] | None:
        reports = self.gate_reports()
        return reports[-1] if reports else None

    def save_gate(self, report: dict[str, Any]) -> Path:
        n = len(self.gate_reports()) + 1
        report["number"] = n
        p = self.dir / "gates" / f"gate-{n}.json"
        p.write_text(json.dumps(report, indent=2) + "\n")
        return p
