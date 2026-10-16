import sys
from pathlib import Path

from marestail.shell import run

PROTECTED = frozenset({"main", "master"})


def real_git(command: list[str], cwd: Path) -> tuple[int, str]:
    return run(command, cwd=cwd, env={"GIT_TERMINAL_PROMPT": "0", "GIT_MERGE_AUTOEDIT": "no"})


def safe_branch(name: str) -> bool:
    if not name or name == "@" or name.startswith("-") or name.startswith("/") or name.endswith("/") or name.endswith("."):
        return False
    if "//" in name or ".." in name or "@{" in name or name.endswith(".lock"):
        return False
    for part in name.split("/"):
        if not part or part in {".", ".."} or part.startswith(".") or part.endswith(".lock") or part.endswith("."):
            return False
    return not any(character in " ~^:?*[\\" or ord(character) < 32 or character == "\x7f" for character in name)


def destination(branch: str) -> str:
    if not safe_branch(branch):
        raise SystemExit(f"refusing unsafe branch name {branch!r}")
    return f"refs/heads/{branch}"


def assert_destination(branch: str, dest: str) -> None:
    # A work branch publishes only to a remote branch of the same name.
    expected = destination(branch)
    protected = {f"refs/heads/{name}" for name in PROTECTED}
    if dest != expected or (branch not in PROTECTED and dest in protected):
        raise SystemExit(f"refusing to push {branch} to {dest}")


def refspec(branch: str) -> str:
    dest = destination(branch)
    assert_destination(branch, dest)
    return f"HEAD:{dest}"


def current_branch(root: Path, git=real_git) -> str:
    code, output = git(["git", "branch", "--show-current"], root)
    branch = output.strip()
    if code != 0 or not branch:
        raise SystemExit("detached HEAD; refusing to push")
    refspec(branch)
    return branch


def push_current(root: Path, *, dry_run: bool = False, git=real_git) -> int:
    root = Path(root)
    spec = refspec(current_branch(root, git))
    if dry_run:
        print(spec)
        return 0
    code, output = git(["git", "push", "-u", "origin", spec], root)
    if code != 0:
        sys.stderr.write((output.strip() or f"git push {spec} failed") + "\n")
        return code or 1
    if output.strip():
        print(output.strip())
    print(f"pushed {spec}")
    return 0
