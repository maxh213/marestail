import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import install

TEMPLATES = install.TEMPLATES
GATE = {"type": "command", "command": "marestail gate --hook", "timeout": 900}
CURSOR_GATE = {"command": "marestail gate --hook", "timeout": 900, "loop_limit": 5}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.delenv("GROK_HOME", raising=False)
    monkeypatch.setattr(time, "time", lambda: 1234.9)
    return folder


@pytest.fixture
def target(tmp_path: Path) -> Path:
    folder = tmp_path / "target"
    folder.mkdir()
    return folder


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def test_install_into_an_empty_repo(home: Path, target: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install.install(target)
    for name in ("marestail.toml", "sonar-project.properties", "PERFORMANCE.md", "guidance/ts.md"):
        assert (target / name).read_text() == (TEMPLATES / name).read_text()
    assert (target / "tasks" / "README.md").read_text() == (TEMPLATES / "tasks-README.md").read_text()
    assert not (target / "guidance" / "cs.md").exists()
    assert (target / "CLAUDE.md").read_text() == (TEMPLATES / "CLAUDE.md").read_text()
    assert (target / "AGENTS.md").read_text() == (TEMPLATES / "CLAUDE.md").read_text()
    assert read_json(target / ".claude" / "settings.json") == {"hooks": {"Stop": [read_json(TEMPLATES / "stop-hook.json")]}}
    assert read_json(target / ".agents" / "hooks.json") == read_json(TEMPLATES / "agy-hooks.json")
    assert read_json(target / ".grok" / "hooks" / "marestail-gate.json") == read_json(TEMPLATES / "grok-hooks.json")
    assert read_json(target / ".cursor" / "hooks.json") == read_json(TEMPLATES / "cursor-hooks.json")
    assert (target / ".gitignore").read_text() == "\n# marestail\n" + "\n".join(install.GITIGNORE_LINES) + "\n"
    assert capsys.readouterr().out == (
        f"trusted {target} for grok project hooks\ninstalled into {target}; edit marestail.toml and sonar-project.properties\n"
    )
    assert (home / ".grok" / "trusted_folders.toml").read_text() == f'[folders."{target}"]\ntrusted = true\ndecided_at = 1234\n'


def test_install_twice_changes_nothing(home: Path, target: Path) -> None:
    install.install(target, gitignore_generated=True)
    before = {path: path.read_text() for path in target.rglob("*") if path.is_file()}
    install.install(target, gitignore_generated=True)
    assert {path: path.read_text() for path in target.rglob("*") if path.is_file()} == before


def test_install_for_dotnet_and_csharp(home: Path, target: Path) -> None:
    (target / "marestail.toml").write_text("[dotnet]\nsolution = 'a.sln'\n")
    (target / "src").mkdir()
    (target / "src" / "App.csproj").write_text("<Project />")
    install.install(target, gitignore_generated=True)
    assert (target / "marestail.toml").read_text() == "[dotnet]\nsolution = 'a.sln'\n"
    assert not (target / "sonar-project.properties").exists()
    assert (target / "guidance" / "cs.md").read_text() == (TEMPLATES / "guidance" / "cs.md").read_text()
    lines = (target / ".gitignore").read_text().splitlines()
    assert lines[-len(install.GITIGNORE_GENERATED_LINES) :] == install.GITIGNORE_GENERATED_LINES


@pytest.mark.parametrize(("text", "expected"), [(None, False), ("[python]\n", False), ("[dotnet]\n", True)])
def test_uses_dotnet(target: Path, text: str | None, expected: bool) -> None:
    if text is not None:
        (target / "marestail.toml").write_text(text)
    assert install.uses_dotnet(target) is expected


def test_copy_if_missing_keeps_existing(tmp_path: Path) -> None:
    source = tmp_path / "s"
    source.write_text("new")
    destination = tmp_path / "d"
    install.copy_if_missing(source, destination)
    assert destination.read_text() == "new"
    source.write_text("newer")
    install.copy_if_missing(source, destination)
    assert destination.read_text() == "new"


@pytest.mark.parametrize(
    ("existing", "keeps"),
    [
        ("# Rules\n\nbe nice\n\n\n", False),
        ("run marestail gate first", True),
        ("", False),
    ],
)
def test_append_instructions(tmp_path: Path, existing: str, keeps: bool) -> None:
    path = tmp_path / "CLAUDE.md"
    path.write_text(existing)
    install.append_instructions(path)
    if keeps:
        assert path.read_text() == existing
        return
    snippet = (TEMPLATES / "CLAUDE.md").read_text()
    assert path.read_text() == existing.rstrip() + ("\n\n" if existing else "") + snippet


def test_merge_hook_keeps_other_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "x", "hooks": {"Stop": [{"command": "other"}], "Pre": []}}))
    install.merge_hook(path)
    assert (
        path.read_text()
        == json.dumps(
            {"model": "x", "hooks": {"Stop": [{"command": "other"}, read_json(TEMPLATES / "stop-hook.json")], "Pre": []}}, indent=2
        )
        + "\n"
    )


