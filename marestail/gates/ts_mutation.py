import json
import shutil
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

REPORT = "reports/mutation/mutation.json"
TEMP_DIR = ".stryker-tmp"
BAD = {"Survived", "NoCoverage", "Timeout", "RuntimeError", "CompileError"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    mutate = changed_sources(ctx)
    if ctx.scope_changed and not mutate:
        return Result.skipped("ts.mutation", "no changed typescript sources")
    command = ["npx", "stryker", "run", "--reporters", "json,progress", "--tempDirName", TEMP_DIR, "--cleanTempDir", "always"]
    if mutate:
        command += ["--mutate", ",".join(mutate)]
    temp = ctx.ts_root() / TEMP_DIR
    report = ctx.ts_root() / REPORT
    shutil.rmtree(temp, ignore_errors=True)
    report.unlink(missing_ok=True)
    try:
        code, output = run(command, cwd=ctx.ts_root(), timeout=7200)
        if not report.exists():
            return Result("ts.mutation", False, f"stryker produced no report (exit {code})", tail(output), time.time() - started)
        survivors = surviving(json.loads(report.read_text()), ctx)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    summary = f"{len(survivors)} surviving mutants" if survivors else "all mutants killed"
    return Result("ts.mutation", not survivors, summary, survivors, time.time() - started)


def changed_sources(ctx: Context) -> list[str]:
    if not ctx.scope_changed:
        return []
    files = ctx.changed_under(ctx.ts_root(), (".ts", ".tsx"))
    root = ctx.ts_root().relative_to(ctx.root)
    return [str(Path(file).relative_to(root)) for file in files if ".test." not in file and ".spec." not in file]


def surviving(report: dict, ctx: Context) -> list[str]:
    findings = []
    for file, data in report.get("files", {}).items():
        name = relative(file, ctx)
        for mutant in data.get("mutants", []):
            if mutant["status"] in BAD:
                line = mutant["location"]["start"]["line"]
                findings.append(f"{name}:{line} {mutant['mutatorName']} {mutant['status']}: {str(mutant.get('replacement', ''))[:60]}")
    return findings


def relative(path: str, ctx: Context) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.ts_root() / path
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return path
