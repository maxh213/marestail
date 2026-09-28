import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marestail.changes import base_text
from marestail.context import DEFAULT_BASE, Context
from marestail.gates._crap import crap_order, crap_score, describe
from marestail.report import Result, elapsed

Fn = dict[str, Any]
Bases = dict[str, dict[str, int]]
ROSE = "{file}:{line} {name} complexity rose from {base} to {cc}; move the new condition into its own function"
UNCOVERED = "; changed lines not covered: "
SHOWN = ("file", "line", "name", "cc", "cov")
UNREAD = "; no base complexity for {file}, crap_max only"


@dataclass(frozen=True)
class Hyper:
    gate: str
    limit: float
    scan_base: Callable[[Path], list[Fn] | None]


def unit(file: str, fn: Fn, start: int, end: int) -> Fn:
    return {"file": file, "line": fn["line"], "start": start, "end": end, "name": fn["name"], "label": fn["name"], "cc": fn["complexity"]}


def unhit_lines(lines: dict[str, int]) -> set[int]:
    return {int(number) for number, hits in lines.items() if hits == 0}


def _member_unit(member: Fn) -> Fn:
    return unit(member["file"], member, member["startLine"], member["endLine"])


def covered_member(member: Fn, coverage: Fn, cov: float) -> Fn:
    lines = coverage["files"].get(member["file"], {}).get("lines", {})
    return {**_member_unit(member), "cov": cov, "missing": unhit_lines(lines)}


def member_units(scanned: tuple[Any, str | None]) -> list[Fn] | None:
    members, error = scanned
    if error:
        return None
    return list(map(_member_unit, members))


def judged(ctx: Context, hyper: Hyper, functions: list[Fn], started: float) -> Result:
    gated = innermost(functions, ctx)
    bases, unread = _base_complexities(ctx, hyper, sorted({fn["file"] for fn in gated}))
    above = _offenders([_judge(ctx, fn, functions, bases) for fn in gated], hyper.limit)
    failing = _failures(above)
    kept = len(above) - len(failing)
    summary = f"{len(gated)} innermost changed functions, {len(above)} above CRAP {hyper.limit:g}, {kept} of them no worse than base"
    return Result(hyper.gate, not failing, summary + _notes(unread), failing, elapsed(started))


def _offenders(entries: list[Fn], limit: float) -> list[Fn]:
    return sorted((entry for entry in entries if entry["crap"] > limit), key=crap_order)


def _failures(above: list[Fn]) -> list[str]:
    return [entry["finding"] for entry in above if entry["finding"]]


def _notes(unread: list[str]) -> str:
    return "".join(UNREAD.format(file=file) for file in unread)


def changed_in(fn: Fn, ctx: Context) -> set[int]:
    return {line for line in ctx.gated_lines(fn["file"]) or set() if fn["start"] <= line <= fn["end"]}


def _span(fn: Fn) -> tuple[int, int]:
    return fn["start"], fn["end"]


def _within(inner: Fn, outer: Fn) -> bool:
    return bool(outer["start"] <= inner["start"] and inner["end"] <= outer["end"])


def encloses(outer: Fn, inner: Fn) -> bool:
    return outer["file"] == inner["file"] and _within(inner, outer) and _span(inner) != _span(outer)


def innermost(functions: list[Fn], ctx: Context) -> list[Fn]:
    touched = _touched_functions(functions, ctx)
    return [fn for fn in touched if not _encloses_any(fn, touched)]


def _touched_functions(functions: list[Fn], ctx: Context) -> list[Fn]:
    return [fn for fn in functions if changed_in(fn, ctx)]


def _encloses_any(outer: Fn, functions: list[Fn]) -> bool:
    return any(encloses(outer, other) for other in functions)


def _outermost_first(fn: Fn) -> tuple[int, int]:
    return fn["start"], -fn["end"]


def nesting_path(fn: Fn, functions: list[Fn]) -> str:
    outer = sorted((other for other in functions if encloses(other, fn)), key=_outermost_first)
    return ".".join([*(other["label"] for other in outer), fn["label"]])


def complexity_by_path(functions: list[Fn]) -> dict[str, int]:
    found: dict[str, int] = {}
    for fn in functions:
        path = nesting_path(fn, functions)
        found[path] = max(fn["cc"], found.get(path, 0))
    return found


def _base_complexities(ctx: Context, hyper: Hyper, files: list[str]) -> tuple[Bases, list[str]]:
    bases: Bases = {}
    unread: list[str] = []
    ctx.work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ctx.work) as temp:
        for file in files:
            _read_base(ctx, hyper, Path(temp), file, (bases, unread))
    return bases, unread


def _read_base(ctx: Context, hyper: Hyper, temp: Path, file: str, found: tuple[Bases, list[str]]) -> None:
    text = base_text(ctx.root, ctx.config.get("git", "base", DEFAULT_BASE), file)
    if text is None:
        return
    scanned = hyper.scan_base(_written(temp / file, text))
    bases, unread = found
    if scanned is None:
        unread.append(file)
    else:
        bases[file] = complexity_by_path([{**fn, "file": file} for fn in scanned])


def _written(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _judge(ctx: Context, fn: Fn, functions: list[Fn], bases: Bases) -> Fn:
    scored = {key: fn[key] for key in SHOWN}
    scored["crap"] = crap_score(fn["cc"], fn["cov"])
    base = bases.get(fn["file"], {}).get(nesting_path(fn, functions))
    missed = sorted(fn["missing"] & changed_in(fn, ctx))
    return {**scored, "finding": finding(scored, base, missed)}


def finding(scored: Fn, base: int | None, missed: list[int]) -> str:
    if base is None:
        return describe(scored)
    if scored["cc"] > base:
        return ROSE.format(base=base, **scored)
    if missed:
        return describe(scored) + UNCOVERED + ", ".join(map(str, missed))
    return ""
