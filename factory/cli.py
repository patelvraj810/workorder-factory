"""`wo` — the work-order CLI. The interface an engineer uses to start, steer and review agent work.

  wo new         human opens a work order: intent + human-owned invariants
  wo plan        planner agent splits it into small features
  wo run         implementer agent does ONE feature (or fixes gate failures) per session
  wo gate        deterministic checks + independent reviewer agent, pinned to a commit
  wo pr          pull request text with full provenance (only after a passing gate)
  wo provenance  FINOS MI-23 provenance record for every agent step
  wo approve     a DIFFERENT human approves (and optionally merges)
  wo kill/resume stop all agent sessions now / allow them again
  wo status / wo stats / wo ledger show|verify
"""
from __future__ import annotations

import argparse
import hashlib
import re
import json
import os
import shutil
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from factory import gitops
from factory.agents import PLAN_SCHEMA, REVIEW_SCHEMA, ROLE_TOOLS, SESSION_SCHEMA, AgentResult, get_backend, model_for, render
from factory.gate import Check, apply_review, reviewer_prompt, run_checks
from factory.ledger import Ledger
from factory.policy import load_policy_at, policy_sha
from factory.workorder import Feature, WorkOrder, WorkOrderStore

console = Console()
HARNESS = "wo-factory/0.2"
PROMPTS_DIR = Path(__file__).parent / "prompts"

# FINOS SDLC Common Controls, MI-23 "Agentic Processes Provenance": what every agent step must answer.
MI23_FIELDS = [
    ("agent, harness, model", "which agentic process operated"),
    ("context resources", "guidelines, policies, prompts used"),
    ("tools", "agentic tools involved"),
    ("session log", "how to access the session logs"),
    ("task", "task details handled"),
    ("result", "result of the process"),
    ("accountable human", "human accountable for the process"),
    ("start/end time", "session start and end timestamps"),
]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes()) if p.exists() else "missing"


# Environment variables that AI coding agents set in the shells they run commands in.
# Seeing one means "an agent is running this command", not "a human typed it".
AGENT_ENV_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "AI_AGENT", "CURSOR_AGENT", "CODEX_SANDBOX", "GEMINI_CLI")


def agent_session_marker() -> str | None:
    """Name of the first agent marker in this process's environment, or None (a human's own terminal)."""
    if os.environ.get("WO_CHANNEL") == "mcp":
        return "WO_CHANNEL=mcp"
    return next((m for m in AGENT_ENV_MARKERS if os.environ.get(m)), None)


def human_only(ctx: "Ctx", action: str, actor: str, wo_id: str | None = None) -> None:
    """Approving, resuming and adding rules are human acts. Refuse (and log) them inside an agent session.

    A speed bump, not a wall: an agent with a shell can clear its environment. The real control is
    identity from outside the agent (SSO, the Git host's review and branch protection).
    """
    marker = agent_session_marker()
    if not marker:
        return
    ctx.ledger.append("human_only.refused", wo_id, actor, action=action, marker=marker)
    raise SystemExit(f"Refused: '{action}' must be done by a human in their own terminal, not by an AI agent "
                     f"(detected {marker}). Open a normal terminal and run it yourself, or approve the pull "
                     f"request on GitHub. This attempt was logged.")


def _mark(c) -> str:
    return "[green]PASS[/]" if c.passed else ("[red]FAIL[/]" if c.blocking else "[yellow]WARN[/]")


def _ts(entry: dict) -> datetime:
    return datetime.fromisoformat(entry["ts"])


def _fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


# --- context -------------------------------------------------------------------------------

