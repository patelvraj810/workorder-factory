"""The gate: checks agent output BEFORE a human reviewer spends time on it.

Order: cheap and deterministic first, the reviewer agent last.
A human only sees a work order when the gate is green (or when they ask to look).

Every report is pinned to the commit it checked (`code_sha`). If code changes after
the gate ran, the approval is refused until the gate runs again.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from factory import gitops
from factory.agents import REVIEW_SCHEMA, AgentResult, render
from factory.policy import Policy

SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "generic_secret": re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{12,}['\"]"),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
}
TEST_CONFIG_FILES = {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass
class GateReport:
    work_order: str
    code_sha: str
    base: str
    passed: bool
    checks: list[Check] = field(default_factory=list)
    review: dict[str, Any] | None = None
    reviewer_model: str = ""
    cost_usd: float = 0.0
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def summary_md(self) -> str:
        lines = [f"Gate on `{self.code_sha[:10]}`: **{'PASS' if self.passed else 'FAIL'}**", ""]
        for c in self.checks:
            mark = "PASS" if c.passed else ("FAIL" if c.blocking else "WARN")
            lines.append(f"- [{mark}] **{c.name}**: {c.detail}")
        if self.review:
            lines.append(f"\nReviewer ({self.reviewer_model}) verdict: **{self.review.get('verdict')}** — {self.review.get('summary', '')}")
            for f in self.review.get("findings", []):
                lines.append(f"  - ({f.get('severity')}) {f.get('file', '')}: {f.get('issue')}"
                             + (f" Failing input: {f['failing_input']}" if f.get("failing_input") else ""))
        return "\n".join(lines)


def _is_agent(email: str) -> bool:
    return email in (gitops.AGENT_EMAIL, gitops.FACTORY_EMAIL)


def _is_pytest(argv: list[str]) -> bool:
    return bool(argv) and (argv[0] == "pytest" or argv[:3] == ["python", "-m", "pytest"]
                           or argv[:3] == ["python3", "-m", "pytest"])


def run_test_command(worktree: Path, cmd: str, invariants: bool) -> tuple[str, str]:
    """Run one configured test command. Returns (status, detail); status is pass | fail | none.

    pytest commands get extras: no cache, a fixed Hypothesis seed (reproducible), and for the
    invariants --noconftest, so an agent-written conftest.py can never influence them.
    Any other command (npm test, npx vitest run invariants/, go test ./...) runs as written.
    """
    argv = shlex.split(cmd)
    if _is_pytest(argv):
        rest = argv[1:] if argv[0] == "pytest" else argv[3:]
        argv = [sys.executable, "-m", "pytest", *rest, "-p", "no:cacheprovider", "--hypothesis-seed=0"]
        if invariants:
            argv.append("--noconftest")
    try:
        p = subprocess.run(argv, cwd=worktree, capture_output=True, text=True, timeout=900,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except FileNotFoundError:
        return "fail", f"command not found: {argv[0]} (check [gate] in factory.toml)"
    out = (p.stdout + p.stderr).strip()
    if p.returncode == 0:
        return "pass", out.splitlines()[-1] if out else "ok"
    if _is_pytest(shlex.split(cmd)) and (p.returncode == 5 or (p.returncode == 4 and "not found" in out)):
        return "none", "no tests found"
    # Keep the useful part: failing test names and Hypothesis' minimal failing example.
    lines = out.splitlines()
    keep = [ln for ln in lines if ln.startswith("FAILED")]
    for i, ln in enumerate(lines):
        if "Failing test case:" in ln or "Falsifying example:" in ln:
            for nxt in lines[i:i + 10]:
                clean = nxt.removeprefix("E").strip()
                keep.append("  " + clean)
                if clean == ")":
                    break
            break
    return "fail", "\n".join(keep) if keep else out[-1500:]


def check_tests(worktree: Path, name: str, cmd: str, invariants: bool) -> Check:
    if not cmd.strip():
        return Check(name, False, "not set up yet: add the command under gate in factory.toml", blocking=False)
    status, detail = run_test_command(worktree, cmd, invariants)
    if status == "none":
        msg = ("no invariants yet: a human should write the rules that must never break"
               if invariants else "no unit tests found")
        return Check(name, False, msg, blocking=False)
    return Check(name, status == "pass", detail)


def check_protected(worktree: Path, base: str, policy: Policy) -> Check:
    touched = [p for _, _, p in gitops.diff_numstat(worktree, base) if policy.is_protected(p)]
    if not touched:
        return Check("protected paths", True, "no human-owned files changed")
    bad, human = [], []
    for path in touched:
        for c in gitops.commits_touching(worktree, base, path):
            # Agents can't commit (no `git commit` on their allow-list); the factory commits their work as the
            # agent identity. So any other author is a person, whose change needs a CODEOWNER's review.
            if _is_agent(c["author_email"]):
                bad.append(f"{path} in {c['sha'][:8]} by {c['author_email']}")
            else:
                human.append(f"{path} ({c['author_email']})")
    if bad:
        return Check("protected paths", False, "agent changed human-owned files: " + "; ".join(bad))
    return Check("protected paths", True,
                 f"changed only by humans: {', '.join(sorted(set(human)))} — needs CODEOWNERS approval")


def check_test_config(worktree: Path, base: str) -> Check:
    bad = []
    for _, _, path in gitops.diff_numstat(worktree, base):
        if Path(path).name in TEST_CONFIG_FILES:
            for c in gitops.commits_touching(worktree, base, path):
                if _is_agent(c["author_email"]):
                    bad.append(path)
    if bad:
        return Check("test config tamper", False, f"agent changed test configuration: {sorted(set(bad))}")
    return Check("test config tamper", True, "no agent changes to conftest/pytest config")


def check_diff_size(worktree: Path, base: str, policy: Policy) -> Check:
    rows = [r for r in gitops.diff_numstat(worktree, base) if not r[2].startswith(".factory/")]
    total = sum(a + r for a, r, _ in rows)
    ok = total <= policy.max_diff_lines
    return Check("diff size", ok, f"{total} changed lines in {len(rows)} files (limit {policy.max_diff_lines})")


def _added_lines(diff: str) -> list[tuple[str, str]]:
    out, current = [], ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((current, line[1:]))
    return out


def check_secrets(diff: str) -> Check:
    hits = []
    for path, line in _added_lines(diff):
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(line):
                hits.append(f"{name} in {path}")
    return Check("secret scan", not hits, "; ".join(hits) if hits else "no secrets in added lines")


def check_no_float_money(diff: str, money_paths: list[str]) -> Check:
    """Repo-specific rule from AGENTS.md, as a cheap deterministic check."""
    hits = [f"{p}: {ln.strip()}" for p, ln in _added_lines(diff)
            if p.startswith(tuple(money_paths)) and re.search(r"\bfloat\s*\(|:\s*float\b|->\s*float\b", ln)]
    where = ", ".join(money_paths)
    return Check("no float on money", not hits, "; ".join(hits[:5]) if hits else f"no float in {where}")


def check_sast(worktree: Path, base: str) -> Check:
    files = [p for _, _, p in gitops.diff_numstat(worktree, base)
             if p.endswith(".py") and not p.startswith(("tests/", "invariants/", ".factory/")) and (worktree / p).exists()]
    if not files:
        return Check("SAST (bandit)", True, "no changed source files")
    p = subprocess.run([sys.executable, "-m", "bandit", "-q", "-f", "json", *files], cwd=worktree,
                       capture_output=True, text=True)
    try:
        results = json.loads(p.stdout).get("results", [])
    except json.JSONDecodeError:
        return Check("SAST (bandit)", False, f"bandit did not run: {p.stderr[-300:]}")
    serious = [r for r in results if r["issue_severity"] in ("MEDIUM", "HIGH")]
    if serious:
        return Check("SAST (bandit)", False,
                     "; ".join(f"{r['filename']}:{r['line_number']} {r['issue_text']}" for r in serious[:5]))
    return Check("SAST (bandit)", True, f"{len(files)} files, {len(results)} low-severity notes")


def run_checks(worktree: Path, base: str, wo_id: str, policy: Policy) -> GateReport:
    """Deterministic checks only. Cheap, repeatable, no model calls."""
    code_sha = gitops.head(worktree)
    diff = gitops.diff_text(worktree, base)
    checks = [
        check_protected(worktree, base, policy),
        check_test_config(worktree, base),
        check_diff_size(worktree, base, policy),
        check_secrets(diff),
        check_sast(worktree, base),
    ]
    if policy.money_paths:
        checks.insert(4, check_no_float_money(diff, policy.money_paths))
    checks.append(check_tests(worktree, "invariants (human-owned)", policy.invariant_cmd, invariants=True))
    checks.append(check_tests(worktree, "unit tests", policy.unit_test_cmd, invariants=False))
    report = GateReport(work_order=wo_id, code_sha=code_sha, base=base, passed=False, checks=checks)
    report.passed = all(c.passed or not c.blocking for c in checks)
    return report


def reviewer_prompt(report: GateReport, wo: dict[str, Any], diff: str) -> str:
    check_summary = "\n".join(f"- {c.name}: {'pass' if c.passed else 'FAIL'} — {c.detail.splitlines()[0] if c.detail else ''}"
                              for c in report.checks)
    return render("reviewer", wo_id=wo["id"], title=wo["title"], intent=wo["intent"],
                  invariants="\n".join(f"- {p}" for p in wo["invariants"]) or "- (repo invariants only)",
                  check_summary=check_summary, diff=diff[:60000])


def apply_review(report: GateReport, res: AgentResult, policy: Policy) -> None:
    """Add the independent reviewer's verdict as the last check."""
    report.review = res.structured
    report.reviewer_model = res.model
    report.cost_usd = res.cost_usd
    threshold = SEVERITY_RANK[policy.blocking_severity]
    blocking = [f for f in res.structured.get("findings", []) if SEVERITY_RANK.get(f.get("severity"), 0) >= threshold]
    report.checks.append(Check("independent reviewer agent", not blocking,
                               f"{res.structured.get('verdict')}: {len(blocking)} blocking finding(s), "
                               f"{len(res.structured.get('findings', []))} total"))
    report.passed = all(c.passed or not c.blocking for c in report.checks)


def run_gate(worktree: Path, base: str, wo: dict[str, Any], policy: Policy, backend, replay_key: str,
             run_review: bool = True) -> GateReport:
    """Convenience wrapper (used by evals). The CLI runs the reviewer through an audited session instead."""
    report = run_checks(worktree, base, wo["id"], policy)
    if run_review:
        prompt = reviewer_prompt(report, wo, gitops.diff_text(worktree, base))
        apply_review(report, backend.run("reviewer", prompt, worktree, REVIEW_SCHEMA, policy, replay_key=replay_key), policy)
    return report
