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
    raw = {"freeze": {"paths": ["src/**"], "spec": ["docs/**"], "allow": {"coder": ["src/ok.py"]}}}
    paths = ["src/a.py", "src/ok.py", "docs/x.md", "pyproject.toml"]
    assert freeze.frozen_paths(config(raw), "coder", paths) == ["src/a.py", "docs/x.md"]


def test_spec_constant() -> None:
    assert freeze.SPEC == ["features/**", "qa/**", "tasks/**", "perf/**", "PERFORMANCE.md", "guidance/**"]


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