class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.repo = Path(args.repo).resolve()
        self.base_ref = getattr(args, "base", None) or gitops.default_base(self.repo)
        if hasattr(args, "base") and not args.base:
            args.base = self.base_ref
        self.policy = load_policy_at(self.repo, self.base_ref)  # trusted copy, never the branch under review
        self.ledger = Ledger(self.repo / ".factory" / "ledger.jsonl")
        self.backend_name = getattr(args, "backend", None) or os.environ.get("WO_BACKEND", "claude")
        rd = getattr(args, "replay_dir", None) or os.environ.get("WO_REPLAY_DIR")
        self.replay_dir = Path(rd).resolve() if rd else None
        rec = getattr(args, "record", None)
        self.record_dir = Path(rec).resolve() if rec else None
        self.channel = os.environ.get("WO_CHANNEL", "cli")

    def backend(self):
        return get_backend(self.backend_name, self.replay_dir)

    def harness(self) -> str:
        if self.backend_name == "claude":
            try:
                from importlib.metadata import version
                return f"{HARNESS} + claude-agent-sdk/{version('claude-agent-sdk')}"
            except Exception:  # noqa: BLE001
                return f"{HARNESS} + claude-agent-sdk"
        return f"{HARNESS} + replay"

    def worktree(self, wo_id: str) -> Path:
        return self.repo / ".factory" / "worktrees" / wo_id

    def store(self, wo_id: str) -> WorkOrderStore:
        wt = self.worktree(wo_id)
        if not wt.exists():
            raise SystemExit(f"Unknown work order {wo_id}")
        return WorkOrderStore(wt, wo_id)

    def events(self, wo_id: str, *names: str) -> list[dict]:
        return [e for e in self.ledger.for_work_order(wo_id) if e["event"] in names]

    def spent(self, wo_id: str) -> float:
        return sum(e["data"].get("cost_usd", 0) or 0 for e in self.ledger.for_work_order(wo_id))

    # -- stop controls --------------------------------------------------------------------
    def kill_file(self) -> Path:
        return self.repo / ".factory" / "KILL"

    def killed(self) -> dict | None:
        if self.policy.kill_switch:
            return {"source": "factory.toml (team-wide)", "reason": "kill_switch = true"}
        if self.kill_file().exists():
            info = json.loads(self.kill_file().read_text())
            info["source"] = f"wo kill by {info.get('by')}"
            return info
        return None

    def hand_back(self, wo: WorkOrder, reason: str, summary: str) -> None:
        wt = self.worktree(wo.id)
        store = WorkOrderStore(wt, wo.id)
        (store.dir / "HANDBACK.md").write_text(
            f"# {wo.id} handed back to {wo.requester}\n\n**Why:** {reason}\n\n"
            f"Agents have stopped. No pull request was opened.\n\n## Last gate\n{summary}\n\n"
            f"## What the agents tried\nSee `progress.md` (session log) and `sessions/`.\n\n"
            f"## Your options\n1. Fix it by hand on branch `{wo.branch}`, then run `wo gate {wo.id}`.\n"
            f"2. Rewrite the intent or split the work, and open a new work order.\n"
            f"3. Abandon it. The record stays in the ledger.\n")
        gitops.commit(wt, f"{wo.id}: handed back ({reason[:50]})", gitops.FACTORY_NAME, gitops.FACTORY_EMAIL,
                      {"Work-Order": wo.id, "Change-Type": "hand-back"})
        self.ledger.append("workorder.handed_back", wo.id, "factory", reason=reason, to=wo.requester,
                           summary_path=f".factory/{wo.id}/HANDBACK.md")
        console.print(f"[bold red]STOP LIMIT:[/] {reason}. Agents stopped. Handed back to {wo.requester}. "
                      f"No PR. See .factory/{wo.id}/HANDBACK.md")

    # -- durable work orders (idea from Temporal: survive crashes, resume from the last good step) --
    def recover(self, wo: WorkOrder) -> list[int]:
        """Find agent sessions that never finished (process died) or failed, keep their leftovers as
        evidence, put the sandbox back to the last good commit, and record what happened.

        Uncommitted changes are only discarded when they belong to a broken agent session.
        If the last agent session closed cleanly, leftover changes are a human's work: refuse, never wipe.
        """
        wt = self.worktree(wo.id)
        events = self.ledger.for_work_order(wo.id)
        closed = {e["data"]["started_seq"]: e for e in events
                  if e["event"] in ("plan.created", "session.completed", "review.completed", "session.failed", "session.abandoned") and "started_seq" in e["data"]}
        started = [e for e in events if e["event"] == "session.started"]
        orphans = [s for s in started if s["seq"] not in closed]
        recovered = {e["data"]["started_seq"] for e in events if e["event"] == "workorder.recovered"}
        broken = [s for s in started if s["seq"] in closed and closed[s["seq"]]["event"] == "session.failed"
                  and s["seq"] not in recovered]
        gitops.exclude_caches(wt)  # also covers work orders opened before this existed
        dirty = gitops.git(wt, "status", "--porcelain")
        if not orphans and not broken:
            if dirty:
                raise SystemExit(f"{wo.id} has uncommitted changes that no agent session owns:\n{dirty}\n"
                                 f"Commit them (they become human-authored) or discard them, then retry.")
            return []
        for s in orphans:
            log_rel = f".factory/{wo.id}/sessions/{s['data'].get('key', s['seq'])}-abandoned.json"
            (wt / log_rel).parent.mkdir(parents=True, exist_ok=True)
            (wt / log_rel).write_text(json.dumps({"started_seq": s["seq"], "role": s["data"].get("role"),
                                                  "reason": "no completion record: the process died (crash, kill, power loss)"},
                                                 indent=2) + "\n")
            live = wt / f".factory/{wo.id}/sessions/{s['data'].get('key', s['seq'])}.tools.jsonl"
            self.ledger.append("session.abandoned", wo.id, "factory", started_seq=s["seq"], role=s["data"].get("role"),
                               result="abandoned", session_log=log_rel, tool_calls=_tool_counts(live),
                               tool_log=str(live.relative_to(wt)) if live.exists() else None)
        evidence = ""
        if dirty:
            gitops.git(wt, "add", "-A", "--", ".", ":(exclude).factory")
            leftover = gitops.git(wt, "diff", "--cached", "--", ".", ":(exclude).factory")
            gitops.git(wt, "reset", "-q")
            seqs = "-".join(str(x["seq"]) for x in orphans + broken)
            evidence = f".factory/{wo.id}/recovered/after-step-{seqs}.patch"
            # Put the sandbox back to the last good commit (sandbox only; never touches main).
            gitops.git(wt, "checkout", "-q", "--", ".")
            gitops.git(wt, "clean", "-fdq", "--", ".", ":(exclude).factory")
            (wt / evidence).parent.mkdir(parents=True, exist_ok=True)
            (wt / evidence).write_text(leftover + "\n")
        gitops.commit(wt, f"{wo.id}: recovered from {len(orphans)} abandoned / {len(broken)} failed session(s)",
                      gitops.FACTORY_NAME, gitops.FACTORY_EMAIL, {"Work-Order": wo.id, "Change-Type": "recovery"})
        for s in orphans + broken:
            self.ledger.append("workorder.recovered", wo.id, "factory", started_seq=s["seq"],
                               leftover_evidence=evidence or None)
        WorkOrderStore(wt, wo.id).append_progress(
            f"## Recovery\nSession step(s) {', '.join(str(x['seq']) for x in orphans + broken)} did not finish. "
            f"Half-done changes were discarded and kept as evidence"
            f"{' in `' + evidence + '`' if evidence else ''}. Continue from the last good commit.")
        gitops.commit(wt, f"{wo.id}: progress note after recovery", gitops.FACTORY_NAME, gitops.FACTORY_EMAIL,
                      {"Work-Order": wo.id, "Change-Type": "recovery"})
        console.print(f"[bold yellow]RECOVERED[/] {len(orphans)} abandoned + {len(broken)} failed session(s). "
                      f"Sandbox reset to last good commit." + (f" Leftovers kept: {evidence}" if evidence else ""))
        return [x["seq"] for x in orphans + broken]

    # -- the one way any agent runs: audit first, fail closed ------------------------------
    def agent_session(self, wo: WorkOrder, role: str, key: str, prompt: str, schema: dict,
                      task: dict) -> tuple[AgentResult, dict, str]:
        wt = self.worktree(wo.id)
        k = self.killed()
        if k:
            self.ledger.append("session.blocked", wo.id, "factory", role=role, reason=f"kill switch on: {k['source']}")
            raise SystemExit(f"Kill switch is on ({k['source']}: {k.get('reason', '')}). No agent session started.")
        spent = self.spent(wo.id)
        if spent >= self.policy.max_workorder_budget_usd:
            self.hand_back(wo, f"budget reached (${spent:.2f} of ${self.policy.max_workorder_budget_usd:.2f})",
                           "Budget stop, not a gate failure.")
            raise SystemExit(1)

        self.recover(wo)  # durable work orders: clean up after any crashed or failed session first
        prompt_file = PROMPTS_DIR / f"{role}.md"
        provenance = {
            "role": role,
            "key": key,
            "harness": self.harness(),
            "model": model_for(role),
            "tools_allowed": ROLE_TOOLS[role],
            "context": {
                "AGENTS.md": sha256_file(wt / "AGENTS.md"),
                "CLAUDE.md": sha256_file(wt / "CLAUDE.md"),
                f"factory.toml@{wo.base_branch}": policy_sha(self.repo, wo.base_branch),
                f"prompts/{role}.md": sha256_file(prompt_file),
                "rendered_prompt": sha256_bytes(prompt.encode()),
            },
            "task": {"work_order": wo.id, "branch": wo.branch, "base": wo.base_branch,
                     "head_before": gitops.head(wt), **task},
            "accountable_human": wo.requester,
            "channel": self.channel,
            "budget_usd": self.policy.max_session_budget_usd,
        }
        # AUDIT FIRST. If the record can't be written, the agent never starts.
        try:
            start = self.ledger.append("session.started", wo.id, f"agent:{model_for(role)}", **provenance)
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"Audit record could not be written ({e}). Agent session NOT started.")
        backend = self.backend()
        live_rel = f".factory/{wo.id}/sessions/{key}.tools.jsonl"
        backend.live_log = wt / live_rel
        try:
            res = backend.run(role, prompt, wt, schema, self.policy, replay_key=key)
        except BaseException as e:
            log_rel = f".factory/{wo.id}/sessions/{key}-failed.json"
            (wt / log_rel).parent.mkdir(parents=True, exist_ok=True)
            (wt / log_rel).write_text(json.dumps({"role": role, "started_seq": start["seq"], "error": str(e)[:2000],
                                                  "error_type": type(e).__name__}, indent=2) + "\n")
            self.ledger.append("session.failed", wo.id, f"agent:{model_for(role)}", started_seq=start["seq"],
                               role=role, result="failed", error=str(e)[:500], session_log=log_rel,
                               tool_calls=_tool_counts(wt / live_rel))
            raise
        log_rel = f".factory/{wo.id}/sessions/{key}.json"
        (wt / log_rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / log_rel).write_text(json.dumps({
            "role": role, "model": res.model, "harness": provenance["harness"], "session_id": res.session_id,
            "started_seq": start["seq"], "cost_usd": res.cost_usd,
            "tokens": {"in": res.input_tokens, "out": res.output_tokens},
            "tool_calls": res.tool_log, "denied": res.denials, "output": res.structured}, indent=2) + "\n")
        return res, start, log_rel

    def closing(self, res: AgentResult, start: dict, log_rel: str, **extra) -> dict:
        """Common MI-23 fields for the event that closes an agent session."""
        return {"started_seq": start["seq"], "model": res.model, "session_id": res.session_id,
                "session_log": log_rel, "tool_calls": dict(Counter(t["tool"] for t in res.tool_log)),
                "denials": res.denials, "cost_usd": res.cost_usd,
                "tokens": {"in": res.input_tokens, "out": res.output_tokens}, **extra}

    def record(self, key: str, result: AgentResult, patch: str | None = None) -> None:
        if not self.record_dir:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        meta = {"model": result.model, "cost_usd": result.cost_usd, "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens, "recorded_from": "live run"}
        (self.record_dir / f"{key}.json").write_text(json.dumps({"meta": meta, "output": result.structured}, indent=2))
        if patch:
            (self.record_dir / f"{key}.patch").write_text(patch + "\n")


def _tool_counts(live_log: Path) -> dict[str, int]:
    """Tool calls recorded on disk during a session (survives crashes)."""
    if not live_log.exists():
        return {}
    return dict(Counter(json.loads(ln)["tool"] for ln in live_log.read_text().splitlines() if ln.strip()))


def derive_status(ctx: Ctx, wo_id: str) -> str:
    status = "open"
    for e in ctx.ledger.for_work_order(wo_id):
        ev = e["event"]
        if ev == "plan.created":
            status = "planned"
        elif ev == "session.completed":
            status = "in progress"
        elif ev == "gate.result":
            status = "gate passed" if e["data"]["passed"] else "gate FAILED"
        elif ev == "gate.accepted":
            status = "gate passed"
        elif ev == "workorder.handed_back":
            status = "handed back"
        elif ev == "approval.granted":
            status = "approved"
        elif ev == "merged":
            status = "merged"
    return status


def agent_trailers(wo: WorkOrder, ctx: Ctx, **extra: str) -> dict[str, str]:
    t = {"Work-Order": wo.id, "Accountable-Owner": wo.requester}
    t.update(extra)
    t["Ledger-Prev"] = ctx.ledger.head()[:16]
    return t


# --- provenance (FINOS MI-23) --------------------------------------------------------------

def provenance(ctx: Ctx, wo_id: str) -> tuple[bool, list[dict], list[str]]:
    """Check every agent step has a complete MI-23 record, and every agent commit has a step."""
    events = ctx.ledger.for_work_order(wo_id)
    wt = ctx.worktree(wo_id)
    closers = {e["data"]["started_seq"]: e for e in events
               if e["event"] in ("plan.created", "session.completed", "review.completed", "session.failed", "session.abandoned")
               and "started_seq" in e["data"]}
    rows, problems = [], []
    for s in (e for e in events if e["event"] == "session.started"):
        d, c = s["data"], closers.get(s["seq"])
        cd = c["data"] if c else {}
        log_ok = bool(cd.get("session_log")) and (wt / cd["session_log"]).exists()
        have = {
            "agent, harness, model": bool(d.get("harness") and d.get("model")),
            "context resources": bool(d.get("context")),
            "tools": "tools_allowed" in d and "tool_calls" in cd,
            "session log": log_ok,
            "task": bool(d.get("task")),
            "result": bool(c) and bool(cd.get("result")),
            "accountable human": bool(d.get("accountable_human")),
            "start/end time": bool(c),
        }
        missing = [k for k, v in have.items() if not v]
        if missing:
            problems.append(f"step {s['seq']} ({d.get('role')}): missing {', '.join(missing)}")
        rows.append({"seq": s["seq"], "role": d.get("role"), "model": cd.get("model", d.get("model")),
                     "harness": d.get("harness"), "start": s["ts"], "end": c["ts"] if c else "",
                     "result": cd.get("result", "never closed") if c else "never closed", "commit": cd.get("commit", ""),
                     "log": cd.get("session_log", ""), "missing": missing})
    # Every agent-authored commit must trace back to a recorded session.
    wo = WorkOrderStore(wt, wo_id).load_wo()
    recorded = {e["data"].get("commit") for e in events if e["data"].get("commit")}
    agent_commits = gitops.git(wt, "log", f"--author={gitops.AGENT_EMAIL}", "--format=%H", f"{wo.base_branch}..HEAD").split()
    for sha in agent_commits:
        if sha not in recorded:
            problems.append(f"agent commit {sha[:10]} has no provenance record in the ledger")
    return not problems, rows, problems


# --- commands ------------------------------------------------------------------------------

TEMPLATES = Path(__file__).parent / "templates"
FACTORY_ROOT = Path(__file__).resolve().parent.parent
GITIGNORE_LINES = [".factory/worktrees/", ".factory/ledger.jsonl", ".factory/KILL", ".factory/proposals/", ".env"]
STACK_IGNORES = {"python": ["__pycache__/", "*.pyc", ".pytest_cache/", ".hypothesis/"],
                 "node": ["node_modules/"], "other": []}
STACKS = {
    "python": {"unit": "python -m pytest -q tests/", "inv": "python -m pytest -q invariants/",
               "bash": ["python -m pytest", "pytest", "git diff", "git status", "ls", "cat"]},
    "node": {"unit": "npm test", "inv": "",
             "bash": ["npm test", "npm run", "npx vitest", "npx jest", "git diff", "git status", "ls", "cat"]},
    "other": {"unit": "", "inv": "", "bash": ["git diff", "git status", "ls", "cat"]},
}


def detect_stack(repo: Path) -> str:
    if (repo / "package.json").exists():
        return "node"
    if any((repo / f).exists() for f in ("pyproject.toml", "requirements.txt", "setup.py", "pytest.ini")) \
            or list(repo.glob("*.py")):
        return "python"
    return "other"


def _write_if_missing(path: Path, text: str, made: list[str], skipped: list[str], repo: Path) -> None:
    rel = str(path.relative_to(repo))
    if path.exists():
        skipped.append(rel)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    made.append(rel)


def cmd_init(args) -> None:
    """Set up a repository for the factory. Only ever creates files that are missing."""
    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise SystemExit(f"No such folder: {repo}")
    odd = [part for part in repo.parts if part != part.strip()]
    if odd:
        raise SystemExit(f"The folder name {odd[0]!r} starts or ends with a space. AI agents tend to drop that "
                         f"space when they write file paths, so every write gets refused. Rename the folder "
                         f"(for example to {odd[0].strip().replace(' ', '-')!r}) and run wo init again.")
    if not (repo / ".git").exists():
        gitops.git(repo, "init", "-q", "-b", "main")
    user = args.user or gitops.git(repo, "config", "user.name", check=False) or os.environ.get("USER", "")
    if not user:
        raise SystemExit("Tell me who you are: wo init --user YOUR_NAME")
    stack = args.stack or detect_stack(repo)
    st = STACKS[stack]
    made: list[str] = []
    skipped: list[str] = []

    toml = (TEMPLATES / "factory.toml").read_text() \
        .replace("__ALLOWED_BASH__", json.dumps(st["bash"])) \
        .replace("__UNIT_TESTS__", st["unit"]).replace("__INVARIANTS__", st["inv"])
    toml = set_policy_values(toml, {**PRESETS[args.preset], "preset": args.preset})
    _write_if_missing(repo / "factory.toml", toml, made, skipped, repo)
    agents = (TEMPLATES / "AGENTS.md").read_text().replace("__PROJECT__", repo.name) \
        .replace("__UNIT_TESTS_DOC__", (st["unit"] + "   # unit tests (you may add tests)") if st["unit"]
                 else "# TODO (human): the command that runs the tests") \
        .replace("__INVARIANTS_DOC__", (st["inv"] + "   # human-owned rules (run, never edit)") if st["inv"]
                 else "# TODO (human): the command that runs invariants/")
    _write_if_missing(repo / "AGENTS.md", agents, made, skipped, repo)
    _write_if_missing(repo / "CLAUDE.md", (TEMPLATES / "CLAUDE.md").read_text(), made, skipped, repo)
    _write_if_missing(repo / "invariants" / "README.md", (TEMPLATES / "invariants_README.md").read_text(),
                      made, skipped, repo)
    _write_if_missing(repo / ".claude" / "skills" / "work-order" / "SKILL.md",
                      (TEMPLATES / "skill_work_order.md").read_text(), made, skipped, repo)

    gi = repo / ".gitignore"
    have = gi.read_text().splitlines() if gi.exists() else []
    missing = [ln for ln in GITIGNORE_LINES + STACK_IGNORES[stack] if ln not in have]
    if missing:
        gi.write_text("\n".join(have + ([""] if have and have[-1] else []) + ["# work-order factory"] + missing) + "\n")
        made.append(".gitignore (added factory lines)")

    server = {"command": sys.executable, "args": ["-m", "factory.mcp_server"],
              "env": {"WO_REPO": str(repo), "WO_USER": user, "WO_BACKEND": "claude",
                      "PYTHONPATH": str(FACTORY_ROOT)}}
    mcp_path = repo / ".mcp.json"
    mcp = json.loads(mcp_path.read_text()) if mcp_path.exists() else {}
    if "work-order-factory" in mcp.get("mcpServers", {}):
        skipped.append(".mcp.json")
    else:
        mcp.setdefault("mcpServers", {})["work-order-factory"] = server
        mcp_path.write_text(json.dumps(mcp, indent=2) + "\n")
        made.append(".mcp.json")

    committed = ""
    if made and not args.no_commit:
        paths = [m.split(" ")[0] for m in made]  # only our files: never sweep up the user's other edits
        email = gitops.git(repo, "config", "user.email", check=False) or gitops.human_email(user)
        gitops.git(repo, "add", "--", *paths)
        gitops.git(repo, "-c", f"user.name={gitops.FACTORY_NAME}", "-c", f"user.email={gitops.FACTORY_EMAIL}",
                   "commit", "-q", "--author", f"{user} <{email}>",
                   "-m", "Set up the work-order factory\n\nChange-Type: factory-setup", "--", *paths)
        committed = gitops.head(repo)
    Ledger(repo / ".factory" / "ledger.jsonl").append(
        "factory.initialized", None, user, stack=stack, preset=args.preset, created=made, kept=skipped,
        commit=committed or None, channel=os.environ.get("WO_CHANNEL", "cli"))

    console.print(f"[bold green]Factory set up[/] in {repo} (project type: {stack}, rules: {args.preset}).")
    for m in made:
        console.print(f"  [green]+[/] {m}")
    for k in skipped:
        console.print(f"  [dim]= {k} (already there, left as is)[/]")
    if committed:
        console.print(f"  Saved as commit {committed[:10]}.")
    console.print("\n[bold]Next:[/]")
    console.print("  1. Open AGENTS.md and fill in the TODO lines (what the project does, its rules).")
    if not st["unit"] or not st["inv"]:
        console.print("  2. Set the test commands under \\[gate] in factory.toml (until then the gate shows WARN).")
    console.print("  3. Restart Claude Code in this folder and approve the 'work-order-factory' server.")
    console.print("     Then just ask: \"Open a work order to ...\"")
    console.print("  [dim]Approving and adding rules (invariants) stay with you, in your own terminal.[/]")


def cmd_new(args) -> None:
    ctx = Ctx(args)
    if args.invariant:
        human_only(ctx, "add human-owned invariants", args.by)
    n = len([e for e in ctx.ledger.entries() if e["event"] == "workorder.created"]) + 1
    wo_id = f"WO-{n:03d}"
    branch = f"wo/{wo_id}"
    wt = ctx.worktree(wo_id)
    gitops.exclude_caches(ctx.repo)
    gitops.git(ctx.repo, "worktree", "add", "-q", "-b", branch, str(wt), args.base)

    inv_paths = []
    for src in args.invariant or []:
        src_p = Path(src).resolve()
        dest = wt / "invariants" / src_p.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy(src_p, dest)
        inv_paths.append(f"invariants/{src_p.name}")
    if inv_paths:
        gitops.commit(wt, f"{wo_id}: add human-owned invariants", args.by, gitops.human_email(args.by),
                      {"Work-Order": wo_id, "Change-Type": "human-invariant"})

    wo = WorkOrder(id=wo_id, title=args.title, intent=args.intent, requester=args.by,
                   base_branch=args.base, branch=branch, invariants=inv_paths)
    store = WorkOrderStore(wt, wo_id)
    store.save_wo(wo)
    store.append_progress(f"# Progress log — {wo_id}: {wo.title}\n\nOpened by {wo.requester}. No sessions yet.")
    sha = gitops.commit(wt, f"{wo_id}: open work order", args.by, gitops.human_email(args.by),
                        {"Work-Order": wo_id, "Change-Type": "work-order"})
    ctx.ledger.append("workorder.created", wo_id, args.by, title=wo.title, intent=wo.intent, base=args.base,
                      branch=branch, commit=sha, channel=ctx.channel,
                      invariants={p: sha256_file(wt / p) for p in inv_paths})
    console.print(f"[bold green]Opened {wo_id}[/] on branch [cyan]{branch}[/] (sandbox: {wt.relative_to(ctx.repo)})")
    console.print(f"Next: [bold]wo plan {wo_id}[/]")


# --- Project rules: presets, plain-English descriptions, safe edits ----------------------------------------

PRESETS = {
    "practice": {"blocking_severity": "off", "max_gate_failures": 5, "max_diff_lines": 400,
                 "require_independent_approver": False},
    "normal": {"blocking_severity": "high", "max_gate_failures": 3, "max_diff_lines": 250,
               "require_independent_approver": True},
    "money-critical": {"blocking_severity": "medium", "max_gate_failures": 3, "max_diff_lines": 200,
                       "require_independent_approver": True},
}
PRESET_INFO = {
    "practice": "For learning and side projects. The reviewer AI gives advice but never stops a change. You can approve your own work.",
    "normal": "For most team projects. Serious concerns from the reviewer AI stop a change. Someone else must approve.",
    "money-critical": "For code that moves money or orders. Medium concerns stop a change too, changes must be smaller, and someone else must approve.",
}
# key -> (toml section, short label, plain-English meaning, kind, choices). Kind "ro" = shown, not editable here.
RULE_INFO = {
    "blocking_severity": ("review", "Reviewer blocks on", "How serious a finding by the reviewer agent must be to stop the change. 'off' = it only advises.", "choice", ["low", "medium", "high", "off"]),
    "require_independent_approver": ("review", "Someone else must approve", "The person who asked for a change can't approve it. Turn off only if you work alone.", "bool", None),
    "max_gate_failures": ("policy", "Tries before hand-back", "After this many failed checks in a row, agents stop and the work comes back to you.", "int", None),
    "max_diff_lines": ("policy", "Biggest agent change (lines)", "Agent changes bigger than this fail the check. Your own rule files don't count.", "int", None),
    "max_session_budget_usd": ("policy", "Budget per agent session ($)", "One agent session stops when it reaches this cost.", "float", None),
    "max_workorder_budget_usd": ("policy", "Budget per work order ($)", "The whole work order is handed back when it reaches this cost.", "float", None),
    "unit_test_cmd": ("gate", "Unit test command", "How the factory runs this project's tests. Empty = WARN.", "str", None),
    "invariant_cmd": ("gate", "Rules (invariants) command", "How the factory runs your must-never-break rules. Empty = WARN.", "str", None),
    "protected_paths": ("policy", "Files agents can never change", "Change these in factory.toml with a reviewed commit.", "ro", None),
    "allowed_bash": ("policy", "Commands agents may run", "One simple command at a time; chaining is always blocked.", "ro", None),
    "money_paths": ("gate", "Money folders (no float)", "Any float added in these folders fails the check.", "ro", None),
}
TOML_KEY = {"unit_test_cmd": "unit_tests", "invariant_cmd": "invariants"}


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(v)


def set_policy_values(text: str, changes: dict) -> str:
    """Edit key = value lines in factory.toml text, keeping comments and layout. Adds missing keys."""
    sections = {"preset": "policy", **{k: v[0] for k, v in RULE_INFO.items()}}
    for key, val in changes.items():
        tkey, section = TOML_KEY.get(key, key), sections.get(key, "policy")
        line = f"{tkey} = {_toml_value(val)}"
        pat = re.compile(rf"^{re.escape(tkey)}\s*=.*$", re.M)
        if pat.search(text):
            text = pat.sub(lambda _m: line, text, count=1)
        elif re.search(rf"^\[{section}\]\s*$", text, re.M):
            text = re.sub(rf"^\[{section}\]\s*$", lambda m: m.group(0) + "\n" + line, text, count=1, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n\n[{section}]\n{line}\n"
    return text


def _parse_rule_value(key: str, raw: str):
    kind, choices = RULE_INFO[key][3], RULE_INFO[key][4]
    if kind == "ro":
        raise SystemExit(f"'{key}' can't be changed here. Edit factory.toml in a reviewed commit.")
    if kind == "bool":
        if raw.lower() not in ("true", "false", "on", "off", "yes", "no"):
            raise SystemExit(f"'{key}' must be true or false")
        return raw.lower() in ("true", "on", "yes")
    if kind == "int":
        v = int(raw)
        if v < 1:
            raise SystemExit(f"'{key}' must be at least 1")
        return v
    if kind == "float":
        v = float(raw)
        if v <= 0:
            raise SystemExit(f"'{key}' must be more than 0")
        return v
    if kind == "choice" and raw not in choices:
        raise SystemExit(f"'{key}' must be one of: {', '.join(choices)}")
    return raw


def rules_view(policy) -> list[dict]:
    out = []
    for key, (section, label, meaning, kind, choices) in RULE_INFO.items():
        out.append({"key": key, "label": label, "meaning": meaning, "kind": kind, "choices": choices,
                    "value": getattr(policy, key)})
    return out


def cmd_rules(args) -> None:
    """Show this project's rules in plain English, or change them (humans only, committed to git)."""
    ctx = Ctx(args)
    if not args.set and not args.preset:
        console.print(f"[bold]Project rules[/] for {ctx.repo.name}"
                      + (f" — preset: [bold]{ctx.policy.preset}[/]" if ctx.policy.preset else ""))
        t = Table(show_lines=False)
        t.add_column("Rule"); t.add_column("Now"); t.add_column("What it means", overflow="fold")
        for r in rules_view(ctx.policy):
            val = r["value"]
            shown = ", ".join(val) if isinstance(val, list) else ("" if val is None else str(val))
            t.add_row(r["label"], shown or "(not set)", r["meaning"])
        console.print(t)
        console.print("[dim]Change: wo rules --set KEY=VALUE --by YOUR_NAME   or   wo rules --preset practice|normal|money-critical --by YOUR_NAME[/]")
        return
    if not args.by:
        raise SystemExit("Say who is changing the rules: --by YOUR_NAME")
    human_only(ctx, "change the project rules", args.by)
    changes: dict = {}
    if args.preset:
        changes.update(PRESETS[args.preset]); changes["preset"] = args.preset
    for item in args.set or []:
        if "=" not in item:
            raise SystemExit(f"Use KEY=VALUE, got {item!r}")
        key, raw = item.split("=", 1)
        if key not in RULE_INFO:
            raise SystemExit(f"Unknown rule {key!r}. Known: {', '.join(k for k, v in RULE_INFO.items() if v[3] != 'ro')}")
        changes[key] = _parse_rule_value(key, raw)
    branch = gitops.git(ctx.repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    if branch != ctx.base_ref:
        raise SystemExit(f"Switch this folder to '{ctx.base_ref}' first (it is on '{branch}'). Rules live on the base branch.")
    if gitops.git(ctx.repo, "status", "--porcelain", "--", "factory.toml"):
        raise SystemExit("factory.toml has unsaved edits. Commit or undo them first.")
    path = ctx.repo / "factory.toml"
    path.write_text(set_policy_values(path.read_text(), changes))
    from factory.policy import load_policy
    load_policy(ctx.repo)  # fails loudly if the edit broke the file
    email = gitops.git(ctx.repo, "config", "user.email", check=False) or gitops.human_email(args.by)
    summary = ", ".join(f"{k}={v}" for k, v in changes.items())
    gitops.git(ctx.repo, "add", "--", "factory.toml")
    gitops.git(ctx.repo, "-c", f"user.name={gitops.FACTORY_NAME}", "-c", f"user.email={gitops.FACTORY_EMAIL}",
               "commit", "-q", "--author", f"{args.by} <{email}>",
               "-m", f"Project rules: {summary}\n\nChange-Type: policy", "--", "factory.toml")
    sha = gitops.head(ctx.repo)
    ctx.ledger.append("policy.changed", None, args.by, changes=changes, commit=sha, channel=ctx.channel)
    console.print(f"[bold green]Rules updated[/] by {args.by}: {summary} (commit {sha[:10]}). "
                  "The next gate uses them.")


def cmd_accept(args) -> None:
    """A human overrules a reviewer-agent finding, with a written reason. Humans only.

    Only the reviewer agent's opinions can be accepted. Failed tests, broken rules, protected files,
    secrets or size never can: those are facts, not opinions.
    """
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)
    human_only(ctx, "accept a reviewer finding", args.by, wo.id)
    if len(args.reason.strip()) < 10:
        raise SystemExit("Write a real reason (at least 10 characters). It goes in the audit record.")
    gate = store.last_gate()
    if not gate:
        raise SystemExit(f"{wo.id} has no gate yet.")
    findings = (gate.get("review") or {}).get("findings", [])
    if not 1 <= args.finding <= len(findings):
        raise SystemExit(f"Gate {gate['number']} has {len(findings)} reviewer finding(s). Pick 1 to {len(findings)}.")
    other_fails = [c["name"] for c in gate["checks"] if not c["passed"] and c.get("blocking", True)
                   and c["name"] != "independent reviewer agent"]
    if other_fails:
        raise SystemExit(f"Can't accept: these checks failed and can't be overruled: {', '.join(other_fails)}.")
    accepted = gate.setdefault("accepted", [])
    if any(a["finding"] == args.finding for a in accepted):
        raise SystemExit(f"Finding {args.finding} is already accepted.")
    f = findings[args.finding - 1]
    accepted.append({"finding": args.finding, "severity": f.get("severity"), "issue": f.get("issue", "")[:300],
                     "by": args.by, "reason": args.reason.strip(),
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    from factory.gate import SEVERITY_RANK
    threshold = SEVERITY_RANK[ctx.policy.blocking_severity]
    done = {a["finding"] for a in accepted}
    still = [i + 1 for i, x in enumerate(findings)
             if SEVERITY_RANK.get(x.get("severity"), 0) >= threshold and (i + 1) not in done]
    for c in gate["checks"]:
        if c["name"] == "independent reviewer agent":
            c["passed"] = not still
            c["detail"] = (f"{len(still)} blocking finding(s) left; {len(done)} accepted by a human"
                           if still else f"all blocking findings accepted by a human ({len(done)})")
    gate["passed"] = all(c["passed"] or not c.get("blocking", True) for c in gate["checks"])
    (store.dir / "gates" / f"gate-{gate['number']}.json").write_text(json.dumps(gate, indent=2) + "\n")
    rel = f".factory/{wo.id}/gates/gate-{gate['number']}.json"
    gitops.git(wt, "add", "--", rel)
    gitops.git(wt, "-c", f"user.name={gitops.FACTORY_NAME}", "-c", f"user.email={gitops.FACTORY_EMAIL}",
               "commit", "-q", "--author", f"{args.by} <{gitops.human_email(args.by)}>",
               "-m", f"{wo.id}: {args.by} accepted reviewer finding {args.finding} of gate {gate['number']}\n\n"
                     f"Reason: {args.reason.strip()}\nWork-Order: {wo.id}\nChange-Type: finding-accepted", "--", rel)
    ctx.ledger.append("finding.accepted", wo.id, args.by, gate=gate["number"], finding=args.finding,
                      severity=f.get("severity"), reason=args.reason.strip(), commit=gitops.head(wt), channel=ctx.channel)
    if gate["passed"]:
        ctx.ledger.append("gate.accepted", wo.id, args.by, number=gate["number"], code_sha=gate["code_sha"])
        console.print(f"[bold green]Accepted[/]. Gate {gate['number']} now passes with your reason on record. "
                      f"Next: [bold]wo pr {wo.id}[/]")
    else:
        console.print(f"Accepted finding {args.finding}. Still blocking: {', '.join(map(str, still))}.")


def cmd_rule(args) -> None:
    """Add a human-owned rule (invariant test) to an open work order. Humans only."""
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    human_only(ctx, "add human-owned invariants", args.by, wo.id)
    if derive_status(ctx, wo.id) in ("approved", "merged"):
        raise SystemExit(f"{wo.id} is already approved. Add the rule to a new work order.")
    wt = ctx.worktree(wo.id)
    src = Path(args.file).resolve()
    if not src.is_file():
        raise SystemExit(f"No such file: {src}")
    dest = wt / "invariants" / src.name
    dest.parent.mkdir(exist_ok=True)
    shutil.copy(src, dest)
    rel = f"invariants/{src.name}"
    gitops.git(wt, "add", "--", rel)
    gitops.git(wt, "-c", f"user.name={gitops.FACTORY_NAME}", "-c", f"user.email={gitops.FACTORY_EMAIL}",
               "commit", "-q", "--author", f"{args.by} <{gitops.human_email(args.by)}>",
               "-m", f"{wo.id}: add human-owned rule {src.name}\n\nWork-Order: {wo.id}\nChange-Type: human-invariant",
               "--", rel)
    sha = gitops.head(wt)
    ctx.ledger.append("invariant.added", wo.id, args.by, path=rel, sha256=sha256_file(dest), commit=sha,
                      channel=ctx.channel)
    console.print(f"[bold green]Rule added[/] to {wo.id}: {rel} (commit {sha[:10]}, by {args.by}).")
    console.print(f"Next: [bold]wo gate {wo.id}[/]. If the code breaks your rule, the gate fails and the next "
                  f"`wo run` fixes the code (agents can never edit the rule).")


def cmd_plan(args) -> None:
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)
    prompt = render("planner", wo_id=wo.id, title=wo.title, requester=wo.requester, intent=wo.intent,
                    invariants="\n".join(f"- `{p}`" for p in wo.invariants) or "- (repo invariants only)")
    with console.status(f"Planner agent ({model_for('planner')}) is reading the code..."):
        res, start, log_rel = ctx.agent_session(wo, "planner", "plan", prompt, PLAN_SCHEMA, {"step": "plan"})
    ctx.record("plan", res)
    features = [Feature(id=f["id"], title=f["title"], acceptance=f["acceptance"]) for f in res.structured["features"]]
    store.save_features(features)
    risks = "\n".join(f"- {r}" for r in res.structured.get("risks", []))
    feats = "\n".join(f"- **{f.id}** {f.title}" for f in features)
    store.append_progress(f"## Plan ({res.model}, ${res.cost_usd:.2f})\n{feats}\n\n**Risks flagged by planner:**\n{risks}")
    sha = gitops.commit(wt, f"{wo.id}: plan ({len(features)} features)", gitops.AGENT_NAME, gitops.AGENT_EMAIL,
                        agent_trailers(wo, ctx, **{"Agent-Role": "planner", "Agent-Model": res.model,
                                                   "Agent-Session": res.session_id}))
    ctx.ledger.append("plan.created", wo.id, f"agent:{res.model}",
                      **ctx.closing(res, start, log_rel, result="planned", commit=sha,
                                    features=[f.id for f in features], risks=res.structured.get("risks", [])))
    table = Table(title=f"{wo.id} plan")
    table.add_column("Feature"); table.add_column("Title"); table.add_column("Acceptance")
    for f in features:
        table.add_row(f.id, f.title, "\n".join(f"• {a}" for a in f.acceptance))
    console.print(table)
    console.print(Markdown("**Risks:**\n" + risks))
    console.print(f"Human checkpoint: edit `.factory/{wo.id}/features.json` if the plan is wrong. Next: [bold]wo run {wo.id}[/]")


def cmd_run(args) -> None:
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)
    status = derive_status(ctx, wo.id)
    if status in ("approved", "merged"):
        raise SystemExit(f"{wo.id} is already approved. Open a new work order for more changes.")
    if status == "handed back":
        raise SystemExit(f"{wo.id} was handed back to {wo.requester}. Agents are stopped. "
                         f"Fix it by hand and run `wo gate {wo.id}`, or open a new work order.")
    ctx.recover(wo)

    last_gate = store.last_gate()
    sessions = ctx.events(wo.id, "session.completed")
    # Finish the planned features first; fix gate findings once everything planned is built.
    fixing = bool(last_gate and not last_gate["passed"] and store.next_feature() is None
                  and (not sessions or sessions[-1]["data"]["after_gate"] < last_gate["number"]))
    if fixing:
        feature = Feature(id="FIX", title=f"Fix gate {last_gate['number']} failures", acceptance=["gate passes"])
        task = ("Gate " + str(last_gate["number"]) + " FAILED. Fix the problems below. Do not start new features.\n"
                "Do not weaken or edit tests in invariants/. Fix the code.\n\n" + last_gate["summary_md"])
    else:
        nf = store.next_feature()
        if nf is None:
            console.print(f"All features are done. Next: [bold]wo gate {wo.id}[/]")
            return
        feature = nf
        task = f"Implement feature {nf.id}: {nf.title}\nAcceptance criteria:\n" + "\n".join(f"- {a}" for a in nf.acceptance)

    n = len(sessions) + 1
    key = f"session-{n}"
    prompt = render("implementer", wo_id=wo.id, title=wo.title, requester=wo.requester, intent=wo.intent,
                    progress=store.progress(), task=task)
    with console.status(f"Session {n}: implementer ({model_for('implementer')}) on {feature.id}..."):
        res, start, log_rel = ctx.agent_session(wo, "implementer", key, prompt, SESSION_SCHEMA,
                                                {"step": "implement", "feature": feature.id, "feature_title": feature.title})

    gitops.git(wt, "add", "-A")
    patch = gitops.git(wt, "diff", "--cached", "--", ".", ":(exclude).factory")
    ctx.record(key, res, patch)
    changed = [ln.split("\t")[2] for ln in gitops.git(wt, "diff", "--cached", "--numstat", "--", ".", ":(exclude).factory").splitlines()]

    out = res.structured
    if not fixing:
        feats = store.load_features()
        for f in feats:
            if f.id == feature.id:
                f.status = "done" if out["status"] == "done" else "blocked"
                f.sessions.append(key)
        store.save_features(feats)
    denied = "\n".join(f"- {d['tool']} `{d['target']}` — {d['reason']}" for d in res.denials) or "- none"
    store.append_progress(
        f"## Session {n} — {feature.id}: {feature.title} ({res.model}, ${res.cost_usd:.2f})\n"
        f"**Status:** {out['status']}\n\n**Summary:** {out['summary']}\n\n"
        f"**Tests added:** {', '.join(out.get('tests_added', [])) or 'none'}\n\n"
        f"**Notes for next session:** {out['notes_for_next_session']}\n\n"
        f"**Concerns:** {'; '.join(out.get('concerns', [])) or 'none'}\n\n**Blocked tool calls:**\n{denied}")
    sha = gitops.commit(wt, f"{wo.id} {feature.id}: {out['summary'][:60]}", gitops.AGENT_NAME, gitops.AGENT_EMAIL,
                        agent_trailers(wo, ctx, **{"Agent-Role": "implementer", "Agent-Model": res.model,
                                                   "Agent-Session": res.session_id, "Feature": feature.id}))
    ctx.ledger.append("session.completed", wo.id, f"agent:{res.model}",
                      **ctx.closing(res, start, log_rel, result=out["status"], n=n, feature=feature.id,
                                    status=out["status"], commit=sha, files_changed=changed,
                                    after_gate=last_gate["number"] if last_gate else 0))

    color = "green" if out["status"] == "done" else "yellow"
    console.print(f"[bold {color}]Session {n} {out['status']}[/] — {feature.id}: {out['summary']}")
    console.print(f"Files: {', '.join(changed) or 'none'}  |  cost ${res.cost_usd:.2f}  |  tool calls {len(res.tool_log)}")
    for d in res.denials:
        console.print(f"[bold red]BLOCKED[/] {d['tool']} {d['target']}: {d['reason']}")
    nxt = store.next_feature()
    console.print(f"Next: [bold]wo run {wo.id}[/] ({nxt.id})" if nxt and not fixing else f"Next: [bold]wo gate {wo.id}[/]")


def cmd_gate(args) -> None:
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)
    wo_dict = json.loads((store.dir / "workorder.json").read_text())
    ctx.recover(wo)  # never gate half-written or uncommitted code: the report must match the commit it names
    unfinished = [f for f in store.load_features() if f.status != "done"]
    if unfinished:
        raise SystemExit(f"Not gating yet: {', '.join(f'{f.id} is {f.status}' for f in unfinished)}. "
                         f"Run `wo run {wo.id}` to finish it first. No gate was counted.")
    n = len(store.gate_reports()) + 1
    with console.status("Gate: tests, invariants, scans..."):
        report = run_checks(wt, wo.base_branch, wo.id, ctx.policy)
    review_info = {}
    if not args.no_review:
        key = f"review-{n}"
        prompt = reviewer_prompt(report, wo_dict, gitops.diff_text(wt, wo.base_branch))
        with console.status(f"Independent reviewer ({model_for('reviewer')}) is reading the diff..."):
            res, start, log_rel = ctx.agent_session(wo, "reviewer", key, prompt, REVIEW_SCHEMA,
                                                    {"step": "review", "gate": n, "code_sha": report.code_sha})
        ctx.record(key, res)
        apply_review(report, res, ctx.policy)
        review_info = ctx.closing(res, start, log_rel, result=res.structured.get("verdict"), gate=n,
                                  findings=len(res.structured.get("findings", [])))
    d = report.to_dict()
    d["summary_md"] = report.summary_md()
    path = store.save_gate(d)
    sha = gitops.commit(wt, f"{wo.id}: gate {d['number']} {'PASS' if report.passed else 'FAIL'}",
                        gitops.FACTORY_NAME, gitops.FACTORY_EMAIL,
                        {"Work-Order": wo.id, "Gate-Code-Sha": report.code_sha, "Gate-Result": "pass" if report.passed else "fail"})
    if review_info:
        review_info["commit"] = sha  # the reviewer's session log is committed with the gate report
        ctx.ledger.append("review.completed", wo.id, f"agent:{review_info['model']}", **review_info)
    ctx.ledger.append("gate.result", wo.id, "factory", number=d["number"], code_sha=report.code_sha,
                      passed=report.passed, failed=[c.name for c in report.checks if not c.passed],
                      reviewer_model=report.reviewer_model,
                      reviewer_verdict=(report.review or {}).get("verdict"), commit=sha)

    table = Table(title=f"{wo.id} gate {d['number']} on {report.code_sha[:10]}")
    table.add_column("Check"); table.add_column("Result"); table.add_column("Detail", overflow="fold")
    for c in report.checks:
        table.add_row(c.name, _mark(c), c.detail[:600])
    console.print(table)
    if report.review:
        for f in report.review.get("findings", []):
            col = {"high": "red", "medium": "yellow"}.get(f.get("severity"), "white")
            console.print(f"[{col}]({f.get('severity')})[/] {f.get('file', '')}: {f.get('issue')}"
                          + (f"\n    failing input: {f['failing_input']}" if f.get("failing_input") else ""))
    console.print(f"[dim]report: {path.relative_to(ctx.repo)}[/]")
    if report.passed:
        console.print(f"[bold green]GATE PASS[/]. A human reviewer can look now. Next: [bold]wo pr {wo.id}[/]")
        return
    # Stop limit: count failed gates in a row.
    streak = 0
    for g in reversed(ctx.events(wo.id, "gate.result")):
        if g["data"]["passed"]:
            break
        streak += 1
    if streak >= ctx.policy.max_gate_failures and derive_status(ctx, wo.id) != "handed back":
        ctx.hand_back(wo, f"{streak} failed gates in a row (limit {ctx.policy.max_gate_failures})", d["summary_md"])
        raise SystemExit(1)
    console.print(f"[bold red]GATE FAIL[/] ({streak} of {ctx.policy.max_gate_failures} before hand-back). "
                  f"No human time spent yet. Next: [bold]wo run {wo.id}[/] (fix session gets this report)")
    raise SystemExit(1)


def cmd_pr(args) -> None:
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)
    gate = store.last_gate()
    if not gate or not gate["passed"]:
        ctx.ledger.append("pr.refused", wo.id, "factory", reason="no passing gate")
        raise SystemExit(f"No change worth reviewing yet: the latest gate has not passed. No PR opened for {wo.id}.")
    ok, rows, problems = provenance(ctx, wo.id)
    events = ctx.ledger.for_work_order(wo.id)
    sessions = [e for e in events if e["event"] == "session.completed"]
    feats = "\n".join(f"| {f.id} | {f.title} | {f.status} | {', '.join(f.sessions)} |" for f in store.load_features())
    sess = "\n".join(f"| {e['data']['n']} | {e['data']['feature']} | {e['actor']} | {e['data']['status']} | "
                     f"${e['data']['cost_usd']:.2f} | {len(e['data']['denials'])} | `{e['data']['commit'][:8]}` |"
                     for e in sessions)
    diffstat = gitops.git(wt, "diff", "--stat", f"{wo.base_branch}...HEAD", "--", ".", ":(exclude).factory")
    owner, tier = catalog_owner(wt)
    body = f"""# {wo.id}: {wo.title}

**Owning team (from catalog-info.yaml):** {owner} · **Risk tier:** {tier} — the approver should come from the owning team.

## Intent (from {wo.requester})
{wo.intent}

## Features
| ID | Title | Status | Sessions |
|---|---|---|---|
{feats}

## Gate
{gate['summary_md']}

## Agent sessions
| # | Feature | Agent | Status | Cost | Blocked calls | Commit |
|---|---|---|---|---|---|---|
{sess}

## Change size
```
{diffstat}
```

## Provenance (FINOS SDLC Common Controls MI-23)
- **Agent steps recorded:** {len(rows)} ({'complete' if ok else 'INCOMPLETE: ' + '; '.join(problems)}). Details: `wo provenance {wo.id}`
- **Accountable owner (author of record):** {wo.requester}
- **Code drafted by:** agents listed above (commit author `{gitops.AGENT_NAME}`, trailers carry model + session)
- **Human-owned invariants:** {', '.join(f'`{p}`' for p in wo.invariants) or 'repo invariants only'}
- **Approver:** pending — must be a human other than {wo.requester}
- **Total agent cost:** ${ctx.spent(wo.id):.2f}
- **Ledger head:** `{ctx.ledger.head()[:16]}` (verify with `wo ledger verify`)

## What the reviewer should look at
Read `.factory/{wo.id}/progress.md` for the session log, then focus on the risks the planner flagged.
"""
    (store.dir / "PR.md").write_text(body)
    gitops.commit(wt, f"{wo.id}: PR description", gitops.FACTORY_NAME, gitops.FACTORY_EMAIL, {"Work-Order": wo.id})
    ctx.ledger.append("pr.prepared", wo.id, "factory", path=f".factory/{wo.id}/PR.md")
    console.print(Markdown(body))
    console.print(f"[dim]Written to .factory/{wo.id}/PR.md. With a remote: gh pr create -F .factory/{wo.id}/PR.md[/]")


