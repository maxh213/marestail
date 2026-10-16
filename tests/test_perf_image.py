from pathlib import Path

import pytest

from marestail.perf import image


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_configured_image_wins(tmp_path: Path) -> None:
    write(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres:16\n")
    assert image.resolve(tmp_path, "postgres:17") == ("postgres:17", "marestail.toml")


def test_empty_repo_uses_default(tmp_path: Path) -> None:
    assert image.resolve(tmp_path, None) == ("postgres:18", "default")
    assert image.resolve(tmp_path, "") == ("postgres:18", "default")


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"docker-compose.yml": "services:\n  db:\n    image: postgres:16\n"}, ("postgres:16", "docker-compose.yml")),
        ({"compose.yaml": "  image: 'postgres:15.2-alpine'\n"}, ("postgres:15.2-alpine", "compose.yaml")),
        (
            {"docker-compose.yml": "services:\n  web:\n    image: redis:7\n", "compose.yml": 'image: "postgres:13"\n'},
            ("postgres:13", "compose.yml"),
        ),
        (
            {"docker-compose.a.yml": "image: postgres:12\n", "docker-compose.b.yml": "image: postgres:11\n"},
            ("postgres:12", "docker-compose.a.yml"),
        ),
        ({"docker-compose.yml": "# uses postgres:10\n"}, ("postgres:18", "default")),
        (
            {".github/workflows/ci.yml": "services:\n  pg:\n    image: postgres:15-alpine\n"},
            ("postgres:15-alpine", ".github/workflows/ci.yml"),
        ),
        (
            {".github/workflows/a.yaml": "nothing here\n", ".github/workflows/b.yaml": "uses postgres:9.6 here"},
            ("postgres:9.6", ".github/workflows/b.yaml"),
        ),
        (
            {".github/workflows/z.yml": "postgres:1", ".github/workflows/a.yaml": "postgres:2"},
            ("postgres:2", ".github/workflows/a.yaml"),
        ),
        ({".tool-versions": "python 3.12\npostgres 14.5\n"}, ("postgres:14", ".tool-versions")),
        ({".tool-versions": "python 3.12\n"}, ("postgres:18", "default")),
        (
            {"compose.yml": "image: postgres:16\n", ".github/workflows/ci.yml": "postgres:15", ".tool-versions": "postgres 14\n"},
            ("postgres:16", "compose.yml"),
        ),
        ({".github/workflows/ci.yml": "postgres:15", ".tool-versions": "postgres 14\n"}, ("postgres:15", ".github/workflows/ci.yml")),
    ],
)
def test_resolve_finds_image(tmp_path: Path, files: dict[str, str], expected: tuple[str, str]) -> None:
    for relative, text in files.items():
        write(tmp_path, relative, text)
    assert image.resolve(tmp_path, None) == expected


def test_tool_versions_directory_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").mkdir()
    assert image.resolve(tmp_path, None) == ("postgres:18", "default")


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres:16-alpine", "postgres:16"),
        ("postgres:16-alpine3.19", "postgres:16"),
        ("postgres:16", "postgres:16"),
        ("postgres:16-alpine-extra", "postgres:16-alpine-extra"),
    ],
)
def test_helper_image(given: str, expected: str) -> None:
    assert image.helper_image(given) == expected
