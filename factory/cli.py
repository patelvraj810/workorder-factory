"""`wo` — the work-order CLI. The interface an engineer uses to start, steer and review agent work.

  wo new      human opens a work order: intent + human-owned invariants
  wo plan     planner agent splits it into small features
  wo run      implementer agent does ONE feature (or fixes gate failures) per session
  wo gate     deterministic checks + independent reviewer agent, pinned to a commit
  wo pr       pull request text with full provenance
  wo approve  a DIFFERENT human approves (and optionally merges)
  wo status / wo stats / wo ledger verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from factory import gitops
from factory.agents import PLAN_SCHEMA, SESSION_SCHEMA, get_backend, model_for, render
from factory.gate import run_gate
from factory.ledger import Ledger
from factory.policy import load_policy_at
from factory.workorder import Feature, WorkOrder, WorkOrderStore

console = Console()


# --- helpers -------------------------------------------------------------------------------

class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.repo = Path(args.repo).resolve()
        self.policy = load_policy_at(self.repo, getattr(args, 'base', None) or 'main')
        self.ledger = Ledger(self.repo / ".factory" / "ledger.jsonl")
        self.backend_name = getattr(args, "backend", None) or os.environ.get("WO_BACKEND", "claude")
        rd = getattr(args, "replay_dir", None) or os.environ.get("WO_REPLAY_DIR")
        self.replay_dir = Path(rd).resolve() if rd else None
        rec = getattr(args, "record", None)
        self.record_dir = Path(rec).resolve() if rec else None

    def backend(self):
        return get_backend(self.backend_name, self.replay_dir)

    def worktree(self, wo_id: str) -> Path:
        return self.repo / ".factory" / "worktrees" / wo_id

    def store(self, wo_id: str) -> WorkOrderStore:
        wt = self.worktree(wo_id)
        if not wt.exists():
            raise SystemExit(f"Unknown work order {wo_id}")
        return WorkOrderStore(wt, wo_id)

    def events(self, wo_id: str, name: str) -> list[dict]:
        return [e for e in self.ledger.for_work_order(wo_id) if e["event"] == name]

    def record(self, key: str, result, patch: str | None = None) -> None:
        if not self.record_dir:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        meta = {"model": result.model, "cost_usd": result.cost_usd, "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens, "recorded_from": "live run"}
        (self.record_dir / f"{key}.json").write_text(json.dumps({"meta": meta, "output": result.structured}, indent=2))
        if patch:
            (self.record_dir / f"{key}.patch").write_text(patch + "\n")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


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


# --- commands ------------------------------------------------------------------------------

def cmd_new(args) -> None:
    ctx = Ctx(args)
    n = len([e for e in ctx.ledger.entries() if e["event"] == "workorder.created"]) + 1
    wo_id = f"WO-{n:03d}"
    branch = f"wo/{wo_id}"
    wt = ctx.worktree(wo_id)
    gitops.git(ctx.repo, "worktree", "add", "-q", "-b", branch, str(wt), args.base)

    # 1) The human's invariants land first, authored by the human.
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

    # 2) The work order itself.
    wo = WorkOrder(id=wo_id, title=args.title, intent=args.intent, requester=args.by,
                   base_branch=args.base, branch=branch, invariants=inv_paths)
    store = WorkOrderStore(wt, wo_id)
    store.save_wo(wo)
    store.append_progress(f"# Progress log — {wo_id}: {wo.title}\n\nOpened by {wo.requester}. No sessions yet.")
    sha = gitops.commit(wt, f"{wo_id}: open work order", args.by, gitops.human_email(args.by),
                        {"Work-Order": wo_id, "Change-Type": "work-order"})
    ctx.ledger.append("workorder.created", wo_id, args.by, title=wo.title, intent=wo.intent, base=args.base,
                      branch=branch, commit=sha,
                      invariants={p: sha256_file(wt / p) for p in inv_paths})
    console.print(f"[bold green]Opened {wo_id}[/] on branch [cyan]{branch}[/] (sandbox: {wt.relative_to(ctx.repo)})")
    console.print(f"Next: [bold]wo plan {wo_id}[/]")


def cmd_plan(args) -> None:
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)
    prompt = render("planner", wo_id=wo.id, title=wo.title, requester=wo.requester, intent=wo.intent,
                    invariants="\n".join(f"- `{p}`" for p in wo.invariants) or "- (repo invariants only)")
    with console.status(f"Planner agent ({model_for('planner')}) is reading the code..."):
        res = ctx.backend().run("planner", prompt, wt, PLAN_SCHEMA, ctx.policy, replay_key="plan")
    ctx.record("plan", res)
    features = [Feature(id=f["id"], title=f["title"], acceptance=f["acceptance"]) for f in res.structured["features"]]
    store.save_features(features)
    risks = "\n".join(f"- {r}" for r in res.structured.get("risks", []))
    feats = "\n".join(f"- **{f.id}** {f.title}" for f in features)
    store.append_progress(f"## Plan ({res.model}, ${res.cost_usd:.2f})\n{feats}\n\n**Risks flagged by planner:**\n{risks}")
    sha = gitops.commit(wt, f"{wo.id}: plan ({len(features)} features)", gitops.AGENT_NAME, gitops.AGENT_EMAIL,
                        agent_trailers(wo, ctx, **{"Agent-Role": "planner", "Agent-Model": res.model,
                                                   "Agent-Session": res.session_id}))
    ctx.ledger.append("plan.created", wo.id, f"agent:{res.model}", features=[f.id for f in features],
                      risks=res.structured.get("risks", []), cost_usd=res.cost_usd, commit=sha,
                      tokens={"in": res.input_tokens, "out": res.output_tokens})
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
    if derive_status(ctx, wo.id) in ("approved", "merged"):
        raise SystemExit(f"{wo.id} is already approved. Open a new work order for more changes.")

    last_gate = store.last_gate()
    sessions = ctx.events(wo.id, "session.completed")
    fixing = bool(last_gate and not last_gate["passed"]
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
        res = ctx.backend().run("implementer", prompt, wt, SESSION_SCHEMA, ctx.policy, replay_key=key)

    # Record the raw change before the factory commits it (for replays/evals).
    gitops.git(wt, "add", "-A")
    patch = gitops.git(wt, "diff", "--cached", "--", ".", ":(exclude).factory")
    ctx.record(key, res, patch)
    changed = [ln.split("\t")[2] for ln in gitops.git(wt, "diff", "--cached", "--numstat").splitlines()]

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
    ctx.ledger.append("session.completed", wo.id, f"agent:{res.model}", n=n, feature=feature.id,
                      status=out["status"], commit=sha, files_changed=changed, cost_usd=res.cost_usd,
                      tokens={"in": res.input_tokens, "out": res.output_tokens},
                      tool_calls=len(res.tool_log), denials=res.denials,
                      after_gate=last_gate["number"] if last_gate else 0)

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
    key = f"review-{len(store.gate_reports()) + 1}"
    with console.status("Gate: tests, invariants, scans, then independent reviewer..."):
        report = run_gate(wt, wo.base_branch, json.loads((store.dir / "workorder.json").read_text()),
                          ctx.policy, ctx.backend(), key, run_review=not args.no_review)
    if report.review is not None and ctx.record_dir:
        class _R:  # minimal shim for record()
            model, cost_usd, input_tokens, output_tokens, structured = report.reviewer_model, report.cost_usd, 0, 0, report.review
        ctx.record(key, _R())
    d = report.to_dict()
    d["summary_md"] = report.summary_md()
    path = store.save_gate(d)
    sha = gitops.commit(wt, f"{wo.id}: gate {d['number']} {'PASS' if report.passed else 'FAIL'}",
                        gitops.FACTORY_NAME, gitops.FACTORY_EMAIL,
                        {"Work-Order": wo.id, "Gate-Code-Sha": report.code_sha, "Gate-Result": "pass" if report.passed else "fail"})
    ctx.ledger.append("gate.result", wo.id, "factory", number=d["number"], code_sha=report.code_sha,
                      passed=report.passed, failed=[c.name for c in report.checks if not c.passed],
                      reviewer_model=report.reviewer_model,
                      reviewer_verdict=(report.review or {}).get("verdict"), cost_usd=report.cost_usd, commit=sha)

    table = Table(title=f"{wo.id} gate {d['number']} on {report.code_sha[:10]}")
    table.add_column("Check"); table.add_column("Result"); table.add_column("Detail", overflow="fold")
    for c in report.checks:
        res = "[green]PASS[/]" if c.passed else "[red]FAIL[/]"
        table.add_row(c.name, res, c.detail[:600])
    console.print(table)
    if report.review:
        for f in report.review.get("findings", []):
            sev = f.get("severity")
            col = {"high": "red", "medium": "yellow"}.get(sev, "white")
            console.print(f"[{col}]({sev})[/] {f.get('file', '')}: {f.get('issue')}"
                          + (f"\n    failing input: {f['failing_input']}" if f.get("failing_input") else ""))
    if report.passed:
        console.print(f"[bold green]GATE PASS[/]. A human reviewer can look now. Next: [bold]wo pr {wo.id}[/]")
    else:
        console.print(f"[bold red]GATE FAIL[/]. No human time spent yet. Next: [bold]wo run {wo.id}[/] (fix session gets this report)")
    console.print(f"[dim]report: {path.relative_to(ctx.repo)}[/]")


def cmd_pr(args) -> None:
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)
    gate = store.last_gate()
    events = ctx.ledger.for_work_order(wo.id)
    sessions = [e for e in events if e["event"] == "session.completed"]
    total_cost = sum(e["data"].get("cost_usd", 0) for e in events)
    feats = "\n".join(f"| {f.id} | {f.title} | {f.status} | {', '.join(f.sessions)} |" for f in store.load_features())
    sess = "\n".join(f"| {e['data']['n']} | {e['data']['feature']} | {e['actor']} | {e['data']['status']} | "
                     f"${e['data']['cost_usd']:.2f} | {len(e['data']['denials'])} | `{e['data']['commit'][:8]}` |"
                     for e in sessions)
    diffstat = gitops.git(wt, "diff", "--stat", f"{wo.base_branch}...HEAD", "--", ".", ":(exclude).factory")
    body = f"""# {wo.id}: {wo.title}