def catalog_owner(repo: Path) -> tuple[str, str]:
    """Owner and risk tier from a Backstage-style catalog-info.yaml (idea from Backstage's software catalog)."""
    p = repo / "catalog-info.yaml"
    if not p.exists():
        return "unknown (no catalog-info.yaml)", "unknown"
    text = p.read_text()
    owner = re.search(r"^\s*owner:\s*(\S+)", text, flags=re.M)
    tier = re.search(r"risk-tier:\s*\"?(T\d)", text)
    return (owner.group(1) if owner else "unknown"), (tier.group(1) if tier else "unknown")


def cmd_provenance(args) -> None:
    ctx = Ctx(args)
    ok, rows, problems = provenance(ctx, args.wo)
    t = Table(title=f"{args.wo} provenance — FINOS MI-23 (Agentic Processes Provenance)")
    for col in ("#", "Role", "Model", "Start", "End", "Result", "Commit", "Session log", "MI-23 fields"):
        t.add_column(col, overflow="fold")
    for r in rows:
        mark = "[green]8/8[/]" if not r["missing"] else f"[red]{8 - len(r['missing'])}/8[/]"
        t.add_row(str(r["seq"]), r["role"], r["model"], r["start"][11:19], r["end"][11:19], r["result"],
                  r["commit"][:8], r["log"].split("/")[-1], mark)
    console.print(t)
    approvals = [e["actor"] for e in ctx.events(args.wo, "approval.granted")]
    console.print(f"Harness: {rows[0]['harness'] if rows else 'n/a'}  |  Human reviewers/approvers: "
                  f"{', '.join(approvals) or 'pending'}")
    if ok:
        console.print(f"[bold green]Provenance complete[/]: {len(rows)} agent steps, every agent commit traced.")
    else:
        for p in problems:
            console.print(f"[red]- {p}[/]")
        raise SystemExit(1)


