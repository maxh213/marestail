import ast
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from operator import attrgetter
from pathlib import Path
from typing import Any, TypeGuard

from marestail.config import Config
from marestail.context import Context, under_benchmarks

SKIP_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "_build",
    "deps",
    "mutants",
    ".marestail",
    ".git",
    "__pycache__",
    "tests",
    "test",
    "coverage",
    "cover",
    "reports",
    "vendor",
    "tmp",
    "spec",
    "target",
}

SHALLOW_MIN_PUBLIC = 4
SHALLOW_MAX_RATIO = 6.0
LONG_FILE_LINES = 300
FORWARDS = "only forwards its arguments"
REPORT_HEADER = "module                                             public  stmts  ratio  lines"
Definition = ast.FunctionDef | ast.AsyncFunctionDef
Item = dict[str, Any]
PUBLIC = "public"
STATEMENTS = "statements"
PASS_THROUGHS = "pass_throughs"
MODE = "depth"
ELIXIR = "elixir"


@dataclass
class Module:
    path: str
    public: list[str]
    statements: int
    pass_throughs: list[str] = field(default_factory=list)
    private_imports: list[str] = field(default_factory=list)
    lines: int = 0

    @property
    def long(self) -> bool:
        return self.lines > LONG_FILE_LINES

    @property
    def ratio(self) -> float:
        return self.statements / max(len(self.public), 1)

    @property
    def shallow(self) -> bool:
        return len(self.public) >= SHALLOW_MIN_PUBLIC and self.ratio < SHALLOW_MAX_RATIO


def raw_modules(config: Config) -> list[Module]:
    return [module for section, scanner in scanners() if config.section(section) is not None for module in scanner(config)]


def scanners() -> list[tuple[str, Callable[[Config], list[Module]]]]:
    return [
        ("python", python_modules),
        ("ts", ts_modules),
        (ELIXIR, elixir_modules),
        ("erlang", erlang_modules),
        ("ruby", ruby_modules),
        ("dotnet", dotnet_modules),
        ("rust", rust_modules),
        ("java", java_modules),
    ]


def python_modules(config: Config) -> list[Module]:
    root = config.root / config.get("python", "root", ".")
    return [python_module(path, root, config.root) for path in python_files(root)]


def python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if not skipped(path, root))


def skipped(path: Path, root: Path) -> bool:
    return skipped_folder(path, root) or skipped_name(path.name) or under_benchmarks(root, path)


def skipped_folder(path: Path, root: Path) -> bool:
    return any(part in SKIP_DIRS or part.endswith("_tests.py") for part in path.relative_to(root).parts[:-1])


def skipped_name(name: str) -> bool:
    return name.endswith("_tests.py") or name.startswith("test_")


def python_module(path: Path, root: Path, repo: Path) -> Module:
    tree = ast.parse(path.read_text())
    label = str(path.relative_to(repo))
    return Module(
        path=label,
        public=public_names(tree),
        statements=statement_count(tree),
        pass_throughs=python_pass_throughs(tree, label),
        private_imports=private_imports(tree, path, root, label),
    )


def statement_count(tree: ast.Module) -> int:
    return sum(1 for node in ast.walk(tree) if isinstance(node, ast.stmt))


def python_pass_throughs(tree: ast.Module, label: str) -> list[str]:
    return [f"{label}:{fn.lineno} {fn.name} {FORWARDS}" for fn in functions(tree) if is_pass_through(fn)]


def public_names(tree: ast.Module) -> list[Any]:
    declared = explicit_all(tree)
    return module_names(tree) if declared is None else declared


def module_names(tree: ast.Module) -> list[str]:
    return [name for node in tree.body for name in declared_names(node) if not name.startswith("_")]


def declared_names(node: ast.stmt) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return assigned_names(node)
    return []


def assigned_names(node: ast.Assign) -> list[str]:
    return [target.id for target in node.targets if isinstance(target, ast.Name)]


def explicit_all(tree: ast.Module) -> list[Any] | None:
    return next((literal_strings(node.value) for node in tree.body if declares_all(node) and listed(node.value)), None)


