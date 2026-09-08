import shutil
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

PASSING = {"killed", "skipped"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    patterns = mutant_patterns(ctx)
    if ctx.scope_changed and not patterns:
        return Result.skipped("py.mutation", "no changed python sources")
    shutil.rmtree(ctx.python_root() / "mutants", ignore_errors=True)
    workers = str(ctx.python("mutation_workers", 4))
    code, output = run(
        [ctx.python_bin("mutmut"), "run", *patterns, "--max-children", workers],
        cwd=ctx.python_root(), timeout=7200,
    )
    if code != 0 and "mutants" not in output.lower():
        return Result("py.mutation", False, "mutmut failed", tail(output), time.time() - started)
    survivors = surviving(ctx, patterns)
    summary = f"{len(survivors)} surviving mutants" if survivors else "all mutants killed"
    return Result("py.mutation", not survivors, summary, survivors, time.time() - started)


def mutant_patterns(ctx: Context) -> list[str]:
    if not ctx.scope_changed:
        return []
    root = ctx.python_root()
    files = ctx.changed_under(root, (".py",))
    modules = [module_name(root, ctx.root / file) for file in files if "tests" not in Path(file).parts]
    return [f"{module}.*" for module in modules if module]


def module_name(root: Path, file: Path) -> str:
    relative = file.resolve().relative_to(root.resolve())
    return ".".join(relative.with_suffix("").parts)


def surviving(ctx: Context, patterns: list[str]) -> list[str]:
    _, output = run([ctx.python_bin("mutmut"), "results"], cwd=ctx.python_root(), timeout=600)
    prefixes = tuple(pattern.rstrip("*") for pattern in patterns)
    findings = []
    for line in output.splitlines():
        name, _, status = line.strip().partition(": ")
        if not status or status in PASSING or (prefixes and not name.startswith(prefixes)):
            continue
        findings.append(f"{name}: {status}")
    return findings


