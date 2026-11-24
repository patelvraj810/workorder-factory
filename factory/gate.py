"""The gate: checks agent output BEFORE a human reviewer spends time on it.

Order: cheap and deterministic first, the reviewer agent last.
A human only sees a work order when the gate is green (or when they ask to look).

Every report is pinned to the commit it checked (`code_sha`). If code changes after
the gate ran, the approval is refused until the gate runs again.
"""
from __future__ import annotations

import json
import re
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


def _pytest(worktree: Path, target: str) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pytest", "-q", target, "-p", "no:cacheprovider", "--hypothesis-seed=0"]
    if target.startswith("invariants"):
        cmd.append("--noconftest")  # agent-written conftest.py can never influence the invariants
    p = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, timeout=600)
    out = (p.stdout + p.stderr).strip()
    if p.returncode == 0:
        return True, out.splitlines()[-1] if out else "ok"
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
    return False, "\n".join(keep) if keep else out[-1500:]


def check_protected(worktree: Path, base: str, policy: Policy) -> Check:
    touched = [p for _, _, p in gitops.diff_numstat(worktree, base) if policy.is_protected(p)]
    if not touched:
        return Check("protected paths", True, "no human-owned files changed")
    bad, human = [], []
    for path in touched:
        for c in gitops.commits_touching(worktree, base, path):
            if _is_agent(c["author_email"]) or "Change-Type: human-invariant" not in c["body"]:
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


def check_no_float_money(diff: str) -> Check:
    """Repo-specific rule from AGENTS.md, as a cheap deterministic check."""
    hits = [f"{p}: {ln.strip()}" for p, ln in _added_lines(diff)
            if p.startswith("orders/") and re.search(r"\bfloat\s*\(|:\s*float\b|->\s*float\b", ln)]
    return Check("no float on money", not hits, "; ".join(hits[:5]) if hits else "no float in orders/")


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


def run_gate(worktree: Path, base: str, wo: dict[str, Any], policy: Policy, backend, replay_key: str,
             run_review: bool = True) -> GateReport:
    code_sha = gitops.head(worktree)
    diff = gitops.diff_text(worktree, base)
    checks = [
        check_protected(worktree, base, policy),
        check_test_config(worktree, base),
        check_diff_size(worktree, base, policy),
        check_secrets(diff),
        check_no_float_money(diff),
        check_sast(worktree, base),
    ]
    ok, detail = _pytest(worktree, "invariants/")
    checks.append(Check("invariants (human-owned)", ok, detail))
    ok, detail = _pytest(worktree, "tests/")
    checks.append(Check("unit tests", ok, detail))

    report = GateReport(work_order=wo["id"], code_sha=code_sha, base=base, passed=False, checks=checks)
    if run_review:
        check_summary = "\n".join(f"- {c.name}: {'pass' if c.passed else 'FAIL'} — {c.detail.splitlines()[0] if c.detail else ''}"
                                  for c in checks)
        prompt = render("reviewer", wo_id=wo["id"], title=wo["title"], intent=wo["intent"],
                        invariants="\n".join(f"- {p}" for p in wo["invariants"]) or "- (repo invariants only)",
                        check_summary=check_summary, diff=diff[:60000])
        res: AgentResult = backend.run("reviewer", prompt, worktree, REVIEW_SCHEMA, policy, replay_key=replay_key)
        report.review = res.structured
        report.reviewer_model = res.model
        report.cost_usd = res.cost_usd
        threshold = SEVERITY_RANK[policy.blocking_severity]
        blocking = [f for f in res.structured.get("findings", []) if SEVERITY_RANK.get(f.get("severity"), 0) >= threshold]
        checks.append(Check("independent reviewer agent", not blocking,
                            f"{res.structured.get('verdict')}: {len(blocking)} blocking finding(s), "
                            f"{len(res.structured.get('findings', []))} total"))
    report.passed = all(c.passed or not c.blocking for c in checks)
    return report