def declares_all(node: ast.stmt) -> TypeGuard[ast.Assign]:
    return isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)


def listed(value: ast.expr) -> TypeGuard[ast.List | ast.Tuple]:
    return isinstance(value, (ast.List, ast.Tuple))


def literal_strings(value: ast.List | ast.Tuple) -> list[Any]:
    return [element.value for element in value.elts if isinstance(element, ast.Constant)]


def functions(tree: ast.Module) -> list[Definition]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def is_pass_through(fn: Definition) -> bool:
    call = returned_call(fn)
    return call is not None and forwards(parameters(fn), call.args)


def returned_call(fn: Definition) -> ast.Call | None:
    value = returned_value(fn)
    return value if isinstance(value, ast.Call) and not value.keywords else None


def returned_value(fn: Definition) -> ast.expr | None:
    statement = fn.body[0]
    if len(fn.body) != 1 or not isinstance(statement, ast.Return) or fn.decorator_list:
        return None
    return statement.value


def parameters(fn: Definition) -> list[str]:
    return [argument.arg for argument in fn.args.args if argument.arg not in ("self", "cls")]


def forwards(params: list[str], args: list[ast.expr]) -> bool:
    passed = plain_names(args)
    return passed is not None and passed == params and len(params) > 0


def plain_names(args: list[ast.expr]) -> list[str] | None:
    passed = [argument.id for argument in args if isinstance(argument, ast.Name)]
    return passed if len(passed) == len(args) else None


def private_imports(tree: ast.Module, path: Path, root: Path, label: str) -> list[str]:
    own_package = path.relative_to(root).parent.parts
    return [
        f"{label}:{node.lineno} imports private module {target} from outside its package"
        for node in import_statements(tree)
        for target in imported_modules(node)
        if outside_private(target, own_package)
    ]


def import_statements(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]


def outside_private(target: str, own_package: tuple[str, ...]) -> bool:
    parts = target.split(".")
    private_index = next((index for index, part in enumerate(parts) if part.startswith("_")), None)
    return private_index is not None and tuple(parts[:private_index]) != own_package[:private_index]


def imported_modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    return from_names(node) if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]


def from_names(node: ast.ImportFrom) -> list[str]:
    if not node.module:
        return []
    return [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]


def forwarders(label: str, found: list[Item]) -> list[str]:
    return [f"{label}:{p['line']} {p['name']} {FORWARDS}" for p in found]


def targeted_forwarders(label: str, found: list[Item]) -> list[str]:
    return [f"{label}:{p['line']} {p['name']} {FORWARDS} to {p['target']}" for p in found]


def ts_modules(config: Config) -> list[Module]:
    from marestail import javascript

    ts_root = config.root / config.get("ts", "root", ".")
    files = ts_files(ts_root, ts_root / config.get("ts", "source", "src"))
    if not files:
        return []
    code, output = javascript.scan(Context(config=config), "depth", files)
    if code != 0:
        raise SystemExit(f"ts depth analysis failed: {output[-300:]}")
    return [ts_module(entry, config.root) for entry in json.loads(output)]


def ts_files(ts_root: Path, source: Path) -> list[Path]:
    return sorted(path for path in source.rglob("*") if ts_source(path, ts_root))


def ts_source(path: Path, ts_root: Path) -> bool:
    return path.suffix in (".ts", ".tsx") and ".test." not in path.name and not skipped(path, ts_root)


def ts_module(entry: Item, repo: Path) -> Module:
    label = str(Path(entry["file"]).resolve().relative_to(repo))
    return Module(path=label, public=entry["exports"], statements=entry[STATEMENTS], pass_throughs=forwarders(label, entry["passThroughs"]))


def source_files(root: Path, pattern: str, test_suffix: str) -> list[Path]:
    return sorted(path for path in root.rglob(pattern) if not skipped(path, root) and not path.name.endswith(test_suffix))


def resolved_file(item: Item, repo: Path) -> str:
    return str(Path(item["file"]).resolve().relative_to(repo.resolve()))


