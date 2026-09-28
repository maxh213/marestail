import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marestail.changes import base_text
from marestail.context import DEFAULT_BASE, Context
from marestail.gates._crap import crap_order, describe
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


def judged(ctx: Context, hyper: Hyper, functions: list[Fn], started: float) -> Result:
    gated = innermost(functions, ctx)
    bases, unread = base_complexities(ctx, hyper, sorted({fn["file"] for fn in gated}))
    above = offenders([judge(ctx, fn, functions, bases) for fn in gated], hyper.limit)
    failing = failures(above)
    kept = len(above) - len(failing)
    summary = f"{len(gated)} innermost changed functions, {len(above)} above CRAP {hyper.limit:g}, {kept} of them no worse than base"
    return Result(hyper.gate, not failing, summary + notes(unread), failing, elapsed(started))


def offenders(entries: list[Fn], limit: float) -> list[Fn]:
    return sorted((entry for entry in entries if entry["crap"] > limit), key=crap_order)


def failures(above: list[Fn]) -> list[str]:
    return [entry["finding"] for entry in above if entry["finding"]]


def notes(unread: list[str]) -> str:
    return "".join(UNREAD.format(file=file) for file in unread)


def changed_in(fn: Fn, ctx: Context) -> set[int]:
    return {line for line in ctx.gated_lines(fn["file"]) or set() if fn["start"] <= line <= fn["end"]}


def span(fn: Fn) -> tuple[int, int]:
    return fn["start"], fn["end"]


def within(inner: Fn, outer: Fn) -> bool:
    return bool(outer["start"] <= inner["start"] and inner["end"] <= outer["end"])


def encloses(outer: Fn, inner: Fn) -> bool:
    return outer["file"] == inner["file"] and within(inner, outer) and span(inner) != span(outer)


def innermost(functions: list[Fn], ctx: Context) -> list[Fn]:
    touched = touched_functions(functions, ctx)
    return [fn for fn in touched if not encloses_any(fn, touched)]


def touched_functions(functions: list[Fn], ctx: Context) -> list[Fn]:
    return [fn for fn in functions if changed_in(fn, ctx)]


def encloses_any(outer: Fn, functions: list[Fn]) -> bool:
    return any(encloses(outer, other) for other in functions)


def outermost_first(fn: Fn) -> tuple[int, int]:
    return fn["start"], -fn["end"]


def nesting_path(fn: Fn, functions: list[Fn]) -> str:
    outer = sorted((other for other in functions if encloses(other, fn)), key=outermost_first)
    return ".".join([*(other["label"] for other in outer), fn["label"]])


def complexity_by_path(functions: list[Fn]) -> dict[str, int]:
    found: dict[str, int] = {}
    for fn in functions:
        path = nesting_path(fn, functions)
        found[path] = max(fn["cc"], found.get(path, 0))
    return found


def base_complexities(ctx: Context, hyper: Hyper, files: list[str]) -> tuple[Bases, list[str]]:
    bases: Bases = {}
    unread: list[str] = []
    ctx.work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ctx.work) as temp:
        for file in files:
            read_base(ctx, hyper, Path(temp), file, (bases, unread))
    return bases, unread


def read_base(ctx: Context, hyper: Hyper, temp: Path, file: str, found: tuple[Bases, list[str]]) -> None:
    text = base_text(ctx.root, ctx.config.get("git", "base", DEFAULT_BASE), file)
    if text is None:
        return
    scanned = hyper.scan_base(written(temp / file, text))
    bases, unread = found
    if scanned is None:
        unread.append(file)
    else:
        bases[file] = complexity_by_path([{**fn, "file": file} for fn in scanned])


def written(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def judge(ctx: Context, fn: Fn, functions: list[Fn], bases: Bases) -> Fn:
    scored = {key: fn[key] for key in SHOWN}
    scored["crap"] = fn["cc"] ** 2 * (1 - fn["cov"]) ** 3 + fn["cc"]
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
