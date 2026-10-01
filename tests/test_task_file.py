import ast
from pathlib import Path

import pytest

from marestail import install, task_file

ROOT = Path(__file__).resolve().parent.parent
NO_FRONT = "no front matter: line 1 must be +++"
UNCLOSED = "front matter is not closed: no +++ line after line 1"
BAD_VALUE = "front matter is not valid TOML: Invalid value (at line 1, column 12)"
BAD_STATEMENT = "front matter is not valid TOML: Invalid statement (at line 2, column 1)"
BAD_DEPENDS = "depends must be an array of strings"
NO_DEPENDS = "front matter has no depends; write depends = [] for a task with no dependencies"
NOT_ID = "is not a task id; use the file name without .md"
SINGLE = [
    ("# 019 title\n", NO_FRONT, False),
    ("\n+++\ndepends = []\n+++\n# x\n", NO_FRONT, False),
    ("+++ \ndepends = []\n+++\n# x\n", NO_FRONT, False),
    ("+++ \r\ndepends = []\r\n+++\r\n# x\n", NO_FRONT, False),
    ('+++\ndepends = ["018-a"]\nstack = true\n# no close\n', UNCLOSED, True),
    ("+++\ndepends = []\n+++ \n# x\n", UNCLOSED, True),
    ("+++\ndepends = [broken\n+++\n# x\n", BAD_VALUE, True),
    ("+++\ndepends = []\n=\n+++\n# x\n", BAD_STATEMENT, True),
    ('+++\ndepends = "018-a"\n+++\n# x\n', BAD_DEPENDS, True),
    ("+++\ndepends = [1]\nstack = true\n+++\n# x\n", BAD_DEPENDS, True),
    ('+++\ndepends = [1, "018-a"]\nstack = true\n+++\n# x\n', BAD_DEPENDS, True),
    ('+++\ndepends = [""]\nstack = true\n+++\n# x\n', f"depends entry '' {NOT_ID}", True),
    ('+++\ndepends = ["sub/018-a"]\nstack = true\n+++\n# x\n', f"depends entry 'sub/018-a' {NOT_ID}", True),
    ('+++\ndepends = ["018-a.md"]\nstack = true\n+++\n# x\n', f"depends entry '018-a.md' {NOT_ID}", True),
    ('+++\ndepends = ["018-a", "018-a"]\nstack = false\n+++\n# x\n', "depends lists 018-a twice", True),
    ('+++\ndepends = ["019-test"]\nstack = true\n+++\n# x\n', "depends on itself", True),
    ('+++\ndepends = ["018-a"]\nstack = "yes"\n+++\n# x\n', "stack must be true or false, got 'yes'", True),
    ('+++\ndepends = ["018-a"]\nstack = 1\n+++\n# x\n', "stack must be true or false, got 1", True),
    ('+++\ndepends = ["018-a"]\n+++\n# x\n', "stack is required when depends is not empty", True),
    ("+++\ndepends = []\nstack = false\n+++\n# x\n", "stack is not allowed when depends is empty", True),
    ('+++\ndepends = ["018-a", "018-b"]\nstack = true\n+++\n# x\n', "stack = true needs exactly one dependency, got 2", True),
    ("+++\n+++\n# x\n", NO_DEPENDS, True),
]
DEPENDENCIES = """## Dependencies

A task that needs another task first says so in a block at the very top of the file, before the title:

    +++
    depends = ["018-runs-stay-nice"]
    stack = true
    +++

`depends` lists task ids: file names in this folder without `.md`. Write `depends = []` for a task that needs nothing. `stack = true` runs the task on its one dependency's branch, after it; `stack = false` starts it on its own branch once every dependency is merged. Leave `stack` out when `depends` is empty. `marestail tasks check` reports every problem in this folder, one line each. `marestail run` works with or without the block.
"""
WRITING = (
    "A task file may open with a `+++` TOML block holding `depends` and `stack`. `depends` lists task ids, the file names "
    "in that folder without the `.md` suffix, and `depends = []` means the task needs nothing. `stack = true` runs the task "
    "on its one dependency's branch, after it; `stack = false` starts the task on its own branch once every dependency is "
    "merged. Leave `stack` out when `depends` is empty. `marestail tasks check` checks a folder of task files. "
    "`marestail run` never requires the block. Roles see one sentence naming the dependencies, not the block."
)
USE_LINE = "marestail tasks check          # front matter and dependencies of every task in tasks/; or pass folders and .md files"