def test_merge_hook_skips_when_gate_present(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"hooks": {"Stop": [{"command": "npx marestail gate"}]}}))
    install.merge_hook(path)
    assert read_json(path) == {"hooks": {"Stop": [{"command": "npx marestail gate"}]}}


def test_merge_agy_hook_adds_to_existing(tmp_path: Path) -> None:
    path = tmp_path / ".agents" / "hooks.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"other": {}, "marestail-gate": {"Stop": [{"command": "lint"}]}}))
    install.merge_agy_hook(path)
    assert read_json(path) == {"other": {}, "marestail-gate": {"Stop": [{"command": "lint"}, GATE]}}


def test_merge_grok_hook_creates_parents(tmp_path: Path) -> None:
    path = tmp_path / ".grok" / "hooks" / "gate.json"
    install.merge_grok_hook(path)
    install.merge_grok_hook(path)
    assert read_json(path) == {"hooks": {"Stop": [{"hooks": [GATE]}]}}


def test_merge_cursor_hook_adds_version_after_existing_keys(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"hooks": {"stop": [{"command": "x"}]}}))
    install.merge_cursor_hook(path)
    assert list(read_json(path)) == ["hooks", "version"]
    assert read_json(path) == {"hooks": {"stop": [{"command": "x"}, CURSOR_GATE]}, "version": 1}


def test_mapping_default_uses_the_fallback() -> None:
    assert install.mapping_default({}, "version", 1) == 1
    assert install.mapping_default({"version": 2}, "version", 1) == 2


def test_mapping_default_rejects_a_missing_key_name() -> None:
    with pytest.raises(TypeError, match=r"^key$"):
        install.mapping_default({}, None, 1)  # type: ignore[arg-type]


def test_merge_cursor_hook_passes_the_version_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def mapping_default(data: dict[str, Any], key: str, default: Any) -> Any:
        seen.append(default)
        return 1

    monkeypatch.setattr(install, "mapping_default", mapping_default)
    path = tmp_path / "hooks.json"
    path.write_text("{}")
    install.merge_cursor_hook(path)
    assert seen == [install.VERSION_DEFAULT]


def test_trust_entry_marks_the_folder_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", lambda: 9.7)
    assert install.trust_entry() == {install.TRUSTED: True, install.DECIDED: 9}
    assert install.require_entry(install.trust_entry())["trusted"] is True


def test_require_entry_rejects_none() -> None:
    with pytest.raises(TypeError, match=r"^entry$"):
        install.require_entry(None)


def test_install_constants() -> None:
    assert install.VERSION == "version"
    assert install.TRUSTED == "trusted"
    assert install.DECIDED == "decided_at"
    assert install.VERSION_DEFAULT == 1
    assert install.EMPTY_MAP == {}
    assert install.EMPTY_LIST == []


def test_mapping_and_listed() -> None:
    assert install.mapping({"hooks": {"stop": []}}, "hooks") == {"stop": []}
    assert install.mapping({}, "hooks") == {}
    assert install.listed({"stop": [1]}, "stop") == [1]
    assert install.listed({}, "stop") == []
    with pytest.raises(TypeError, match=r"^map$"):
        install.mapping({"hooks": 1}, "hooks")


def test_listed_rejects_a_non_list() -> None:
    with pytest.raises(TypeError, match=r"^list$"):
        install.listed({"stop": 1}, "stop")


def test_add_template_stops_without_template_key() -> None:
    settings: dict[str, Any] = {}
    install.add_template_stops(settings, {}, "hooks", "Stop")
    assert settings == {"hooks": {"Stop": []}}


def test_merge_cursor_hook_keeps_version(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"version": 2}))
    install.merge_cursor_hook(path)
    assert read_json(path) == {"version": 2, "hooks": {"stop": [CURSOR_GATE]}}


