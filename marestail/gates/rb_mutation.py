import json
import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.perf.scope import is_benchmark
from marestail.report import Result
from marestail.ruby import bundle, listify
from marestail.shell import run, tail

RESULTS_DIR = Path(".mutant") / "results"
SKIP_DIRS = {"vendor", "spec", "test", "tmp", "log", "node_modules", ".git", "coverage"}
DECLARATION = re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*(?:::[A-Z]\w*)*)")
IDENTIFICATION = re.compile(r"^(evil|neutral|noop):(.+):(\d+):(\S+)$", re.MULTILINE)
RESULTS_LINE = re.compile(r"^Results:\s*(\d+)$", re.MULTILINE)
MAX_FINDINGS = 60
INSTALL = 'add gem "mutant" and gem "mutant-rspec" to the Gemfile and run bundle install'


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.ruby_root()
    if ctx.ruby("mutation", True) is False:
        return Result.skipped("rb.mutation", "disabled: [ruby] mutation = false")
    scope = ctx.mutation_files("ruby", root, (".rb",))
    if scope.mode == "error":
        return Result("rb.mutation", False, scope.note, [], time.time() - started)
    subjects = changed_subjects(ctx, scope.files or [])
    if scope.mode != "full" and not subjects:
        return Result.skipped("rb.mutation", "no changed ruby sources")
    code, output = run([*bundler(ctx), "info", "mutant"], cwd=root, timeout=120)
    if code == 127:
        return Result(
            "rb.mutation", False, "bundle not available", ["bundle is not installed: install ruby and bundler"], time.time() - started
        )
    if code != 0:
        return Result("rb.mutation", False, "mutant is not in the bundle", [f"mutant is not installed: {INSTALL}"], time.time() - started)
    before = sessions(root)
    code, output = run(bundle(ctx, "mutant", "run", *subjects), cwd=root, timeout=7200)
    created = sessions(root) - before
    if created:
        return session_result(ctx, created, output, started, scope.note)
    match = RESULTS_LINE.search(output)
    if not match:
        return Result("rb.mutation", False, f"mutant produced no report (exit {code})", tail(output), time.time() - started)
    return verdict(int(match.group(1)), stdout_findings(output, ctx), output, started, scope.note)


def session_result(ctx: Context, created: set[Path], output: str, started: float, note: str = "") -> Result:
    try:
        report = json.loads(max(created, key=lambda path: path.stat().st_mtime).read_text())
    except (OSError, json.JSONDecodeError):
        return Result("rb.mutation", False, "mutant session report unreadable", tail(output), time.time() - started)
    total = 0
    failures: list[tuple[str, int, str, str]] = []
    for subject in report.get("subject_results", []):
        syntax, line = label(subject.get("identification", ""))
        path = relative(subject.get("source_path", ""), ctx)
        for result in subject.get("coverage_results", []):
            total += 1
            if any(result.get("criteria_result", {}).values()):
                continue
            kind = result.get("mutation_result", {}).get("mutation_type", "evil")
            failures.append((path, line, syntax, kind))
    return verdict(total, failures, output, started, note)


def verdict(total: int, failures: list[tuple[str, int, str, str]], output: str, started: float, note: str = "") -> Result:
    if total == 0:
        return Result("rb.mutation", False, "no mutants were generated", tail(output), time.time() - started)
    counts: dict[tuple[str, int, str, str], int] = {}
    for failure in failures:
        counts[failure] = counts.get(failure, 0) + 1
    findings = [describe(path, line, syntax, kind, count) for (path, line, syntax, kind), count in counts.items()]
    summary = f"{len(failures)} of {total} mutants not killed" if findings else f"all {total} mutants killed"
    summary += f" {note}" if note else ""
    return Result("rb.mutation", not findings, summary, findings[:MAX_FINDINGS], time.time() - started)


def stdout_findings(output: str, ctx: Context) -> list[tuple[str, int, str, str]]:
    failures = []
    for kind, subject, line, _code in IDENTIFICATION.findall(output):
        syntax, _, path = subject.rpartition(":")
        failures.append((relative(path, ctx), int(line), syntax or "?", kind))
    return failures


def bundler(ctx: Context) -> list[str]:
    prefix = listify(ctx.ruby("exec", ["bundle", "exec"]))
    return prefix[:-1] or ["bundle"]


def sessions(root: Path) -> set[Path]:
    folder = root / RESULTS_DIR
    return set(folder.glob("*.json")) if folder.is_dir() else set()


def changed_subjects(ctx: Context, files: list[str]) -> list[str]:
    names = set()
    for file in files:
        path = Path(file)
        if not any(part in SKIP_DIRS for part in path.parts) and not is_benchmark(path):
            names.update(constants(ctx.root / path))
    return sorted(f"{name}*" for name in names)


def constants(path: Path) -> set[str]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return set()
    return {match.group(1) for line in text.splitlines() if (match := DECLARATION.match(line))}


def label(identification: str) -> tuple[str, int]:
    parts = identification.rsplit(":", 2)
    if len(parts) != 3 or not parts[2].isdigit():
        return identification or "?", 0
    return parts[0], int(parts[2])


def relative(path: str, ctx: Context) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.ruby_root() / path
    try:
        return str(candidate.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return path


def describe(path: str, line: int, syntax: str, kind: str, count: int) -> str:
    noun = "mutant" if count == 1 else "mutants"
    if kind == "evil":
        return f"{path}:{line} {syntax}: {count} {noun} survived"
    if kind == "neutral":
        return f"{path}:{line} {syntax}: {count} neutral {noun} failed, tests do not pass unmutated"
    return f"{path}:{line} {syntax}: {count} {kind} {noun} failed"
