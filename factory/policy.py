"""Load the repo's factory.toml policy (human-owned)."""
from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Policy:
    protected_paths: list[str] = field(default_factory=list)
    secret_paths: list[str] = field(default_factory=list)
    max_diff_lines: int = 250
    max_session_budget_usd: float = 1.50
    allowed_bash: list[str] = field(default_factory=list)
    require_independent_approver: bool = True
    blocking_severity: str = "high"
    kill_switch: bool = False
    max_gate_failures: int = 3
    max_workorder_budget_usd: float = 5.00
    # [gate]: how this project runs its checks. Python/pytest by default; any command works.
    setup_cmd: str = ""
    unit_test_cmd: str = "python -m pytest -q tests/"
    invariant_cmd: str = "python -m pytest -q invariants/"
    money_paths: list[str] = field(default_factory=list)

    def is_protected(self, rel_path: str) -> bool:
        rel = rel_path.lstrip("./")
        for p in self.protected_paths:
            if p.endswith("/") and (rel == p.rstrip("/") or rel.startswith(p)):
                return True
            if rel == p:
                return True
        return False

    def is_secret(self, rel_path: str) -> bool:
        name = Path(rel_path).name
        return any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_path, pat) for pat in self.secret_paths)

    def bash_allowed(self, command: str) -> bool:
        cmd = command.strip()
        # No chaining or redirection tricks: one simple command only.
        if any(tok in cmd for tok in (";", "&&", "||", "|", "`", "$(", ">", "<")):
            return False
        return any(cmd == a or cmd.startswith(a + " ") for a in self.allowed_bash)


def load_policy_at(repo: Path, ref: str) -> Policy:
    """Policy from a trusted ref (the base branch), never from the branch under review.

    Otherwise an agent could loosen its own limits in the same change it is judged on.
    """
    import subprocess
    p = subprocess.run(["git", "show", f"{ref}:factory.toml"], cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        raise FileNotFoundError(f"No factory.toml on {ref}. A human must add one first.")
    return _parse(p.stdout)


def load_policy(repo: Path) -> Policy:
    path = repo / "factory.toml"
    if not path.exists():
        raise FileNotFoundError(f"No factory.toml in {repo}. A human must add one first.")
    return _parse(path.read_text())


def _parse(text: str) -> Policy:
    data = tomllib.loads(text)
    pol, rev, gate = data.get("policy", {}), data.get("review", {}), data.get("gate", {})
    return Policy(
        protected_paths=pol.get("protected_paths", []),
        secret_paths=pol.get("secret_paths", []),
        max_diff_lines=int(pol.get("max_diff_lines", 250)),
        max_session_budget_usd=float(pol.get("max_session_budget_usd", 1.5)),
        allowed_bash=pol.get("allowed_bash", []),
        require_independent_approver=bool(rev.get("require_independent_approver", True)),
        blocking_severity=rev.get("blocking_severity", "high"),
        kill_switch=bool(pol.get("kill_switch", False)),
        max_gate_failures=int(pol.get("max_gate_failures", 3)),
        max_workorder_budget_usd=float(pol.get("max_workorder_budget_usd", 5.0)),
        setup_cmd=str(gate.get("setup", "")),
        unit_test_cmd=str(gate.get("unit_tests", "python -m pytest -q tests/")),
        invariant_cmd=str(gate.get("invariants", "python -m pytest -q invariants/")),
        money_paths=list(gate.get("money_paths", [])),
    )


def policy_sha(repo: Path, ref: str) -> str:
    import hashlib
    import subprocess
    p = subprocess.run(["git", "show", f"{ref}:factory.toml"], cwd=repo, capture_output=True, text=True)
    return hashlib.sha256(p.stdout.encode()).hexdigest()