def place(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def front(body: str, *lines: str) -> str:
    toml = "\n".join(lines)
    gap = "\n" if lines else ""
    return f"+++\n{toml}{gap}+++\n{body}"


def rejected(path: Path) -> list[str]:
    with pytest.raises(task_file.TaskFileError) as caught:
        task_file.read(path)
    assert isinstance(caught.value, ValueError)
    return list(caught.value.problems)


def assert_rejection(path: Path, problem: str, rejects: bool) -> None:
    if not rejects:
        assert task_file.read(path).has_front_matter is False
        return
    assert rejected(path) == [problem]


def test_read_parses_blocks(tmp_path: Path) -> None:
    plain = place(tmp_path / "018-runs-stay-nice.md", "# Title\n\n+++\nstill\n")
    assert task_file.read(plain) == task_file.TaskFile("018-runs-stay-nice", plain, (), None, "# Title\n\n+++\nstill\n", False)
    body = "# Title\n\nBody.\n"
    empty = place(tmp_path / "019-empty.md", front(body, "depends = []"))
    assert task_file.read(empty) == task_file.TaskFile("019-empty", empty, (), None, body, True)
    stacked = place(tmp_path / "020-one.md", front(body, 'depends = ["a"]', "stack = true"))
    assert task_file.read(stacked) == task_file.TaskFile("020-one", stacked, ("a",), True, body, True)
    own = place(tmp_path / "021-own.md", front(body, 'depends = ["a"]', "stack = false"))
    assert task_file.read(own).stack is False
    pair = place(tmp_path / "022-pair.md", front(body, 'depends = ["a", "b"]', "stack = false"))
    assert task_file.read(pair).depends == ("a", "b")
    assert task_file.read(place(tmp_path / "023-blank.md", "")).body == ""
    spaced = "+++ \ndepends = []\n+++\n# x\n"
    assert task_file.read(place(tmp_path / "024-space.md", spaced)).body == spaced
    lead = "\n+++\ndepends = []\n+++\n# x\n"
    assert task_file.read(place(tmp_path / "025-lead.md", lead)).has_front_matter is False
    crlf = tmp_path / "026-crlf.md"
    crlf.write_bytes(b'+++\r\ndepends = ["a"]\r\nstack = true\r\n+++\r\n# ok\n')
    parsed = task_file.read(crlf)
    assert (parsed.depends, parsed.stack, parsed.body, parsed.has_front_matter) == (("a",), True, "# ok\n", True)
    with pytest.raises(FileNotFoundError):
        task_file.read(tmp_path / "missing.md")


def test_read_ignores_a_fence_with_a_space_before_cr(tmp_path: Path) -> None:
    path = tmp_path / "027-cr.md"
    path.write_bytes(b"+++ \r\ndepends = []\r\n+++\r\n# x\n")
    loaded = task_file.read(path)
    assert loaded.has_front_matter is False
    assert loaded.body == path.read_text()


@pytest.mark.parametrize(("text", "problem", "rejects"), SINGLE)
def test_read_reports_each_front_matter_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, problem: str, rejects: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    assert_rejection(place(Path("tasks/019-test.md"), text), problem, rejects)
    assert task_file.check([Path("tasks/019-test.md")]) == [f"tasks/019-test.md: {problem}"]


def test_check_rejects_a_spaced_cr_fence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path("tasks/019-test.md")
    path.parent.mkdir()
    path.write_bytes(b"+++ \r\ndepends = []\r\n+++\r\n# x\n")
    assert task_file.check([path]) == [f"tasks/019-test.md: {NO_FRONT}"]


def test_unclosed_and_bad_toml_stop_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("tasks/019-open.md"), '+++\ndepends = ["019-open"]\nstack = "yes"\nextra = 1\n')
    place(Path("tasks/019-toml.md"), "+++\ndepends = [broken\nextra = 1\n+++\n# x\n")
    assert task_file.check([Path("tasks")]) == [
        f"tasks/019-open.md: {UNCLOSED}",
        f"tasks/019-toml.md: {BAD_VALUE}",
    ]


def test_read_sorts_unknown_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = place(Path("tasks/021-keys.md"), "+++\nzebra = 1\nalpha = 2\n+++\n# x\n")
    problems = [
        "unknown front matter key 'alpha'; allowed keys are depends and stack",
        "unknown front matter key 'zebra'; allowed keys are depends and stack",
        NO_DEPENDS,
    ]
    assert rejected(path) == problems
    assert task_file.check([Path("tasks/021-keys.md")]) == [f"tasks/021-keys.md: {item}" for item in problems]


