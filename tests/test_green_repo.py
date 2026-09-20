import argparse
import ast
import io
import json
import re
import shutil
import socket
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

from marestail import cli, graph, report
from marestail import config as config_module
from marestail.gates import comments, configured_gates, py_mutation, py_runtime
from marestail.report import Result
from tests.conftest import ForbiddenCallError, make_context


def repo_root() -> Path:
    return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip())


ROOT = repo_root()
PASSING_SCRIPTS = [
    "test-agent-backends.py",
    "test-audit.py",
    "test-csproj-additions.py",
    "test-drop-ignored.py",
    "test-route.py",
    "test-scope-hard.py",
    "test-sonar-worktree.py",
    "test-practices.py",
]
PERF_FAIL_LINE = "verdict-commit-files: '' != 'perf/bench_x.py'"
FAST_GATES = ["py.tests", "py.crap", "py.lint", "py.deps", "py.runtime", "comments", "depth", "deadcode", "docs"]
RESULT_LINE = re.compile(r"^\[ok  \] .{14} .+  \(\d+\.\d+s\)$")
DOCUMENTED = [
    "MARESTAIL_AGENT",
    "MARESTAIL_AGY",
    "MARESTAIL_CLAUDE",
    "MARESTAIL_CURSOR",
    "MARESTAIL_DANDELION",
    "MARESTAIL_FOCUS",
    "MARESTAIL_GATE_ACTIVE",
    "MARESTAIL_GROK",
    "MARESTAIL_GROK_EFFORT",
    "MARESTAIL_KILO",
    "MARESTAIL_KILO_VARIANT",
    "MARESTAIL_KIMI",
    "MARESTAIL_LIMIT_WAIT_SECONDS",
    "MARESTAIL_LIMIT_WAITS",
    "MARESTAIL_PERF_DB_HOME",
    "MARESTAIL_PERF_DB_PORT",
    "MARESTAIL_PERF_DB_PREFIX",
    "MARESTAIL_PERF_DB_ROWS",
    "MARESTAIL_PERF_DB_VOLUME",
    "MARESTAIL_SCOPE",
    "MARESTAIL_SONAR_PASSWORD",
    "CLAUDE_CONFIG_DIR",
    "DANDELION_CLAUDE_WORK_CONFIG_DIR",
    "GROK_HOME",
    "JAVA_HOME",
]
SPLIT_MODULES = ["marestail/runner.py", "marestail/install.py", "marestail/context.py"]
SCANNER_SOURCE_SUFFIXES = {".java", ".cs", ".mjs", ".rb", ".rs", ".escript", ".exs"}
FROZEN_SCANNER_PROJECTS = [
    "marestail/cs/scan/Scan.csproj",
    "marestail/jvm/pmd-ruleset.xml",
    "marestail/jvm/tools/pom.xml",
    "marestail/rs/scan/Cargo.toml",
]


def package_files() -> list[Path]:
    return sorted((ROOT / "marestail").rglob("*.py"))


def gate_names(tier: str) -> list[str]:
    return [gate.name for gate in configured_gates(config_module.load(ROOT), tier, None)]


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        ("fast", FAST_GATES),
        ("sonar", [*FAST_GATES, "sonar"]),
        ("full", [*FAST_GATES, "py.mutation", "sonar"]),
        ("qa", FAST_GATES),
        ("all", [*FAST_GATES, "py.mutation", "sonar"]),
    ],
)
def test_every_tier_names_the_same_gates_in_the_same_order(tier: str, expected: list[str]) -> None:
    assert gate_names(tier) == expected


def test_py_runtime_line_is_the_skip_line() -> None:
    result = py_runtime.run_gate(make_context(ROOT, config_module.load(ROOT).raw))
    assert report.render_one(result) == "[ok  ] py.runtime     skipped: nothing declares the interpreter that ships  (0.0s)"


def test_passing_result_lines_keep_their_shape() -> None:
    rendered = report.render([Result("comments", True, "no comments", [], 0.84), Result("docs", True, "docs match the code", [], 12.0)])
    lines = rendered.splitlines()
    assert all(RESULT_LINE.match(line) for line in lines[:2])
    assert lines[-1] == "GATE PASSED"


