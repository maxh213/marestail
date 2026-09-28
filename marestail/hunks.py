import re
from itertools import takewhile
from pathlib import Path

from marestail import freeze
from marestail.config import Config
from marestail.shell import run

Hunk = tuple[str, int, int]

PATHSPEC = ["--", ".", ":(exclude)features", ":(exclude)qa", ":(exclude).marestail"]
FILE_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
NEW_PATH = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
HUNK_HEADER = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
FIRST_HUNK = "\n@@"
LISTED = re.compile(r"^\s*(?:[-*] )?([^\s:]+):(\d+)(?:-(\d+))?")
HUNKS_HEADING = "## Hunks"
HEADING = "## "
NO_MOVES = "under hyper no file may be renamed, moved or deleted"
WHITESPACE = "whitespace or formatting only; under hyper leave code the fix does not need as it is"
UNLISTED = f"not listed under {HUNKS_HEADING}"
MOVED_STATUS = ("R", "D")
DELETED = "D"
TAB = "\t"
CHECKED_ROLES = ("coder", "architect")


def problems(config: Config, start: str, handoff: str) -> list[str]:
    move_findings = dict(moved_files(config, start))
    changed = changed_hunks(config, start, set(move_findings.values()))
    reformatted = whitespace_only(changed, parse_hunks(git_diff(config, start, "-U0", "-M", "-w")))
    unlisted = unlisted_hunks(changed, reformatted, listed_hunks(handoff))
    return list(move_findings) + findings(reformatted, WHITESPACE) + findings(unlisted, UNLISTED)


def changed_hunks(config: Config, start: str, skipped: set[str]) -> list[Hunk]:
    return [hunk for hunk in parse_hunks(git_diff(config, start, "-U0", "-M")) if hunk[0] not in skipped]


def whitespace_only(changed: list[Hunk], substantive: list[Hunk]) -> list[Hunk]:
    return [hunk for hunk in changed if not covered(hunk, substantive)]


def unlisted_hunks(changed: list[Hunk], reformatted: list[Hunk], listed: list[Hunk]) -> list[Hunk]:
    return [hunk for hunk in changed if unlisted_hunk(hunk, reformatted, listed)]


def unlisted_hunk(hunk: Hunk, reformatted: list[Hunk], listed: list[Hunk]) -> bool:
    return hunk not in reformatted and needs_listing(hunk[0]) and not covered(hunk, listed)


def git_diff(config: Config, start: str, *options: str) -> str:
    _, output = run(["git", "diff", *options, f"{start}..HEAD", *PATHSPEC], cwd=config.root)
    return output


def moved_files(config: Config, start: str) -> list[tuple[str, str]]:
    status = git_diff(config, start, "--name-status", "-M")
    return [moved_file(line.split(TAB)) for line in status.splitlines() if line[:1] in MOVED_STATUS]


def moved_file(fields: list[str]) -> tuple[str, str]:
    if fields[0] == DELETED:
        return f"{fields[1]}: deleted; {NO_MOVES}", fields[1]
    return f"{fields[1]} -> {fields[2]}: renamed or moved; {NO_MOVES}", fields[2]


def parse_hunks(diff: str) -> list[Hunk]:
    return [hunk for chunk in FILE_HEADER.split(diff) for hunk in chunk_hunks(chunk)]


def chunk_hunks(chunk: str) -> list[Hunk]:
    path = NEW_PATH.search(chunk.split(FIRST_HUNK, 1)[0])
    if path is None:
        return []
    return [(path.group(1), *span(match)) for match in HUNK_HEADER.finditer(chunk)]


def span(match: re.Match[str]) -> tuple[int, int]:
    start = int(match.group(1))
    return start, max(start, start + int(match.group(2) or 1) - 1)


def covered(hunk: Hunk, others: list[Hunk]) -> bool:
    return any(overlaps(hunk, other) for other in others)


def overlaps(one: Hunk, other: Hunk) -> bool:
    return one[0] == other[0] and one[1] <= other[2] and other[1] <= one[2]


def needs_listing(path: str) -> bool:
    return freeze.is_source(path) and not freeze.is_test(path)


def findings(hunks: list[Hunk], problem: str) -> list[str]:
    return [f"{path}:{start}-{end}: {problem}" for path, start, end in hunks]


def listed_hunks(handoff: str) -> list[Hunk]:
    matches = [LISTED.match(line) for line in hunks_section(handoff)]
    return [listed_hunk(match) for match in matches if match]


def listed_hunk(match: re.Match[str]) -> Hunk:
    start = int(match.group(2))
    return match.group(1), start, int(match.group(3) or start)


def hunks_section(handoff: str) -> list[str]:
    lines = handoff.splitlines()
    headings = [index for index, line in enumerate(lines) if line.rstrip() == HUNKS_HEADING]
    if not headings:
        return []
    return list(takewhile(lambda line: not line.startswith(HEADING), lines[headings[0] + 1 :]))


def review(config: Config, start: str, handoffs: Path) -> dict[str, str]:
    return {
        "Diff stat": git_diff(config, start, "--stat").strip(),
        "Diff": git_diff(config, start).strip(),
        "Hunks": "\n".join(filter(None, (latest_listing(handoffs, role) for role in CHECKED_ROLES))),
    }


def latest_listing(handoffs: Path, role: str) -> str:
    reports = sorted(handoffs.glob(f"*-{role}.md"))
    return hunks_listing(reports[-1]) if reports else ""


def hunks_listing(report: Path) -> str:
    body = [line for line in hunks_section(report.read_text()) if line.strip()]
    return "\n".join([f"## {report.stem}", *body]) if body else ""
