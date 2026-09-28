from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import _location, changes, context
from marestail.config import Config
from marestail.context import Context, MutationScope
from tests.conftest import FakeRun, commit_all, git, make_context

LANGUAGES = ["python", "ts", "elixir", "ruby", "dotnet", "erlang", "rust", "java"]


def tree(root: Path, *paths: str) -> None:
    for path in paths:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("one\ntwo\n")


def test_focus_path_joins_relative_and_keeps_absolute(tmp_path: Path) -> None:
    config = make_context(tmp_path).config
    inner = tmp_path / "a.py"
    inner.write_text("x")
    assert context.focus_path(config, Path("a.py")) == inner
    assert context.focus_path(config, inner) == inner


def test_locate_focus_inside_and_outside(tmp_path: Path) -> None:
    config = make_context(tmp_path).config
    (tmp_path / "src").mkdir()
    (tmp_path / "a.py").write_text("x")
    assert context.locate_focus(config, "a.py") == "a.py"
    assert context.locate_focus(config, str(tmp_path / "src")) == "src"
    assert context.locate_focus(config, ".") == "."
    assert context.locate_focus(config, "missing") is None
    assert context.locate_focus(config, str(tmp_path.parent)) is None


def test_branch_mutation_files_passes_the_repo_root(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Path, str]] = []

    def fake_base_exists(root: Path, base: str) -> bool:
        seen.append((root, base))
        return False

    monkeypatch.setattr(context, "base_exists", fake_base_exists)
    ctx = make_context(git_repo, {"git": {"base": "main"}})
    assert ctx.mutation_files("python", git_repo, (".py",)).mode == "full"
    assert seen == [(git_repo, "main")]


def test_branch_mutation_files_passes_the_base_to_changed_files(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Path, str]] = []

    def fake_changed_files(root: Path, base: str) -> set[str]:
        seen.append((root, base))
        return set()

    monkeypatch.setattr(context, "base_exists", lambda _root, _base: True)
    monkeypatch.setattr(context, "changed_files", fake_changed_files)
    ctx = make_context(git_repo, {"git": {"base": "main"}})
    assert ctx.mutation_files("python", git_repo, (".py",)).mode == "skip"
    assert seen == [(git_repo, "main")]


