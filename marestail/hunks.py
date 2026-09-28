import re
from itertools import takewhile
from pathlib import Path

from marestail import freeze
from marestail.config import Config
from marestail.shell import run

__all__ = ["CHECKED_ROLES", "problems", "review"]

CHECKED_ROLES = ("coder", "architect")

_Hunk = tuple[str, int, int]

_PATHSPEC = ["--", ".", ":(exclude)features", ":(exclude)qa", ":(exclude).marestail"]
_FILE_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
_NEW_PATH = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_HUNK_HEADER = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
_LISTED = re.compile(r"^\s*(?:[-*] )?([^\s:]+):(\d+)(?:-(\d+))?")
_HUNKS_HEADING = "## Hunks"
_HEADING = "## "
_NO_MOVES = "under hyper no file may be renamed, moved or deleted"
_WHITESPACE = "whitespace or formatting only; under hyper leave code the fix does not need as it is"
_UNLISTED = f"not listed under {_HUNKS_HEADING}"
_MOVED_STATUS = ("R", "D")
_DELETED = "D"
_TAB = "\t"


def problems(config: Config, start: str, handoff: str) -> list[str]:
    move_findings = dict(_moved_files(config, start))
    changed = _changed_hunks(config, start, set(move_findings.values()))
    reformatted = _whitespace_only(changed, _parse_hunks(_git_diff(config, start, "-U0", "-w")))
    unlisted = _unlisted_hunks(changed, reformatted, _listed_hunks(handoff))
    return list(move_findings) + _findings(reformatted, _WHITESPACE) + _findings(unlisted, _UNLISTED)


def _changed_hunks(config: Config, start: str, skipped: set[str]) -> list[_Hunk]:
    return [hunk for hunk in _parse_hunks(_git_diff(config, start, "-U0")) if hunk[0] not in skipped]


def _whitespace_only(changed: list[_Hunk], substantive: list[_Hunk]) -> list[_Hunk]:
    return [hunk for hunk in changed if not _covered(hunk, substantive)]


def _unlisted_hunks(changed: list[_Hunk], reformatted: list[_Hunk], listed: list[_Hunk]) -> list[_Hunk]:
    return [hunk for hunk in changed if _unlisted_hunk(hunk, reformatted, listed)]


def _unlisted_hunk(hunk: _Hunk, reformatted: list[_Hunk], listed: list[_Hunk]) -> bool:
    return hunk not in reformatted and _needs_listing(hunk[0]) and not _covered(hunk, listed)


def _git_diff(config: Config, start: str, *options: str) -> str:
    _, output = run(["git", "diff", *options, f"{start}..HEAD", *_PATHSPEC], cwd=config.root)
    return output


def _moved_files(config: Config, start: str) -> list[tuple[str, str]]:
    status = _git_diff(config, start, "--name-status", "-M")
    return [_moved_file(line.split(_TAB)) for line in status.splitlines() if line[:1] in _MOVED_STATUS]


def _moved_file(fields: list[str]) -> tuple[str, str]:
    if fields[0] == _DELETED:
        return f"{fields[1]}: deleted; {_NO_MOVES}", fields[1]
    return f"{fields[1]} -> {fields[2]}: renamed or moved; {_NO_MOVES}", fields[2]


def _parse_hunks(diff: str) -> list[_Hunk]:
    return [hunk for chunk in _FILE_HEADER.split(diff) for hunk in _chunk_hunks(chunk)]


def _chunk_hunks(chunk: str) -> list[_Hunk]:
    path = _NEW_PATH.search(chunk)
    if path is None:
        return []
    return [(path.group(1), *_span(match)) for match in _HUNK_HEADER.finditer(chunk)]


def _span(match: re.Match[str]) -> tuple[int, int]:
    start = int(match.group(1))
    return start, max(start, start + int(match.group(2) or 1) - 1)


def _covered(hunk: _Hunk, others: list[_Hunk]) -> bool:
    return any(_overlaps(hunk, other) for other in others)


def _overlaps(one: _Hunk, other: _Hunk) -> bool:
    return one[0] == other[0] and one[1] <= other[2] and other[1] <= one[2]


def _needs_listing(path: str) -> bool:
    return freeze.is_source(path) and not freeze.is_test(path)


def _findings(hunks: list[_Hunk], problem: str) -> list[str]:
    return [f"{path}:{start}-{end}: {problem}" for path, start, end in hunks]


def _listed_hunks(handoff: str) -> list[_Hunk]:
    matches = [_LISTED.match(line) for line in _hunks_section(handoff)]
    return [_listed_hunk(match) for match in matches if match]


def _listed_hunk(match: re.Match[str]) -> _Hunk:
    start = int(match.group(2))
    return match.group(1), start, int(match.group(3) or start)


def _hunks_section(handoff: str) -> list[str]:
    lines = handoff.splitlines()
    headings = [index for index, line in enumerate(lines) if line.rstrip() == _HUNKS_HEADING]
    if not headings:
        return []
    return list(takewhile(lambda line: not line.startswith(_HEADING), lines[headings[0] + 1 :]))


def review(config: Config, start: str, handoffs: Path) -> dict[str, str]:
    return {
        "Diff stat": _git_diff(config, start, "--stat").strip(),
        "Diff": _git_diff(config, start).strip(),
        "Hunks": "\n".join(filter(None, (_latest_listing(handoffs, role) for role in CHECKED_ROLES))),
    }


def _latest_listing(handoffs: Path, role: str) -> str:
    reports = sorted(handoffs.glob(f"*-{role}.md"))
    return _hunks_listing(reports[-1]) if reports else ""


def _hunks_listing(report: Path) -> str:
    body = [line for line in _hunks_section(report.read_text()) if line.strip()]
    return "\n".join([f"## {report.stem}", *body]) if body else ""
