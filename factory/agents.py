"""Agent backends.

- ClaudeBackend: real agents via the Claude Agent SDK (uses your Claude Code login or API key).
- ReplayBackend: replays recorded agent output (patch + structured result). Used for tests,
  evals and as the demo backup when the network fails.

Both go through the same guard rules, so the safety story is identical.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factory.policy import Policy

PROMPTS = Path(__file__).parent / "prompts"

# --- Structured output schemas (the hand-off protocol between roles) -------------------

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "acceptance": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title", "acceptance"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["features", "risks"],
}

SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "feature_id": {"type": "string"},
        "status": {"type": "string", "enum": ["done", "blocked"]},
        "summary": {"type": "string"},
        "tests_added": {"type": "array", "items": {"type": "string"}},
        "notes_for_next_session": {"type": "string"},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["feature_id", "status", "summary", "notes_for_next_session"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "request_changes"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "file": {"type": "string"},
                    "issue": {"type": "string"},
                    "failing_input": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["severity", "issue"],
            },
        },
    },
    "required": ["verdict", "summary", "findings"],
}

ROLE_TOOLS = {
    "planner": ["Read", "Glob", "Grep"],
    "implementer": ["Read", "Glob", "Grep", "Write", "Edit", "Bash"],
    "reviewer": ["Read", "Glob", "Grep"],
}

ROLE_MODEL_ENV = {"planner": "WO_PLANNER_MODEL", "implementer": "WO_IMPLEMENTER_MODEL", "reviewer": "WO_REVIEWER_MODEL"}
# The reviewer defaults to a different model from the implementer on purpose: less shared blind spots.
ROLE_MODEL_DEFAULT = {"planner": "opus", "implementer": "sonnet", "reviewer": "opus"}


def model_for(role: str) -> str:
    return os.environ.get(ROLE_MODEL_ENV[role], ROLE_MODEL_DEFAULT[role])


def render(template: str, **values: Any) -> str:
    text = (PROMPTS / f"{template}.md").read_text()
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


# --- Guard: the rules every tool call must pass ---------------------------------------

def _inside(path: str, root: Path) -> tuple[bool, str]:
    p = Path(path)
    full = (p if p.is_absolute() else root / p).resolve()
    try:
        rel = full.relative_to(root.resolve())
    except ValueError:
        return False, str(full)
    return True, str(rel)


def guard(tool: str, tool_input: dict[str, Any], worktree: Path, policy: Policy) -> tuple[bool, str]:
    """Return (allowed, reason). Pure function so it is easy to test."""
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        ok, rel = _inside(tool_input.get("file_path", ""), worktree)
        if not ok:
            return False, f"write outside the work-order sandbox: {rel}"
        if policy.is_protected(rel) or rel.startswith(".factory"):
            return False, f"'{rel}' is human-owned (protected path)"
        if policy.is_secret(rel):
            return False, f"'{rel}' is a secret file"
        return True, ""
    if tool in ("Read", "Glob", "Grep"):
        target = tool_input.get("file_path") or tool_input.get("path") or "."
        ok, rel = _inside(target, worktree)
        if not ok:
            return False, f"read outside the work-order sandbox: {rel}"
        if policy.is_secret(rel):
            return False, f"'{rel}' is a secret file"
        return True, ""
    if tool == "Bash":
        cmd = tool_input.get("command", "")
        if not policy.bash_allowed(cmd):
            return False, f"command not on the allow-list: {cmd!r}"
        return True, ""
    return False, f"tool '{tool}' is not allowed for this role"


@dataclass
class AgentResult:
    role: str
    model: str
    structured: dict[str, Any]
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    session_id: str = ""
    tool_log: list[dict[str, str]] = field(default_factory=list)
    denials: list[dict[str, str]] = field(default_factory=list)
    replayed: bool = False


def _live(path: Path | None, entry: dict[str, str]) -> None:
    """Write each tool call to disk the moment it happens, so a crash can't erase what the agent touched."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())


# --- Real backend -----------------------------------------------------------------------