def cmd_approve(args) -> None:
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)

    def refuse(reason: str) -> None:
        ctx.ledger.append("approval.refused", wo.id, args.by, reason=reason)
        console.print(f"[bold red]Approval refused:[/] {reason}")
        raise SystemExit(1)

    human_only(ctx, "approve a work order", args.by, wo.id)
    if ctx.policy.require_independent_approver and args.by == wo.requester:
        refuse(f"{args.by} requested this change. The approver must be a different person.")
    gate = store.last_gate()
    if not gate or not gate["passed"]:
        refuse("the latest gate did not pass")
    code_changes = gitops.git(wt, "diff", "--name-only", gate["code_sha"], "HEAD", "--", ".", ":(exclude).factory")
    if code_changes:
        refuse(f"code changed after gate {gate['number']} ran ({code_changes.split()[0]}...). Run `wo gate` again.")
    ok, rows, problems = provenance(ctx, wo.id)
    if not ok:
        refuse("provenance incomplete (FINOS MI-23): " + "; ".join(problems))

    entry = ctx.ledger.append("approval.granted", wo.id, args.by, gate=gate["number"], code_sha=gate["code_sha"],
                              provenance_steps=len(rows), note=args.note or "")
    console.print(f"[bold green]Approved[/] by {args.by} (requester {wo.requester}) at gate {gate['number']} "
                  f"on {gate['code_sha'][:10]}. Provenance complete ({len(rows)} agent steps). Ledger entry {entry['hash'][:12]}.")
    if args.merge:
        msg = (f"Merge {wo.id}: {wo.title}\n\nWork-Order: {wo.id}\nAccountable-Owner: {wo.requester}\n"
               f"Approved-By: {args.by}\nGate: {gate['number']} pass on {gate['code_sha']}\n"
               f"Provenance: complete, {len(rows)} agent steps (FINOS MI-23)\nLedger-Entry: {entry['hash']}")
        gitops.git(ctx.repo, "-c", f"user.name={args.by}", "-c", f"user.email={gitops.human_email(args.by)}",
                   "merge", "--no-ff", "-q", wo.branch, "-m", msg)
        sha = gitops.head(ctx.repo)
        ctx.ledger.append("merged", wo.id, args.by, commit=sha, into=wo.base_branch)
        console.print(f"[bold green]Merged[/] {wo.branch} into {wo.base_branch} as {sha[:10]}. "
                      "Production deploy stays in your normal release pipeline.")