def test_changed_scope_line_names_files_lines_and_focus(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"a.py", "b.py"}, focus={"marestail"}, changed_lines_map={"a.py": {1, 2, 3}})
    line = cli.scope_line(ctx)
    assert line is not None
    assert re.fullmatch(r"changed \(\d+ files, \d+ lines\) \+ focus: marestail", line)
    assert report.render([], line).splitlines()[0] == "scope: changed (2 files, 3 lines) + focus: marestail"


def test_hard_scope_line_names_the_focus(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, focus={"marestail/cli.py"}, hard=True)
    assert report.render([], cli.scope_line(ctx)).splitlines()[0] == "scope: hard: marestail/cli.py"


def test_scope_all_prints_no_scope_line(tmp_path: Path) -> None:
    rendered = report.render([Result("docs", True, "docs match the code")], cli.scope_line(make_context(tmp_path)))
    assert not any(line.startswith("scope:") for line in rendered.splitlines())
    assert rendered.splitlines()[-1] == "GATE PASSED"


def test_gate_json_keeps_scope_focus_and_results() -> None:
    payload = report.to_json([Result("py.tests", True, "1 passed")], "all", set())
    assert list(json.loads(payload)) == ["scope", "focus", "results"]
    assert '"gate": "py.tests"' in payload


def test_a_comment_fails_the_comments_gate(tmp_path: Path) -> None:
    (tmp_path / "marestail").mkdir()
    (tmp_path / "marestail" / "_deliberate_comment.py").write_text("# a comment\npass\n")
    result = comments.run_gate(make_context(tmp_path, {"comments": {"paths": ["marestail"]}}))
    rendered = report.render([result])
    assert "marestail/_deliberate_comment.py:1 comment: # a comment" in rendered
    assert rendered.endswith("GATE FAILED: comments")


@pytest.mark.parametrize("program", ["docker", "claude", "grok", "kilo", "kimi", "cursor-agent", "agy", "dandelion", "sonar-scanner"])
def test_tests_cannot_start_agents_or_docker(program: str) -> None:
    with pytest.raises(ForbiddenCallError):
        subprocess.run([program, "--version"], check=False)


def test_tests_cannot_reach_the_network() -> None:
    with pytest.raises(ForbiddenCallError):
        socket.create_connection(("localhost", 9000))


def long_functions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [
        f"{path.name}:{node.lineno} {node.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.end_lineno is not None and node.end_lineno - node.lineno >= 30
    ]


def test_orchestration_functions_are_split_into_small_helpers() -> None:
    paths = [ROOT / name for name in SPLIT_MODULES] + sorted((ROOT / "marestail" / "gates").glob("*.py"))
    found = [finding for path in paths for finding in long_functions(path)]
    assert [finding for finding in found if not re.fullmatch(r"__init__\.py:\d+ registry", finding)] == []


def test_this_repo_graph_starts_with_python_modules() -> None:
    text = graph.python_graph(config_module.load(ROOT))
    assert text.splitlines()[0] == "## Python modules"
    assert "package_name" in text


def test_help_lists_every_subcommand() -> None:
    text = cli.build_parser().format_help()
    listed = re.search(r"\{([a-z,]+)\}", text)
    assert listed is not None
    assert listed.group(1).split(",") == ["gate", "run", "install", "sonar", "watch", "perf", "route", "graph", "depth"]


def parse_help(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    parser.parse_args([*argv, "--help"])


@pytest.mark.parametrize(
    ("argv", "flags"),
    [
        (["gate"], ["--tier", "--scope", "--focus", "--only", "--json", "--hook"]),
        (["run"], ["--from", "--to", "--auto", "--scope", "--focus", "--model", "--retries", "--effort", "--agent"]),
        (["install"], ["--gitignore-generated"]),
        (["sonar"], ["up", "down", "setup"]),
        (["perf"], ["run", "db"]),
        (["watch"], ["--refresh", "--all"]),
    ],
)
def test_subcommand_help_lists_its_flags_in_order(argv: list[str], flags: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parse_help(parser, argv)
    text = capsys.readouterr().out
    positions = [text.index(flag) for flag in flags]
    assert positions == sorted(positions)


def tools_references(script: Path) -> set[tuple[str, str]]:
    tree = ast.parse(script.read_text())
    return {
        (node.module, alias.name) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module for alias in node.names
    }


@pytest.mark.parametrize("script", sorted((ROOT / "tools").glob("test-*.py")), ids=lambda path: path.name)
def test_tools_scripts_still_find_every_name_they_import(script: Path) -> None:
    missing = [
        f"{module}.{name}"
        for module, name in tools_references(script)
        if module.startswith("marestail") and not hasattr(__import__(module, fromlist=[name]), name)
    ]
    assert missing == []


def last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def run_tools_script(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "tools" / name)], cwd=ROOT, capture_output=True, text=True, timeout=180)


