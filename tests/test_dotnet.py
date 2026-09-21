import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet
from tests.conftest import FakeRun, make_context

APP = "<Project />"


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotnet, "HOST", {})
    monkeypatch.setattr(dotnet, "PROJECTS", {})


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def context(base: Path, **section: Any) -> Any:
    ctx = make_context(base, {"dotnet": section})
    ctx.work.mkdir(exist_ok=True)
    return ctx


@pytest.mark.parametrize(("value", "expected"), [(None, []), ([1, "a"], ["1", "a"]), (3, ["3"]), ("x", ["x"])])
def test_listify(value: Any, expected: list[str]) -> None:
    assert dotnet.listify(value) == expected


@pytest.mark.parametrize(("value", "expected"), [([], []), ([1, "a"], ["1", "a"]), (3, ["3"]), ("x", ["x"])])
def test_configured_list(value: Any, expected: list[str]) -> None:
    assert dotnet.configured_list(value) == expected


def test_configured_list_rejects_none() -> None:
    with pytest.raises(TypeError) as raised:
        dotnet.configured_list(None)
    assert str(raised.value) == "list"


def test_dotnet_constants() -> None:
    assert (dotnet.SLASH, dotnet.REPLACE, dotnet.EMPTY, dotnet.MUTATION_EXCLUDE) == ("/", "replace", [], "mutation_exclude")


