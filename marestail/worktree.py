from pathlib import Path

from marestail.config import Config
from marestail.shell import run


def start_file(config: Config, task: str) -> Path:
    return config.work / "runs" / task / "start-commit"


def head(config: Config) -> str:
    _, output = run(["git", "rev-parse", "HEAD"], cwd=config.root)
    return output.strip()


def start_commit(config: Config, task: str) -> tuple[str, str]:
    path = start_file(config, task)
    if path.exists():
        return path.read_text().strip(), ""
    base = config.get("git", "base", "origin/master")
    code, output = run(["git", "merge-base", base, "HEAD"], cwd=config.root)
    if code == 0:
        return output.strip(), f"no recorded start commit for {task}; using git merge-base {base} HEAD"
    return head(config), f"no recorded start commit for {task} and no merge-base with {base}; using HEAD"


def add(root: Path, path: Path, sha: str) -> tuple[int, str]:
    return run(["git", "worktree", "add", "--detach", str(path), sha], cwd=root)


def remove(root: Path, path: Path) -> None:
    run(["git", "worktree", "remove", "--force", str(path)], cwd=root)