def under_mutmut() -> bool:
    return Path.cwd().name == "mutants"


def restricted_path() -> bool:
    return shutil.which("ps") is None or under_mutmut()


@pytest.mark.skipif(restricted_path(), reason="diagnostic scripts need a normal PATH")
@pytest.mark.parametrize("name", PASSING_SCRIPTS)
def test_diagnostic_script_exits_ok(name: str) -> None:
    completed = run_tools_script(name)
    assert completed.returncode == 0
    assert "ok" in last_line(completed.stdout + completed.stderr)


@pytest.mark.skipif(restricted_path() or shutil.which("docker") is None, reason="perf-db needs docker")
def test_perf_db_script_exits_ok() -> None:
    completed = run_tools_script("test-perf-db.py")
    assert completed.returncode == 0
    assert "ok" in last_line(completed.stdout + completed.stderr)


@pytest.mark.skipif(restricted_path(), reason="diagnostic scripts need a normal PATH")
def test_tools_test_perf_fails_as_before() -> None:
    completed = run_tools_script("test-perf.py")
    assert completed.returncode == 1
    assert last_line(completed.stdout + completed.stderr) == PERF_FAIL_LINE


def test_unchecked_mutants_fail_the_mutation_gate() -> None:
    assert py_mutation.STATUS_BY_EXIT_CODE[None] == "not checked"
    assert "not checked" not in py_mutation.PASSING
    assert "no tests" not in py_mutation.PASSING
    assert "survived" not in py_mutation.PASSING


def test_full_tier_runs_mutation_before_sonar() -> None:
    names = gate_names("full")
    assert names[-2:] == ["py.mutation", "sonar"]
    assert "py.mutation" in names


def test_repo_root_is_the_git_toplevel() -> None:
    assert repo_root() == ROOT
    assert (ROOT / "marestail.toml").is_file()


def test_last_line_and_mutmut_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert last_line("") == ""
    assert last_line("\n\nok\n") == "ok"
    monkeypatch.chdir(tmp_path)
    assert under_mutmut() is False
    assert restricted_path() is False
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert restricted_path() is True
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/ps")
    folder = tmp_path / "mutants"
    folder.mkdir()
    monkeypatch.chdir(folder)
    assert under_mutmut() is True
    assert restricted_path() is True


def readme_section() -> str:
    text = (ROOT / "README.md").read_text()
    return text.split("## Environment variables", 1)[1].split("\n## ", 1)[0]


def test_readme_documents_every_environment_variable() -> None:
    section = readme_section()
    assert [name for name in DOCUMENTED if f"`{name}`" not in section] == []
    assert "`HOME` and `PATH`" in section


def comments_in(path: Path) -> list[str]:
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
    return [f"{path}:{token.start[0]}" for token in tokens if token.type == tokenize.COMMENT and not token.string.startswith("#!")]


def docstrings_in(path: Path) -> list[str]:
    kinds = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    return [
        f"{path}:{getattr(node, 'lineno', 1)}"
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, kinds) and ast.get_docstring(node)
    ]


def test_no_comments_or_docstrings_under_marestail() -> None:
    assert [found for path in package_files() for found in comments_in(path) + docstrings_in(path)] == []


def test_non_python_scanner_sources_are_outside_the_package() -> None:
    found = [path.relative_to(ROOT).as_posix() for path in (ROOT / "marestail").rglob("*") if path.suffix in SCANNER_SOURCE_SUFFIXES]
    assert found == []


def test_frozen_scanner_projects_stay_in_the_package() -> None:
    missing = [name for name in FROZEN_SCANNER_PROJECTS if not (ROOT / name).is_file()]
    moved = [name for name in FROZEN_SCANNER_PROJECTS if (ROOT / "scanners" / Path(name).relative_to("marestail")).is_file()]
    assert missing + moved == []
