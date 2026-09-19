import json
import shutil
import time
from pathlib import Path

from marestail.context import Context, MutationScope, is_benchmark
from marestail.report import Result
from marestail.shell import run, tail

GATE = "py.mutation"
STATUS_BY_EXIT_CODE = {
    1: "killed",
    3: "killed",
    0: "survived",
    5: "no tests",
    33: "no tests",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught by type check",
    -24: "timeout",
    24: "timeout",
    152: "timeout",
    255: "timeout",
    2: "interrupted",
    None: "not checked",
}
PASSING = {"killed", "skipped", "caught by type check"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    scope = ctx.mutation_files("python", ctx.python_root(), (".py",))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], time.time() - started)
    patterns = mutant_patterns(ctx, scope.files or [])
    if nothing_to_mutate(scope, patterns):
        return Result.skipped(GATE, "no changed python sources")
    return mutate(ctx, patterns, scope.note, started)


def nothing_to_mutate(scope: MutationScope, patterns: list[str]) -> bool:
    return scope.mode != "full" and not patterns


def mutate(ctx: Context, patterns: list[str], note: str, started: float) -> Result:
    shutil.rmtree(ctx.python_root() / "mutants", ignore_errors=True)
    workers = str(ctx.python("mutation_workers", 4))
    code, output = run(
        [ctx.python_bin("mutmut"), "run", *patterns, "--max-children", workers],
        cwd=ctx.python_root(),
        timeout=7200,
    )
    if code != 0 and "mutants" not in output.lower():
        return Result(GATE, False, "mutmut failed", tail(output), time.time() - started)
    total, survivors = surviving(ctx, patterns)
    if total == 0:
        return Result(GATE, False, "no mutants were generated", tail(output), time.time() - started)
    return Result(GATE, not survivors, mutation_summary(total, survivors, note), survivors, time.time() - started)


def mutation_summary(total: int, survivors: list[str], note: str) -> str:
    summary = f"{len(survivors)} of {total} mutants not killed" if survivors else f"all {total} mutants killed"
    return summary + (f" {note}" if note else "")


def mutant_patterns(ctx: Context, files: list[str]) -> list[str]:
    return [f"{module}.*" for module in mutable_modules(ctx, files) if module]


def mutable_modules(ctx: Context, files: list[str]) -> list[str]:
    root = ctx.python_root()
    return [module_name(root, ctx.root / file) for file in files if mutable(file)]


def mutable(file: str) -> bool:
    return "tests" not in Path(file).parts and not is_benchmark(file)


def module_name(root: Path, file: Path) -> str:
    relative = file.resolve().relative_to(root.resolve())
    return ".".join(relative.with_suffix("").parts)


def surviving(ctx: Context, patterns: list[str]) -> tuple[int, list[str]]:
    prefixes = tuple(pattern.rstrip("*") for pattern in patterns)
    statuses = mutant_statuses(ctx.python_root() / "mutants", prefixes)
    return len(statuses), [f"{name}: {status}" for name, status in statuses if status not in PASSING]


def mutant_statuses(folder: Path, prefixes: tuple[str, ...]) -> list[tuple[str, str]]:
    statuses: list[tuple[str, str]] = []
    for meta in sorted(folder.rglob("*.meta")):
        statuses.extend(
            (name, STATUS_BY_EXIT_CODE.get(code, "suspicious")) for name, code in exit_codes(meta).items() if selected(name, prefixes)
        )
    return statuses


def exit_codes(meta: Path) -> dict[str, int | None]:
    codes: dict[str, int | None] = json.loads(meta.read_text()).get("exit_code_by_key", {})
    return codes


def selected(name: str, prefixes: tuple[str, ...]) -> bool:
    return not prefixes or name.startswith(prefixes)
