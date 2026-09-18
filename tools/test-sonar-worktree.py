#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.config import Config
from marestail.context import Context
from marestail.gates.sonar import git_mounts, scanner_command

CREDS = {"url": "http://127.0.0.1:9000", "token": "test-token"}


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def context(root: Path) -> Context:
    return Context(config=Config(root=root, raw={"sonar": {"project_key": "k"}}))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="marestail-sonar-worktree-") as temp:
        folder = Path(temp).resolve()
        main = folder / "main"
        main.mkdir()
        git(main, "init", "-q", "-b", "main")
        git(main, "-c", "user.email=t@marestail", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        worktree = folder / "wt"
        git(main, "worktree", "add", "-q", "-b", "wt", str(worktree))
        plain = folder / "plain"
        plain.mkdir()
        expect("main-checkout-needs-no-extra-mount", git_mounts(context(main)), [])
        expect("worktree-mounts-common-dir-read-only", git_mounts(context(worktree)), ["-v", f"{main / '.git'}:{main / '.git'}:ro"])
        expect("not-a-repo-mounts-nothing", git_mounts(context(plain)), [])
        command = scanner_command(context(worktree), CREDS, "k")
        expect(
            "mount-before-image",
            command.index(f"{main / '.git'}:{main / '.git'}:ro") < command.index("sonarsource/sonar-scanner-cli"),
            True,
        )
        expect("project-still-mounted", f"{worktree}:{worktree}" in command, True)
    print("sonar worktree ok")
