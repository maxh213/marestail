import ast
import json
import shutil
import time
from itertools import compress, count
from operator import ne
from pathlib import Path
from typing import Any

from marestail.context import NO_CHANGED_MUTANTS, Context, MutationScope, is_benchmark
from marestail.report import Result, elapsed
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
MUTMUT = "__mutmut_"
ORIGINAL = "__mutmut_orig"
METHOD_MARK = "ǁ"
Status = tuple[str, str]
Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def run_gate(ctx: Context) -> Result:
    started = time.time()
    scope = ctx.mutation_files("python", ctx.python_root(), (".py",))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    patterns = mutant_patterns(ctx, scope.files or [])
    if nothing_to_mutate(scope, patterns):
        return Result.skipped(GATE, "no changed python sources")
    return mutate(ctx, patterns, str(scope.note), started)


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
        return Result(GATE, False, "mutmut failed", tail(output), elapsed(started))
    statuses = mutant_statuses(ctx.python_root() / "mutants", tuple(map(mutant_prefix, patterns)))
    if not statuses:
        return Result(GATE, False, "no mutants were generated", tail(output), elapsed(started))
    return judged(ctx.on_changed_lines(statuses, MutantLines(ctx).where), note, started)


def judged(statuses: list[Status], note: str, started: float) -> Result:
    if not statuses:
        return Result(GATE, True, NO_CHANGED_MUTANTS, [], elapsed(started))
    survivors = [f"{name}: {status}" for name, status in statuses if status not in PASSING]
    return Result(GATE, not survivors, mutation_summary(len(statuses), survivors, note), survivors, elapsed(started))


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


def mutant_statuses(folder: Path, prefixes: tuple[str, ...]) -> list[tuple[str, str]]:
    statuses: list[tuple[str, str]] = []
    for meta in sorted(folder.rglob("*.meta")):
        statuses.extend(
            (name, STATUS_BY_EXIT_CODE.get(code, "suspicious")) for name, code in exit_codes(meta).items() if selected(name, prefixes)
        )
    return statuses


def exit_codes(meta: Path) -> dict[str, int | None]:
    codes = codes_field(json.loads(meta.read_text()))
    return codes if isinstance(codes, dict) else {}


def codes_field(data: dict[str, Any]) -> Any:
    if "exit_code_by_key" not in data:
        return {}
    return data["exit_code_by_key"]


def selected(name: str, prefixes: tuple[str, ...]) -> bool:
    return not prefixes or name.startswith(prefixes)


STAR = "*"


def mutant_prefix(pattern: str) -> str:
    return pattern[: -len(STAR)] if pattern.endswith(STAR) else pattern


class MutantLines:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.root = ctx.python_root()
        self.sources: dict[Path, tuple[list[str], ast.Module] | None] = {}

    def where(self, status: Status) -> str:
        module, _, key = status[0].rpartition(".")
        relative = module_file(self.root, module)
        return f"{(self.root / relative).relative_to(self.ctx.root)}:{self.line(relative, key)}"

    def line(self, relative: Path, key: str) -> int:
        function = key.rpartition(MUTMUT)[0]
        names = function[2:].split(METHOD_MARK)
        source = self.definition(self.root / relative, names)
        mutated = self.root / "mutants" / relative
        original = self.definition(mutated, [*names[:-1], function + ORIGINAL])
        mutant = self.definition(mutated, [*names[:-1], key])
        if source is None or original is None or mutant is None:
            return 0
        return source.lineno + 1 + first_difference(self.body(mutated, original), self.body(mutated, mutant))

    def parsed(self, path: Path) -> tuple[list[str], ast.Module] | None:
        if path not in self.sources:
            self.sources[path] = parse_file(path)
        return self.sources[path]

    def definition(self, path: Path, names: list[str]) -> Definition | None:
        parsed = self.parsed(path)
        return None if parsed is None else find_definition(parsed[1].body, names)

    def body(self, path: Path, node: Definition) -> list[str]:
        parsed = self.parsed(path)
        return [] if parsed is None else parsed[0][node.lineno : node.end_lineno]


def module_file(root: Path, module: str) -> Path:
    candidate = Path(*module.split(".")).with_suffix(".py")
    return candidate if (root / candidate).exists() else Path(*module.split(".")) / "__init__.py"


def parse_file(path: Path) -> tuple[list[str], ast.Module] | None:
    try:
        text = path.read_text()
        return text.splitlines(), ast.parse(text)
    except (OSError, SyntaxError, ValueError):
        return None


def find_definition(body: list[ast.stmt], names: list[str]) -> Definition | None:
    node = named(body, names[0])
    if node is None or len(names) == 1:
        return node
    return find_definition(node.body, names[1:])


def named(body: list[ast.stmt], name: str) -> Definition | None:
    return next((node for node in body if isinstance(node, DEFINITIONS) and node.name == name), None)


def first_difference(original: list[str], mutant: list[str]) -> int:
    return next(compress(count(), map(ne, original, mutant)), 0)