def test_check_unknown_key_and_missing_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("tasks/021-extra.md"), '+++\ndepends = ["018-a"]\nextra = 1\n+++\n# x\n')
    assert task_file.check([Path("tasks/021-extra.md")]) == [
        "tasks/021-extra.md: unknown front matter key 'extra'; allowed keys are depends and stack",
        "tasks/021-extra.md: stack is required when depends is not empty",
    ]


def test_check_empty_stack_and_bad_depends_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("tasks/019-empty.md"), front("# x\n", "depends = []", "stack = true"))
    place(Path("tasks/019-type.md"), front("# x\n", 'depends = "018-a"', 'stack = "yes"'))
    place(Path("tasks/019-num.md"), "+++\ndepends = [1]\n+++\n# x\n")
    assert task_file.check([Path("tasks/019-empty.md")]) == [
        "tasks/019-empty.md: stack is not allowed when depends is empty",
        "tasks/019-empty.md: stack = true needs exactly one dependency, got 0",
    ]
    assert task_file.check([Path("tasks/019-type.md")]) == [
        "tasks/019-type.md: depends must be an array of strings",
        "tasks/019-type.md: stack must be true or false, got 'yes'",
    ]
    assert rejected(Path("tasks/019-num.md")) == [BAD_DEPENDS]


def test_check_stack_only_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("tasks/019-only-true.md"), front("# x\n", "stack = true"))
    place(Path("tasks/019-only-false.md"), front("# x\n", "stack = false"))
    only = NO_DEPENDS
    assert task_file.check([Path("tasks/019-only-true.md")]) == [f"tasks/019-only-true.md: {only}"]
    assert task_file.check([Path("tasks/019-only-false.md")]) == [f"tasks/019-only-false.md: {only}"]


def test_check_backslash_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path("tasks/019-slash.md")
    path.parent.mkdir()
    path.write_bytes(b'+++\ndepends = ["018\\\\a"]\nstack = true\n+++\n# x\n')
    problem = "depends entry '018\\a' is not a task id; use the file name without .md"
    assert path.read_bytes().count(b"\\") == 2
    assert problem.encode().count(b"\\") == 1
    assert task_file.check([path]) == [f"tasks/019-slash.md: {problem}"]
    assert rejected(path) == [problem]


def test_check_problem_order_counts_invalid_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = place(Path("tasks/019-test.md"), front("# x\n", 'depends = ["", "sub/018", "018-a", "018-a"]', "stack = true"))
    problems = [
        f"depends entry '' {NOT_ID}",
        f"depends entry 'sub/018' {NOT_ID}",
        "depends lists 018-a twice",
        "stack = true needs exactly one dependency, got 4",
    ]
    assert rejected(path) == problems
    assert task_file.check([path]) == [f"tasks/019-test.md: {item}" for item in problems]


def test_check_clean_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty").mkdir()
    assert task_file.check([Path("empty")]) == []
    tasks = tmp_path / "notoml" / "tasks"
    place(tasks / "018-a.md", front("# 018\n", "depends = []"))
    place(tasks / "019-b.md", front("# 019\n", 'depends = ["018-a"]', "stack = true"))
    place(tasks / "020-c.md", front("# 020\n", 'depends = ["018-a", "019-b"]', "stack = false"))
    (tasks / "021-crlf.md").write_bytes(b"+++\r\ndepends = []\r\n+++\r\n# ok\n")
    assert not (tmp_path / "notoml" / "marestail.toml").exists()
    assert task_file.check([Path("notoml/tasks")]) == []


def test_check_folder_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("named/README.md"), "# guide\n")
    place(Path("named/readme.md"), "# lower\n")
    place(Path("named/Readme.MD"), "# mixed\n")
    place(Path("named/notes.txt"), "not a task\n")
    place(Path("named/018-bad.md"), "# no block\n")
    place(Path("named/nested/019-bad.md"), "+++\ndepends = []\n")
    assert task_file.check([Path("named")]) == [f"named/018-bad.md: {NO_FRONT}"]
    assert task_file.check([Path("named/README.md")]) == [f"named/README.md: {NO_FRONT}"]
    assert task_file.check([Path("named/readme.md")]) == [f"named/readme.md: {NO_FRONT}"]
    assert task_file.check([Path("named/notes.txt")]) == ["named/notes.txt: not a .md file"]
    assert task_file.check([Path("named/nested/019-bad.md")]) == [f"named/nested/019-bad.md: {UNCLOSED}"]
    assert task_file.check([Path("missing_dir/")]) == ["missing_dir: no such file or folder"]