class ClaudeBackend:
    name = "claude"
    live_log: Path | None = None

    def run(self, role: str, prompt: str, cwd: Path, schema: dict, policy: Policy, replay_key: str = "") -> AgentResult:
        import anyio

        return anyio.run(self._run, role, prompt, cwd, schema, policy)

    async def _run(self, role: str, prompt: str, cwd: Path, schema: dict, policy: Policy) -> AgentResult:
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, ResultMessage, query

        tool_log: list[dict[str, str]] = []
        denials: list[dict[str, str]] = []
        tools = ROLE_TOOLS[role]

        async def pre_tool(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            tool, tin = input_data.get("tool_name", ""), input_data.get("tool_input", {})
            if tool not in tools:
                allowed, reason = False, f"tool '{tool}' not allowed for {role}"
            else:
                allowed, reason = guard(tool, tin, cwd, policy)
            target = tin.get("file_path") or tin.get("path") or tin.get("command") or tin.get("pattern") or ""
            tool_log.append({"tool": tool, "target": str(target)[:200], "allowed": str(allowed)})
            _live(self.live_log, {**tool_log[-1], **({"reason": reason} if not allowed else {})})
            if allowed:
                return {}
            denials.append({"tool": tool, "target": str(target)[:200], "reason": reason})
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": f"Blocked by factory policy: {reason}"}}

        model = model_for(role)
        options = ClaudeAgentOptions(
            cwd=str(cwd),
            model=model,
            tools=tools,
            allowed_tools=tools,
            permission_mode="dontAsk",
            setting_sources=["project"],  # loads the repo's CLAUDE.md -> AGENTS.md
            system_prompt={"type": "preset", "preset": "claude_code"},
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool])]},
            output_format={"type": "json_schema", "schema": schema},
            max_budget_usd=policy.max_session_budget_usd,
            max_turns=60,
        )
        result: ResultMessage | None = None
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                result = msg
        if result is None or result.is_error or not result.structured_output:
            detail = (result.result or result.subtype) if result else "no result"
            raise RuntimeError(f"{role} agent did not return a valid result: {detail}")
        usage = result.usage or {}
        return AgentResult(
            role=role, model=model, structured=result.structured_output,
            cost_usd=float(result.total_cost_usd or 0.0),
            input_tokens=int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            session_id=result.session_id, tool_log=tool_log, denials=denials,
        )


# --- Replay backend ---------------------------------------------------------------------

class ReplayBackend:
    """Replays a recorded run: `<dir>/<key>.json` (structured result + meta) and optional `<key>.patch`.

    The patch is checked against the same guard as a live agent: a recorded patch that
    touches a protected path is blocked exactly like a live write would be.
    """

    name = "replay"
    live_log: Path | None = None

    def __init__(self, replay_dir: Path):
        self.dir = replay_dir

    def run(self, role: str, prompt: str, cwd: Path, schema: dict, policy: Policy, replay_key: str = "") -> AgentResult:
        rec_path = self.dir / f"{replay_key}.json"
        if not rec_path.exists():
            raise FileNotFoundError(f"No recording for '{replay_key}' in {self.dir}")
        rec = json.loads(rec_path.read_text())
        denials: list[dict[str, str]] = []
        tool_log: list[dict[str, str]] = []
        patch = self.dir / f"{replay_key}.patch"
        if patch.exists():
            files = subprocess.run(["git", "apply", "--numstat", str(patch)], cwd=cwd, capture_output=True, text=True).stdout
            touched = [line.split("\t")[2] for line in files.splitlines() if line.strip()]
            blocked = []
            for rel in touched:
                ok, reason = guard("Write", {"file_path": rel}, cwd, policy)
                tool_log.append({"tool": "Write", "target": rel, "allowed": str(ok)})
                _live(self.live_log, tool_log[-1])
                if not ok:
                    blocked.append(rel)
                    denials.append({"tool": "Write", "target": rel, "reason": reason})
            excludes = [f"--exclude={b}" for b in blocked]
            p = subprocess.run(["git", "apply", *excludes, str(patch)], cwd=cwd, capture_output=True, text=True)
            if p.returncode != 0:
                raise RuntimeError(f"replay patch {patch.name} did not apply: {p.stderr}")
            if os.environ.get("WO_SIMULATE_CRASH") == replay_key:
                # Demo/test only: die like a laptop losing power mid-session. No handler runs.
                os._exit(137)
        meta = rec.get("meta", {})
        return AgentResult(
            role=role, model=f"replay:{meta.get('model', model_for(role))}", structured=rec["output"],
            cost_usd=float(meta.get("cost_usd", 0.0)), input_tokens=int(meta.get("input_tokens", 0)),
            output_tokens=int(meta.get("output_tokens", 0)), session_id=f"replay-{replay_key}",
            tool_log=tool_log, denials=denials, replayed=True,
        )


def get_backend(name: str, replay_dir: Path | None = None):
    if name == "claude":
        return ClaudeBackend()
    if name == "replay":
        if replay_dir is None:
            raise ValueError("--replay-dir is required for the replay backend")
        return ReplayBackend(replay_dir)
    raise ValueError(f"unknown backend {name}")
