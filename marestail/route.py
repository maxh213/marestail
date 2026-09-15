import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = "https://github.com/maxh213/dandelion"
MODES = {"dandelion/route": (), "dandelion/route-best": ("--high",)}
BACKENDS = {"claude": "claude", "claude-work": "claude", "agy": "agy", "kimi": "kimi", "grok": "grok", "cursor": "cursor"}
NO_ROUTE = "none"
TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class Choice:
    line: str
    backend: str
    model: str
    effort: str | None
    env: dict[str, str] = field(default_factory=dict)


def is_routed(model: str | None) -> bool:
    return model in MODES


def binary() -> str:
    return os.environ.get("MARESTAIL_DANDELION", "dandelion")


def installed() -> bool:
    return shutil.which(binary()) is not None


def install_hint() -> str:
    return (
        f"dandelion is not installed: no `{binary()}` on PATH.\n"
        f"marestail route and the dandelion/route models need it to pick a subscription with quota left. Install it from {REPO}:\n"
        f"  git clone {REPO}.git\n"
        "  cd dandelion && npm ci && npm link\n"
        "It needs a Node.js that runs TypeScript directly (22.18 or newer).\n"
    )


def require() -> None:
    if not installed():
        raise SystemExit(install_hint().rstrip())


def command(args: list[str]) -> int:
    if not installed():
        sys.stderr.write(install_hint())
        return 127
    return subprocess.run([binary(), "route", *args], check=False).returncode


def choose(model: str, cwd: Path) -> tuple[Choice | None, str]:
    require()
    try:
        completed = subprocess.run([binary(), "route", *MODES[model]], cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return None, f"dandelion route timed out after {TIMEOUT_SECONDS}s"
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    line = lines[-1] if lines else ""
    if line == NO_ROUTE:
        return None, "no subscription has quota left"
    if completed.returncode != 0 or not line:
        detail = (completed.stderr.strip() or line or "no output")[-200:]
        return None, f"dandelion route failed with exit {completed.returncode}: {detail}"
    return parse(line), ""


def parse(line: str) -> Choice:
    words = line.split()
    if len(words) not in (2, 3):
        raise SystemExit(f"dandelion route printed {line!r}; expected `<model> [effort] <provider>`")
    provider = words[-1]
    if provider not in BACKENDS:
        raise SystemExit(f"dandelion route picked {provider!r}, which marestail has no backend for; it knows {', '.join(BACKENDS)}")
    effort = words[1] if len(words) == 3 else None
    return Choice(line, BACKENDS[provider], words[0], effort, account_env(provider))


def account_env(provider: str) -> dict[str, str]:
    if provider != "claude-work":
        return {}
    configured = os.environ.get("DANDELION_CLAUDE_WORK_CONFIG_DIR") or "~/.claude-work"
    return {"CLAUDE_CONFIG_DIR": str(Path(configured).expanduser())}