def test_check_uses_the_first_spelling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "once" / "sub").mkdir(parents=True)
    place(Path("once/019-bad.md"), "# title\n")
    assert task_file.check([Path("once/sub/../019-bad.md"), Path("once")]) == [f"once/sub/../019-bad.md: {NO_FRONT}"]
    place(Path("tasks/019-x.md"), "# x\n")
    assert task_file.check([Path("tasks"), Path("tasks/019-x.md")]) == [f"tasks/019-x.md: {NO_FRONT}"]
    assert task_file.check([Path("./tasks/019-x.md")]) == [f"tasks/019-x.md: {NO_FRONT}"]


def test_check_missing_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("ord/019-a.md"), front("# x\n", 'depends = ["099-z", "098-a"]', "stack = false"))
    assert task_file.check([Path("ord/019-a.md")]) == [
        "ord/019-a.md: depends on 099-z, but ord/099-z.md does not exist",
        "ord/019-a.md: depends on 098-a, but ord/098-a.md does not exist",
    ]
    place(Path("left/019-a.md"), front("# x\n", 'depends = ["020-b"]', "stack = true"))
    place(Path("right/020-b.md"), front("# x\n", "depends = []"))
    assert task_file.check([Path("left"), Path("right")]) == ["left/019-a.md: depends on 020-b, but left/020-b.md does not exist"]
    (tmp_path / "left" / "sub").mkdir(parents=True)
    place(Path("left/019-a.md"), front("# x\n", 'depends = ["020-gone"]', "stack = true"))
    assert task_file.check([Path("left/sub/../019-a.md")]) == [
        "left/sub/../019-a.md: depends on 020-gone, but left/sub/../020-gone.md does not exist"
    ]


def test_check_ignores_an_unchecked_dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("box/018-prev.md"), front("# p\n", 'depends = ["019-next"]', "stack = true"))
    place(Path("box/019-next.md"), front("# n\n", 'depends = ["018-prev"]', "stack = true"))
    assert task_file.check([Path("box/019-next.md")]) == []
    assert task_file.check([Path("box")]) == ["box/018-prev.md: dependency cycle: 018-prev -> 019-next -> 018-prev"]
    place(Path("box/017-bad.md"), "+++\ndepends = []\n")
    place(Path("box/016-uses-bad.md"), front("# u\n", 'depends = ["017-bad"]', "stack = true"))
    assert task_file.check([Path("box/016-uses-bad.md")]) == []
    assert task_file.check([Path("box/017-bad.md")]) == [f"box/017-bad.md: {UNCLOSED}"]


def test_check_duplicate_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for folder in ("alpha", "beta", "mid"):
        place(Path(folder) / "019-dup.md", front("# x\n", "depends = []"))
    assert task_file.check([Path("alpha"), Path("beta"), Path("mid")]) == [
        "beta/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md",
        "mid/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md",
    ]
    assert task_file.check([Path("mid"), Path("alpha")]) == ["mid/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md"]


def test_check_cycles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("cycle/020-b.md"), front("# b\n", 'depends = ["021-c"]', "stack = true"))
    place(Path("cycle/021-c.md"), front("# c\n", 'depends = ["019-a"]', "stack = true"))
    place(Path("cycle/019-a.md"), front("# a\n", 'depends = ["020-b"]', "stack = true"))
    place(Path("cycle/022-d.md"), front("# d\n", 'depends = ["021-c"]', "stack = true"))
    assert task_file.check([Path("cycle")]) == ["cycle/019-a.md: dependency cycle: 019-a -> 020-b -> 021-c -> 019-a"]
    place(Path("twin/019-a.md"), front("# a\n", 'depends = ["020-b", "021-c"]', "stack = false"))
    place(Path("twin/020-b.md"), front("# b\n", 'depends = ["019-a"]', "stack = true"))
    place(Path("twin/021-c.md"), front("# c\n", 'depends = ["019-a"]', "stack = true"))
    assert task_file.check([Path("twin")]) == [
        "twin/019-a.md: dependency cycle: 019-a -> 020-b -> 019-a",
        "twin/019-a.md: dependency cycle: 019-a -> 021-c -> 019-a",
    ]