def test_paths(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert (ctx.root, ctx.work) == (tmp_path, tmp_path / ".marestail")


@pytest.mark.parametrize(
    ("fields", "scoped", "name"),
    [
        ({}, False, "all"),
        ({"scope_changed": True}, True, "changed"),
        ({"focus": {"a.py"}}, True, "changed"),
        ({"hard": True, "focus": {"a.py"}}, True, "hard"),
        ({"hard": True}, False, "hard"),
        ({"scope_changed": True, "hyper": True}, True, "hyper"),
    ],
)
def test_scope_name(tmp_path: Path, fields: dict[str, Any], scoped: bool, name: str) -> None:
    ctx = make_context(tmp_path, **fields)
    assert (ctx.scoped, ctx.scope_name) == (scoped, name)


@pytest.mark.parametrize("language", LANGUAGES)
def test_language_settings(tmp_path: Path, language: str) -> None:
    ctx = make_context(tmp_path, {language: {"root": "sub", "flag": 3}})
    getter = getattr(ctx, language)
    assert (getter("flag"), getter("missing"), getter("missing", "d")) == (3, None, "d")
    assert getattr(ctx, f"{language}_root")() == tmp_path / "sub"


@pytest.mark.parametrize("language", LANGUAGES)
def test_language_root_default(tmp_path: Path, language: str) -> None:
    assert getattr(make_context(tmp_path), f"{language}_root")() == tmp_path


def test_python_bin(tmp_path: Path) -> None:
    assert make_context(tmp_path).python_bin("ruff") == str(tmp_path / ".venv" / "bin" / "ruff")
    assert make_context(tmp_path, {"python": {"venv": "env"}}).python_bin("mypy") == str(tmp_path / "env" / "bin" / "mypy")


def test_section_root(tmp_path: Path) -> None:
    assert make_context(tmp_path, {"x": {"root": "/abs"}}).section_root("x") == Path("/abs")


@pytest.mark.parametrize(
    ("path", "expected"),
    [("src/a.py", True), ("src/a.pyi", True), ("src/a.txt", False), ("other/a.py", False)],
)
def test_matches(path: str, expected: bool) -> None:
    assert context.matches(path, Path("src"), (".py", ".pyi")) is expected


def test_scoped_files() -> None:
    assert context.scoped_files(["a"]) == MutationScope("scoped", ["a"])
    assert context.scoped_files([]) == MutationScope("skip", [])


def test_changed_under_merges_changed_and_focus(tmp_path: Path) -> None:
    tree(tmp_path, "src/a.py", "src/pkg/b.py", "src/pkg/c.txt", "src/pkg/deep/d.py", "other/e.py", "src/f.py")
    ctx = make_context(
        tmp_path,
        changed={"src/z.py", "other/y.py", "src/n.txt"},
        focus={"src/pkg", "src/a.py", "other/e.py", "src/f.txt", "missing"},
    )
    assert ctx.changed_under(tmp_path / "src", (".py",)) == ["src/a.py", "src/pkg/b.py", "src/pkg/deep/d.py", "src/z.py"]


def test_focus_under_directory_outside_folder(tmp_path: Path) -> None:
    tree(tmp_path, "other/e.py", "src/a.py")
    ctx = make_context(tmp_path, focus={"other", "."})
    assert ctx.focus_under(Path("src"), (".py",)) == {"src/a.py"}


def test_focus_entry(tmp_path: Path) -> None:
    tree(tmp_path, "src/a.py", "src/b.txt")
    ctx = make_context(tmp_path)
    assert ctx.focus_entry("src/a.py", Path("src"), (".py",)) == {"src/a.py"}
    assert ctx.focus_entry("src/b.txt", Path("src"), (".py",)) == set()
    assert ctx.focus_entry("src", Path("src"), (".py",)) == {"src/a.py"}
    assert ctx.focus_entry("nope", Path("."), (".py",)) == set()


@pytest.mark.parametrize(
    ("path", "expected"),
    [("a.py", True), ("pkg", True), ("pkg/x/y.py", True), ("pkgx/y.py", False), ("b.py", False)],
)
def test_in_focus(tmp_path: Path, path: str, expected: bool) -> None:
    assert make_context(tmp_path, focus={"a.py", "pkg"}).in_focus(path) is expected


def test_in_scope(tmp_path: Path) -> None:
    assert make_context(tmp_path).in_scope("anything") is True
    scoped = make_context(tmp_path, scope_changed=True, changed={"a.py"}, focus={"pkg"})
    assert [scoped.in_scope(path) for path in ("a.py", "pkg/b.py", "c.py")] == [True, True, False]


def test_gated_lines(tmp_path: Path) -> None:
    tree(tmp_path, "pkg/b.py")
    (tmp_path / "pkg" / "empty.py").write_text("")
    assert make_context(tmp_path).gated_lines("a.py") is None
    ctx = make_context(tmp_path, scope_changed=True, focus={"pkg"}, changed_lines_map={"a.py": {3}})
    assert ctx.gated_lines("a.py") == {3}
    assert ctx.gated_lines("c.py") is None
    assert ctx.gated_lines("pkg/b.py") == {1, 2}
    assert ctx.gated_lines("pkg/empty.py") == set()
    assert ctx.gated_lines("pkg/missing.py") == set()


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, "all"),
        ({"hard": True, "focus": {"b", "a"}}, "hard: a, b"),
        ({"scope_changed": True, "changed": {"x", "y"}, "changed_lines_map": {"x": {1, 2}, "y": {5}}}, "changed (2 files, 3 lines)"),
        ({"focus": {"b", "a"}}, "changed (0 files, 0 lines) + focus: a, b"),
        ({"scope_changed": True, "hyper": True, "changed": {"x"}, "changed_lines_map": {"x": {6}}}, "hyper: 1 changed lines in 1 files"),
        ({"scope_changed": True, "hyper": True}, "hyper: 0 changed lines in 0 files"),
    ],
)
def test_scope_summary(tmp_path: Path, fields: dict[str, Any], expected: str) -> None:
    assert make_context(tmp_path, **fields).scope_summary() == expected


def test_global_note(tmp_path: Path) -> None:
    assert make_context(tmp_path).global_note("ok") == "ok"
    assert make_context(tmp_path, scope_changed=True).global_note("ok") == "ok (global gate — scope: changed)"
    assert make_context(tmp_path, hard=True, focus={"a"}).global_note("ok") == "ok (global gate — scope: hard)"


def test_mutation_files_rejects_bad_setting(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"python": {"mutation_scope": "some"}})
    note = '[python] mutation_scope must be "changed" or "all", got \'some\''
    assert ctx.mutation_files("python", tmp_path, (".py",)) == MutationScope("error", note=note)