def plain_modules(output: str, repo: Path) -> list[Module]:
    return [
        Module(
            path=resolved_file(item, repo),
            public=item.get(PUBLIC, []),
            statements=item.get(STATEMENTS, 0),
            pass_throughs=item.get(PASS_THROUGHS, []),
        )
        for item in json.loads(output)
    ]


def elixir_modules(config: Config) -> list[Module]:
    from marestail import elixir

    root = config.root / config.get(ELIXIR, "root", ".")
    files = source_files(root, "*.ex", "_test.exs")
    if not files:
        return []
    code, output = elixir.scan(Context(config=config), "depth", files)
    if code != 0:
        return []
    return plain_modules(output, config.root)


def erlang_modules(config: Config) -> list[Module]:
    from marestail import erlang

    ctx = Context(config=config)
    files = erlang.source_files(ctx)
    if not files:
        return []
    code, output = erlang.escript(ctx, "depth.escript", list(map(str, files)))
    if code != 0:
        return []
    return [erlang_module(item, config.root) for item in json.loads(output)]


def erlang_module(item: Item, repo: Path) -> Module:
    label = resolved_file(item, repo)
    return Module(
        path=label,
        public=item.get(PUBLIC, []),
        statements=item.get(STATEMENTS, 0),
        pass_throughs=forwarders(label, item.get(PASS_THROUGHS, [])),
    )


def ruby_modules(config: Config) -> list[Module]:
    from marestail.ruby import scan

    root = config.root / config.get("ruby", "root", ".")
    files = source_files(root, "*.rb", "_spec.rb")
    if not files:
        return []
    code, output = scan(Context(config=config), MODE, files)
    if code != 0:
        return []
    return plain_modules(output or "[]", config.root)


def targeted_module(item: Item, label: str) -> Module:
    return Module(
        path=label, public=item[PUBLIC], statements=item[STATEMENTS], pass_throughs=targeted_forwarders(label, item[PASS_THROUGHS])
    )


def scanned_items(data: object) -> list[Item]:
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def dotnet_modules(config: Config) -> list[Module]:
    from marestail import dotnet

    ctx = Context(config=config)
    files = dotnet.sources(ctx)
    if not files:
        return []
    data, error = dotnet.scan(ctx, MODE, files)
    if error:
        return []
    return [targeted_module(item, item["file"]) for item in scanned_items(data)]


def rust_modules(config: Config) -> list[Module]:
    from marestail import rust

    ctx = Context(config=config)
    files = rust.sources(ctx)
    if not files:
        return []
    data, error = rust.scan(ctx, MODE, files)
    if error:
        return []
    return [targeted_module(item, rust.rel(ctx, item["file"])) for item in scanned_items(data)]


def java_modules(config: Config) -> list[Module]:
    from marestail import java

    ctx = Context(config=config)
    files = java.sources(ctx)
    if not files:
        return []
    data, error = java.scan(ctx, MODE, files)
    if error:
        raise SystemExit(f"java depth analysis failed: {error}")
    return [targeted_module(item, item["file"]) for item in scanned_items(data)]


def rule_breaks(modules: list[Module]) -> list[str]:
    return [problem for module in modules for problem in module.pass_throughs + module.private_imports]


def report(modules: list[Module]) -> str:
    lines = [REPORT_HEADER, *(report_line(module) for module in sorted(modules, key=attrgetter("ratio")))]
    problems = rule_breaks(modules)
    if problems:
        lines += ["", "hard rules:", *problems]
    return "\n".join(lines)


def report_line(module: Module) -> str:
    marks = [mark for mark, on in (("shallow", module.shallow), ("long", module.long)) if on]
    suffix = "  " + ", ".join(marks) if marks else ""
    return f"{module.path:<50} {len(module.public):>6} {module.statements:>6} {module.ratio:>6.1f} {module.lines:>6}{suffix}"


def analyse(config: Config) -> list[Module]:
    modules = raw_modules(config)
    for module in modules:
        path = config.root / module.path
        module.lines = len(path.read_text().splitlines()) if path.is_file() else 0
    return modules
