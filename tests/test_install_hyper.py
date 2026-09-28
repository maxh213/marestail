import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from marestail import _install, install, shell
from tests.conftest import commit_all, git

TEMPLATES = _install.TEMPLATES
ROOT = Path(__file__).resolve().parent.parent
TOOLED = {"root": "web", "tooling": ".marestail/tooling"}
NPM = ["npm", "install", "--prefix", ".marestail/tooling"]


@pytest.fixture
def grok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "grok"
    monkeypatch.setenv("GROK_HOME", str(folder))
    return folder


@pytest.fixture
def seeded(git_repo: Path) -> Path:
    (git_repo / ".gitignore").write_text("node_modules/\n")
    (git_repo / "CLAUDE.md").write_text("team rules\n")
    commit_all(git_repo, "seed")
    return git_repo


class Npm:
    def __init__(self, code: int) -> None:
        self.code = code
        self.calls: list[tuple[list[str], Path, Any]] = []
        self.real = shell.run

    def __call__(self, command: list[str], cwd: Path, **options: Any) -> tuple[int, str]:
        if command[0] != "npm":
            return self.real(command, cwd, **options)
        self.calls.append((command, cwd, options.get("timeout")))
        return self.code, "npm out\nnpm err\n"


def fake_npm(monkeypatch: pytest.MonkeyPatch, code: int = 0) -> Npm:
    npm = Npm(code)
    monkeypatch.setattr(_install, "run", npm)
    return npm


def tooling(target: Path, ts: dict[str, Any] | None = None) -> _install.Tooling:
    return _install.Tooling(target, TOOLED if ts is None else ts)


def test_hyper_install_writes_nothing_tracked(
    seeded: Path, grok: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    npm = fake_npm(monkeypatch)

    assert install.install(seeded, hyper=True) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"trusted {seeded.resolve()} for grok project hooks",
        "left tracked files alone: .gitignore, CLAUDE.md",
        f"installed into {seeded} with --scope hyper; nothing to commit, see .git/info/exclude",
    ]
    assert git(seeded, "status", "--porcelain") == ""
    assert npm.calls == [(NPM, seeded, 1800)]
    assert (seeded / ".marestail" / "tooling" / "npm.log").read_text() == "npm out\nnpm err\n"
    assert (seeded / "CLAUDE.md").read_text() == "team rules\n"
    assert not (seeded / "AGENTS.md").exists()
    assert 'tooling = ".marestail/tooling"' in (seeded / "marestail.toml").read_text()