@pytest.mark.parametrize("setting", ["changed", "all"])
def test_mutation_files_scoped(tmp_path: Path, setting: str) -> None:
    raw = {"python": {"mutation_scope": setting}}
    ctx = make_context(tmp_path, raw, scope_changed=True, changed={"src/a.py", "b.py"})
    assert ctx.mutation_files("python", tmp_path / "src", (".py",)) == MutationScope("scoped", ["src/a.py"])
    assert ctx.mutation_files("python", tmp_path / "lib", (".py",)) == MutationScope("skip", [])


def test_mutation_files_all(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(changes, [])
    ctx = make_context(tmp_path, {"python": {"mutation_scope": "all"}})
    assert ctx.mutation_files("python", tmp_path, (".py",)) == MutationScope("full")
    assert fake.calls == []


def test_mutation_files_without_base(git_repo: Path) -> None:
    ctx = make_context(git_repo, {"git": {"base": "nope"}})
    assert ctx.mutation_files("python", git_repo, (".py",)) == MutationScope("full", note="(no base nope; full run)")
    default = make_context(git_repo)
    assert default.mutation_files("ts", git_repo, (".ts",)) == MutationScope("full", note="(no base origin/master; full run)")


def test_mutation_files_against_base(git_repo: Path) -> None:
    tree(git_repo, "base.py")
    commit_all(git_repo, "base")
    git(git_repo, "checkout", "-q", "-b", "work")
    tree(git_repo, "src/b.py", "src/a.py", "src/c.txt", "top.py")
    ctx = make_context(git_repo, {"git": {"base": "main"}})
    assert ctx.mutation_files("python", git_repo / "src", (".py",)) == MutationScope("scoped", ["src/a.py", "src/b.py"])
    assert ctx.mutation_files("python", git_repo / "lib", (".py",)) == MutationScope("skip", [])


def test_build_hard(tmp_path: Path) -> None:
    config = Config(root=tmp_path, raw={})
    built = context.build(config, False, {"a.py"}, hard=True)
    assert built == Context(config=config, scope_changed=True, focus={"a.py"}, hard=True)


def test_build_hard_needs_focus(tmp_path: Path) -> None:
    config = Config(root=tmp_path, raw={})
    with pytest.raises(SystemExit) as raised:
        context.build(config, True, None, hard=True)
    assert str(raised.value) == "--scope hard needs at least one focus path: pass --focus or set [focus] paths in marestail.toml"


def test_build_hyper_reads_the_diff_and_drops_focus(git_repo: Path) -> None:
    tree(git_repo, "a.py")
    commit_all(git_repo, "base")
    git(git_repo, "checkout", "-q", "-b", "work")
    (git_repo / "a.py").write_text("one\nTWO\n")
    config = Config(root=git_repo, raw={"git": {"base": "main"}})
    expected = Context(config=config, scope_changed=True, changed={"a.py"}, changed_lines_map={"a.py": {2}}, hyper=True)
    assert context.build(config, False, {"x"}, hyper=True) == expected


def hyper_context(root: Path) -> Context:
    return make_context(root, scope_changed=True, hyper=True, changed={"app/a.py", "app/b.py"}, changed_lines_map={"app/a.py": {6}})


@pytest.mark.parametrize(
    ("finding", "kept", "file_level"),
    [
        ("app/a.py:6 comment: # doubled", True, 0),
        ("ruff: app/a.py:6:34: E711 Comparison", True, 0),
        ("  --> app/a.py:6:13", True, 0),
        ("app/a.py:2 comment: # legacy note", False, 0),
        ("app/b.py:3 no lines changed here", False, 0),
        ("app/c.py:6 not in the diff", False, 0),
        ("app/a.py:0 MINOR python:S1451: Add a header", False, 1),
        ("app/c.py:0 not in the diff", False, 0),
        ("app/a.py unused file", False, 1),
        ("Would reformat: app/b.py", False, 1),
        ("1 file would be reformatted", False, 0),
    ],
)
def test_keeps_only_changed_lines_under_hyper(tmp_path: Path, finding: str, kept: bool, file_level: int) -> None:
    ctx = hyper_context(tmp_path)
    assert ctx.keeps(finding) is kept
    assert ctx.take_file_level() == file_level
    assert ctx.take_file_level() == 0


def test_keeps_everything_outside_hyper(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"a.py"})
    assert ctx.on_changed_lines(["b.py:1 x", "a.py:0 y", "text"]) == ["b.py:1 x", "a.py:0 y", "text"]
    assert ctx.take_file_level() == 0