def cmd_ci(args) -> None:
    """Re-run the gate in CI, on a plain checkout (idea from Dagger: same checks locally and in CI).

    Deterministic checks only: no model calls, no secrets needed in CI. Then it checks that every
    agent commit carries provenance trailers, and that the gate recorded on the laptop matches the
    code being merged. If someone skipped the factory, CI still catches it.
    """
    repo = Path(args.repo).resolve()
    policy = load_policy_at(repo, args.base)  # trusted copy from the target branch
    log = gitops.git(repo, "log", "--format=%B", f"{args.base}..HEAD")
    wo_ids = sorted(set(re.findall(r"^Work-Order: (WO-\d+)", log, flags=re.M)))
    report = run_checks(repo, args.base, ",".join(wo_ids) or "none", policy)

    need = ("Work-Order", "Accountable-Owner", "Agent-Model", "Agent-Session")
    bad = []
    for sha in gitops.git(repo, "log", f"--author={gitops.AGENT_EMAIL}", "--format=%H", f"{args.base}..HEAD").split():
        body = gitops.git(repo, "show", "-s", "--format=%B", sha)
        missing = [t for t in need if f"\n{t}: " not in "\n" + body]
        if missing:
            bad.append(f"{sha[:10]} missing {', '.join(missing)}")
    report.checks.append(Check("agent commit trailers", not bad, "; ".join(bad) or "every agent commit carries provenance trailers"))

    for wid in wo_ids:
        gates = sorted((repo / ".factory" / wid / "gates").glob("gate-*.json"), key=lambda p: int(p.stem.split("-")[1]))
        if not gates:
            report.checks.append(Check(f"{wid} recorded gate", False, "no gate report in the branch"))
            continue
        g = json.loads(gates[-1].read_text())
        drift = gitops.git(repo, "diff", "--name-only", g["code_sha"], "HEAD", "--", ".", ":(exclude).factory", check=False)
        ok = g["passed"] and not drift
        report.checks.append(Check(f"{wid} recorded gate matches", ok,
                                   f"gate {g['number']} {'passed' if g['passed'] else 'FAILED'} on {g['code_sha'][:10]}"
                                   + (f"; code changed since: {drift.split()[0]}..." if drift else "; no code changes since")))
    if not wo_ids and gitops.git(repo, "log", f"--author={gitops.AGENT_EMAIL}", "--format=%H", f"{args.base}..HEAD"):
        report.checks.append(Check("work order", False, "agent commits found but no Work-Order trailer"))
    passed = all(c.passed or not c.blocking for c in report.checks)

    t = Table(title=f"CI gate on {gitops.head(repo)[:10]} vs {args.base} ({', '.join(wo_ids) or 'no work order'})")
    t.add_column("Check"); t.add_column("Result"); t.add_column("Detail", overflow="fold")
    for c in report.checks:
        t.add_row(c.name, _mark(c), c.detail[:400])
    console.print(t)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"## Factory CI gate: {'PASS' if passed else 'FAIL'}\n\n| Check | Result | Detail |\n|---|---|---|\n")
            for c in report.checks:
                f.write(f"| {c.name} | {'PASS' if c.passed else ('FAIL' if c.blocking else 'WARN')} | {c.detail.splitlines()[0][:150] if c.detail else ''} |\n")
    console.print("[bold green]CI GATE PASS[/]" if passed else "[bold red]CI GATE FAIL[/]")
    if not passed:
        raise SystemExit(1)


