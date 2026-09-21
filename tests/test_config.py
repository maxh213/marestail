from pathlib import Path
from typing import Any

import pytest

from marestail import config
from marestail.config import Config


def test_section_returns_tables_only(tmp_path: Path) -> None:
    loaded = Config(root=tmp_path, raw={"python": {"root": "src"}, "flat": "text"})
    assert (loaded.section("python"), loaded.section("flat"), loaded.section("missing")) == ({"root": "src"}, None, None)


@pytest.mark.parametrize(
    ("section", "key", "default", "expected"),
    [("python", "root", ".", "src"), ("python", "venv", ".venv", ".venv"), ("flat", "root", "d", "d"), ("missing", "root", None, None)],
)
def test_get(tmp_path: Path, section: str, key: str, default: Any, expected: Any) -> None:
    loaded = Config(root=tmp_path, raw={"python": {"root": "src"}, "flat": "text"})
    assert loaded.get(section, key, default) == expected


def test_get_default_is_none(tmp_path: Path) -> None:
    assert Config(root=tmp_path, raw={}).get("a", "b") is None


def test_get_rejects_non_string_names(tmp_path: Path) -> None:
    loaded = Config(root=tmp_path, raw={"python": {"root": "src"}})
    with pytest.raises(TypeError, match=r"^name$"):
        loaded.get(None, "root", ".")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"^name$"):
        loaded.get("python", None, ".")  # type: ignore[arg-type]


def test_work(tmp_path: Path) -> None:
    assert Config(root=tmp_path, raw={}).work == tmp_path / ".marestail"


def test_find_root_walks_up(tmp_path: Path) -> None:
    (tmp_path / config.FILENAME).write_text("")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert config.find_root(nested) == tmp_path
    assert config.find_root(tmp_path) == tmp_path


def test_find_root_fails(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        config.find_root(tmp_path)
    assert str(raised.value) == f"no marestail.toml found above {tmp_path}"


def test_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "marestail.toml").write_text('[python]\nroot = "src"\n')
    (tmp_path / "src").mkdir()
    monkeypatch.chdir(tmp_path / "src")
    loaded = config.load(Path("."))
    assert (loaded.root, loaded.raw) == (tmp_path.resolve(), {"python": {"root": "src"}})
    assert (tmp_path / ".marestail").is_dir()
    assert config.load(tmp_path).raw == {"python": {"root": "src"}}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [({}, set()), ({"focus": {"paths": ["a.py", 3]}}, {"a.py", "3"}), ({"focus": {"paths": "b.py"}}, {"b.py"})],
)
def test_focus_paths(tmp_path: Path, raw: dict[str, Any], expected: set[str]) -> None:
    assert config.focus_paths(Config(root=tmp_path, raw=raw)) == expected
