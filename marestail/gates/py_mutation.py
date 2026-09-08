import json
import shutil
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

STATUS_BY_EXIT_CODE = {
    1: "killed", 3: "killed", 0: "survived", 5: "no tests", 33: "no tests", 34: "skipped", 35: "suspicious",
    36: "timeout", 37: "caught by type check", -24: "timeout", 24: "timeout", 152: "timeout", 255: "timeout",
    2: "interrupted", None: "not checked",
}
PASSING = {"killed", "skipped", "caught by type check"}


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
    total, survivors = surviving(ctx, patterns)
    if total == 0:
        return Result("py.mutation", False, "no mutants were generated", tail(output), time.time() - started)
    summary = f"{len(survivors)} of {total} mutants not killed" if survivors else f"all {total} mutants killed"
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


def surviving(ctx: Context, patterns: list[str]) -> tuple[int, list[str]]:
    prefixes = tuple(pattern.rstrip("*") for pattern in patterns)
    total = 0
    findings = []
    for meta in sorted((ctx.python_root() / "mutants").rglob("*.meta")):
        for name, code in json.loads(meta.read_text()).get("exit_code_by_key", {}).items():
            if prefixes and not name.startswith(prefixes):
                continue
            total += 1
            status = STATUS_BY_EXIT_CODE.get(code, "suspicious")
            if status not in PASSING:
                findings.append(f"{name}: {status}")
    return total, findings