def cmd_kill(args) -> None:
    ctx = Ctx(args)
    info = {"by": args.by, "reason": args.reason, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ctx.kill_file().parent.mkdir(parents=True, exist_ok=True)
    ctx.kill_file().write_text(json.dumps(info))
    ctx.ledger.append("kill_switch.on", None, args.by, reason=args.reason)
    console.print(f"[bold red]KILL SWITCH ON[/] by {args.by}: {args.reason}. No new agent session will start. "
                  "Undo with `wo resume` (humans only).")


def cmd_resume(args) -> None:
    ctx = Ctx(args)
    human_only(ctx, "turn the kill switch off", args.by)
    if ctx.kill_file().exists():
        ctx.kill_file().unlink()
    ctx.ledger.append("kill_switch.off", None, args.by, note=args.note or "")
    still = ctx.killed()
    msg = f" Still blocked by {still['source']}." if still else ""
    console.print(f"[bold green]Kill switch off[/] by {args.by}.{msg}")


def cmd_status(args) -> None:
    ctx = Ctx(args)
    ids = sorted({e["work_order"] for e in ctx.ledger.entries() if e["work_order"]})
    k = ctx.killed()
    if k:
        console.print(f"[bold red]KILL SWITCH ON[/] ({k['source']}): {k.get('reason', '')}")
    if args.wo:
        store = ctx.store(args.wo)
        wo = store.load_wo()
        ev = ctx.ledger.for_work_order(wo.id)
        done = {e["data"]["started_seq"] for e in ev if "started_seq" in e["data"]}
        stuck = [e for e in ev if e["event"] == "session.started" and e["seq"] not in done]
        if stuck:
            console.print(f"[yellow]{len(stuck)} agent session(s) never finished (step {', '.join(str(e['seq']) for e in stuck)}). "
                          f"The next `wo run` recovers them automatically.[/]")
        console.print(f"[bold]{wo.id}[/] {wo.title} — [cyan]{derive_status(ctx, wo.id)}[/] (owner {wo.requester}, "
                      f"spent ${ctx.spent(wo.id):.2f} of ${ctx.policy.max_workorder_budget_usd:.2f})")
        t = Table(); t.add_column("Feature"); t.add_column("Status"); t.add_column("Title"); t.add_column("Sessions")
        for f in store.load_features():
            t.add_row(f.id, f.status, f.title, ", ".join(f.sessions))
        console.print(t)
        g = store.last_gate()
        if g:
            console.print(Markdown(g["summary_md"]))
        return
    t = Table(title="Work orders")
    for col in ("ID", "Title", "Owner", "Status", "Sessions", "Gates", "Cost"):
        t.add_column(col)
    for wid in ids:
        ev = ctx.ledger.for_work_order(wid)
        created = next(e for e in ev if e["event"] == "workorder.created")
        t.add_row(wid, created["data"]["title"][:40], created["actor"], derive_status(ctx, wid),
                  str(sum(e["event"] == "session.completed" for e in ev)), str(sum(e["event"] == "gate.result" for e in ev)),
                  f"${ctx.spent(wid):.2f}")
    console.print(t)


def cmd_stats(args) -> None:
    """What a team lead and an auditor see: is this working, is it safe, is it getting better?"""
    ctx = Ctx(args)
    entries = ctx.ledger.entries()
    wos = sorted({e["work_order"] for e in entries if e["work_order"]})
    by = lambda name: [e for e in entries if e["event"] == name]  # noqa: E731
    created = {e["work_order"]: e for e in by("workorder.created")}
    gates, merged, approvals = by("gate.result"), by("merged"), by("approval.granted")
    first_gates: dict[str, dict] = {}
    for g in gates:
        first_gates.setdefault(g["work_order"], g)
    failed_checks = Counter(c for g in gates for c in g["data"]["failed"])
    closers = by("session.completed") + by("plan.created") + by("review.completed")
    denials = sum(len(e["data"].get("denials", [])) for e in closers)
    cost = sum(e["data"].get("cost_usd", 0) or 0 for e in entries)

    second_person = [a for a in approvals if a["actor"] != created.get(a["work_order"], {}).get("actor")]
    self_refused = [e for e in by("approval.refused") if "requested this change" in e["data"]["reason"]]
    lead_times = [(_ts(m) - _ts(created[m["work_order"]])).total_seconds() for m in merged if m["work_order"] in created]
    waits = []
    for a in approvals:
        passed = [g for g in gates if g["work_order"] == a["work_order"] and g["data"]["passed"] and g["seq"] < a["seq"]]
        if passed:
            waits.append((_ts(a) - _ts(passed[0])).total_seconds())
    reverts = [ln for ln in gitops.git(ctx.repo, "log", ctx.base_ref, "--grep=^Revert", "--format=%s", check=False).splitlines()
               if "WO-" in ln]
    complete = sum(1 for w in wos if (ctx.worktree(w)).exists() and provenance(ctx, w)[0])

    t = Table(title="Factory stats (all numbers come from the ledger and git)")
    t.add_column("Area"); t.add_column("Metric"); t.add_column("Value")
    t.add_row("Flow", "Work orders opened / merged / handed back",
              f"{len(wos)} / {len(merged)} / {len(by('workorder.handed_back'))}")
    t.add_row("Flow", "Median lead time (opened → merged)", _fmt_dur(statistics.median(lead_times) if lead_times else None))
    t.add_row("Flow", "Median review wait (first passing gate → approval)", _fmt_dur(statistics.median(waits) if waits else None))
    t.add_row("Quality", "Gate runs / pass rate", f"{len(gates)} / {100 * sum(g['data']['passed'] for g in gates) / max(len(gates), 1):.0f}%")
    t.add_row("Quality", "First gate pass rate (agent right first time)",
              f"{100 * sum(g['data']['passed'] for g in first_gates.values()) / max(len(first_gates), 1):.0f}%")
    t.add_row("Quality", "Gate failures by check", ", ".join(f"{k}: {v}" for k, v in sorted(failed_checks.items())) or "none")
    t.add_row("Quality", "Reverts of merged work orders", str(len(reverts)))
    t.add_row("Control", "Approved by a second person", f"{len(second_person)} of {len(approvals)} "
              f"({100 * len(second_person) / max(len(approvals), 1):.0f}%) — enforced")
    t.add_row("Control", "Self-approval attempts refused", str(len(self_refused)))
    t.add_row("Control", "Blocked tool calls (policy)", str(denials))
    t.add_row("Control", "Agent sessions failed / blocked by kill switch",
              f"{len(by('session.failed'))} / {len(by('session.blocked'))}")
    t.add_row("Control", "Work orders with complete MI-23 provenance", f"{complete} of {len(wos)}")
    t.add_row("Cost", "Total agent cost", f"${cost:.2f}")
    t.add_row("Cost", "Cost per merged change", f"${cost / max(len(merged), 1):.2f}")
    console.print(t)
    scorecard(entries)


def scorecard(entries: list[dict]) -> None:
    """Agent scorecard by role and model (idea from Paperclip's agent performance reviews).

    Helps answer: which model should do which job, and is each one worth its cost?
    """
    started = {e["seq"]: e for e in entries if e["event"] == "session.started"}
    closers = [e for e in entries if e["event"] in ("plan.created", "session.completed", "review.completed", "session.failed", "session.abandoned")
               and e["data"].get("started_seq") in started]
    gates = [e for e in entries if e["event"] == "gate.result"]
    rows: dict[tuple[str, str], dict] = {}
    for c in closers:
        st = started[c["data"]["started_seq"]]
        role = st["data"]["role"]
        model = c["data"].get("model") or st["data"].get("model")
        r = rows.setdefault((role, model), {"n": 0, "ok": 0, "broken": 0, "cost": 0.0, "denied": 0, "wos": set(), "fix": 0,
                                            "agree": 0, "reviews": 0, "extra_blocks": 0})
        r["n"] += 1
        r["cost"] += c["data"].get("cost_usd", 0) or 0
        r["denied"] += len(c["data"].get("denials", []))
        r["wos"].add(c["work_order"])
        if c["event"] in ("session.failed", "session.abandoned"):
            r["broken"] += 1
        else:
            r["ok"] += 1
        if c["data"].get("feature") == "FIX":
            r["fix"] += 1
        if c["event"] == "review.completed":
            g = next((g for g in gates if g["work_order"] == c["work_order"] and g["data"]["number"] == c["data"].get("gate")), None)
            if g:
                det_failed = bool([x for x in g["data"]["failed"] if x != "independent reviewer agent"])
                blocked = c["data"].get("result") == "request_changes"
                r["reviews"] += 1
                r["agree"] += int(blocked == det_failed)
                r["extra_blocks"] += int(blocked and not det_failed)
    first_gate = {}
    for g in gates:
        first_gate.setdefault(g["work_order"], g["data"]["passed"])
    t = Table(title="Agent scorecard (by role and model)")
    for col in ("Role", "Model", "Sessions (ok / broken)", "Avg cost", "Blocked calls", "Quality signal"):
        t.add_column(col, overflow="fold")
    for (role, model), r in sorted(rows.items()):
        if role == "implementer":
            wos = [w for w in r["wos"] if w in first_gate]
            q = (f"first-gate pass {100 * sum(first_gate[w] for w in wos) / max(len(wos), 1):.0f}% over {len(wos)} WO; "
                 f"{r['fix']} fix session(s)")
        elif role == "reviewer":
            q = (f"agreed with tests {r['agree']}/{r['reviews']}; blocked {r['extra_blocks']} time(s) when tests passed "
                 f"(check: real catch or noise?)")
        else:
            q = "—"
        t.add_row(role, model, f"{r['ok']} / {r['broken']}", f"${r['cost'] / max(r['n'], 1):.2f}", str(r["denied"]), q)
    console.print(t)


def cmd_ledger(args) -> None:
    ctx = Ctx(args)
    if args.action == "verify":
        ok, idx, msg = ctx.ledger.verify()
        console.print(f"[bold {'green' if ok else 'red'}]{'OK' if ok else 'TAMPERED'}[/]: {msg} "
                      f"({len(ctx.ledger.entries())} entries, head {ctx.ledger.head()[:16]})")
        if not ok:
            raise SystemExit(1)
    else:
        t = Table(title="Audit ledger")
        for col in ("#", "Time", "WO", "Event", "Actor", "Hash"):
            t.add_column(col)
        for e in ctx.ledger.entries():
            if args.wo and e["work_order"] != args.wo:
                continue
            t.add_row(str(e["seq"]), e["ts"][11:19], e["work_order"] or "", e["event"], e["actor"], e["hash"][:10])
        console.print(t)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="wo", description="Work-order software factory")
    p.add_argument("--repo", default=".", help="target git repository (default: current dir)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def agent_opts(sp):
        sp.add_argument("--backend", choices=["claude", "replay"], help="default: $WO_BACKEND or claude")
        sp.add_argument("--replay-dir", help="recordings for the replay backend (default: $WO_REPLAY_DIR)")
        sp.add_argument("--record", help="save agent output here so it can be replayed later")

    s = sub.add_parser("init", help="set up a repository for the factory (safe to re-run)"); s.set_defaults(fn=cmd_init)
    s.add_argument("--user", help="your name (default: git config user.name)")
    s.add_argument("--stack", choices=sorted(STACKS), help="project type (default: detected)")
    s.add_argument("--preset", choices=sorted(PRESETS), default="normal",
                   help="how strict: practice | normal | money-critical (default: normal)")
    s.add_argument("--no-commit", action="store_true", help="write the files but do not commit them")
    s = sub.add_parser("new", help="open a work order"); s.set_defaults(fn=cmd_new)
    s.add_argument("title"); s.add_argument("--intent", required=True); s.add_argument("--by", required=True)
    s.add_argument("--invariant", action="append", help="human-written invariant test file (repeatable)")
    s.add_argument("--base", help="branch to start from (default: main, else master, else current)")
    s = sub.add_parser("rules", help="show or change this project's rules in plain English")
    s.set_defaults(fn=cmd_rules); s.add_argument("--set", action="append", metavar="KEY=VALUE")
    s.add_argument("--preset", choices=sorted(PRESETS)); s.add_argument("--by")
    s = sub.add_parser("accept", help="overrule a reviewer-agent finding with a written reason (humans only)")
    s.set_defaults(fn=cmd_accept); s.add_argument("wo"); s.add_argument("--finding", type=int, required=True)
    s.add_argument("--reason", required=True); s.add_argument("--by", required=True)
    s = sub.add_parser("ui", help="open the dashboard in your browser")
    s.set_defaults(fn=lambda a: __import__("factory.ui", fromlist=["serve"]).serve(a))
    s.add_argument("--port", type=int, default=8765); s.add_argument("--user", help="your name (default: git user.name)")
    s.add_argument("--no-open", action="store_true", help="don't open a browser")
    s = sub.add_parser("rule", help="add a human-owned rule to an open work order (humans only)")
    s.set_defaults(fn=cmd_rule); s.add_argument("wo"); s.add_argument("file"); s.add_argument("--by", required=True)
    s = sub.add_parser("plan", help="planner agent splits the work"); s.set_defaults(fn=cmd_plan); s.add_argument("wo"); agent_opts(s)
    s = sub.add_parser("run", help="one implementer session"); s.set_defaults(fn=cmd_run); s.add_argument("wo"); agent_opts(s)
    s = sub.add_parser("gate", help="checks + reviewer agent"); s.set_defaults(fn=cmd_gate); s.add_argument("wo"); agent_opts(s)
    s.add_argument("--no-review", action="store_true", help="skip the reviewer agent (deterministic checks only)")
    s = sub.add_parser("pr", help="write the PR description"); s.set_defaults(fn=cmd_pr); s.add_argument("wo")
    s = sub.add_parser("provenance", help="FINOS MI-23 provenance check"); s.set_defaults(fn=cmd_provenance); s.add_argument("wo")
    s = sub.add_parser("approve", help="independent human approval"); s.set_defaults(fn=cmd_approve)
    s.add_argument("wo"); s.add_argument("--by", required=True); s.add_argument("--note"); s.add_argument("--merge", action="store_true")
    s = sub.add_parser("ci", help="re-run the gate in CI on a plain checkout"); s.set_defaults(fn=cmd_ci)
    s.add_argument("--base", default="origin/main", help="target branch ref (default: origin/main)")
    s = sub.add_parser("kill", help="stop all agent sessions now"); s.set_defaults(fn=cmd_kill)
    s.add_argument("--by", required=True); s.add_argument("--reason", required=True)
    s = sub.add_parser("resume", help="allow agent sessions again (humans only)"); s.set_defaults(fn=cmd_resume)
    s.add_argument("--by", required=True); s.add_argument("--note")
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status); s.add_argument("wo", nargs="?")
    s = sub.add_parser("stats"); s.set_defaults(fn=cmd_stats)
    s = sub.add_parser("ledger"); s.set_defaults(fn=cmd_ledger)
    s.add_argument("action", choices=["show", "verify"]); s.add_argument("--wo")

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except gitops.GitError as e:
        console.print(f"[red]{e}[/]")
        sys.exit(2)


if __name__ == "__main__":
    main()