## Intent (from {wo.requester})
{wo.intent}

## Features
| ID | Title | Status | Sessions |
|---|---|---|---|
{feats}

## Gate
{gate['summary_md'] if gate else 'Gate has not run.'}

## Agent sessions
| # | Feature | Agent | Status | Cost | Blocked calls | Commit |
|---|---|---|---|---|---|---|
{sess}

## Change size
```
{diffstat}
```

## Provenance
- **Accountable owner (author of record):** {wo.requester}
- **Code drafted by:** agents listed above (commit author `{gitops.AGENT_NAME}`, trailers carry model + session)
- **Human-owned invariants:** {', '.join(f'`{p}`' for p in wo.invariants) or 'repo invariants only'}
- **Approver:** pending — must be a human other than {wo.requester}
- **Total agent cost:** ${total_cost:.2f}
- **Ledger head:** `{ctx.ledger.head()[:16]}` (verify with `wo ledger verify`)

## What the reviewer should look at
Read `.factory/{wo.id}/progress.md` for the session log, then focus on the risks the planner flagged.
"""
    (store.dir / "PR.md").write_text(body)
    gitops.commit(wt, f"{wo.id}: PR description", gitops.FACTORY_NAME, gitops.FACTORY_EMAIL, {"Work-Order": wo.id})
    ctx.ledger.append("pr.prepared", wo.id, "factory", path=f".factory/{wo.id}/PR.md")
    console.print(Markdown(body))
    console.print(f"[dim]Written to .factory/{wo.id}/PR.md. With a remote: gh pr create -F .factory/{wo.id}/PR.md[/]")


def cmd_approve(args) -> None:
    ctx = Ctx(args)
    store = ctx.store(args.wo)
    wo = store.load_wo()
    wt = ctx.worktree(wo.id)

    def refuse(reason: str) -> None:
        ctx.ledger.append("approval.refused", wo.id, args.by, reason=reason)
        console.print(f"[bold red]Approval refused:[/] {reason}")
        raise SystemExit(1)

    if ctx.policy.require_independent_approver and args.by == wo.requester:
        refuse(f"{args.by} requested this change. The approver must be a different person.")
    gate = store.last_gate()
    if not gate or not gate["passed"]:
        refuse("the latest gate did not pass")
    code_changes = gitops.git(wt, "diff", "--name-only", gate["code_sha"], "HEAD", "--", ".", ":(exclude).factory")
    if code_changes:
        refuse(f"code changed after gate {gate['number']} ran ({code_changes.split()[0]}...). Run `wo gate` again.")

    entry = ctx.ledger.append("approval.granted", wo.id, args.by, gate=gate["number"], code_sha=gate["code_sha"],
                              note=args.note or "")
    console.print(f"[bold green]Approved[/] by {args.by} (requester {wo.requester}) at gate {gate['number']} "
                  f"on {gate['code_sha'][:10]}. Ledger entry {entry['hash'][:12]}.")
    if args.merge:
        msg = (f"Merge {wo.id}: {wo.title}\n\nWork-Order: {wo.id}\nAccountable-Owner: {wo.requester}\n"
               f"Approved-By: {args.by}\nGate: {gate['number']} pass on {gate['code_sha']}\nLedger-Entry: {entry['hash']}")
        gitops.git(ctx.repo, "-c", f"user.name={args.by}", "-c", f"user.email={gitops.human_email(args.by)}",
                   "merge", "--no-ff", "-q", wo.branch, "-m", msg)
        sha = gitops.head(ctx.repo)
        ctx.ledger.append("merged", wo.id, args.by, commit=sha, into=wo.base_branch)
        console.print(f"[bold green]Merged[/] {wo.branch} into {wo.base_branch} as {sha[:10]}. "
                      "Production deploy stays in your normal release pipeline.")


def cmd_status(args) -> None:
    ctx = Ctx(args)
    ids = sorted({e["work_order"] for e in ctx.ledger.entries() if e["work_order"]})
    if args.wo:
        store = ctx.store(args.wo)
        wo = store.load_wo()
        console.print(f"[bold]{wo.id}[/] {wo.title} — [cyan]{derive_status(ctx, wo.id)}[/] (owner {wo.requester})")
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
                  f"${sum(e['data'].get('cost_usd', 0) for e in ev):.2f}")
    console.print(t)


def cmd_stats(args) -> None:
    """What a team lead sees: is this working, and is it getting better?"""
    ctx = Ctx(args)
    entries = ctx.ledger.entries()
    wos = sorted({e["work_order"] for e in entries if e["work_order"]})
    gates = [e for e in entries if e["event"] == "gate.result"]
    first_gates = {}
    for g in gates:
        first_gates.setdefault(g["work_order"], g)
    failed_checks: dict[str, int] = {}
    for g in gates:
        for c in g["data"]["failed"]:
            failed_checks[c] = failed_checks.get(c, 0) + 1
    sessions = [e for e in entries if e["event"] == "session.completed"]
    merged = [e for e in entries if e["event"] == "merged"]
    denials = sum(len(e["data"]["denials"]) for e in sessions)
    cost = sum(e["data"].get("cost_usd", 0) for e in entries)
    t = Table(title="Factory stats (from the ledger)")
    t.add_column("Metric"); t.add_column("Value")
    t.add_row("Work orders opened / merged", f"{len(wos)} / {len(merged)}")
    t.add_row("Agent sessions", str(len(sessions)))
    t.add_row("Gate runs / pass rate", f"{len(gates)} / {100 * sum(g['data']['passed'] for g in gates) / max(len(gates), 1):.0f}%")
    t.add_row("First gate pass rate (agent got it right first time)",
              f"{100 * sum(g['data']['passed'] for g in first_gates.values()) / max(len(first_gates), 1):.0f}%")
    t.add_row("Gate failures by check", ", ".join(f"{k}: {v}" for k, v in sorted(failed_checks.items())) or "none")
    t.add_row("Blocked tool calls (policy)", str(denials))
    t.add_row("Approvals refused", str(sum(e["event"] == "approval.refused" for e in entries)))
    t.add_row("Total agent cost", f"${cost:.2f}")
    t.add_row("Cost per merged work order", f"${cost / max(len(merged), 1):.2f}")
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

    s = sub.add_parser("new", help="open a work order"); s.set_defaults(fn=cmd_new)
    s.add_argument("title"); s.add_argument("--intent", required=True); s.add_argument("--by", required=True)
    s.add_argument("--invariant", action="append", help="human-written invariant test file (repeatable)")
    s.add_argument("--base", default="main")
    s = sub.add_parser("plan", help="planner agent splits the work"); s.set_defaults(fn=cmd_plan); s.add_argument("wo"); agent_opts(s)
    s = sub.add_parser("run", help="one implementer session"); s.set_defaults(fn=cmd_run); s.add_argument("wo"); agent_opts(s)
    s = sub.add_parser("gate", help="checks + reviewer agent"); s.set_defaults(fn=cmd_gate); s.add_argument("wo"); agent_opts(s)
    s.add_argument("--no-review", action="store_true", help="skip the reviewer agent (deterministic checks only)")
    s = sub.add_parser("pr", help="write the PR description"); s.set_defaults(fn=cmd_pr); s.add_argument("wo")
    s = sub.add_parser("approve", help="independent human approval"); s.set_defaults(fn=cmd_approve)
    s.add_argument("wo"); s.add_argument("--by", required=True); s.add_argument("--note"); s.add_argument("--merge", action="store_true")
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
