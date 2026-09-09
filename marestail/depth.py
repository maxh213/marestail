import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

from marestail.config import Config
from marestail.shell import run

SKIP_DIRS = {"node_modules", ".venv", "venv", "dist", "build", "_build", "deps", "mutants", ".marestail", ".git", "__pycache__", "tests", "test", "coverage", "cover", "reports"}
TS_SCRIPT = Path(__file__).resolve().parent / "js" / "ts_depth.mjs"
EX_SCRIPT = Path(__file__).resolve().parent / "ex" / "depth.exs"
SHALLOW_MIN_PUBLIC = 4
SHALLOW_MAX_RATIO = 6.0


@dataclass
class Module:
    path: str
    public: list[str]
    statements: int
    pass_throughs: list[str] = field(default_factory=list)
    private_imports: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.statements / max(len(self.public), 1)

    @property
    def shallow(self) -> bool:
        return len(self.public) >= SHALLOW_MIN_PUBLIC and self.ratio < SHALLOW_MAX_RATIO


def analyse(config: Config) -> list[Module]:
    return python_modules(config) + ts_modules(config) + elixir_modules(config)


def python_modules(config: Config) -> list[Module]:
    if config.section("python") is None:
        return []
    root = config.root / config.get("python", "root", ".")
    files = sorted(p for p in root.rglob("*.py") if not skipped(p, root))
    return [python_module(path, root, config.root) for path in files]


def skipped(path: Path, root: Path) -> bool:
    return any(part in SKIP_DIRS or part.endswith("_tests.py") for part in path.relative_to(root).parts[:-1]) or path.name.endswith("_tests.py") or path.name.startswith("test_")


def python_module(path: Path, root: Path, repo: Path) -> Module:
    tree = ast.parse(path.read_text())
    module = Module(
        path=str(path.relative_to(repo)),
        public=public_names(tree),
        statements=sum(1 for node in ast.walk(tree) if isinstance(node, ast.stmt)),
    )
    label = module.path
    module.pass_throughs = [f"{label}:{fn.lineno} {fn.name} only forwards its arguments" for fn in functions(tree) if is_pass_through(fn)]
    module.private_imports = private_imports(tree, path, root, label)
    return module


def public_names(tree: ast.Module) -> list[str]:
    declared = explicit_all(tree)
    if declared is not None:
        return declared
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            names.append(node.name)
        if isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name) and not t.id.startswith("_"))
    return names


def explicit_all(tree: ast.Module) -> list[str] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                return [c.value for c in node.value.elts if isinstance(c, ast.Constant)]
    return None


def functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def is_pass_through(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if len(fn.body) != 1 or not isinstance(fn.body[0], ast.Return) or fn.decorator_list:
        return False
    call = fn.body[0].value
    if not isinstance(call, ast.Call) or call.keywords:
        return False
    params = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
    passed = [a.id for a in call.args if isinstance(a, ast.Name)]
    return len(passed) == len(call.args) and passed == params and len(params) > 0


def private_imports(tree: ast.Module, path: Path, root: Path, label: str) -> list[str]:
    own_package = path.relative_to(root).parent.parts
    findings = []
    for node in ast.walk(tree):
        for target in imported_modules(node):
            parts = target.split(".")
            private_index = next((i for i, part in enumerate(parts) if part.startswith("_")), None)
            if private_index is not None and tuple(parts[:private_index]) != own_package[: private_index]:
                findings.append(f"{label}:{node.lineno} imports private module {target} from outside its package")
    return findings


def imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
    return []


def ts_modules(config: Config) -> list[Module]:
    if config.section("ts") is None:
        return []
    ts_root = config.root / config.get("ts", "root", ".")
    source = ts_root / config.get("ts", "source", "src")
    files = sorted(p for p in source.rglob("*") if p.suffix in (".ts", ".tsx") and ".test." not in p.name and not skipped(p, ts_root))
    if not files:
        return []
    code, output = run(["node", str(TS_SCRIPT), str(ts_root), *map(str, files)], cwd=ts_root)
    if code != 0:
        raise SystemExit(f"ts depth analysis failed: {output[-300:]}")
    modules = []
    for entry in json.loads(output):
        label = str(Path(entry["file"]).resolve().relative_to(config.root))
        modules.append(Module(
            path=label,
            public=entry["exports"],
            statements=entry["statements"],
            pass_throughs=[f"{label}:{p['line']} {p['name']} only forwards its arguments" for p in entry["passThroughs"]],
        ))
    return modules


def elixir_modules(config: Config) -> list[Module]:
    if config.section("elixir") is None:
        return []
    root = config.root / config.get("elixir", "root", ".")
    files = sorted(p for p in root.rglob("*.ex") if not skipped(p, root) and not p.name.endswith("_test.exs"))
    if not files:
        return []
    code, output = run(["elixir", str(EX_SCRIPT), *map(str, files)], cwd=root)
    if code != 0:
        return []
    modules = []
    for item in json.loads(output):
        rel = str(Path(item["file"]).resolve().relative_to(config.root.resolve()))
        modules.append(Module(
            path=rel,
            public=item.get("public", []),
            statements=item.get("statements", 0),
            pass_throughs=item.get("pass_throughs", []),
        ))
    return modules


def report(modules: list[Module]) -> str:
    lines = ["module                                             public  stmts  ratio"]
    for module in sorted(modules, key=lambda m: m.ratio):
        mark = "  shallow" if module.shallow else ""
        lines.append(f"{module.path:<50} {len(module.public):>6} {module.statements:>6} {module.ratio:>6.1f}{mark}")
    problems = [p for m in modules for p in m.pass_throughs + m.private_imports]
    if problems:
        lines += ["", "hard rules:", *problems]
    return "\n".join(lines)
