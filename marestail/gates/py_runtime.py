import ast
import re
import sys
import time
import tomllib
from itertools import takewhile
from pathlib import Path

from marestail.context import Context, under_benchmarks
from marestail.report import Result, elapsed

GATE = "py.runtime"
FROM_LINE = re.compile(r"^\s*FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE)
IMAGE_VERSION = re.compile(r"python[:\-]?(\d+)\.(\d+)")
SHEBANG = re.compile(r"^#!.*?python(\d+)\.(\d+)")
RUFF_TARGET = re.compile(r"^py(\d)(\d+)$")
SKIP_DIRS = {"node_modules", ".venv", "venv", "mutants", "dist", "build", ".git", "__pycache__", ".marestail"}

Version = tuple[int, int]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.python_root()
    if not root.exists():
        return Result.skipped(GATE, "no python root")
    declared = deployed_version(ctx)
    if declared is None:
        return Result.skipped(GATE, "nothing declares the interpreter that ships")
    shipped, source = declared
    findings = agreement_findings(ctx, shipped, source) + parse_findings(ctx, root, shipped)
    named = f"{name(shipped)} from {source}"
    summary = f"{named}; tooling agrees and every source parses" if not findings else f"{len(findings)} findings against {named}"
    return Result(GATE, not findings, ctx.global_note(summary), findings, elapsed(started))


def name(version: Version) -> str:
    return f"{version[0]}.{version[1]}"


def deployed_version(ctx: Context) -> tuple[Version, str] | None:
    stated = ctx.python("runtime")
    if stated:
        found = floor_version(str(stated))
        if found is not None:
            return found, "marestail.toml [python] runtime"
    return image_version(ctx)


def image_version(ctx: Context) -> tuple[Version, str] | None:
    for relative in ctx.python("deploy_files", ["Dockerfile"]):
        found = file_image_version(ctx.root / relative)
        if found is not None:
            return found, relative
    return None


def file_image_version(path: Path) -> Version | None:
    if not path.exists():
        return None
    return last_python_image(FROM_LINE.findall(path.read_text()))


def last_python_image(images: list[str]) -> Version | None:
    for image in reversed(images):
        found = IMAGE_VERSION.search(image)
        if found:
            return version_of(found)
    return None


def version_of(found: re.Match[str]) -> Version:
    return int(found.group(1)), int(found.group(2))


def floor_version(text: str) -> Version | None:
    parts = text.split(".")
    return next(filter(None, map(digit_pair, parts, parts[1:])), None)


def digit_pair(before: str, after: str) -> Version | None:
    major, minor = leading_digits(before[::-1])[::-1], leading_digits(after)
    return (int(major), int(minor)) if major and minor else None


def leading_digits(text: str) -> str:
    return "".join(takewhile(str.isdecimal, text))


def agreement_findings(ctx: Context, shipped: Version, source: str) -> list[str]:
    findings = []
    for where, claimed in claims(ctx):
        if claimed != shipped:
            findings.append(f"{where} says Python {name(claimed)} but {source} ships {name(shipped)}")
    return findings


def claims(ctx: Context) -> list[tuple[str, Version]]:
    return pyproject_claims(ctx) + shebang_claims(ctx)


def pyproject_claims(ctx: Context) -> list[tuple[str, Version]]:
    path = ctx.root / "pyproject.toml"
    if not path.exists():
        return []
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    tools = raw.get("tool", {})
    return [
        claim
        for claim in (
            floor_claim("pyproject.toml [project] requires-python", raw.get("project", {}).get("requires-python")),
            ruff_claim(tools.get("ruff", {}).get("target-version")),
            floor_claim("pyproject.toml [tool.mypy] python_version", tools.get("mypy", {}).get("python_version")),
        )
        if claim is not None
    ]


def floor_claim(where: str, stated: object) -> tuple[str, Version] | None:
    if not isinstance(stated, str):
        return None
    found = floor_version(stated)
    return None if found is None else (where, found)


def ruff_claim(stated: object) -> tuple[str, Version] | None:
    if not isinstance(stated, str):
        return None
    found = RUFF_TARGET.match(stated)
    return None if found is None else ("pyproject.toml [tool.ruff] target-version", version_of(found))


def shebang_claims(ctx: Context) -> list[tuple[str, Version]]:
    claimed = []
    for path in sources(ctx, ctx.python_root()):
        found = SHEBANG.match(path.read_text().partition("\n")[0])
        if found:
            claimed.append((f"{path.relative_to(ctx.root)} shebang", version_of(found)))
    return claimed


def sources(ctx: Context, root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if not skipped(ctx, path))


def skipped(ctx: Context, path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts) or under_benchmarks(ctx.root, path)


def parse_findings(ctx: Context, root: Path, shipped: Version) -> list[str]:
    if shipped > highest_understood():
        return [f"this interpreter cannot check syntax for Python {name(shipped)}; run the gate on {name(shipped)} or newer"]
    findings = []
    for path in sources(ctx, root):
        broken = parse_error(path, shipped)
        if broken is not None:
            findings.append(broken.replace(str(ctx.root) + "/", ""))
    return findings


def highest_understood() -> Version:
    return sys.version_info[0], sys.version_info[1]


def parse_error(path: Path, shipped: Version) -> str | None:
    try:
        ast.parse(path.read_text(), filename=str(path), feature_version=shipped)
    except SyntaxError as error:
        return f"{path}:{error.lineno} will not parse on Python {name(shipped)}: {error.msg}"
    return None
