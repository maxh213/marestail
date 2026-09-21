import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from marestail.context import Context, MutationScope, is_benchmark
from marestail.report import Result, elapsed
from marestail.ruby import SKIP_DIRS, bundle, listify, relative
from marestail.shell import run, tail

GATE = "rb.mutation"
RESULTS_DIR = Path(".mutant") / "results"
DECLARATION = re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*(?:::[A-Z]\w*)*)")
MUTANT_KINDS = {"evil", "neutral", "noop"}
MUTANT_TAIL = re.compile(r":(\d+):(\S+)")
RESULTS_LINE = re.compile(r"^Results:\s*(\d+)$", re.MULTILINE)
MAX_FINDINGS = 60
INSTALL = 'add gem "mutant" and gem "mutant-rspec" to the Gemfile and run bundle install'
REPLACE = "replace"
COLON = ":"
EMPTY = ""
EMPTY_LIST: list[Any] = []
SUBJECT_RESULTS = "subject_results"
COVERAGE_RESULTS = "coverage_results"
IDENTIFICATION = "identification"
SOURCE_PATH = "source_path"

Failure = tuple[str, int, str, str]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if mutation_off(ctx.ruby("mutation")):
        return Result.skipped(GATE, "disabled: [ruby] mutation = false")
    scope = ctx.mutation_files("ruby", ctx.ruby_root(), (".rb",))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    return scoped_run(ctx, scope, started)


def mutation_off(value: object) -> bool:
    return value is False


def scoped_run(ctx: Context, scope: MutationScope, started: float) -> Result:
    subjects = changed_subjects(ctx, scope.files or [])
    if nothing_to_mutate(scope, subjects):
        return Result.skipped(GATE, "no changed ruby sources")
    return bundle_problem(ctx, started) or mutate(ctx, subjects, scope.note, started)


def nothing_to_mutate(scope: MutationScope, subjects: list[str]) -> bool:
    return scope.mode != "full" and not subjects


def bundle_problem(ctx: Context, started: float) -> Result | None:
    code, _ = run([*bundler(ctx), "info", "mutant"], cwd=ctx.ruby_root(), timeout=120)
    if code == 127:
        return Result(GATE, False, "bundle not available", ["bundle is not installed: install ruby and bundler"], elapsed(started))
    if code != 0:
        return Result(GATE, False, "mutant is not in the bundle", [f"mutant is not installed: {INSTALL}"], elapsed(started))
    return None


def mutate(ctx: Context, subjects: list[str], note: str, started: float) -> Result:
    root = ctx.ruby_root()
    before = sessions(root)
    code, output = run(bundle(ctx, "mutant", "run", *subjects), cwd=root, timeout=7200)
    created = sessions(root) - before
    if created:
        return session_result(ctx, created, output, started, note)
    return stdout_result(ctx, code, output, started, note)


def stdout_result(ctx: Context, code: int, output: str, started: float, note: str) -> Result:
    match = RESULTS_LINE.search(output)
    if not match:
        return Result(GATE, False, f"mutant produced no report (exit {code})", tail(output), elapsed(started))
    return verdict(int(match.group(1)), stdout_findings(output, ctx), output, started, note)