def test_trust_grok_folder_uses_grok_home(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    grok = tmp_path / "grok"
    monkeypatch.setenv("GROK_HOME", str(grok))
    store = grok / "trusted_folders.toml"
    grok.mkdir()
    store.write_text('[folders."/old"]\ntrusted = false\ndecided_at = 7\n\n[folders."/bare"]\n[folders."/other"]\ntrusted = true\n')
    install.trust_grok_folder(tmp_path / "x" / "..")
    assert store.read_text() == (
        '[folders."/old"]\ntrusted = false\ndecided_at = 7\n\n'
        '[folders."/bare"]\ntrusted = true\ndecided_at = 1234\n\n'
        '[folders."/other"]\ntrusted = true\ndecided_at = 1234\n\n'
        f'[folders."{tmp_path}"]\ntrusted = true\ndecided_at = 1234\n'
    )
    assert store.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr().out == f"trusted {tmp_path} for grok project hooks\n"


def test_trust_grok_folder_skips_trusted(home: Path, target: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = home / ".grok" / "trusted_folders.toml"
    store.parent.mkdir()
    store.write_text(f'[folders."{target}"]\ntrusted = true\ndecided_at = 1\n')
    install.trust_grok_folder(target)
    assert store.read_text() == f'[folders."{target}"]\ntrusted = true\ndecided_at = 1\n'
    assert capsys.readouterr().out == ""


def test_trust_grok_folder_retrusts_untrusted(home: Path, target: Path) -> None:
    store = home / ".grok" / "trusted_folders.toml"
    store.parent.mkdir()
    store.write_text(f'[folders."{target}"]\ntrusted = false\ndecided_at = 1\n')
    install.trust_grok_folder(target)
    assert store.read_text() == f'[folders."{target}"]\ntrusted = true\ndecided_at = 1234\n'


def test_trust_grok_folder_with_empty_store(home: Path, target: Path) -> None:
    store = home / ".grok" / "trusted_folders.toml"
    store.parent.mkdir()
    store.write_text("folders = {}\n")
    install.trust_grok_folder(target)
    assert store.read_text() == f'[folders."{target}"]\ntrusted = true\ndecided_at = 1234\n'
    folders = install.trusted_folders(store)
    assert folders[str(target.resolve())] == {install.TRUSTED: True, install.DECIDED: 1234}


def test_trust_grok_folder_reports_write_errors(home: Path, target: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (home / ".grok").write_text("not a folder")
    install.trust_grok_folder(target)
    output = capsys.readouterr().out
    assert output == f"could not trust {target} for grok hooks: [Errno 17] File exists: '{home / '.grok'}'\n"


@pytest.mark.parametrize(
    ("entry", "expected"), [(None, False), (True, False), ({"trusted": False}, False), ({"trusted": 1}, True), ({}, False)]
)
def test_is_trusted(entry: Any, expected: bool) -> None:
    assert install.is_trusted(entry) is expected


def test_folder_lines(home: Path) -> None:
    assert install.folder_lines('a"b', {"trusted": 0, "decided_at": 5.7}) == ['[folders."a\\"b"]', "trusted = false", "decided_at = 5", ""]
    assert install.folder_lines("p", "odd") == ['[folders."p"]', "trusted = true", "decided_at = 1234", ""]


def test_extend_gitignore_appends_missing_lines(tmp_path: Path) -> None:
    path = tmp_path / ".gitignore"
    path.write_text("node_modules/\n.venv/\n")
    install.extend_gitignore(path, ["qa/", ".venv/"])
    missing = [line for line in install.GITIGNORE_LINES if line != ".venv/"]
    assert path.read_text() == "\n".join(["node_modules/", ".venv/", "", "# marestail", *missing, "qa/"]) + "\n"


def test_extend_gitignore_reuses_header(tmp_path: Path) -> None:
    path = tmp_path / ".gitignore"
    path.write_text("# marestail\n.marestail/\n")
    install.extend_gitignore(path)
    assert path.read_text() == "\n".join(["# marestail", *install.GITIGNORE_LINES]) + "\n"


def test_extend_gitignore_leaves_complete_file_alone(tmp_path: Path) -> None:
    path = tmp_path / ".gitignore"
    path.write_text("\n".join(install.GITIGNORE_LINES))
    install.extend_gitignore(path, [])
    assert path.read_text() == "\n".join(install.GITIGNORE_LINES)
