"""`wo ui`: a local dashboard to see and decide.

People talk to their AI tool (Claude Code, the Claude app, Cursor, Codex) to get work done. This page is
where they SEE what is happening and DECIDE the human-only steps: approve, add a rule, overrule a
reviewer finding with a reason, stop everything, change the project rules.

Design choices:
- Every button runs the same `wo` command a human would type, so the same checks, refusals and audit
  record apply. The page holds no logic of its own that could drift from the CLI.
- Local only: bound to 127.0.0.1, every API call needs a random token, and the Host header must be local.
- If this server was started from inside an AI agent session, it is read-only: an agent must never be
  able to click "approve" for you.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from factory import gitops

STATIC = Path(__file__).parent / "ui_static"
FACTORY_ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# --- Reading state ------------------------------------------------------------------------------------------

def _ctx(repo: Path):
    from factory.cli import Ctx
    return Ctx(argparse.Namespace(repo=str(repo), base=None))


def _money(x: float) -> str:
    return f"${x:.2f}"


def describe(e: dict[str, Any]) -> tuple[str, str]:
    """One ledger event in plain English, plus a tone: ok | warn | bad | human | info."""
    d, actor, ev = e.get("data", {}), e.get("actor", ""), e["event"]
    who = actor.replace("agent:", "AI ")
    cost = f" · {_money(d['cost_usd'])}" if d.get("cost_usd") else ""
    table = {
        "workorder.created": (f"Opened by {actor}: {d.get('title', '')}", "human"),
        "session.started": (f"AI {d.get('role', 'agent')} started ({d.get('model', '')})", "info"),
        "plan.created": (f"Planned: {len(d.get('features', []))} step(s){cost}", "ok"),
        "session.completed": (f"Built {d.get('feature', '')}: {d.get('status', '')}"
                              f" · {len(d.get('files_changed', []))} file(s){cost}",
                              "ok" if d.get("status") == "done" else "warn"),
        "session.failed": (f"AI session failed: {str(d.get('error', ''))[:120]}", "bad"),
        "session.abandoned": ("A crashed session was cleaned up (leftovers kept as evidence)", "warn"),
        "workorder.recovered": ("Recovered after a crash", "warn"),
        "session.blocked": (f"Not started: {d.get('reason', '')}", "bad"),
        "review.completed": (f"Reviewer ({d.get('model', '')}): {d.get('result', '')}, "
                             f"{d.get('findings', 0)} finding(s){cost}", "info"),
        "gate.result": (f"Check {d.get('number')}: " + ("passed" if d.get("passed") else
                        "failed — " + ", ".join(d.get("failed", []))), "ok" if d.get("passed") else "bad"),
        "gate.accepted": (f"{actor} accepted the reviewer's findings, so check {d.get('number')} passes", "human"),
        "finding.accepted": (f"{actor} accepted finding {d.get('finding')}: “{d.get('reason', '')}”", "human"),
        "invariant.added": (f"Rule added by {actor}: {d.get('path', '')}", "human"),
        "workorder.handed_back": (f"Handed back to you: {d.get('reason', '')}", "bad"),
        "pr.prepared": ("Pull request text written", "ok"),
        "pr.refused": (f"PR refused: {d.get('reason', '')}", "warn"),
        "approval.refused": (f"Approval refused: {d.get('reason', '')}", "warn"),
        "approval.granted": (f"Approved by {actor}", "human"),
        "merged": (f"Merged into {d.get('into', '')} by {actor}", "ok"),
        "human_only.refused": (f"Blocked: an AI agent tried to {d.get('action', '')}", "bad"),
        "kill_switch.on": (f"Everything stopped by {actor}: {d.get('reason', '')}", "bad"),
        "kill_switch.off": (f"Agents allowed again by {actor}", "human"),
        "policy.changed": (f"Rules changed by {actor}", "human"),
    }
    text, tone = table.get(ev, (ev.replace(".", " ").replace("_", " ").capitalize() + f" ({who})", "info"))
    return text, tone


def _running(ctx, wo_id: str) -> dict | None:
    closed = {e["data"].get("started_seq") for e in ctx.ledger.for_work_order(wo_id) if "started_seq" in e.get("data", {})}
    open_ = [e for e in ctx.events(wo_id, "session.started") if e["seq"] not in closed]
    return open_[-1]["data"] if open_ else None


def next_step(status: str, unfinished: list, gate: dict | None, running: dict | None) -> dict[str, str]:
    if running:
        return {"text": f"An AI {running.get('role', 'agent')} is working now. Watch it under “Live”.", "action": ""}
    if status == "open":
        return {"text": "Next: plan it. Ask your AI “plan WO”, or press Plan.", "action": "plan"}
    if status in ("planned", "in progress") and unfinished:
        return {"text": f"Next: build {unfinished[0]}. Ask your AI, or press Build next step.", "action": "run"}
    if status in ("planned", "in progress"):
        return {"text": "All steps are built. Next: run the checks.", "action": "gate"}
    if status == "gate FAILED":
        only_review = gate and all(c["passed"] or not c.get("blocking", True) or c["name"] == "independent reviewer agent"
                                   for c in gate["checks"])
        if only_review:
            return {"text": "Only the reviewer agent objected. Fix it (Build next step), or accept a finding with a reason.",
                    "action": "run"}
        return {"text": "A check failed. Next: Build next step — the AI fixes what the check found.", "action": "run"}
    if status == "gate passed":
        return {"text": "Checks passed. Next: write the PR text, then approve.", "action": "pr"}
    if status == "handed back":
        return {"text": "The AI stopped and handed this back to you. Fix it yourself and run the checks, "
                        "or accept reviewer findings with a reason.", "action": "gate"}
    if status in ("approved", "merged"):
        return {"text": "Done.", "action": ""}
    return {"text": "", "action": ""}


def collect_state(repo: Path, user: str, read_only: str | None) -> dict[str, Any]:
    from factory.cli import derive_status, rules_view, PRESET_INFO
    ctx = _ctx(repo)
    wos = []
    for e in ctx.ledger.entries():
        if e["event"] != "workorder.created":
            continue
        wo_id = e["work_order"]
        status = derive_status(ctx, wo_id)
        wt = ctx.worktree(wo_id)
        unfinished, gate = [], None
        if wt.exists():
            from factory.workorder import WorkOrderStore
            st = WorkOrderStore(wt, wo_id)
            unfinished = [f.id for f in st.load_features() if f.status != "done"]
            gate = st.last_gate()
        running = _running(ctx, wo_id)
        needs_you = status in ("gate passed", "handed back") or (status == "gate FAILED" and not running)
        wos.append({"id": wo_id, "title": e["data"].get("title", ""), "requester": e["actor"], "status": status,
                    "spent": round(ctx.spent(wo_id), 2), "running": bool(running), "needs_you": needs_you,
                    "next": next_step(status, unfinished, gate, running)})
    killed = ctx.killed()
    return {
        "project": repo.name, "repo": str(repo), "user": user, "read_only": read_only,
        "base": ctx.base_ref, "killed": killed,
        "preset": ctx.policy.preset, "presets": PRESET_INFO,
        "budget": {"session": ctx.policy.max_session_budget_usd, "work_order": ctx.policy.max_workorder_budget_usd},
        "rules": rules_view(ctx.policy),
        "work_orders": list(reversed(wos)),
        "proposals": sorted(p.name for p in (repo / ".factory" / "proposals").glob("*") if p.is_file())
        if (repo / ".factory" / "proposals").exists() else [],
    }


def collect_detail(repo: Path, wo_id: str) -> dict[str, Any]:
    from factory.cli import derive_status
    from factory.workorder import WorkOrderStore
    ctx = _ctx(repo)
    wt = ctx.worktree(wo_id)
    events = ctx.ledger.for_work_order(wo_id)
    timeline = [{"ts": e["ts"], "text": describe(e)[0], "tone": describe(e)[1], "event": e["event"]} for e in events]
    out: dict[str, Any] = {"id": wo_id, "status": derive_status(ctx, wo_id), "timeline": timeline,
                           "spent": round(ctx.spent(wo_id), 2), "worktree": wt.exists()}
    if not wt.exists():
        return out
    st = WorkOrderStore(wt, wo_id)
    wo = st.load_wo()
    out.update({"title": wo.title, "intent": wo.intent, "requester": wo.requester, "branch": wo.branch,
                "features": [{"id": f.id, "title": f.title, "status": f.status, "acceptance": f.acceptance}
                             for f in st.load_features()]})
    gate = st.last_gate()
    if gate:
        accepted = {a["finding"]: a for a in gate.get("accepted", [])}
        findings = []
        for i, f in enumerate((gate.get("review") or {}).get("findings", []), start=1):
            findings.append({"n": i, "severity": f.get("severity"), "file": f.get("file", ""),
                             "issue": f.get("issue", ""), "failing_input": f.get("failing_input", ""),
                             "accepted": accepted.get(i)})
        out["gate"] = {"number": gate["number"], "passed": gate["passed"], "code_sha": gate.get("code_sha", "")[:10],
                       "checks": [{"name": c["name"], "passed": c["passed"], "blocking": c.get("blocking", True),
                                   "detail": c.get("detail", "")} for c in gate["checks"]],
                       "findings": findings,
                       "can_accept": all(c["passed"] or not c.get("blocking", True) or c["name"] == "independent reviewer agent"
                                         for c in gate["checks"])}
    hb = st.dir / "HANDBACK.md"
    out["handback"] = hb.read_text()[:3000] if hb.exists() else ""
    running = _running(ctx, wo_id)
    out["running"] = running
    logs = sorted((st.dir / "sessions").glob("*.tools.jsonl"), key=lambda p: p.stat().st_mtime) \
        if (st.dir / "sessions").exists() else []
    live = []
    if logs:
        for ln in logs[-1].read_text().splitlines()[-25:]:
            try:
                live.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    out["live"] = {"session": logs[-1].name.replace(".tools.jsonl", "") if logs else "", "calls": live}
    unfinished = [f["id"] for f in out["features"] if f["status"] != "done"]
    out["next"] = next_step(out["status"], unfinished, gate, running)
    return out


# --- Actions: each one is a normal `wo` command ---------------------------------------------------------------

SAFE_ID = re.compile(r"^WO-\d{3,}$")


def build_command(body: dict[str, Any], user: str, proposals: list[str]) -> list[str]:
    a = body.get("action")
    wo = body.get("wo", "")
    if a in ("plan", "run", "gate", "pr", "approve", "rule", "accept") and not SAFE_ID.match(wo):
        raise ValueError("Pick a work order first.")
    if a == "new":
        title, intent = str(body.get("title", "")).strip(), str(body.get("intent", "")).strip()
        if not title or not intent:
            raise ValueError("Write a short title and what you want changed.")
        return ["new", title, "--intent", intent, "--by", user]
    if a in ("plan", "run", "gate", "pr"):
        return [a, wo]
    if a == "approve":
        return ["approve", wo, "--by", user, "--merge"]
    if a == "rule":
        name = body.get("file", "")
        if name not in proposals:
            raise ValueError("Pick a drafted rule from .factory/proposals/.")
        return ["rule", wo, str(Path(".factory") / "proposals" / name), "--by", user]
    if a == "accept":
        reason = str(body.get("reason", "")).strip()
        return ["accept", wo, "--finding", str(int(body.get("finding", 0))), "--reason", reason, "--by", user]
    if a == "kill":
        return ["kill", "--by", user, "--reason", str(body.get("reason") or "stopped from the dashboard")]
    if a == "resume":
        return ["resume", "--by", user]
    if a == "rules":
        cmd = ["rules", "--by", user]
        if body.get("preset"):
            cmd += ["--preset", str(body["preset"])]
        for k, v in (body.get("set") or {}).items():
            cmd += ["--set", f"{k}={v}"]
        return cmd
    raise ValueError(f"Unknown action {a!r}")


def run_wo(repo: Path, args: list[str]) -> dict[str, Any]:
    env = {**os.environ, "WO_CHANNEL": "ui", "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "150",
           "PYTHONPATH": str(FACTORY_ROOT) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    p = subprocess.run([sys.executable, "-m", "factory.cli", "--repo", str(repo), *args],
                       capture_output=True, text=True, env=env, timeout=3600)
    out = ANSI.sub("", (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip())
    return {"ok": p.returncode == 0, "output": out[-8000:], "command": "wo " + " ".join(args)}


# --- HTTP ------------------------------------------------------------------------------------------------------

def make_handler(repo: Path, user: str, token: str, read_only: str | None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: Any) -> None:
            self._send(code, json.dumps(data, default=str).encode(), "application/json")

        def _local_host(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _authed(self, allow_query: bool) -> bool:
            if self.headers.get("X-Token") == token:
                return True
            return allow_query and parse_qs(urlparse(self.path).query).get("t", [""])[0] == token

        def do_GET(self):
            if not self._local_host():
                return self._send(403, b"local only", "text/plain")
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                if not self._authed(allow_query=True):
                    return self._send(403, b"Open the link printed by `wo ui` (it carries a private token).", "text/plain")
                html = (STATIC / "index.html").read_text().replace("__TOKEN__", token)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if not self._authed(allow_query=False):
                return self._json(403, {"error": "missing token"})
            try:
                if path == "/api/state":
                    return self._json(200, collect_state(repo, user, read_only))
                m = re.match(r"^/api/wo/(WO-\d{3,})$", path)
                if m:
                    return self._json(200, collect_detail(repo, m.group(1)))
            except SystemExit as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_host() or not self._authed(allow_query=False):
                return self._json(403, {"error": "forbidden"})
            if urlparse(self.path).path != "/api/action":
                return self._json(404, {"error": "not found"})
            if read_only:
                return self._json(200, {"ok": False, "output": f"This dashboard is read-only: it was started inside an "
                                        f"AI agent session ({read_only}). Start `wo ui` from your own terminal to use buttons."})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or b"{}")
                state_props = collect_state(repo, user, read_only)["proposals"]
                cmd = build_command(body, user, state_props)
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(200, {"ok": False, "output": str(e)})
            return self._json(200, run_wo(repo, cmd))

    return Handler


def serve(args: argparse.Namespace) -> None:
    from factory.cli import agent_session_marker
    repo = Path(args.repo).resolve()
    if not (repo / "factory.toml").exists():
        raise SystemExit(f"No factory here ({repo}). Run `wo init` in your project first.")
    user = args.user or os.environ.get("WO_USER") or gitops.git(repo, "config", "user.name", check=False)
    if not user:
        raise SystemExit("Tell me who you are: wo ui --user YOUR_NAME")
    read_only = agent_session_marker()
    token = secrets.token_urlsafe(18)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(repo, user, token, read_only))
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    print(f"Work-order factory dashboard for {repo.name}, signed in as {user}"
          + (f" (READ-ONLY: started inside an AI agent session, {read_only})" if read_only else ""))
    print(f"Open: {url}")
    print("Only this computer can reach it. Press Ctrl+C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