def newest_report(created: set[Path]) -> dict[str, Any] | None:
    try:
        report: dict[str, Any] = json.loads(max(created, key=lambda path: path.stat().st_mtime).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return report


def session_result(ctx: Context, created: set[Path], output: str, started: float, note: str) -> Result:
    report = newest_report(created)
    if report is None:
        return Result(GATE, False, "mutant session report unreadable", tail(output), elapsed(started))
    total, failures = session_failures(report, ctx)
    return verdict(total, failures, output, started, note)


def list_field(data: dict[str, Any], key: str) -> list[Any]:
    if key not in data:
        return EMPTY_LIST
    value = data[key]
    return value if isinstance(value, list) else EMPTY_LIST


def text_field(data: dict[str, Any], key: str) -> str:
    if key not in data:
        return EMPTY
    value = data[key]
    return value if isinstance(value, str) else EMPTY


def session_failures(report: dict[str, Any], ctx: Context) -> tuple[int, list[Failure]]:
    total = 0
    failures: list[Failure] = []
    for subject in list_field(report, SUBJECT_RESULTS):
        total += len(list_field(subject, COVERAGE_RESULTS))
        failures.extend(subject_failures(subject, ctx))
    return total, failures


def subject_failures(subject: dict[str, Any], ctx: Context) -> list[Failure]:
    syntax, line = label(text_field(subject, IDENTIFICATION))
    path = relative(text_field(subject, SOURCE_PATH), ctx)
    return [(path, line, syntax, mutation_kind(result)) for result in list_field(subject, COVERAGE_RESULTS) if not killed(result)]


def killed(result: dict[str, Any]) -> bool:
    return any(result.get("criteria_result", {}).values())


def mutation_kind(result: dict[str, Any]) -> str:
    kind: str = result.get("mutation_result", {}).get("mutation_type", "evil")
    return kind


def verdict(total: int, failures: list[Failure], output: str, started: float, note: str = "") -> Result:
    if total == 0:
        return Result(GATE, False, "no mutants were generated", tail(output), elapsed(started))
    findings = [describe(*failure, count) for failure, count in Counter(failures).items()]
    summary = f"{len(failures)} of {total} mutants not killed" if findings else f"all {total} mutants killed"
    return Result(GATE, not findings, with_note(summary, note), findings[:MAX_FINDINGS], elapsed(started))


def with_note(summary: str, note: str) -> str:
    return f"{summary} {note}" if note else summary


def stdout_findings(output: str, ctx: Context) -> list[Failure]:
    failures = []
    for kind, subject, line in identifications(output):
        syntax, _, path = right_colon(subject)
        failures.append((relative(path, ctx), int(line), syntax or "?", kind))
    return failures


def identifications(output: str) -> list[tuple[str, str, str]]:
    return [found for found in map(identification, output.split("\n")) if found]


def identification(line: str) -> tuple[str, str, str] | None:
    kind, _, rest = line.partition(":")
    if kind not in MUTANT_KINDS:
        return None
    match = next(filter(None, (MUTANT_TAIL.fullmatch(rest, at) for at in colons_from_right(rest))), None)
    return None if match is None else (kind, rest[: match.start()], match.group(1))


def right_colon(text: str) -> tuple[str, str, str]:
    if COLON not in text:
        return EMPTY, EMPTY, text
    index = text.rindex(COLON)
    return text[:index], COLON, text[index + 1 :]


def colons_from_right(text: str) -> list[int]:
    last = len(text) - 1
    return [at for at in range(last, 0, -1) if text[at] == ":"]


BUNDLE = ["bundle"]


def bundler(ctx: Context) -> list[str]:
    prefix = listify(ctx.ruby("exec"))
    return prefix[:-1] or BUNDLE


def sessions(root: Path) -> set[Path]:
    folder = root / RESULTS_DIR
    chosen = (empty_sessions, json_sessions)[folder.is_dir()]
    return chosen(folder)


def empty_sessions(_folder: Path) -> set[Path]:
    return set()


def json_sessions(folder: Path) -> set[Path]:
    return set(folder.glob("*.json"))


def mutable(path: Path) -> bool:
    return not any(part in SKIP_DIRS for part in path.parts) and not is_benchmark(path)


def changed_subjects(ctx: Context, files: list[str]) -> list[str]:
    names: set[str] = set()
    for file in files:
        if mutable(Path(file)):
            names.update(constants(ctx.root / file))
    return sorted(f"{name}*" for name in names)


def constants(path: Path) -> set[str]:
    try:
        text = path.read_text(errors=REPLACE)
    except OSError:
        return set()
    return {match.group(1) for line in text.splitlines() if (match := DECLARATION.match(line))}


def label(identification: str) -> tuple[str, int]:
    parts = split_label(identification)
    if len(parts) != 3 or not parts[2].isdigit():
        return identification or "?", 0
    return parts[0], int(parts[2])


def split_label(identification: str) -> list[str]:
    rest, sep, line = right_colon(identification)
    if not sep:
        return [identification]
    owner, _, path = right_colon(rest)
    return [owner, path, line] if owner else [rest, EMPTY, line]


def describe(path: str, line: int, syntax: str, kind: str, count: int) -> str:
    noun = "mutant" if count == 1 else "mutants"
    if kind == "evil":
        return f"{path}:{line} {syntax}: {count} {noun} survived"
    if kind == "neutral":
        return f"{path}:{line} {syntax}: {count} neutral {noun} failed, tests do not pass unmutated"
    return f"{path}:{line} {syntax}: {count} {kind} {noun} failed"
