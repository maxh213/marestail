import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from marestail import _hyper, _install, _tooling, install, runner, shell
from marestail.config import Config
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
    monkeypatch.setattr(_hyper, "run", npm)
    return npm


def tooling(target: Path, ts: dict[str, Any] | None = None) -> _tooling.Tooling:
    return _tooling.Tooling(target, TOOLED if ts is None else ts)


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

    assert _hyper.install_hyper(seeded) == 1

    assert str(seeded.resolve()) in (grok / "trusted_folders.toml").read_text()

    assert capsys.readouterr().out.splitlines()[-1] == (
        "npm install --prefix .marestail/tooling failed (exit 3); everything else is installed, see .marestail/tooling/npm.log"
    )


def ignored(root: Path, *paths: str) -> bool:
    checked = subprocess.run(["git", "check-ignore", "--no-index", *paths], cwd=root, capture_output=True, text=True, check=False)
    return checked.stdout.split() == list(paths)


def test_hyper_install_twice_hides_everything_in_one_block(seeded: Path, grok: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_npm(monkeypatch)

    _hyper.install_hyper(seeded)
    _hyper.install_hyper(seeded)

    exclude = (seeded / ".git" / "info" / "exclude").read_text()
    assert exclude.count(_hyper.HYPER_START) == 1
    assert ignored(seeded, "marestail.toml", "PERFORMANCE.md", "tasks/README.md", "guidance/ts.md", ".claude/settings.local.json")
    assert ignored(seeded, ".marestail/tooling/package.json", ".agents/hooks.json", ".grok/hooks/marestail-gate.json")
    assert json.loads((seeded / ".marestail" / "tooling" / "package.json").read_text())["name"] == "marestail-tooling"
    assert not (seeded / "package.json").exists()
    assert not (seeded / "sonar-project.properties").exists()
    assert git(seeded, "status", "--porcelain") == ""


def test_hyper_install_keeps_committed_marestail_files(
    seeded: Path, grok: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_npm(monkeypatch)
    kept = {
        "marestail.toml": '[git]\nbase = "main"\n',
        "PERFORMANCE.md": "ours\n",
        ".claude/settings.local.json": "{}\n",
        "sonar-project.properties": "sonar.projectKey=ours\n",
    }
    for name, text in kept.items():
        shell.ensure_dir((seeded / name).parent)
        (seeded / name).write_text(text)
    git(seeded, "add", "-f", *kept)
    commit_all(seeded, "ours")

    assert _hyper.install_hyper(seeded) == 0

    assert {name: (seeded / name).read_text() for name in kept} == kept
    assert not (seeded / ".marestail" / "tooling" / "sonar-project.properties").exists()
    assert capsys.readouterr().out.splitlines() == [
        f"trusted {seeded.resolve()} for grok project hooks",
        "left tracked files alone: .claude/settings.local.json, .gitignore, CLAUDE.md, PERFORMANCE.md, marestail.toml, sonar-project.properties",
        "no Stop hook for claude: .claude/settings.local.json is tracked",
        _hyper.NO_TOOLING,
        f"installed into {seeded} with --scope hyper; nothing to commit, see .git/info/exclude",
    ]
    assert git(seeded, "status", "--porcelain") == ""


def test_hyper_install_keeps_untracked_files_and_merges_the_local_hook(seeded: Path, grok: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_npm(monkeypatch)
    (seeded / "guidance").mkdir()
    (seeded / "guidance" / "ts.md").write_text("mine\n")
    (seeded / ".claude").mkdir()
    (seeded / ".claude" / "settings.local.json").write_text('{"env": {"A": "1"}}\n')

    _hyper.install_hyper(seeded)

    settings = json.loads((seeded / ".claude" / "settings.local.json").read_text())
    assert (seeded / "guidance" / "ts.md").read_text() == "mine\n"
    assert settings["env"] == {"A": "1"}
    assert "marestail gate --hook" in json.dumps(settings["hooks"]["Stop"])


def test_force_added_marestail_file_is_dropped_by_the_runner(seeded: Path, grok: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_npm(monkeypatch)
    _hyper.install_hyper(seeded)
    config = Config(root=seeded, raw={})
    before = runner.head(config)
    git(seeded, "add", "-f", "marestail.toml")
    git(seeded, "commit", "-q", "-m", "work")

    runner.drop_ignored_since(config, before)

    assert "marestail.toml" not in git(seeded, "ls-files").split()
    assert (seeded / "marestail.toml").exists()


def test_a_second_worktree_shares_the_exclusions(seeded: Path, grok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_npm(monkeypatch)
    _hyper.install_hyper(seeded)
    linked = tmp_path / "linked"
    git(seeded, "worktree", "add", "-q", "--detach", str(linked))
    (linked / "marestail.toml").write_text("")

    assert ignored(linked, "marestail.toml", ".marestail/tooling/package.json")
    assert git(linked, "status", "--porcelain") == ""


def test_hyper_install_refuses_a_folder_outside_git(
    tmp_path: Path, grok: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    npm = fake_npm(monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()

    assert _hyper.install_hyper(plain) == 1

    assert npm.calls == []

    assert capsys.readouterr().out == f"marestail install --scope hyper needs a git repository: {plain}\n"
    assert list(plain.iterdir()) == []
    assert not grok.exists()


class Recorder:
    def __init__(self, code: int, output: str) -> None:
        self.reply = (code, output)
        self.calls: list[tuple[list[str], Path, dict[str, Any]]] = []

    def __call__(self, command: list[str], cwd: Path, **options: Any) -> tuple[int, str]:
        self.calls.append((command, cwd, options))
        return self.reply


def record(monkeypatch: pytest.MonkeyPatch, code: int, output: str) -> Recorder:
    recorder = Recorder(code, output)
    monkeypatch.setattr(_hyper, "run", recorder)
    return recorder


def test_exclude_file_is_what_git_names(git_repo: Path, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert _hyper.exclude_file(git_repo) == ".git/info/exclude"
    assert _hyper.exclude_file(plain) is None


def test_exclude_file_of_a_worktree_is_the_common_one(git_repo: Path, tmp_path: Path) -> None:
    commit_all(git_repo, "seed")
    linked = tmp_path / "linked"
    git(git_repo, "worktree", "add", "--detach", str(linked))

    assert Path(str(_hyper.exclude_file(linked))).resolve() == (git_repo / ".git" / "info" / "exclude").resolve()


def test_exclude_file_asks_git_once_and_strips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = record(monkeypatch, 0, " .git/info/exclude\n")

    assert _hyper.exclude_file(tmp_path) == ".git/info/exclude"
    assert recorder.calls == [(["git", "rev-parse", "--git-path", "info/exclude"], tmp_path, {"timeout": 60})]


def test_exclude_file_is_none_when_git_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record(monkeypatch, 128, ".git/info/exclude\n")

    assert _hyper.exclude_file(tmp_path) is None


def test_tracked_paths_asks_git_for_the_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = record(monkeypatch, 0, "a b\0PERFORMANCE.md\0")

    assert _hyper.tracked_paths(tmp_path) == {"a b", "PERFORMANCE.md"}
    assert recorder.calls == [(["git", "ls-files", "-z", "--", *_hyper.TRACK_CANDIDATES], tmp_path, {"timeout": 60})]


def test_tracked_paths_only_lists_candidates(git_repo: Path) -> None:
    for name in ("CLAUDE.md", "PERFORMANCE.md", "other.md"):
        (git_repo / name).write_text("x")
    (git_repo / ".cursor").mkdir()
    (git_repo / ".cursor" / "hooks.json").write_text("{}")
    git(git_repo, "add", "CLAUDE.md", "other.md", ".cursor/hooks.json")

    assert _hyper.tracked_paths(git_repo) == {"CLAUDE.md", ".cursor/hooks.json"}


def test_tracked_paths_splits_several_nested_names(git_repo: Path) -> None:
    (git_repo / "guidance").mkdir()
    (git_repo / "guidance" / "ts.md").write_text("x")
    (git_repo / ".agents").mkdir()
    (git_repo / ".agents" / "hooks.json").write_text("{}")
    git(git_repo, "add", "guidance/ts.md", ".agents/hooks.json")

    assert _hyper.tracked_paths(git_repo) == {"guidance/ts.md", ".agents/hooks.json"}


def test_hyper_config_adds_tooling_under_ts() -> None:
    text = _hyper.hyper_config()

    assert '\n[ts]\ntooling = ".marestail/tooling"\n' in text
    assert text.count("tooling = ") == 1
    assert text.replace('tooling = ".marestail/tooling"\n', "") == (TEMPLATES / "marestail.toml").read_text()


def test_hyper_config_adds_tooling_under_the_first_ts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "marestail.toml").write_text("a = 1\n[ts]\nroot = 'x'\n[ts]\n")
    monkeypatch.setattr(_hyper, "TEMPLATES", tmp_path)

    assert _hyper.hyper_config() == "a = 1\n[ts]\ntooling = \".marestail/tooling\"\nroot = 'x'\n[ts]\n"


def test_hyper_files_add_cs_guidance_only_for_csharp(tmp_path: Path) -> None:
    bare = ["PERFORMANCE.md", "guidance/ts.md", "marestail.toml", "tasks/README.md"]
    assert sorted(_hyper.hyper_files(tmp_path)) == bare
    assert _hyper.hyper_files(tmp_path)["tasks/README.md"] == (TEMPLATES / "tasks-README.md").read_text()
    (tmp_path / "App.csproj").write_text("")
    assert sorted(_hyper.hyper_files(tmp_path)) == sorted([*bare, "guidance/cs.md"])
    assert _hyper.hyper_files(tmp_path)["guidance/cs.md"] == (TEMPLATES / "guidance" / "cs.md").read_text()


def test_write_hyper_tree_skips_tracked_and_existing(tmp_path: Path) -> None:
    (tmp_path / "PERFORMANCE.md").write_text("ours\n")

    _hyper.write_hyper_tree(tmp_path, {"guidance/ts.md"})

    assert (tmp_path / "PERFORMANCE.md").read_text() == "ours\n"
    assert not (tmp_path / "guidance" / "ts.md").exists()
    assert (tmp_path / "tasks" / "README.md").read_text() == (TEMPLATES / "tasks-README.md").read_text()
    assert (tmp_path / "marestail.toml").read_text() == _hyper.hyper_config()


def test_local_hooks_are_sorted_by_path() -> None:
    paths = [path for _, path, _ in _hyper.local_hooks()]

    assert paths == sorted(paths)
    assert [backend for backend, _, _ in _hyper.local_hooks()] == ["agy", "claude", "cursor", "grok"]


def test_apply_local_hooks_skips_tracked_files(tmp_path: Path) -> None:
    lines = _hyper.apply_local_hooks(tmp_path, {".cursor/hooks.json", ".agents/hooks.json"})

    assert lines == ["no Stop hook for agy: .agents/hooks.json is tracked", "no Stop hook for cursor: .cursor/hooks.json is tracked"]
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / ".agents").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()
    assert "marestail gate --hook" in (tmp_path / ".claude" / "settings.local.json").read_text()
    assert (tmp_path / ".grok" / "hooks" / "marestail-gate.json").exists()


@pytest.mark.parametrize(("text", "expected"), [("[ts]\nroot = 'web'\n", {"root": "web"}), ("ts = 1\n", {}), ("[git]\n", {})])
def test_ts_section(tmp_path: Path, text: str, expected: dict[str, Any]) -> None:
    (tmp_path / "marestail.toml").write_text(text)
    assert _hyper.ts_section(tmp_path) == expected


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

    assert not kinds.has(_tooling.VITEST_KINDS)
    assert not kinds.has_vitest()
    assert not kinds.has_tsconfig()
    (tmp_path / "web" / "vite.config.mts").write_text("")
    (tmp_path / "web" / "tsconfig.app.json").write_text("")
    assert kinds.has(_tooling.VITEST_KINDS)
    assert kinds.has_vitest()
    assert kinds.has_tsconfig()


def test_each_source_expands_every_pattern_per_source(tmp_path: Path) -> None:
    expanded = tooling(tmp_path, {"sources": ["app", "lib"]}).each_source(["<S>/a", "!<S>/b"])

    assert expanded == '["app/a", "lib/a", "!app/b", "!lib/b"]'


def test_package_json_depends_on_the_runner(tmp_path: Path) -> None:
    vitest = json.loads(_tooling.package_json(tooling(tmp_path)))
    jest = json.loads(_tooling.package_json(tooling(tmp_path, {"runner": "jest"})))

    assert list(vitest) == ["name", "private", "devDependencies"]
    assert (vitest["name"], vitest["private"]) == ("marestail-tooling", True)
    assert list(vitest["devDependencies"]) == sorted(vitest["devDependencies"])
    assert "vitest" in vitest["devDependencies"]
    assert "@stryker-mutator/jest-runner" not in vitest["devDependencies"]
    assert set(jest["devDependencies"]) == {*_tooling.BASE_DEPENDENCIES, "@stryker-mutator/jest-runner"}
    assert _tooling.package_json(tooling(tmp_path)).endswith("}\n")


def test_package_json_text_is_pinned(tmp_path: Path) -> None:
    head = '{\n  "name": "marestail-tooling",\n  "private": true,\n  "devDependencies": {\n    "@stryker-mutator/core": "^9.0.0",\n'
    tail = (
        '    "dependency-cruiser": "^16.0.0",\n    "eslint": "^9.0.0",\n    "knip": "^5.0.0",\n'
        '    "typescript": "^5.8.0",\n    "typescript-eslint": "^8.0.0"'
    )
    vitest_tail = ',\n    "vitest": "^3.2.0"\n  }\n}\n'
    vitest_mid = '    "@stryker-mutator/vitest-runner": "^9.0.0",\n    "@vitest/coverage-v8": "^3.2.0",\n'

    assert _tooling.package_json(tooling(tmp_path)) == head + vitest_mid + tail + vitest_tail
    assert _tooling.package_json(tooling(tmp_path, {"runner": "jest"})) == (
        head + '    "@stryker-mutator/jest-runner": "^9.0.0",\n' + tail + "\n  }\n}\n"
    )


def test_tsconfig_types_follow_the_runner(tmp_path: Path) -> None:
    vitest = _tooling.tsconfig_text(tooling(tmp_path))
    jest = _tooling.tsconfig_text(tooling(tmp_path, {**TOOLED, "runner": "jest", "sources": ["app"]}))

    assert '    "types": ["vitest/globals"]\n  },\n  "include": ["../../web/src"]\n}\n' in vitest
    assert '    "typeRoots": ["../../web/node_modules/@types"]\n  },\n  "include": ["../../web/app"]\n}\n' in jest
    assert '"types"' not in jest


def test_depcruise_points_at_the_tooling_tsconfig_unless_the_repo_has_one(tmp_path: Path) -> None:
    config = tooling(tmp_path)
    template = (TEMPLATES / "dependency-cruiser.cjs").read_text()

    assert _tooling.depcruise_text(config) == template.replace('"tsconfig.app.json"', json.dumps(str(config.folder / "tsconfig.json")))
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "tsconfig.app.json").write_text("")
    assert _tooling.depcruise_text(config) == template


def test_vitest_eslint_and_knip_texts(tmp_path: Path) -> None:
    config = tooling(tmp_path)

    assert '    include: ["src/**/*.{test,spec}.{ts,tsx}"],\n' in _tooling.vitest_text(config)
    assert (
        'coverage: { provider: "v8", include: ["src/**/*.{ts,tsx}"], exclude: ["src/**/*.{test,spec}.{ts,tsx}"] },'
        in _tooling.vitest_text(config)
    )
    assert '{ files: ["src/**/*.{ts,tsx}"], extends: [tseslint.configs.recommended] },' in _tooling.eslint_text(config)
    assert (
        _tooling.knip_text(config) == '{\n  "entry": ["src/index.{ts,tsx}", "src/main.{ts,tsx}"],\n  "project": ["src/**/*.{ts,tsx}"]\n}\n'
    )


def test_runner_options(tmp_path: Path) -> None:
    config = tooling(tmp_path)

    assert _tooling.runner_options(tooling(tmp_path, {"runner": "jest"})) == '  "jest": { "projectType": "custom" },\n'
    assert _tooling.runner_options(config) == f'  "vitest": {{ "configFile": "{config.folder}/vitest.config.ts" }},\n'
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "vitest.config.ts").write_text("")
    assert _tooling.runner_options(config) == ""


def test_stryker_text(tmp_path: Path) -> None:
    text = _tooling.stryker_text(tooling(tmp_path, {"runner": "jest"}))

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
    assert '"plugins": ["@stryker-mutator/vitest-runner"],' in _tooling.stryker_text(tooling(tmp_path))


def test_sonar_text_also_excludes_marestail(tmp_path: Path) -> None:
    text = _tooling.sonar_text(tooling(tmp_path))
    template = (TEMPLATES / "sonar-project.properties").read_text().splitlines()

    assert [line for line in text.splitlines() if line not in template] == [
        line + ",.marestail/**" for line in template if line.startswith("sonar.exclusions=")
    ]
    assert text.endswith("UTF-8\n")
    assert _tooling.sonar_line("sonar.exclusions=a") == "sonar.exclusions=a,.marestail/**"
    assert _tooling.sonar_line("x.sonar.exclusions=a") == "x.sonar.exclusions=a"


def test_write_tooling_skips_kinds_the_repo_has(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    for name in ("eslint.config.js", "tsconfig.app.json", ".dependency-cruiser.cjs", "stryker.conf.js", "knip.config.ts", "vite.config.ts"):
        (web / name).write_text("")
    (tmp_path / "sonar-project.properties").write_text("")
    config = tooling(tmp_path)

    _tooling.write_tooling(config)

    assert sorted(path.name for path in config.folder.iterdir()) == ["package.json"]


def test_write_tooling_overwrites_its_own_files(tmp_path: Path) -> None:
    config = tooling(tmp_path)
    config.folder.mkdir(parents=True)
    (config.folder / "knip.json").write_text("stale")

    _tooling.write_tooling(config)

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
    assert (config.folder / "knip.json").read_text() == _tooling.knip_text(config)


def test_depcruise_kind_follows_the_configured_name(tmp_path: Path) -> None:
    config = tooling(tmp_path, {**TOOLED, "depcruise_config": ".dependency-cruiser.js"})
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / ".dependency-cruiser.cjs").write_text("")

    _tooling.write_tooling(config)

    assert (config.folder / ".dependency-cruiser.cjs").exists()


def test_extend_exclude_appends_one_block(tmp_path: Path) -> None:
    path = tmp_path / "info" / "exclude"

    _hyper.extend_exclude(path)
    _hyper.extend_exclude(path)

    lines = path.read_text().splitlines()
    assert lines == [_hyper.HYPER_START, *_install.GITIGNORE_LINES, *_hyper.HYPER_EXCLUDES, _hyper.HYPER_END]
    assert path.read_text().endswith("# end marestail\n")


def test_extend_exclude_keeps_existing_text(tmp_path: Path) -> None:
    path = tmp_path / "exclude"
    path.write_text("# sample")

    _hyper.extend_exclude(path)

    assert path.read_text().startswith(f"# sample\n{_hyper.HYPER_START}\n")


@pytest.mark.parametrize(("text", "expected"), [("", ""), ("a", "a\n"), ("a\n", "a\n")])
def test_ended(text: str, expected: str) -> None:
    assert _hyper.ended(text) == expected


def test_print_notes(capsys: pytest.CaptureFixture[str]) -> None:
    _hyper.print_notes({"marestail.toml", "PERFORMANCE.md", ".claude/settings.local.json"}, ["no Stop hook"], {})
    _hyper.print_notes(set(), [], {"tooling": "x"})

    assert capsys.readouterr().out.splitlines() == [
        "left tracked files alone: .claude/settings.local.json, PERFORMANCE.md, marestail.toml",
        "no Stop hook",
        _hyper.NO_TOOLING,
    ]


def test_npm_outcome_logs_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    npm = fake_npm(monkeypatch)
    (tmp_path / ".marestail" / "tooling").mkdir(parents=True)

    assert _hyper.npm_outcome(tmp_path, "x/exclude") == 0

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
