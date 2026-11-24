"""Thin git helpers. Every agent works in its own git worktree (one per work order)."""
from __future__ import annotations

import subprocess
from pathlib import Path

AGENT_NAME = "wo-agent"
AGENT_EMAIL = "wo-agent@factory.invalid"
FACTORY_NAME = "wo-factory"
FACTORY_EMAIL = "wo-factory@factory.invalid"


class GitError(RuntimeError):
    pass


def git(cwd: Path, *args: str, check: bool = True, env: dict | None = None) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{p.stderr.strip()}")
    return p.stdout.strip()


def human_email(name: str) -> str:
    return f"{name}@example.com"


def commit(cwd: Path, message: str, author_name: str, author_email: str, trailers: dict[str, str]) -> str:
    """Commit all changes. Author = who wrote it. Committer = the factory. Trailers = provenance."""
    git(cwd, "add", "-A")
    if not git(cwd, "status", "--porcelain"):
        return git(cwd, "rev-parse", "HEAD")
    body = message.rstrip() + "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    git(
        cwd,
        "-c", f"user.name={FACTORY_NAME}", "-c", f"user.email={FACTORY_EMAIL}",
        "commit", "-q", "--author", f"{author_name} <{author_email}>", "-m", body,
    )
    return git(cwd, "rev-parse", "HEAD")


def default_base(repo: Path) -> str:
    """The branch work orders start from: main, else master, else whatever is checked out."""
    for name in ("main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False):
            return name
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False) or "main"


def head(cwd: Path) -> str:
    return git(cwd, "rev-parse", "HEAD")


def diff_numstat(cwd: Path, base: str) -> list[tuple[int, int, str]]:
    out = git(cwd, "diff", "--numstat", f"{base}...HEAD")
    rows = []
    for line in out.splitlines():
        a, r, path = line.split("\t", 2)
        rows.append((int(a) if a != "-" else 0, int(r) if r != "-" else 0, path))
    return rows


def diff_text(cwd: Path, base: str, exclude_factory: bool = True) -> str:
    args = ["diff", f"{base}...HEAD", "--", "."]
    if exclude_factory:
        args.append(":(exclude).factory")
    return git(cwd, *args)


def commits_touching(cwd: Path, base: str, path_prefix: str) -> list[dict[str, str]]:
    """Commits in base..HEAD that touch `path_prefix`, with author email and body."""
    shas = git(cwd, "log", "--format=%H", f"{base}..HEAD", "--", path_prefix).split()
    out = []
    for sha in shas:
        out.append({
            "sha": sha,
            "author_email": git(cwd, "show", "-s", "--format=%ae", sha),
            "body": git(cwd, "show", "-s", "--format=%B", sha),
        })
    return out
