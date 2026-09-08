import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

REPORT = "reports/mutation/mutation.json"
BAD = {"Survived", "NoCoverage", "Timeout", "RuntimeError", "CompileError"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    mutate = changed_sources(ctx)
    if ctx.scope_changed and not mutate:
        return Result.skipped("ts.mutation", "no changed typescript sources")
    command = ["npx", "stryker", "run", "--reporters", "json,progress"]
    if mutate:
        command += ["--mutate", ",".join(mutate)]
    code, output = run(command, cwd=ctx.ts_root(), timeout=7200)
    report = ctx.ts_root() / REPORT
    if not report.exists():
        return Result("ts.mutation", False, "stryker produced no report", tail(output), time.time() - started)
    survivors = surviving(json.loads(report.read_text()))
    summary = f"{len(survivors)} surviving mutants" if survivors else "all mutants killed"
    return Result("ts.mutation", not survivors, summary, survivors, time.time() - started)


def changed_sources(ctx: Context) -> list[str]:
    if not ctx.scope_changed:
        return []
    files = ctx.changed_under(ctx.ts_root(), (".ts", ".tsx"))
    root = ctx.ts_root().relative_to(ctx.root)
    return [str(Path(file).relative_to(root)) for file in files if ".test." not in file and ".spec." not in file]


def surviving(report: dict) -> list[str]:
    findings = []
    for file, data in report.get("files", {}).items():
        for mutant in data.get("mutants", []):
            if mutant["status"] in BAD:
                line = mutant["location"]["start"]["line"]
                findings.append(f"{file}:{line} {mutant['mutatorName']} {mutant['status']}: {mutant.get('replacement', '')[:60]}")
    return findings


