import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from marestail._guidance import uses_csharp
from marestail._install import (
    AGENTS_MD,
    CLAUDE_MD,
    CONFIG,
    GITIGNORE_LINES,
    PERFORMANCE,
    SONAR,
    TEMPLATES,
    merge_agy_hook,
    merge_cursor_hook,
    merge_grok_hook,
    merge_hook,
    trust_grok_folder,
)
from marestail._tooling import TOOLING, Tooling, write_tooling
from marestail.shell import ensure_dir, run

HYPER_START = "# marestail (install --scope hyper)"
HYPER_END = "# end marestail"
CLAUDE_LOCAL = ".claude/settings.local.json"
AGY_HOOKS = ".agents/hooks.json"
GROK_HOOKS = ".grok/hooks/marestail-gate.json"
CURSOR_HOOKS = ".cursor/hooks.json"
TASKS_README = "tasks/README.md"
TS_GUIDANCE = "guidance/ts.md"
CS_GUIDANCE = "guidance/cs.md"
HYPER_EXCLUDES = [
    CONFIG,
    SONAR,
    "guidance/",
    "tasks/",
    "features/",
    "qa/",
    "perf/",
    PERFORMANCE,
    CLAUDE_LOCAL,
    AGY_HOOKS,
    GROK_HOOKS,
    CURSOR_HOOKS,
]
TRACK_CANDIDATES = [
    ".gitignore",
    AGENTS_MD,
    CLAUDE_MD,
    CONFIG,
    SONAR,
    TASKS_README,
    PERFORMANCE,
    TS_GUIDANCE,
    CS_GUIDANCE,
    CLAUDE_LOCAL,
    AGY_HOOKS,
    GROK_HOOKS,
    CURSOR_HOOKS,
]
NO_TOOLING = f'marestail.toml has no [ts] tooling; add tooling = "{TOOLING}" under [ts] so the gates use it'

__all__ = ["install_hyper"]


def install_hyper(target: Path) -> int:
    exclude = exclude_file(target)
    if exclude is None:
        print(f"marestail install --scope hyper needs a git repository: {target}")
        return 1
    tracked = tracked_paths(target)
    write_hyper_tree(target, tracked)
    skipped = apply_local_hooks(target, tracked)
    ts = ts_section(target)
    write_tooling(Tooling(target, ts))
    extend_exclude(target / exclude)
    trust_grok_folder(target)
    print_notes(tracked, skipped, ts)
    return npm_outcome(target, exclude)


def exclude_file(target: Path) -> str | None:
    code, output = run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=target, timeout=60)
    return output.strip() if code == 0 else None


def tracked_paths(target: Path) -> set[str]:
    _, output = run(["git", "ls-files", "-z", "--", *TRACK_CANDIDATES], cwd=target, timeout=60)
    return {path for path in output.split("\0") if path}


def hyper_config() -> str:
    return (TEMPLATES / CONFIG).read_text().replace("\n[ts]\n", f'\n[ts]\ntooling = "{TOOLING}"\n', 1)


def hyper_files(target: Path) -> dict[str, str]:
    files = {
        CONFIG: hyper_config(),
        TASKS_README: (TEMPLATES / "tasks-README.md").read_text(),
        PERFORMANCE: (TEMPLATES / PERFORMANCE).read_text(),
        TS_GUIDANCE: (TEMPLATES / TS_GUIDANCE).read_text(),
    }
    if uses_csharp(target):
        files[CS_GUIDANCE] = (TEMPLATES / CS_GUIDANCE).read_text()
    return files


def write_hyper_tree(target: Path, tracked: set[str]) -> None:
    for name, text in hyper_files(target).items():
        if name not in tracked:
            write_if_missing(target / name, text)


def write_if_missing(path: Path, text: str) -> None:
    if not path.exists():
        ensure_dir(path.parent)
        path.write_text(text)


Hook = tuple[str, str, Callable[[Path], None]]


def local_hooks() -> list[Hook]:
    return [
        ("agy", AGY_HOOKS, merge_agy_hook),
        ("claude", CLAUDE_LOCAL, merge_hook),
        ("cursor", CURSOR_HOOKS, merge_cursor_hook),
        ("grok", GROK_HOOKS, merge_grok_hook),
    ]


def apply_local_hooks(target: Path, tracked: set[str]) -> list[str]:
    return [line for hook in local_hooks() for line in local_hook(target, hook, tracked)]


def local_hook(target: Path, hook: Hook, tracked: set[str]) -> list[str]:
    backend, name, merge = hook
    if name in tracked:
        return [f"no Stop hook for {backend}: {name} is tracked"]
    merge(target / name)
    return []


def ts_section(target: Path) -> dict[str, Any]:
    with (target / CONFIG).open("rb") as handle:
        ts = tomllib.load(handle).get("ts")
    return ts if isinstance(ts, dict) else {}


def extend_exclude(path: Path) -> None:
    existing = path.read_text() if path.exists() else ""
    if HYPER_START in existing.splitlines():
        return
    ensure_dir(path.parent)
    block = [HYPER_START, *GITIGNORE_LINES, *HYPER_EXCLUDES, HYPER_END]
    path.write_text(ended(existing) + "\n".join(block) + "\n")


def ended(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"


def print_notes(tracked: set[str], skipped: list[str], ts: dict[str, Any]) -> None:
    alone = [f"left tracked files alone: {', '.join(sorted(tracked))}"] if tracked else []
    missing = [] if "tooling" in ts else [NO_TOOLING]
    for line in [*alone, *skipped, *missing]:
        print(line)


def npm_outcome(target: Path, exclude: str) -> int:
    code, output = run(["npm", "install", "--prefix", TOOLING], cwd=target, timeout=1800)
    (target / TOOLING / "npm.log").write_text(output)
    if code != 0:
        print(f"npm install --prefix {TOOLING} failed (exit {code}); everything else is installed, see {TOOLING}/npm.log")
        return 1
    print(f"installed into {target} with --scope hyper; nothing to commit, see {exclude}")
    return 0