def test_check_orders_lines_by_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    place(Path("alpha/018-bad.md"), "+++\ndepends = []\n")
    place(Path("alpha/019-a.md"), front("# a\n", 'depends = ["099-missing", "020-b"]', "stack = false"))
    place(Path("alpha/020-b.md"), front("# b\n", 'depends = ["019-a"]', "stack = true"))
    place(Path("alpha/022-d.md"), front("# d\n", 'depends = ["019-a"]', "stack = true"))
    place(Path("alpha/023-on-bad.md"), front("# o\n", 'depends = ["018-bad"]', "stack = true"))
    place(Path("beta/019-a.md"), front("# a\n", "depends = []"))
    found = task_file.check([Path("alpha"), Path("beta")])
    assert found == [
        f"alpha/018-bad.md: {UNCLOSED}",
        "alpha/019-a.md: depends on 099-missing, but alpha/099-missing.md does not exist",
        "alpha/019-a.md: dependency cycle: 019-a -> 020-b -> 019-a",
        "beta/019-a.md: duplicate task id 019-a: also alpha/019-a.md",
    ]


def test_docs_and_import_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert_documented()
    assert_imports()
    assert_tasks_untouched()
    assert_install_copies_the_readme(tmp_path, monkeypatch, capsys)


def test_checkout_root_skips_a_mutants_copy(tmp_path: Path) -> None:
    copied = tmp_path / "mutants" / "tests" / "test_task_file.py"
    copied.parent.mkdir(parents=True)
    direct = tmp_path / "tests" / "test_task_file.py"
    direct.parent.mkdir()
    assert checkout_root(copied) == tmp_path
    assert checkout_root(direct) == tmp_path


def test_task_file_is_listed_with_the_foundations() -> None:
    contracts = (ROOT / ".importlinter").read_text()
    layer = next(line for line in contracts.splitlines() if line.strip().startswith("marestail.report :"))
    assert [part.strip() for part in layer.split(":")] == [
        "marestail.report",
        "marestail.config",
        "marestail.changes",
        "marestail._location",
        "marestail.task_file",
    ]
    foundations = contracts.split("[importlinter:contract:foundations-import-nothing-above]", 1)[1]
    assert "marestail.task_file" in foundations.split("forbidden_modules", 1)[0].split()


def assert_documented() -> None:
    template = (ROOT / "templates" / "tasks-README.md").read_text()
    readme = (ROOT / "README.md").read_text()
    assert template.endswith(DEPENDENCIES)
    assert "Name files `NNN-short-name.md`." in template
    use = readme.split("```sh\n", 1)[1].split("```", 1)[0]
    assert use.split("dandelion/route-best", 1)[1].splitlines()[1] == USE_LINE
    writing = readme.split("## Writing tasks\n\n", 1)[1].split("\n## ", 1)[0]
    assert WRITING in writing
    assert "`tasks/README.md`" in writing
    assert "/" not in writing.split("\n\n")[-1]


def checkout_root(test_file: Path) -> Path:
    root = test_file.resolve().parent.parent
    if root.name == "mutants":
        return root.parent
    return root


def assert_imports() -> None:
    assert imported_modules(checkout_root(Path(__file__)) / "marestail" / "task_file.py") == {"tomllib", "pathlib", "dataclasses"}


def assert_tasks_untouched() -> None:
    tasks = checkout_root(Path(__file__)) / "tasks"
    assert "## Dependencies" not in (tasks / "README.md").read_text()
    assert (tasks / "018-runs-stay-nice.md").read_text().startswith("# 018 — runs stay nice")
    assert (tasks / "019-tasks-say-what-they-depend-on.md").read_text().startswith("# 019 — task files say what they depend on")
    assert opening_fences(tasks) == []


def opening_fences(folder: Path) -> list[str]:
    return [path.name for path in sorted(folder.glob("*.md")) if path.read_text().splitlines()[0] == "+++"]


def assert_install_copies_the_readme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    target = tmp_path / "target"
    target.mkdir()
    assert install.install(target) == 0
    copied = target / "tasks" / "README.md"
    assert copied.read_text() == (ROOT / "templates" / "tasks-README.md").read_text()
    copied.write_text("keep\n")
    assert install.install(target) == 0
    assert copied.read_text() == "keep\n"
    capsys.readouterr()


def imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        add_import(found, node)
    return found


def add_import(found: set[str], node: ast.AST) -> None:
    if isinstance(node, ast.Import):
        found.update(alias.name.split(".")[0] for alias in node.names)
        return
    add_from(found, node)


def add_from(found: set[str], node: ast.AST) -> None:
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        found.add(node.module.split(".")[0])