def test_hyper_install_reports_a_failed_npm(
    seeded: Path, grok: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_npm(monkeypatch, 3)

    assert _install.install_hyper(seeded) == 1

    assert capsys.readouterr().out.splitlines()[-1] == (
        "npm install --prefix .marestail/tooling failed (exit 3); everything else is installed, see .marestail/tooling/npm.log"
    )


def test_hyper_install_refuses_a_folder_outside_git(tmp_path: Path, grok: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert _install.install_hyper(plain) == 1

    assert capsys.readouterr().out == f"marestail install --scope hyper needs a git repository: {plain}\n"
    assert list(plain.iterdir()) == []
    assert not grok.exists()


def test_exclude_file_is_what_git_names(git_repo: Path, tmp_path: Path) -> None:
    assert _install.exclude_file(git_repo) == ".git/info/exclude"
    assert _install.exclude_file(tmp_path / "missing") is None


def test_tracked_paths_only_lists_candidates(git_repo: Path) -> None:
    for name in ("CLAUDE.md", "PERFORMANCE.md", "other.md"):
        (git_repo / name).write_text("x")
    (git_repo / ".cursor").mkdir()
    (git_repo / ".cursor" / "hooks.json").write_text("{}")
    git(git_repo, "add", "CLAUDE.md", "other.md", ".cursor/hooks.json")

    assert _install.tracked_paths(git_repo) == {"CLAUDE.md", ".cursor/hooks.json"}


def test_hyper_config_adds_tooling_under_ts() -> None:
    text = _install.hyper_config()

    assert '\n[ts]\ntooling = ".marestail/tooling"\n' in text
    assert text.count("tooling = ") == 1
    assert text.replace('tooling = ".marestail/tooling"\n', "") == (TEMPLATES / "marestail.toml").read_text()


def test_hyper_files_add_cs_guidance_only_for_csharp(tmp_path: Path) -> None:
    assert sorted(_install.hyper_files(tmp_path)) == ["PERFORMANCE.md", "guidance/ts.md", "marestail.toml", "tasks/README.md"]
    assert _install.hyper_files(tmp_path)["tasks/README.md"] == (TEMPLATES / "tasks-README.md").read_text()
    (tmp_path / "App.csproj").write_text("")
    assert _install.hyper_files(tmp_path)["guidance/cs.md"] == (TEMPLATES / "guidance" / "cs.md").read_text()


def test_write_hyper_tree_skips_tracked_and_existing(tmp_path: Path) -> None:
    (tmp_path / "PERFORMANCE.md").write_text("ours\n")

    _install.write_hyper_tree(tmp_path, {"guidance/ts.md"})

    assert (tmp_path / "PERFORMANCE.md").read_text() == "ours\n"
    assert not (tmp_path / "guidance" / "ts.md").exists()
    assert (tmp_path / "tasks" / "README.md").read_text() == (TEMPLATES / "tasks-README.md").read_text()
    assert (tmp_path / "marestail.toml").read_text() == _install.hyper_config()


def test_local_hooks_are_sorted_by_path() -> None:
    paths = [path for _, path, _ in _install.local_hooks()]

    assert paths == sorted(paths)
    assert [backend for backend, _, _ in _install.local_hooks()] == ["agy", "claude", "cursor", "grok"]


def test_apply_local_hooks_skips_tracked_files(tmp_path: Path) -> None:
    lines = _install.apply_local_hooks(tmp_path, {".cursor/hooks.json", ".agents/hooks.json"})

    assert lines == ["no Stop hook for agy: .agents/hooks.json is tracked", "no Stop hook for cursor: .cursor/hooks.json is tracked"]
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / ".agents").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()
    assert "marestail gate --hook" in (tmp_path / ".claude" / "settings.local.json").read_text()
    assert (tmp_path / ".grok" / "hooks" / "marestail-gate.json").exists()


@pytest.mark.parametrize(("text", "expected"), [("[ts]\nroot = 'web'\n", {"root": "web"}), ("ts = 1\n", {}), ("[git]\n", {})])
def test_ts_section(tmp_path: Path, text: str, expected: dict[str, Any]) -> None:
    (tmp_path / "marestail.toml").write_text(text)
    assert _install.ts_section(tmp_path) == expected


def test_tooling_defaults(tmp_path: Path) -> None:
    plain = tooling(tmp_path, {})

    assert plain.folder == tmp_path / ".marestail" / "tooling"
    assert plain.ts_root == tmp_path
    assert plain.sources == ["src"]
    assert plain.up == "../.."
    assert plain.tsconfig_name == "tsconfig.app.json"
    assert not plain.jest


def test_tooling_reads_ts_settings(tmp_path: Path) -> None:
    jest = tooling(tmp_path, {"root": "web", "runner": "jest", "source": "lib", "tsconfig": "tsconfig.json"})

    assert jest.ts_root == tmp_path / "web"
    assert jest.sources == ["lib"]
    assert jest.up == "../../web"
    assert jest.tsconfig_name == "tsconfig.json"
    assert jest.jest
    assert jest.has_vitest()
    assert tooling(tmp_path, {"sources": ["app", "lib"], "source": "x"}).sources == ["app", "lib"]


def test_tooling_kinds_look_in_the_ts_root(tmp_path: Path) -> None:
    kinds = tooling(tmp_path)
    (tmp_path / "web").mkdir()

    assert not kinds.has(_install.VITEST_KINDS)
    assert not kinds.has_vitest()
    assert not kinds.has_tsconfig()
    (tmp_path / "web" / "vite.config.mts").write_text("")
    (tmp_path / "web" / "tsconfig.app.json").write_text("")
    assert kinds.has(_install.VITEST_KINDS)
    assert kinds.has_vitest()
    assert kinds.has_tsconfig()


def test_each_source_expands_every_pattern_per_source(tmp_path: Path) -> None:
    expanded = tooling(tmp_path, {"sources": ["app", "lib"]}).each_source(["<S>/a", "!<S>/b"])

    assert expanded == '["app/a", "lib/a", "!app/b", "!lib/b"]'


def test_package_json_depends_on_the_runner(tmp_path: Path) -> None:
    vitest = json.loads(_install.package_json(tooling(tmp_path)))
    jest = json.loads(_install.package_json(tooling(tmp_path, {"runner": "jest"})))

    assert list(vitest) == ["name", "private", "devDependencies"]
    assert (vitest["name"], vitest["private"]) == ("marestail-tooling", True)
    assert list(vitest["devDependencies"]) == sorted(vitest["devDependencies"])
    assert "vitest" in vitest["devDependencies"]
    assert "@stryker-mutator/jest-runner" not in vitest["devDependencies"]
    assert set(jest["devDependencies"]) == {*_install.BASE_DEPENDENCIES, "@stryker-mutator/jest-runner"}
    assert _install.package_json(tooling(tmp_path)).endswith("}\n")


def test_tsconfig_types_follow_the_runner(tmp_path: Path) -> None:
    vitest = _install.tsconfig_text(tooling(tmp_path))
    jest = _install.tsconfig_text(tooling(tmp_path, {**TOOLED, "runner": "jest", "sources": ["app"]}))

    assert '    "types": ["vitest/globals"]\n  },\n  "include": ["../../web/src"]\n}\n' in vitest
    assert '    "typeRoots": ["../../web/node_modules/@types"]\n  },\n  "include": ["../../web/app"]\n}\n' in jest
    assert '"types"' not in jest


def test_depcruise_points_at_the_tooling_tsconfig_unless_the_repo_has_one(tmp_path: Path) -> None:
    config = tooling(tmp_path)
    template = (TEMPLATES / "dependency-cruiser.cjs").read_text()

    assert _install.depcruise_text(config) == template.replace('"tsconfig.app.json"', json.dumps(str(config.folder / "tsconfig.json")))
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "tsconfig.app.json").write_text("")
    assert _install.depcruise_text(config) == template


