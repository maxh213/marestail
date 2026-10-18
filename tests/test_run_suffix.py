import argparse
import ast
import itertools
from pathlib import Path

import pytest

from marestail import run_suffix

ROOT = Path(__file__).resolve().parents[1]
FLAG = "must be 1 to 32 characters from a-z, 0-9 and -, got {raw!r}"
ROW = (
    "| `MARESTAIL_RUN_SUFFIX` | per-run suffix (`[a-z0-9-]{1,32}`) for the names a run shares with the machine: "
    "Sonar project `<key>_<suffix>`, perf containers `…_<suffix>` and their ports; `--run-suffix` on `run`, `gate` and `sonar` sets it |"
)
SETUP_LINE = "marestail sonar setup        # local SonarQube in docker, token in ~/.config/marestail"
RUN_LINE = "marestail run tasks/001.md --run-suffix a1   # a second run of this repo on this machine, in its own checkout"
PRUNE_LINE = "marestail sonar prune --run-suffix a1   # delete the Sonar project that suffix created"
PARAGRAPH = (
    "`MARESTAIL_RUN_SUFFIX`, or `--run-suffix` on `run`, `gate` and `sonar`, lets two runs of one repo share a machine, "
    "each in its own checkout, without editing a tracked file. The suffix is added to the Sonar project key (`<key>_<suffix>`) "
    "and name (`<name> (<suffix>)`), and to the perf containers and their ports. It is not added to the SonarQube server, "
    "the perf volume, or anything under `.marestail/`. The gate creates the suffixed project on its first analysis, "
    "and `marestail sonar prune` deletes that project."
)
NICE = "`marestail run` starts at nice 19"
DB_SENTENCE = (
    "With `MARESTAIL_RUN_SUFFIX` set, the containers end in `_<suffix>`, the ports move by a per-suffix offset unless "
    "`MARESTAIL_PERF_DB_PORT` is set, `down` removes only that run's containers, and goldens in the shared volume are still "
    "reused; two suffixes that hash to the same port fail at `docker run` because the port is already allocated, "
    "and `MARESTAIL_PERF_DB_PORT` is the way out."
)
DB_START = "A bench that touches a database runs with `--db`"
JOBS_ENV = "MARESTAIL_JOBS"
SUFFIX_ENV = "MARESTAIL_RUN_SUFFIX"
ADJACENT = """
import pytest

@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    monkeypatch.delenv("MARESTAIL_JOBS", raising=False)
    monkeypatch.delenv("MARESTAIL_RUN_SUFFIX", raising=False)
"""
REVERSED = """
import pytest

@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    monkeypatch.delenv("MARESTAIL_RUN_SUFFIX", raising=False)
    monkeypatch.delenv("MARESTAIL_JOBS", raising=False)
"""
OUTSIDE = """
import pytest

def other(monkeypatch):
    monkeypatch.delenv("MARESTAIL_JOBS", raising=False)
    monkeypatch.delenv("MARESTAIL_RUN_SUFFIX", raising=False)

@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    return None
"""
SEPARATED = """
import pytest

@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    monkeypatch.delenv("MARESTAIL_JOBS", raising=False)
    monkeypatch.setattr(object, "nothing", None)
    monkeypatch.delenv("MARESTAIL_RUN_SUFFIX", raising=False)
"""
NOT_AUTOUSE = """
import pytest

@pytest.fixture(autouse=False)
def hermetic(monkeypatch):
    monkeypatch.delenv("MARESTAIL_JOBS", raising=False)
    monkeypatch.delenv("MARESTAIL_RUN_SUFFIX", raising=False)
"""
RAISED = """
import pytest

@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    monkeypatch.delenv("MARESTAIL_JOBS", raising=False)
    monkeypatch.delenv("MARESTAIL_RUN_SUFFIX", raising=True)
"""
LONG = "a" * 32
VALID = [
    ("a1", "k_a1", "K (a1)"),
    ("a", "k_a", "K (a)"),
    ("0", "k_0", "K (0)"),
    ("-", "k_-", "K (-)"),
    ("a-1-b", "k_a-1-b", "K (a-1-b)"),
    (LONG, "k_" + LONG, "K (" + LONG + ")"),
]
BAD = ["", "A1", "a_1", "a.1", "a/b", "a 1", "a1\n", "é", "a" * 33]


def rejected(raw: str) -> str:
    return f"{run_suffix.ENV} {FLAG.format(raw=raw)}"


