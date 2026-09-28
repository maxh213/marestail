from pathlib import Path

import pytest

from marestail import freeze
from marestail.config import Config
from tests.conftest import make_context

ROOT = Path("/repo")


def config(raw: dict[str, object] | None = None) -> Config:
    return make_context(ROOT, raw).config


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("pkg/setup.cfg", "**/setup.cfg", True),
        ("setup.cfg", "**/setup.cfg", True),
        ("a/b/tsconfig.base.json", "**/tsconfig*.json", True),
        ("setup.cfgx", "**/setup.cfg", False),
        ("features/a.feature", "features/**", True),
        ("features", "features/**", False),
        ("featuresx/a", "features/**", False),
        ("marestail.toml", "marestail.toml", True),
        ("sub/marestail.toml", "marestail.toml", False),
        ("mix.lock", "mix.*", True),
    ],
)
def test_matches(path: str, pattern: str, expected: bool) -> None:
    assert freeze.matches(path, pattern) is expected


def test_matches_any() -> None:
    assert freeze.matches_any("qa/x.md", ["features/**", "qa/**"]) is True
    assert freeze.matches_any("src/x.py", ["features/**", "qa/**"]) is False
    assert freeze.matches_any("src/x.py", []) is False


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("coder", ["pyproject.toml", "features/a.feature", "qa/a.md", "CLAUDE.md", "app/App.csproj"]),
        ("specifier", ["pyproject.toml", "CLAUDE.md", "app/App.csproj"]),
        ("architect", ["features/a.feature", "qa/a.md", "CLAUDE.md", "app/App.csproj"]),
    ],
)
def test_frozen_paths_by_role(role: str, expected: list[str]) -> None:
    paths = ["src/a.py", "pyproject.toml", "features/a.feature", "qa/a.md", "CLAUDE.md", "app/App.csproj"]
    assert freeze.frozen_paths(config(), role, paths) == expected


def test_frozen_paths_follow_configuration() -> None:
    raw: dict[str, object] = {"freeze": {"paths": ["src/**"], "spec": ["docs/**"], "allow": {"coder": ["src/ok.py"]}}}
    paths = ["src/a.py", "src/ok.py", "docs/x.md", "pyproject.toml"]
    assert freeze.frozen_paths(config(raw), "coder", paths) == ["src/a.py", "docs/x.md"]


def test_every_spec_pattern_freezes_a_path_for_a_coder() -> None:
    paths = ["features/a.feature", "qa/a.md", "tasks/a.md", "perf/bench_a.py", "PERFORMANCE.md", "guidance/a.md"]
    assert freeze.frozen_paths(config(), "coder", paths) == paths
    assert freeze.frozen_paths(config(), "specifier", paths) == ["tasks/a.md", "perf/bench_a.py", "PERFORMANCE.md", "guidance/a.md"]


ADD = '+    <PackageReference Include="Foo" Version="1.2" />'
VISIBLE = '+  <InternalsVisibleTo Include="Tests" />'


@pytest.mark.parametrize(
    ("path", "lines", "expected"),
    [
        ("app/App.csproj", ["--- a/app/App.csproj", "+++ b/app/App.csproj", " context", ADD], True),
        ("App.csproj", [VISIBLE], True),
        ("app/App.csproj", ["+  <ItemGroup>", ADD, "+  </ItemGroup>", "+", "+   "], True),
        ("app/App.csproj", ["+\t<ItemGroup>\t"], False),
        ("app/App.csproj", ["+<ItemGroup>", "+</ItemGroup>"], False),
        ("app/App.csproj", [ADD, '-    <PackageReference Include="Bar" />'], False),
        ("app/App.csproj", [ADD, "+  <Other />"], False),
        ("app/App.csproj", [ADD, "+ x <ItemGroup>"], False),
        ("app/App.csproj", ['+<PackageReference Include="Foo" Version="" />'], False),
        ("app/App.csproj", ["--- a", "+++ b"], False),
        ("app/App.csproj", [], False),
        ("app/App.props", [ADD], False),
    ],
)
def test_tolerated(path: str, lines: list[str], expected: bool) -> None:
    assert freeze.tolerated(path, "\n".join(lines)) is expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("+", True),
        ("+ \t ", True),
        ("+<ItemGroup>", True),
        ("+  </ItemGroup>  ", True),
        ("+ <ItemGroup></ItemGroup>", False),
        ("-<ItemGroup>", False),
        ("<ItemGroup>", False),
        ("", False),
    ],
)
def test_is_wrapper(line: str, expected: bool) -> None:
    assert freeze.is_wrapper(line) is expected


def test_changed_lines_skip_headers_and_context() -> None:
    diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n context\n-old\n+new\n"
    assert freeze.changed_lines(diff) == ["-old", "+new"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_x.py", True),
        ("a/test/x.rb", True),
        ("spec/x.rb", True),
        ("src/__tests__/x.ts", True),
        ("test_x.py", True),
        ("x_test.go", True),
        ("src/app.test.ts", True),
        ("src/app.spec.ts", True),
        ("src/app.ts", False),
        ("tests", False),
        ("latest/x.py", False),
    ],
)
def test_is_test(path: str, expected: bool) -> None:
    assert freeze.is_test(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"), [("lib/extra.py", True), ("src/app.tsx", True), ("a.hrl", True), ("package.json", False), ("README.md", False)]
)
def test_is_source(path: str, expected: bool) -> None:
    assert freeze.is_source(path) is expected


def test_frozen_paths_under_hyper_freeze_everything_but_source_and_tests() -> None:
    paths = ["package.json", "package-lock.json", ".gitignore", "README.md", ".github/workflows/ci.yml", "note.txt"]
    code = ["lib/extra.py", "src/app.ts", "tests/test_x.py", "src/app.test.ts", "tests/data.json"]
    assert freeze.frozen_paths(config(), "coder", paths + code, True) == paths
    assert freeze.frozen_paths(config(), "coder", paths + code) == []


def test_frozen_paths_under_hyper_keep_the_role_allow_list_and_the_freeze_list() -> None:
    assert freeze.frozen_paths(config(), "architect", ["pyproject.toml", "vite.config.js"], True) == ["vite.config.js"]