def test_vitest_eslint_and_knip_texts(tmp_path: Path) -> None:
    config = tooling(tmp_path)

    assert '    include: ["src/**/*.{test,spec}.{ts,tsx}"],\n' in _install.vitest_text(config)
    assert (
        'coverage: { provider: "v8", include: ["src/**/*.{ts,tsx}"], exclude: ["src/**/*.{test,spec}.{ts,tsx}"] },'
        in _install.vitest_text(config)
    )
    assert '{ files: ["src/**/*.{ts,tsx}"], extends: [tseslint.configs.recommended] },' in _install.eslint_text(config)
    assert (
        _install.knip_text(config) == '{\n  "entry": ["src/index.{ts,tsx}", "src/main.{ts,tsx}"],\n  "project": ["src/**/*.{ts,tsx}"]\n}\n'
    )


def test_runner_options(tmp_path: Path) -> None:
    config = tooling(tmp_path)

    assert _install.runner_options(tooling(tmp_path, {"runner": "jest"})) == '  "jest": { "projectType": "custom" },\n'
    assert _install.runner_options(config) == f'  "vitest": {{ "configFile": "{config.folder}/vitest.config.ts" }},\n'
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "vitest.config.ts").write_text("")
    assert _install.runner_options(config) == ""


def test_stryker_text(tmp_path: Path) -> None:
    text = _install.stryker_text(tooling(tmp_path, {"runner": "jest"}))

    assert text == (
        "{\n"
        '  "testRunner": "jest",\n'
        '  "plugins": ["@stryker-mutator/jest-runner"],\n'
        '  "jest": { "projectType": "custom" },\n'
        '  "mutate": ["src/**/*.ts", "src/**/*.tsx", "!src/**/*.test.*", "!src/**/*.spec.*"],\n'
        '  "ignorePatterns": [".marestail"],\n'
        '  "coverageAnalysis": "perTest"\n'
        "}\n"
    )
    assert '"plugins": ["@stryker-mutator/vitest-runner"],' in _install.stryker_text(tooling(tmp_path))


