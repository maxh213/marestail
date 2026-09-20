import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import tail

GATE = "cs.mutation"
OUTPUT_DIR = "stryker"
REPORT = Path("reports") / "mutation-report.json"
BAD = {"Survived", "NoCoverage", "Timeout", "RuntimeError", "CompileError"}
SENTRY = re.compile(r'<PackageReference\s+Include="Sentry', re.IGNORECASE)
SENTRY_SWITCH = "<SentryDisableSourceGenerator>true</SentryDisableSourceGenerator>"
INSTALL = "dotnet-stryker is not installed: run `dotnet tool install dotnet-stryker` in the .NET root"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    found = dotnet.project_pair(ctx)
    if isinstance(found, str):
        return Result(GATE, False, found, [])
    blocked = precondition(ctx, *found)
    if blocked is not None:
        return blocked
    return scoped_run(ctx, found, started)


def precondition(ctx: Context, product: Path, tests: Path) -> Result | None:
    if tests.resolve() == product.resolve():
        return Result(
            GATE,
            False,
            "stryker needs the tests in their own .csproj",
            [f"{dotnet.rel(ctx, product)}:1 holds both product and test code"],
            0.0,
        )
    csproj = product.read_text(errors="replace")
    if SENTRY.search(csproj) and SENTRY_SWITCH not in csproj:
        return Result(
            GATE,
            False,
            "stryker cannot roll back mutants in Sentry's generated code",
            [f"{dotnet.rel(ctx, product)}:1 add {SENTRY_SWITCH} to a <PropertyGroup>"],
            0.0,
        )
    return None


def scoped_run(ctx: Context, pair: tuple[Path, Path], started: float) -> Result:
    scope = ctx.mutation_files("dotnet", ctx.dotnet_root(), (".cs",))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    targets = mutation_targets(ctx, scope.files)
    if scope.mode != "full" and not targets:
        return Result.skipped(GATE, "no changed C# sources")
    return stryker(ctx, pair, targets, (scope.note, started))


def stryker(ctx: Context, pair: tuple[Path, Path], targets: list[str], run_info: tuple[str, float]) -> Result:
    note, started = run_info
    out = ctx.work / OUTPUT_DIR
    shutil.rmtree(out, ignore_errors=True)
    refused = restore_failure(ctx, started)
    if refused is not None:
        return refused
    code, output = dotnet.dotnet(ctx, command(ctx, *pair, out, targets), timeout=7200)
    report = out / REPORT
    if not report.exists():
        return Result(GATE, False, dotnet.hint(code, output) or missing(output, code), tail(output), elapsed(started))
    return verdict(load_mutants(ctx, report), output, note, started)


def restore_failure(ctx: Context, started: float) -> Result | None:
    code, output = dotnet.dotnet(ctx, ["tool", "restore"], timeout=900)
    if code == 0:
        return None
    message = dotnet.hint(code, output) or f"dotnet tool restore failed: {INSTALL}"
    return Result(GATE, False, message, tail(output), elapsed(started))


def load_mutants(ctx: Context, report: Path) -> list[tuple[str, dict[str, Any]]]:
    files = json.loads(report.read_text()).get("files", {})
    return [
        (dotnet.rel(ctx, file), mutant)
        for file, data in files.items()
        for mutant in data.get("mutants", [])
        if not dotnet.mutation_excluded(ctx, dotnet.rel(ctx, file))
    ]


def verdict(mutants: list[tuple[str, dict[str, Any]]], output: str, note: str, started: float) -> Result:
    if not mutants:
        return Result(GATE, False, "no mutants were generated", tail(output), elapsed(started))
    findings = [describe(name, mutant) for name, mutant in mutants if mutant["status"] in BAD]
    return Result(GATE, not findings, summary(len(findings), len(mutants), note), findings, elapsed(started))


def summary(failed: int, total: int, note: str) -> str:
    text = f"{failed} of {total} mutants not killed" if failed else f"all {total} mutants killed"
    return text + (f" {note}" if note else "")


def mutation_targets(ctx: Context, files: list[str] | None) -> list[str]:
    if files is None:
        return []
    wanted = set(files)
    return [dotnet.rel(ctx, path) for path in dotnet.sources(ctx) if dotnet.rel(ctx, path) in wanted]


def missing(output: str, code: int) -> str:
    if "cannot find a tool" in output.lower() or "was not found" in output.lower():
        return INSTALL
    return f"stryker produced no report (exit {code})"


def command(ctx: Context, product: Path, tests: Path, out: Path, targets: list[str]) -> list[str]:
    prefix = dotnet.rel(ctx, ctx.dotnet_root()) + "/"
    excludes = [f"!**/{pattern.strip('/').removeprefix(prefix)}" for pattern in dotnet.mutation_patterns(ctx)]
    includes = ["**/" + name.removeprefix(prefix) for name in targets]
    return [
        "stryker",
        "--skip-version-check",
        "--break-on-initial-test-failure",
        "--test-project",
        str(tests),
        "--project",
        product.name,
        "-O",
        str(out),
        "-r",
        "json",
        "-r",
        "progress",
        *mutate(excludes + includes),
    ]


def mutate(patterns: list[str]) -> list[str]:
    return [arg for pattern in patterns for arg in ("-m", pattern)]


def describe(name: str, mutant: dict[str, Any]) -> str:
    line = mutant.get("location", {}).get("start", {}).get("line", 0)
    replacement = " ".join(str(mutant.get("replacement", "")).split())[:60]
    return f"{name}:{line} {mutant.get('mutatorName', 'mutant')} {mutant['status']}: {replacement}"
