from pathlib import Path

from marestail.shell import run


def changed_files(root: Path, base: str) -> set[str]:
    committed = diff_names(root, ["git", "diff", "--name-only", f"{base}...HEAD"])
    working = diff_names(root, ["git", "status", "--porcelain", "--untracked-files=all"])
    return committed | working


def diff_names(root: Path, command: list[str]) -> set[str]:
    code, output = run(command, cwd=root)
    if code != 0:
        return set()
    return {parse_line(line) for line in output.splitlines() if line.strip()}


def parse_line(line: str) -> str:
    if line[:2].strip() and line[2:3] == " " and len(line) > 3:
        return line[3:].split(" -> ")[-1]
    return line.strip()