def test_unset_suffix_leaves_names_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_suffix.current() is None
    assert run_suffix.named("k") == "k"
    assert run_suffix.titled("K") == "K"
    monkeypatch.setenv(run_suffix.ENV, "a1")
    assert run_suffix.current() == "a1"
    assert run_suffix.named("k") == "k_a1"
    monkeypatch.delenv(run_suffix.ENV)
    assert run_suffix.current() is None
    assert run_suffix.named("k") == "k"
    assert clears_suffix_beside_jobs((ROOT / "tests" / "conftest.py").read_text())


@pytest.mark.parametrize(("suffix", "named", "titled"), VALID)
def test_valid_suffix_is_appended_once(monkeypatch: pytest.MonkeyPatch, suffix: str, named: str, titled: str) -> None:
    monkeypatch.setenv(run_suffix.ENV, suffix)
    assert run_suffix.current() == suffix
    assert run_suffix.argument(suffix) == suffix
    assert run_suffix.named("k") == named
    assert run_suffix.titled("K") == titled


@pytest.mark.parametrize("raw", BAD)
def test_bad_suffix_is_a_system_exit(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(run_suffix.ENV, raw)
    with pytest.raises(SystemExit) as raised:
        run_suffix.current()
    assert str(raised.value) == rejected(raw)
    assert "\n" not in str(raised.value)


def test_hermetic_clears_the_suffix_beside_jobs() -> None:
    assert clears_suffix_beside_jobs(ADJACENT)
    assert clears_suffix_beside_jobs(REVERSED)
    assert not clears_suffix_beside_jobs(OUTSIDE)
    assert not clears_suffix_beside_jobs(SEPARATED)
    assert not clears_suffix_beside_jobs(NOT_AUTOUSE)
    assert not clears_suffix_beside_jobs(RAISED)


@pytest.mark.parametrize("raw", BAD)
def test_bad_suffix_is_an_argument_error(raw: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError) as raised:
        run_suffix.argument(raw)
    assert str(raised.value) == FLAG.format(raw=raw)


def test_module_imports_only_os_and_re() -> None:
    tree = ast.parse((ROOT / "marestail" / "run_suffix.py").read_text())
    assert isinstance(tree, ast.Module)
    assert module_imports(tree) == {"os", "re"}
    assert argument_imports_argparse(tree)
    planted = ast.parse("import os\nimport re\nfrom pathlib import Path\n")
    assert isinstance(planted, ast.Module)
    assert module_imports(planted) == {"os", "re", "pathlib"}


CAUGHT = [
    "import run_suffix",
    "import marestail.run_suffix",
    "import marestail.run_suffix as other",
    "from run_suffix import current",
    "from marestail import run_suffix",
    "from marestail import run_suffix as other",
    "from marestail.run_suffix import current",
    "from . import run_suffix",
]
MISSED = [
    "import marestail.run_suffix_tool",
    "from marestail import run_suffix_tool",
    "from marestail.run_suffix_tool import current",
    "from marestail.run_suffix.extra import current",
]


def test_setup_does_not_import_run_suffix() -> None:
    tree = ast.parse((ROOT / "marestail" / "sonar" / "setup.py").read_text())
    assert isinstance(tree, ast.Module)
    assert "run_suffix" not in imported(tree)
    for source in CAUGHT:
        assert "run_suffix" in imported(parsed(source))
    for source in MISSED:
        assert "run_suffix" not in imported(parsed(source))


def test_readme_and_layer_say_where_the_suffix_lives() -> None:
    readme = (ROOT / "README.md").read_text()
    lines = readme.splitlines()
    task = line_index(lines, "| `MARESTAIL_TASK` |")
    assert lines[task + 1] == ROW
    assert lines[task + 2].startswith("| `MARESTAIL_QA_CMD_TIMEOUT` |")
    setup = line_index(lines, SETUP_LINE)
    assert lines[setup + 1] == RUN_LINE
    assert lines[setup + 2] == PRUNE_LINE
    paragraphs = use_paragraphs(readme)
    assert paragraphs[0] == PARAGRAPH
    assert paragraphs[1].startswith(NICE)
    paragraph = db_paragraph(readme)
    assert paragraph.startswith(DB_START)
    assert paragraph.endswith(DB_SENTENCE)
    assert not db_paragraph(f"{DB_START} stays.\n\n{DB_SENTENCE}").endswith(DB_SENTENCE)
    layer = layer_line()
    assert "marestail.run_suffix" in layer
    assert "marestail.run_suffix" not in jobs_line()
    text = (ROOT / ".importlinter").read_text()
    assert "marestail.run_suffix" in contract_block(text, "foundations-import-nothing-above")
    assert text.count("[importlinter:contract:") == 38


def module_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        names.update(top_import(node))
    return names


def top_import(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {node.module or ""}
    return set()


def imported(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        names.update(import_node(node))
    return names


def import_node(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return imported_modules(node.names)
    if isinstance(node, ast.ImportFrom):
        return imported_from(node)
    return set()


def imported_modules(aliases: list[ast.alias]) -> set[str]:
    found: set[str] = set()
    for alias in aliases:
        found |= module_name(alias.name)
    return found


def imported_from(node: ast.ImportFrom) -> set[str]:
    found = module_name(node.module or "")
    return found | {alias.name for alias in node.names}


def module_name(module: str) -> set[str]:
    found = {module}
    if module.rsplit(".", 1)[-1] == "run_suffix":
        found.add("run_suffix")
    return found


def parsed(source: str) -> ast.Module:
    tree = ast.parse(source)
    assert isinstance(tree, ast.Module)
    return tree


def argument_imports_argparse(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "argument":
            return body_imports_argparse(node)
    return False


def body_imports_argparse(node: ast.FunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            return any(alias.name == "argparse" for alias in child.names)
    return False


def clears_suffix_beside_jobs(source: str) -> bool:
    tree = ast.parse(source)
    assert isinstance(tree, ast.Module)
    return suffix_beside_jobs(hermetic_body(tree))


def hermetic_body(tree: ast.Module) -> list[ast.stmt]:
    for node in tree.body:
        found = hermetic_function(node)
        if found is not None:
            return found.body
    return []


def hermetic_function(node: ast.stmt) -> ast.FunctionDef | None:
    if isinstance(node, ast.FunctionDef) and node.name == "hermetic" and autouse(node):
        return node
    return None


def autouse(node: ast.FunctionDef) -> bool:
    return any(autouse_decorator(item) for item in node.decorator_list)


def autouse_decorator(item: ast.expr) -> bool:
    if not isinstance(item, ast.Call):
        return False
    return any(keyword_is(keyword, "autouse", True) for keyword in item.keywords)


def keyword_is(keyword: ast.keyword, name: str, expected: object) -> bool:
    value = keyword.value
    return keyword.arg == name and isinstance(value, ast.Constant) and value.value is expected


def suffix_beside_jobs(body: list[ast.stmt]) -> bool:
    names = [delenv_target(statement) for statement in body]
    pairs = list(itertools.pairwise(names))
    return (JOBS_ENV, SUFFIX_ENV) in pairs or (SUFFIX_ENV, JOBS_ENV) in pairs


def delenv_target(statement: ast.stmt) -> str | None:
    call = called(statement)
    if call is None or not env_delete(call) or not raising_false(call):
        return None
    return text_arg(call)


def called(statement: ast.stmt) -> ast.Call | None:
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        return statement.value
    return None


def env_delete(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "delenv":
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "monkeypatch"


def raising_false(call: ast.Call) -> bool:
    return any(keyword_is(keyword, "raising", False) for keyword in call.keywords)


def text_arg(call: ast.Call) -> str | None:
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def db_paragraph(readme: str) -> str:
    for chunk in readme.split("\n\n"):
        text = chunk.strip()
        if text.startswith(DB_START):
            return text
    raise AssertionError(DB_START)


def line_index(lines: list[str], prefix: str) -> int:
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return index
    raise AssertionError(prefix)


def layer_line() -> str:
    for line in (ROOT / ".importlinter").read_text().splitlines():
        if "marestail.report" in line and "marestail._location" in line:
            return line
    raise AssertionError("layer")


def jobs_line() -> str:
    for line in (ROOT / ".importlinter").read_text().splitlines():
        if "marestail.jobs" in line:
            return line
    raise AssertionError("jobs")


def use_paragraphs(readme: str) -> list[str]:
    after = readme.split("## Use", 1)[1].split("```", 2)[2]
    return [chunk.strip() for chunk in after.split("\n\n") if chunk.strip()]


def contract_block(text: str, name: str) -> str:
    start = text.index(f"[importlinter:contract:{name}]")
    rest = text[start:]
    end = rest.index("\n[importlinter:contract:", 1)
    return rest[:end]