def test_on_changed_lines_keeps_order_and_counts_file_level(tmp_path: Path) -> None:
    ctx = hyper_context(tmp_path)
    kept = ctx.on_changed_lines(["app/a.py:6 b", "app/a.py:2 old", "app/a.py:6 a", "app/a.py:0 whole"])
    assert (kept, ctx.file_level) == (["app/a.py:6 b", "app/a.py:6 a"], 1)


def test_on_changed_lines_filters_records_by_where(tmp_path: Path) -> None:
    ctx = hyper_context(tmp_path)
    records = [{"line": 6}, {"line": 7}, {"line": 0}]
    assert ctx.on_changed_lines(records, lambda record: f"app/a.py:{record['line']}") == [{"line": 6}]
    assert ctx.take_file_level() == 1


@pytest.mark.parametrize(("count", "note"), [(0, ""), (2, "; 2 file-level findings not gated under hyper")])
def test_file_level_note(tmp_path: Path, count: int, note: str) -> None:
    ctx = make_context(tmp_path, file_level=count)
    assert (ctx.file_level_note(), ctx.file_level) == (note, 0)


def test_build_unscoped(tmp_path: Path) -> None:
    config = Config(root=tmp_path, raw={})
    assert context.build(config, False) == Context(config=config)


def test_build_scoped_reads_git(git_repo: Path) -> None:
    tree(git_repo, "a.py")
    commit_all(git_repo, "base")
    git(git_repo, "checkout", "-q", "-b", "work")
    (git_repo / "a.py").write_text("one\nTWO\n")
    config = Config(root=git_repo, raw={"git": {"base": "main"}})
    expected = Context(config=config, scope_changed=True, changed={"a.py"}, focus={"x"}, changed_lines_map={"a.py": {2}})
    assert context.build(config, False, {"x"}) == expected
    assert context.build(config, True).changed == {"a.py"}


def test_build_uses_default_base(git_repo: Path) -> None:
    tree(git_repo, "a.py")
    commit_all(git_repo)
    config = Config(root=git_repo, raw={})
    assert context.build(config, True) == Context(config=config, scope_changed=True)


def test_diff_context_reads_configured_git_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    files: list[tuple[Path, str]] = []
    lines: list[tuple[Path, str]] = []

    def changed_files(root: Path, base: str) -> set[str]:
        files.append((root, base))
        return {"a.py"}

    def changed_lines(root: Path, base: str) -> dict[str, set[int]]:
        lines.append((root, base))
        return {"a.py": {1}}

    monkeypatch.setattr(context, "changed_files", changed_files)
    monkeypatch.setattr(context, "changed_lines", changed_lines)
    config = Config(root=tmp_path, raw={"git": {"base": "origin/work"}})
    built = context.diff_context(config, True, {"src"})
    assert files == [(tmp_path, "origin/work")]
    assert lines == [(tmp_path, "origin/work")]
    assert built == Context(config=config, scope_changed=True, changed={"a.py"}, focus={"src"}, changed_lines_map={"a.py": {1}})
    unscoped = context.diff_context(config, False, {"src"})
    assert unscoped == Context(config=config, focus={"src"})
    assert files == [(tmp_path, "origin/work")]
    bases: list[str] = []

    def record_base(root: Path, base: str) -> set[str]:
        bases.append(base)
        return set()

    monkeypatch.setattr(context, "changed_files", record_base)
    monkeypatch.setattr(context, "changed_lines", lambda root, base: {})
    context.diff_context(Config(root=tmp_path, raw={}), True, set())
    assert bases == [context.DEFAULT_BASE]


@pytest.mark.parametrize(
    ("scope", "focus", "clash"),
    [
        ("all", {"x"}, "--focus cannot be combined with --scope all"),
        ("hyper", {"x"}, "--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else"),
        ("hyper", set(), None),
        ("hard", {"x"}, None),
        (None, {"x"}, None),
    ],
)
def test_focus_clash_names_scopes_that_refuse_focus(scope: str | None, focus: set[str], clash: str | None) -> None:
    assert context.focus_clash(scope, focus) == clash


@pytest.mark.parametrize(
    ("finding", "place"),
    [
        ("ruff: app/a.py:6:34: E711 comparison", ("app/a.py", 6)),
        ("--> app/a.py:14:13", ("app/a.py", 14)),
        ("compile: warning (lib/x.ex:3)", ("lib/x.ex", 3)),
        ("a:b:3 text", ("b", 3)),
        ("::3 text", None),
        ("app/a.py:x no line", None),
        ("no location at all", None),
    ],
)
def test_location_reads_the_first_path_and_line(finding: str, place: tuple[str, int] | None) -> None:
    assert _location.location(finding) == place