def test_env_creates_homes(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    assert dotnet.env(ctx) == {
        "HOME": str(tmp_path / ".marestail" / "dotnethome"),
        "NUGET_PACKAGES": str(tmp_path / ".marestail" / "nuget"),
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    }
    assert (tmp_path / ".marestail" / "dotnethome").is_dir()
    assert (tmp_path / ".marestail" / "nuget").is_dir()


@pytest.mark.parametrize(("code", "expected"), [(0, True), (1, False)])
def test_host_dotnet_probes_once(tmp_path: Path, fake_run: Callable[..., FakeRun], code: int, expected: bool) -> None:
    fake = fake_run(dotnet, [(code, "8.0.100")])
    ctx = context(tmp_path)
    assert dotnet.host_dotnet(ctx) is expected
    assert dotnet.host_dotnet(ctx) is expected
    assert fake.calls == [["dotnet", "--version"]]
    assert fake.options[0]["timeout"] == 60
    assert fake.options[0]["cwd"] == tmp_path
    assert fake.options[0]["env"]["DOTNET_NOLOGO"] == "1"


def test_dotnet_bin_prefers_configured_command(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(dotnet)
    ctx = context(tmp_path, dotnet=["/opt/dn", "--x"])
    assert dotnet.dotnet_bin(ctx, tmp_path, False, {}, "dotnet") == ["/opt/dn", "--x"]
    assert fake.calls == []


def test_dotnet_bin_uses_host_for_other_programs(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(dotnet, [(0, "")])
    ctx = context(tmp_path, dotnet="/opt/dn")
    assert dotnet.dotnet_bin(ctx, tmp_path, False, {}, "sh") == ["sh"]


def test_dotnet_bin_falls_back_to_docker(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(dotnet, [(127, "")])
    ctx = context(tmp_path, image="img:1")
    home = tmp_path / ".marestail"
    assert dotnet.dotnet_bin(ctx, tmp_path / "src", True, {"SONAR_TOKEN": "t"}, "sh") == [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{tmp_path}:{tmp_path}",
        "-v",
        f"{dotnet.MARESTAIL_ROOT}:{dotnet.MARESTAIL_ROOT}",
        "--network",
        "host",
        "-e",
        f"HOME={home / 'dotnethome'}",
        "-e",
        f"NUGET_PACKAGES={home / 'nuget'}",
        "-e",
        "DOTNET_CLI_TELEMETRY_OPTOUT=1",
        "-e",
        "DOTNET_NOLOGO=1",
        "-e",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1",
        "-e",
        "SONAR_TOKEN=t",
        "-w",
        str(tmp_path / "src"),
        "img:1",
        "sh",
    ]


def test_docker_skips_marestail_mount_inside_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotnet, "MARESTAIL_ROOT", tmp_path / "tool")
    command = dotnet.docker_command(context(tmp_path), tmp_path, False, {}, "dotnet")
    assert command[:7] == ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{tmp_path}:{tmp_path}"]
    assert command[7] == "-e"
    assert "--network" not in command
    assert command[-3:] == [str(tmp_path), dotnet.IMAGE, "dotnet"]


def test_dotnet_can_join_the_host_network(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(dotnet, [(127, ""), (0, "ok")])
    ctx = context(tmp_path, image="img:1")
    assert dotnet.dotnet(ctx, ["build"], network=True) == (0, "ok")
    assert "--network" in fake.calls[-1]
    assert "host" in fake.calls[-1]


def test_dotnet_default_stays_off_the_host_network(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(dotnet, [(127, ""), (0, "ok")])
    ctx = context(tmp_path, image="img:1")
    assert dotnet.dotnet(ctx, ["build"], extra={"K": "v"}) == (0, "ok")
    command = fake.calls[-1]
    assert "--network" not in command
    assert "-e" in command
    assert "K=v" in command
    assert "-w" in command
    assert command[command.index("-w") + 1] == str(tmp_path)


def test_staged_project_creates_nested_work(tmp_path: Path) -> None:
    source = tmp_path / "Program.cs"
    project = tmp_path / "Scan.csproj"
    source.write_text("class P {}")
    project.write_text(APP)
    ctx = context(tmp_path)
    ctx.work.rmdir()
    first = dotnet.staged_project(ctx, source, project)
    assert first.is_file()
    again = dotnet.staged_project(ctx, source, project)
    assert again == first
    assert (ctx.work / dotnet.STAGE / dotnet.PROGRAM_CS).read_text() == "class P {}"


def test_dotnet_runs_in_root_by_default(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(dotnet, [(0, ""), (0, "done")])
    ctx = context(tmp_path, root="src")
    assert dotnet.dotnet(ctx, ["build"]) == (0, "done")
    assert fake.calls[1] == ["dotnet", "build"]
    assert fake.options[1]["cwd"] == tmp_path / "src"
    assert fake.options[1]["timeout"] == 1800
    assert "EXTRA" not in fake.options[1]["env"]


def test_dotnet_passes_cwd_extra_and_program(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(dotnet, [(0, ""), (3, "bad")])
    ctx = context(tmp_path)
    assert dotnet.dotnet(ctx, ["a.sh"], cwd=tmp_path / "x", timeout=5, extra={"EXTRA": "1"}, program="sh") == (3, "bad")
    assert fake.calls[1] == ["sh", "a.sh"]
    assert fake.options[1]["cwd"] == tmp_path / "x"
    assert fake.options[1]["timeout"] == 5
    assert fake.options[1]["env"]["EXTRA"] == "1"
    assert fake.options[1]["env"]["DOTNET_NOLOGO"] == "1"


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (127, "", f"dotnet unavailable: {dotnet.INSTALL_HINT}"),
        (1, "Cannot connect to the Docker daemon at unix://", f"dotnet unavailable: {dotnet.INSTALL_HINT}"),
        (125, "Unable to find image 'x' locally", f"docker image missing: docker pull {dotnet.IMAGE}"),
        (1, "error CS0001", None),
    ],
)
def test_hint(code: int, output: str, expected: str | None) -> None:
    assert dotnet.hint(code, output) == expected


def test_failure_keeps_output_tail() -> None:
    assert dotnet.failure(1, "  " + "x" * 400 + "END  ", "boom") == "boom: " + "x" * 297 + "END"
    assert dotnet.failure(127, "", "boom") == f"dotnet unavailable: {dotnet.INSTALL_HINT}"


def test_rel(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    ctx = context(tmp_path / "repo")
    assert dotnet.rel(ctx, tmp_path / "repo" / "src" / "A.cs") == "src/A.cs"
    assert dotnet.rel(ctx, str(tmp_path / "repo")) == "."
    assert dotnet.rel(ctx, tmp_path / "other") == str(tmp_path / "other")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("src/obj/A.cs", True),
        ("src/bin/A.cs", True),
        ("src/A.g.cs", True),
        ("src/A.Designer.cs", True),
        ("src/A.AssemblyInfo.cs", True),
        ("perf/Bench.cs", True),
        ("src/A.cs", False),
        ("obj.cs", False),
    ],
)
def test_generated(tmp_path: Path, name: str, expected: bool) -> None:
    assert dotnet.generated(context(tmp_path), tmp_path / name) is expected


def test_csprojs_skip_generated(tmp_path: Path) -> None:
    write(tmp_path, {"b/B.csproj": APP, "a/A.csproj": APP, "obj/C.csproj": APP})
    assert dotnet.csprojs(context(tmp_path)) == [tmp_path / "a" / "A.csproj", tmp_path / "b" / "B.csproj"]


@pytest.mark.parametrize(("name", "expected"), [("AppTests.csproj", True), ("AppTest.csproj", True), ("App.csproj", False)])
def test_test_named(name: str, expected: bool) -> None:
    assert dotnet.test_named(Path(name)) is expected


def test_configured_projects(tmp_path: Path) -> None:
    ctx = context(tmp_path, root="cs", project="App/App.csproj", test_project="T/T.csproj")
    assert dotnet.project(ctx) == tmp_path / "cs" / "App" / "App.csproj"
    assert dotnet.test_project(ctx) == tmp_path / "cs" / "T" / "T.csproj"


def test_discovered_projects(tmp_path: Path) -> None:
    write(tmp_path, {"App/App.csproj": APP, "AppTests/AppTests.csproj": APP})
    ctx = context(tmp_path)
    assert dotnet.project(ctx) == tmp_path / "App" / "App.csproj"
    assert dotnet.test_project(ctx) == tmp_path / "AppTests" / "AppTests.csproj"
    assert dotnet.named_csprojs(ctx, True) == [tmp_path / "AppTests" / "AppTests.csproj"]


def test_single_project_holds_tests(tmp_path: Path) -> None:
    write(tmp_path, {"App.csproj": APP})
    assert dotnet.test_project(context(tmp_path)) == tmp_path / "App.csproj"


def test_ambiguous_projects(tmp_path: Path) -> None:
    write(tmp_path, {"A.csproj": APP, "B.csproj": APP, "ATests.csproj": APP, "BTests.csproj": APP})
    ctx = context(tmp_path)
    assert dotnet.project(ctx) is None
    assert dotnet.test_project(ctx) is None


def test_two_test_projects_do_not_fall_back_to_the_product(tmp_path: Path) -> None:
    write(tmp_path, {"App.csproj": APP, "ATests.csproj": APP, "BTests.csproj": APP})
    ctx = context(tmp_path)
    assert dotnet.project(ctx) == tmp_path / "App.csproj"
    assert dotnet.test_project(ctx) is None


def test_paths_or_raise(tmp_path: Path) -> None:
    path = tmp_path / "App.csproj"
    assert dotnet.paths_or_raise(path, path) == (path, path)
    with pytest.raises(TypeError) as raised:
        dotnet.paths_or_raise(None, path)
    assert str(raised.value) == "pair"
    with pytest.raises(TypeError):
        dotnet.paths_or_raise(path, None)


def test_projects_found_and_cached(tmp_path: Path) -> None:
    write(tmp_path, {"App/App.csproj": APP, "AppTests/AppTests.csproj": APP})
    ctx = context(tmp_path)
    expected = (tmp_path / "App" / "App.csproj", tmp_path / "AppTests" / "AppTests.csproj")
    assert dotnet.projects(ctx) == (*expected, None)
    assert dotnet.project_pair(ctx) == expected
    assert dotnet.PROJECTS[str(tmp_path)] == expected


def test_projects_missing_lists_candidates(tmp_path: Path) -> None:
    write(tmp_path, {"cs/A.csproj": APP, "cs/B.csproj": APP})
    ctx = context(tmp_path, root="cs")
    message = "set [dotnet] project and test_project in marestail.toml (.csproj files under cs: cs/A.csproj, cs/B.csproj)"
    assert dotnet.projects(ctx) == (None, None, message)


def test_projects_missing_file_says_none(tmp_path: Path) -> None:
    ctx = context(tmp_path, project="App.csproj")
    message = "set [dotnet] project and test_project in marestail.toml (.csproj files under .: none)"
    assert dotnet.project_pair(ctx) == message


def test_projects_configured_test_project_missing(tmp_path: Path) -> None:
    write(tmp_path, {"App.csproj": APP})
    ctx = context(tmp_path, test_project="Gone.csproj")
    assert isinstance(dotnet.project_pair(ctx), str)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("AppTests/Anything.cs", True),
        ("App/Thing.cs", False),
        ("App/ThingTests.cs", True),
        ("App/ThingTest.cs", True),
        ("App/test/Thing.cs", True),
        ("App/Tests/Thing.cs", True),
    ],
)
def test_is_test_with_separate_projects(tmp_path: Path, name: str, expected: bool) -> None:
    write(tmp_path, {"App/App.csproj": APP, "AppTests/AppTests.csproj": APP})
    assert dotnet.is_test(context(tmp_path), tmp_path / name) is expected


def test_is_test_with_shared_folder(tmp_path: Path) -> None:
    write(tmp_path, {"App.csproj": APP, "AppTests.csproj": APP})
    ctx = context(tmp_path)
    assert dotnet.is_test(ctx, tmp_path / "Thing.cs") is False
    assert dotnet.in_separate_test_project(ctx, tmp_path / "Thing.cs") is False


def test_is_test_without_projects(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    assert dotnet.in_separate_test_project(ctx, tmp_path / "Thing.cs") is False
    assert dotnet.is_test(ctx, tmp_path / "tests" / "Thing.cs") is True


def test_files_and_sources(tmp_path: Path) -> None:
    write(
        tmp_path,
        {"App/App.csproj": APP, "AppTests/AppTests.csproj": APP, "App/B.cs": "", "App/A.cs": "", "App/obj/X.cs": "", "AppTests/T.cs": ""},
    )
    ctx = context(tmp_path)
    assert dotnet.files(ctx) == [tmp_path / "App" / "A.cs", tmp_path / "App" / "B.cs", tmp_path / "AppTests" / "T.cs"]
    assert dotnet.sources(ctx) == [tmp_path / "App" / "A.cs", tmp_path / "App" / "B.cs"]


def test_in_scope(tmp_path: Path) -> None:
    paths = [tmp_path / "A.cs", tmp_path / "B.cs"]
    assert dotnet.in_scope(context(tmp_path), paths) is paths
    scoped = make_context(tmp_path, scope_changed=True, changed={"B.cs"})
    assert dotnet.in_scope(scoped, paths) == [tmp_path / "B.cs"]


def test_matching_lines(tmp_path: Path) -> None:
    write(tmp_path, {"A.cs": "a\nbad\nc\nbad here\n"})
    assert dotnet.matching_lines(tmp_path / "A.cs", lambda line: "bad" in line) == [2, 4]


def test_matching_lines_replaces_invalid_bytes(tmp_path: Path) -> None:
    path = tmp_path / "A.cs"
    path.write_bytes(b"ok\n\xffbad\n")
    assert dotnet.matching_lines(path, lambda line: "bad" in line) == [2]


@pytest.mark.parametrize(("root", "expected"), [(".", ""), ("src/cs", "src/cs/")])
def test_root_prefix(tmp_path: Path, root: str, expected: str) -> None:
    assert dotnet.root_prefix(context(tmp_path, root=root)) == expected


@pytest.mark.parametrize(
    ("relative", "expected"),
    [("cs/Gen", True), ("cs/Gen/A.cs", True), ("cs/Generated.cs", False), ("cs/Api/X.Dto.cs", True), ("cs/Api/X.cs", False)],
)
def test_coverage_excluded(tmp_path: Path, relative: str, expected: bool) -> None:
    ctx = context(tmp_path, root="cs", coverage_exclude=["/Gen/", "Api/*.Dto.cs"])
    assert dotnet.coverage_excluded(ctx, relative) is expected


def test_coverage_excluded_accepts_a_string(tmp_path: Path) -> None:
    ctx = context(tmp_path, coverage_exclude="Gen")
    assert dotnet.coverage_excluded(ctx, "Gen/A.cs") is True
    blank = context(tmp_path)
    assert dotnet.coverage_excluded(blank, "Gen/A.cs") is False
    assert dotnet.coverage_excluded(blank, "None") is False
    assert dotnet.coverage_excluded(blank, "/") is False


def test_mutation_patterns_fall_back_to_coverage(tmp_path: Path) -> None:
    assert dotnet.mutation_patterns(context(tmp_path, coverage_exclude=["Gen"])) == ["Gen"]
    assert dotnet.mutation_patterns(context(tmp_path, coverage_exclude=["Gen"], mutation_exclude="Mut")) == ["Mut"]


@pytest.mark.parametrize(
    ("relative", "expected"),
    [("cs/Gen/A.cs", True), ("cs/Mut/A.cs", True), ("cs/Other.cs", False), ("cs/Keep/A.cs", False)],
)
def test_mutation_excluded(tmp_path: Path, relative: str, expected: bool) -> None:
    ctx = context(tmp_path, root="cs", mutation_exclude=["cs/Gen", "/Mut/"], coverage_exclude=["Keep"])
    assert dotnet.mutation_excluded(ctx, relative) is expected


def test_load_coverage(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    assert dotnet.load_coverage(ctx) is None
    (ctx.work / dotnet.COVERAGE_JSON).write_text('{"files": {}}')
    assert dotnet.load_coverage(ctx) == {"files": {}}


def scanner_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    scan_dir = tmp_path / "scan"
    write(scan_dir, {"Program.cs": "class P {}", "Scan.csproj": APP})
    monkeypatch.setattr(dotnet, "SCAN_DIR", scan_dir)
    monkeypatch.setattr(dotnet, "HOST", {"dotnet": True})
    return scan_dir


def build_reply(dll: Path, code: int = 0) -> Callable[[list[str]], tuple[int, str]]:
    def reply(command: list[str]) -> tuple[int, str]:
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_text("dll")
        return code, "built"

    return reply


def test_scanner_project_falls_back_to_the_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "scan", {dotnet.PROGRAM_CS: "class P {}"})
    monkeypatch.setattr(dotnet, "SCAN_DIR", tmp_path / "scan")
    assert dotnet.scanner_project() == dotnet.SCAN_PROJECT
    assert dotnet.scanner_source() == tmp_path / "scan" / dotnet.PROGRAM_CS


def test_colocated_project_and_source() -> None:
    root = Path("/crate")
    assert dotnet.colocated(root / "Program.cs", root / "Scan.csproj") is True
    assert dotnet.colocated(root / "src" / "Program.cs", root / "Scan.csproj") is False


def test_scanner_sources_live_in_the_package() -> None:
    assert dotnet.SCAN_DIR == dotnet.PACKAGE / "cs" / "scan"
    assert (dotnet.SCAN_DIR / dotnet.PROGRAM_CS).is_file()
    assert (dotnet.SCAN_DIR / dotnet.PROJECT_FILE).is_file()


def test_build_scanner_stages_a_split_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    write(tmp_path / "scan", {dotnet.PROGRAM_CS: "class P {}"})
    write(tmp_path / "frozen", {dotnet.PROJECT_FILE: APP})
    monkeypatch.setattr(dotnet, "SCAN_DIR", tmp_path / "scan")
    monkeypatch.setattr(dotnet, "SCAN_PROJECT", tmp_path / "frozen" / dotnet.PROJECT_FILE)
    monkeypatch.setattr(dotnet, "HOST", {"dotnet": True})
    (tmp_path / "repo").mkdir()
    ctx = context(tmp_path / "repo")
    out = ctx.work / "cs-scan"
    fake = fake_run(dotnet, build_reply(out / dotnet.SCAN_DLL))
    assert dotnet.build_scanner(ctx) is None
    staged = ctx.work / dotnet.STAGE
    assert fake.calls[0][2] == str(staged / dotnet.PROJECT_FILE)
    assert (staged / dotnet.PROGRAM_CS).read_text() == "class P {}"


def test_build_scanner_builds_and_stamps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    scan_dir = scanner_dir(tmp_path, monkeypatch)
    (tmp_path / "repo").mkdir()
    ctx = context(tmp_path / "repo")
    out = ctx.work / "cs-scan"
    fake = fake_run(dotnet, build_reply(out / dotnet.SCAN_DLL))
    assert dotnet.build_scanner(ctx) is None
    assert fake.calls == [
        [
            "dotnet",
            "build",
            str(scan_dir / "Scan.csproj"),
            "-c",
            "Release",
            "-nologo",
            "-v",
            "q",
            f"-p:BaseIntermediateOutputPath={out}/obj/",
            f"-p:BaseOutputPath={out}/bin/",
            "-o",
            str(out),
        ]
    ]
    assert fake.options[0]["cwd"] is ctx.root
    assert fake.options[0]["timeout"] == 900
    assert (out / "stamp").read_text() == dotnet.scanner_digest()
    assert dotnet.build_scanner(ctx) is None
    assert len(fake.calls) == 1


def test_build_scanner_rebuilds_on_stale_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    scanner_dir(tmp_path, monkeypatch)
    ctx = context(tmp_path)
    out = ctx.work / "cs-scan"
    write(out, {dotnet.SCAN_DLL: "old", "stamp": "stale"})
    fake = fake_run(dotnet, [(0, "")])
    assert dotnet.build_scanner(ctx) is None
    assert len(fake.calls) == 1
    assert dotnet.scanner_current(out, "stale") is False


def test_build_scanner_reports_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    scanner_dir(tmp_path, monkeypatch)
    ctx = context(tmp_path)
    fake_run(dotnet, build_reply(ctx.work / "cs-scan" / dotnet.SCAN_DLL, code=1))
    assert dotnet.build_scanner(ctx) == "C# scanner build failed: built"
    assert not (ctx.work / "cs-scan" / "stamp").exists()


def test_build_scanner_reports_missing_dll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    scanner_dir(tmp_path, monkeypatch)
    fake_run(dotnet, [(0, " warn \n")])
    assert dotnet.build_scanner(context(tmp_path)) == "C# scanner build failed: warn"


def fresh_scanner(ctx: Any) -> None:
    write(ctx.work / "cs-scan", {dotnet.SCAN_DLL: "dll", "stamp": dotnet.scanner_digest()})


def test_scan_rejects_missing_context() -> None:
    with pytest.raises(TypeError, match=r"^ctx$"):
        dotnet.scan(None, "deps", [])  # type: ignore[arg-type]


def test_scan_returns_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    scanner_dir(tmp_path, monkeypatch)
    ctx = context(tmp_path)
    fresh_scanner(ctx)
    out = ctx.work / "cs-deps.json"
    out.write_text("stale")

    def reply(command: list[str]) -> tuple[int, str]:
        assert not out.exists()
        out.write_text(json.dumps({"edges": []}))
        return 0, ""

    fake = fake_run(dotnet, reply)
    paths = [tmp_path / "A.cs", tmp_path / "B.cs"]
    assert dotnet.scan(ctx, "deps", paths) == ({"edges": []}, None)
    listing = ctx.work / "cs-deps.txt"
    assert listing.read_text() == f"{tmp_path / 'A.cs'}\n{tmp_path / 'B.cs'}\n"
    dll = ctx.work / "cs-scan" / dotnet.SCAN_DLL
    assert fake.calls == [["dotnet", str(dll), "deps", "--root", str(tmp_path), "--out", str(out), f"@{listing}"]]
    assert fake.options[0]["timeout"] == 600
    assert fake.options[0]["cwd"] is tmp_path


def test_scan_reports_scanner_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    scanner_dir(tmp_path, monkeypatch)
    ctx = context(tmp_path)
    fresh_scanner(ctx)
    fake_run(dotnet, [(2, "crash")])
    assert dotnet.scan(ctx, "dead", []) == (None, "C# scanner failed (dead): crash")


def test_scan_reports_missing_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    scanner_dir(tmp_path, monkeypatch)
    ctx = context(tmp_path)
    fresh_scanner(ctx)
    fake_run(dotnet, [(127, "")])
    assert dotnet.scan(ctx, "dead", []) == (None, f"dotnet unavailable: {dotnet.INSTALL_HINT}")


def test_scan_reports_build_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    scanner_dir(tmp_path, monkeypatch)
    fake = fake_run(dotnet, [(127, "")])
    assert dotnet.scan(context(tmp_path), "deps", []) == (None, f"dotnet unavailable: {dotnet.INSTALL_HINT}")
    assert fake.calls[0][1] == "build"
    assert len(fake.calls) == 1
