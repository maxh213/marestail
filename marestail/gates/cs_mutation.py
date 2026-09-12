import json
import re
import shutil
import time
from pathlib import Path

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

OUTPUT_DIR = "stryker"
REPORT = Path("reports") / "mutation-report.json"
BAD = {"Survived", "NoCoverage", "Timeout", "RuntimeError", "CompileError"}
SENTRY = re.compile(r'<PackageReference\s+Include="Sentry', re.IGNORECASE)
SENTRY_SWITCH = "<SentryDisableSourceGenerator>true</SentryDisableSourceGenerator>"
INSTALL = "dotnet-stryker is not installed: run `dotnet tool install dotnet-stryker` in the .NET root"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    product, tests, error = dotnet.projects(ctx)
    if error:
        return Result("cs.mutation", False, error, [], 0.0)
    if tests.resolve() == product.resolve():
        return Result("cs.mutation", False, "stryker needs the tests in their own .csproj", [f"{dotnet.rel(ctx, product)}:1 holds both product and test code"], 0.0)
    csproj = product.read_text(errors="replace")
    if SENTRY.search(csproj) and SENTRY_SWITCH not in csproj:
        return Result("cs.mutation", False, "stryker cannot roll back mutants in Sentry's generated code", [f"{dotnet.rel(ctx, product)}:1 add {SENTRY_SWITCH} to a <PropertyGroup>"], 0.0)
    targets = [dotnet.rel(ctx, path) for path in dotnet.in_scope(ctx, dotnet.sources(ctx))]
    if ctx.scope_changed and not targets:
        return Result.skipped("cs.mutation", "no changed C# sources")
    out = ctx.work / OUTPUT_DIR
    shutil.rmtree(out, ignore_errors=True)
    code, output = dotnet.dotnet(ctx, ["tool", "restore"], timeout=900)
    if code != 0:
        message = dotnet.hint(code, output) or f"dotnet tool restore failed: {INSTALL}"
        return Result("cs.mutation", False, message, tail(output), time.time() - started)
    code, output = dotnet.dotnet(ctx, command(ctx, product, tests, out, targets), timeout=7200)
    report = out / REPORT
    if not report.exists():
        message = dotnet.hint(code, output) or missing(output, code)
        return Result("cs.mutation", False, message, tail(output), time.time() - started)
    files = json.loads(report.read_text()).get("files", {})
    mutants = [
        (dotnet.rel(ctx, file), mutant)
        for file, data in files.items()
        for mutant in data.get("mutants", [])
        if not dotnet.mutation_excluded(ctx, dotnet.rel(ctx, file))
    ]
    if not mutants:
        return Result("cs.mutation", False, "no mutants were generated", tail(output), time.time() - started)
    findings = [describe(name, mutant) for name, mutant in mutants if mutant["status"] in BAD]
    summary = f"{len(findings)} of {len(mutants)} mutants not killed" if findings else f"all {len(mutants)} mutants killed"
    return Result("cs.mutation", not findings, summary, findings, time.time() - started)


def missing(output: str, code: int) -> str:
    if "cannot find a tool" in output.lower() or "was not found" in output.lower():
        return INSTALL
    return f"stryker produced no report (exit {code})"


def command(ctx: Context, product: Path, tests: Path, out: Path, targets: list[str]) -> list[str]:
    args = [
        "stryker", "--skip-version-check", "--break-on-initial-test-failure",
        "--test-project", str(tests), "--project", product.name,
        "-O", str(out), "-r", "json", "-r", "progress",
    ]
    excludes = dotnet.listify(ctx.dotnet("mutation_exclude", [])) or dotnet.listify(ctx.dotnet("coverage_exclude", []))
    for pattern in excludes:
        name = pattern.strip("/")
        if name.startswith(dotnet.rel(ctx, ctx.dotnet_root()) + "/"):
            name = name.removeprefix(dotnet.rel(ctx, ctx.dotnet_root()) + "/")
        args += ["-m", f"!**/{name}"]
    if ctx.scope_changed:
        for name in targets:
            args += ["-m", "**/" + name.removeprefix(dotnet.rel(ctx, ctx.dotnet_root()) + "/")]
    return args


def describe(name: str, mutant: dict) -> str:
    line = mutant.get("location", {}).get("start", {}).get("line", 0)
    replacement = " ".join(str(mutant.get("replacement", "")).split())[:60]
    return f"{name}:{line} {mutant.get('mutatorName', 'mutant')} {mutant['status']}: {replacement}"
