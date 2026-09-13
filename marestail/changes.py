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


def changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    lines: dict[str, set[int]] = {}
    for command in (["git", "diff", "-U0", f"{base}...HEAD"], ["git", "diff", "-U0", "HEAD"]):
        merge_hunks(lines, diff_hunks(root, command))
    for path in untracked(root):
        found = file_lines(root / path)
        if found is not None:
            lines[path] = found
    return lines


def diff_hunks(root: Path, command: list[str]) -> dict[str, set[int]]:
    code, output = run(command, cwd=root)
    if code != 0:
        return {}
    hunks: dict[str, set[int]] = {}
    current: str | None = None
    for line in output.splitlines():
        if line.startswith("+++ "):
            current = parse_plus(line)
        elif line.startswith("@@") and current is not None:
            start, count = hunk_span(line)
            hunks.setdefault(current, set()).update(range(start, start + count))
    return hunks


def parse_plus(line: str) -> str | None:
    path = line[4:].strip()
    if path == "/dev/null":
        return None
    return path[2:] if path.startswith("b/") else path


def hunk_span(header: str) -> tuple[int, int]:
    token = next(part for part in header.split() if part.startswith("+"))
    start, _, count = token[1:].partition(",")
    return int(start), int(count) if count else 1


def merge_hunks(lines: dict[str, set[int]], hunks: dict[str, set[int]]) -> None:
    for path, added in hunks.items():
        lines.setdefault(path, set()).update(added)


def untracked(root: Path) -> set[str]:
    code, output = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    if code != 0:
        return set()
    return {parse_line(line) for line in output.splitlines() if line.startswith("??")}


def base_exists(root: Path, base: str) -> bool:
    code, _ = run(["git", "rev-parse", "--verify", "--quiet", base], cwd=root)
    return code == 0


def file_lines(path: Path) -> set[int] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw:
        return None
    return set(range(1, len(raw.decode("utf-8", errors="replace").splitlines()) + 1))