def test_sonar_text_also_excludes_marestail(tmp_path: Path) -> None:
    text = _install.sonar_text(tooling(tmp_path))
    template = (TEMPLATES / "sonar-project.properties").read_text().splitlines()

    assert [line for line in text.splitlines() if line not in template] == [
        line + ",.marestail/**" for line in template if line.startswith("sonar.exclusions=")
    ]
    assert text.endswith("UTF-8\n")
    assert _install.sonar_line("sonar.exclusions=a") == "sonar.exclusions=a,.marestail/**"
    assert _install.sonar_line("x.sonar.exclusions=a") == "x.sonar.exclusions=a"


def test_write_tooling_skips_kinds_the_repo_has(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    for name in ("eslint.config.js", "tsconfig.app.json", ".dependency-cruiser.cjs", "stryker.conf.js", "knip.config.ts", "vite.config.ts"):
        (web / name).write_text("")
    (tmp_path / "sonar-project.properties").write_text("")
    config = tooling(tmp_path)

    _install.write_tooling(config)

    assert sorted(path.name for path in config.folder.iterdir()) == ["package.json"]


def test_write_tooling_overwrites_its_own_files(tmp_path: Path) -> None:
    config = tooling(tmp_path)
    config.folder.mkdir(parents=True)
    (config.folder / "knip.json").write_text("stale")

    _install.write_tooling(config)

    names = sorted(path.name for path in config.folder.iterdir())
    assert names == sorted(
        [
            ".dependency-cruiser.cjs",
            "eslint.config.mjs",
            "knip.json",
            "package.json",
            "sonar-project.properties",
            "stryker.config.json",
            "tsconfig.json",
            "vitest.config.ts",
        ]
    )
    assert (config.folder / "knip.json").read_text() == _install.knip_text(config)


def test_depcruise_kind_follows_the_configured_name(tmp_path: Path) -> None:
    config = tooling(tmp_path, {**TOOLED, "depcruise_config": ".dependency-cruiser.js"})
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / ".dependency-cruiser.cjs").write_text("")

    _install.write_tooling(config)

    assert (config.folder / ".dependency-cruiser.cjs").exists()


def test_extend_exclude_appends_one_block(tmp_path: Path) -> None:
    path = tmp_path / "info" / "exclude"

    _install.extend_exclude(path)
    _install.extend_exclude(path)

    lines = path.read_text().splitlines()
    assert lines == [_install.HYPER_START, *_install.GITIGNORE_LINES, *_install.HYPER_EXCLUDES, _install.HYPER_END]
    assert path.read_text().endswith("# end marestail\n")


def test_extend_exclude_keeps_existing_text(tmp_path: Path) -> None:
    path = tmp_path / "exclude"
    path.write_text("# sample")

    _install.extend_exclude(path)

    assert path.read_text().startswith(f"# sample\n{_install.HYPER_START}\n")


@pytest.mark.parametrize(("text", "expected"), [("", ""), ("a", "a\n"), ("a\n", "a\n")])
def test_ended(text: str, expected: str) -> None:
    assert _install.ended(text) == expected


def test_print_notes(capsys: pytest.CaptureFixture[str]) -> None:
    _install.print_notes({"marestail.toml", "PERFORMANCE.md", ".claude/settings.local.json"}, ["no Stop hook"], {})
    _install.print_notes(set(), [], {"tooling": "x"})

    assert capsys.readouterr().out.splitlines() == [
        "left tracked files alone: .claude/settings.local.json, PERFORMANCE.md, marestail.toml",
        "no Stop hook",
        _install.NO_TOOLING,
    ]


def test_npm_outcome_logs_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    npm = fake_npm(monkeypatch)
    (tmp_path / ".marestail" / "tooling").mkdir(parents=True)

    assert _install.npm_outcome(tmp_path, "x/exclude") == 0

    assert npm.calls == [(NPM, tmp_path, 1800)]
    assert capsys.readouterr().out == f"installed into {tmp_path} with --scope hyper; nothing to commit, see x/exclude\n"


def test_install_hyper_script_passes_and_readme_documents_it() -> None:
    script = ROOT / "tools" / "test-install-hyper.py"
    completed = subprocess.run([sys.executable, str(script)], cwd=ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.splitlines()[-1] == "install-hyper ok"
    readme = (ROOT / "README.md").read_text()
    section = readme.split("## Installing into a repository that does not use marestail\n", 1)[1].split("\n## ", 1)[0]
    for phrase in ("marestail install --scope hyper", ".git/info/exclude", ".marestail/tooling", "venv"):
        assert phrase in section
