import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet
from marestail.gates import sonar
from tests.conftest import make_context

CREDS = {"url": "http://sonar:9000", "token": "tok"}
KEY = "proj"


def test_failed_analysis_tail_length() -> None:
    assert sonar.FAILED_TAIL == 15


Responder = Callable[[str, dict[str, Any]], dict[str, Any]]


class FakeClient:
    def __init__(self, respond: Responder) -> None:
        self.respond = respond
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.gets.append((path, params))
        return self.respond(path, params)

    def post(self, path: str, **params: Any) -> dict[str, Any]:
        self.posts.append((path, params))
        return {}


def healthy(path: str, params: dict[str, Any]) -> dict[str, Any]:
    replies: dict[str, dict[str, Any]] = {
        "api/ce/task": {"task": {"status": "SUCCESS"}},
        "api/qualitygates/project_status": {"projectStatus": {"status": "OK"}},
        "api/measures/component": {"component": {"measures": [{"metric": "coverage", "value": "100"}]}},
    }
    return replies.get(path, {})


def languages(distribution: str | None, coverage: bool) -> Responder:
    measures = [{"metric": "coverage", "value": "100"}] if coverage else []
    measures += [] if distribution is None else [{"metric": "ncloc_language_distribution", "value": distribution}]

    def respond(path: str, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("metricKeys") == "coverage,ncloc_language_distribution":
            return {"component": {"measures": measures}}
        return healthy(path, params)

    return respond


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("marestail.gates.sonar.time.sleep", slept.append)
    return slept


def install_client(monkeypatch: pytest.MonkeyPatch, respond: Responder) -> list[FakeClient]:
    made: list[FakeClient] = []

    def factory(url: str, token: str) -> FakeClient:
        made.append(FakeClient(respond))
        assert (url, token) == ("http://sonar:9000", "tok")
        return made[-1]

    monkeypatch.setattr(sonar, "credentials", lambda: dict(CREDS))
    monkeypatch.setattr(sonar, "Client", factory)
    return made


def scanner(root: Path, code: int = 0, task: str = "ceTaskId=T1\n") -> Callable[[list[str]], tuple[int, str]]:
    def reply(command: list[str]) -> tuple[int, str]:
        if command[0] == "git":
            return 1, ""
        (root / ".marestail" / "scannerwork").mkdir(parents=True, exist_ok=True)
        (root / ".marestail" / "scannerwork" / "report-task.txt").write_text(task)
        return code, "scan out\nscan end"

    return reply


def test_not_set_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sonar, "credentials", lambda: None)
    result = sonar.run_gate(make_context(tmp_path))
    assert (result.gate, result.ok, result.summary, result.findings, result.seconds) == (
        "sonar",
        False,
        "not set up",
        ["run: marestail sonar setup"],
        0.0,
    )


def test_scanner_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Any) -> None:
    install_client(monkeypatch, healthy)
    fake = fake_run(sonar, scanner(tmp_path, code=2))
    result = sonar.run_gate(make_context(tmp_path, {"sonar": {"project_key": KEY}}))
    assert (result.ok, result.summary, result.findings) == (False, "scanner failed", ["scan out", "scan end"])
    assert fake.calls[1][0] == "docker"
    assert fake.options[1] == {"cwd": tmp_path, "timeout": 1800}


def test_missing_task_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Any) -> None:
    install_client(monkeypatch, healthy)
    fake_run(sonar, scanner(tmp_path, task="other=1\n"))
    result = sonar.run_gate(make_context(tmp_path, {"sonar": {"project_key": KEY}}))
    task_file = tmp_path / ".marestail" / "scannerwork" / "report-task.txt"
    assert (result.ok, result.summary) == (False, "analysis did not complete")
    assert result.findings == [f"no ceTaskId in {task_file}", "scan out", "scan end"]


def test_clean_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Any, sleeps: list[float]) -> None:
    made = install_client(monkeypatch, healthy)
    fake_run(sonar, scanner(tmp_path))
    result = sonar.run_gate(make_context(tmp_path, {"sonar": {"project_key": KEY}}))
    assert (result.ok, result.summary, result.findings) == (True, "sonar clean", [])
    assert made[0].gets[0] == ("api/ce/task", {"id": "T1"})
    assert sleeps == []


def test_language_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Any) -> None:
    install_client(monkeypatch, languages("py=10", coverage=False))
    fake_run(sonar, scanner(tmp_path))
    raw = {"sonar": {"project_key": KEY}, "erlang": {}, "rust": {"root": "crate"}, "java": {}}
    result = sonar.run_gate(make_context(tmp_path, raw))
    assert result.findings == [
        ".:1 SonarQube received no Erlang lines (languages: py=10); run: marestail sonar setup",
        "marestail.toml:1 SonarQube imported no erlang coverage; run er.tests first so .marestail/eunit.coverdata exists",
        "crate:1 SonarQube received no Rust lines (languages: py=10); put the crate's src in sonar.sources",
        "sonar-project.properties:1 SonarQube imported no rust coverage; run rs.tests first and set sonar.rust.lcov.reportPaths=.marestail/rs-lcov.info",
        ".:1 SonarQube received no Java lines (languages: py=10); put the sources in sonar.sources",
        "marestail.toml:1 SonarQube imported no java coverage; run java.tests first so .marestail/java-jacoco.xml exists",
    ]
    assert (result.ok, result.summary) == (False, "6 sonar findings")


def test_languages_present(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"erlang": {}, "rust": {}, "java": {}})
    client: Any = FakeClient(languages("erlang=1;java=2;rust=3", coverage=True))
    assert sonar.language_findings(ctx, client, KEY) == []
    assert client.gets == [("api/measures/component", {"component": KEY, "metricKeys": "coverage,ncloc_language_distribution"})] * 3


def test_dotnet_language_check(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"dotnet": {"root": "app"}})
    (tmp_path / "app").mkdir()
    client: Any = FakeClient(languages("", coverage=True))
    assert sonar.language_findings(ctx, client, KEY) == ["app:1 SonarQube received no C# lines (languages: none)"]
    client = FakeClient(languages(None, coverage=False))
    assert sonar.language_findings(ctx, client, KEY) == [
        "app:1 SonarQube received no C# lines (languages: none)",
        "marestail.toml:1 SonarQube imported no coverage; run cs.tests first so its OpenCover report exists",
    ]
    empty: Any = FakeClient(lambda path, params: {})
    assert sonar.language_findings(ctx, empty, KEY)[0].startswith("app:1")


def test_dotnet_run_uses_its_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_client(monkeypatch, languages("cs=5", coverage=True))
    scans: list[tuple[Any, ...]] = []

    def fake_scan(ctx: Any, creds: dict[str, str], key: str) -> tuple[int, str]:
        scans.append((creds, key))
        report = tmp_path / ".sonarqube" / "out" / ".sonar"
        report.mkdir(parents=True)
        (report / "report-task.txt").write_text("ceTaskId=D1\n")
        return 0, ""

    monkeypatch.setattr(sonar, "dotnet_scan", fake_scan)
    result = sonar.run_gate(make_context(tmp_path, {"sonar": {"project_key": KEY}, "dotnet": {}}))
    assert (result.ok, result.summary) == (True, "sonar clean")
    assert scans == [(CREDS, KEY)]


def test_scanner_command(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(sonar, [(0, "")])
    root = str(tmp_path)
    assert sonar.scanner_command(make_context(tmp_path), CREDS, KEY) == [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "-u",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "SONAR_HOST_URL=http://sonar:9000",
        "-e",
        "SONAR_TOKEN=tok",
        "-e",
        f"SONAR_USER_HOME={root}/.marestail/sonar-cache",
        "-v",
        f"{root}:{root}",
        "-w",
        root,
        "sonarsource/sonar-scanner-cli",
        "-Dsonar.projectKey=proj",
        f"-Dsonar.projectBaseDir={root}",
        f"-Dsonar.working.directory={root}/.marestail/scannerwork",
        "-Dsonar.exclusions=perf/**",
    ]
    assert fake.calls == [["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]]
    assert fake.options == [{"cwd": tmp_path}]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((1, "/elsewhere/.git\n"), []),
        ((0, "\n"), []),
        ((0, "junk\n/{root}/.git\n"), []),
        ((0, "/shared/main/.git\n"), ["-v", "/shared/main/.git:/shared/main/.git:ro"]),
    ],
)
def test_git_mounts(tmp_path: Path, fake_run: Any, reply: tuple[int, str], expected: list[str]) -> None:
    fake_run(sonar, [(reply[0], reply[1].replace("/{root}", str(tmp_path)))])
    assert sonar.git_mounts(make_context(tmp_path)) == expected


def test_java_properties(tmp_path: Path) -> None:
    assert sonar.java_properties(make_context(tmp_path)) == []
    ctx = make_context(tmp_path, {"java": {"root": "svc", "build_dir": "out"}})
    assert sonar.java_properties(ctx) == [
        f"-Dsonar.java.binaries={tmp_path}/svc/out/classes",
        f"-Dsonar.coverage.jacoco.xmlReportPaths={tmp_path}/.marestail/java-jacoco.xml",
    ]
    (tmp_path / "sonar-project.properties").write_text("sonar.java.binaries=x\n")
    assert sonar.java_properties(ctx) == [f"-Dsonar.coverage.jacoco.xmlReportPaths={tmp_path}/.marestail/java-jacoco.xml"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "perf/**"),
        ("sonar.projectKey=x\n", "perf/**"),
        ("sonar.exclusions=a/**, ,b/**\nsonar.exclusions=c/**\n", "a/**,b/**,perf/**"),
        ("sonar.exclusions=perf/**,x\n", "perf/**,x"),
    ],
)
def test_scanner_exclusions(tmp_path: Path, text: str, expected: str) -> None:
    (tmp_path / "sonar-project.properties").write_text(text)
    assert sonar.scanner_exclusions(make_context(tmp_path)) == expected


def test_this_repo_scanner_exclusions_follow_the_properties_file() -> None:
    root = Path(__file__).resolve().parent.parent
    parts = sonar.scanner_exclusions(make_context(root)).split(",")
    assert "perf/**" in parts
    assert "marestail/tui/**" not in parts
    assert "marestail/**/*.java" not in parts


def test_properties_parsing() -> None:
    text = "# c=1\n! bang=2\n\n=novalue\na = 1\nb: two=2\nc=x:y\nlong=one,\\\n  two\nplain\n"
    assert sonar.properties(text) == [("a", "1"), ("b", "two=2"), ("c", "x:y"), ("long", "one,  two")]


def test_dotnet_refuses_properties_file(tmp_path: Path) -> None:
    (tmp_path / "sonar-project.properties").write_text("")
    assert sonar.dotnet_scan(make_context(tmp_path), CREDS, KEY) == (1, sonar.PROPERTIES_CONFLICT)
    assert sonar.PROPERTIES_CONFLICT.startswith("delete sonar-project.properties: the SonarScanner for .NET refuses")


def test_dotnet_project_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotnet, "projects", lambda ctx: (None, None, "set [dotnet] project"))
    assert sonar.dotnet_scan(make_context(tmp_path), CREDS, KEY) == (1, "set [dotnet] project")


def dotnet_calls(monkeypatch: pytest.MonkeyPatch, replies: list[tuple[int, str]]) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    pending = list(replies)

    def fake(ctx: Any, args: list[str], **options: Any) -> tuple[int, str]:
        calls.append((args, options))
        return pending.pop(0)

    monkeypatch.setattr(dotnet, "dotnet", fake)
    return calls


@pytest.mark.parametrize(("output", "expected"), [("tool broke", "tool broke"), ("Unable to find image x", None)])
def test_dotnet_tool_install_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, expected: str | None) -> None:
    product = tmp_path / "App.csproj"
    monkeypatch.setattr(dotnet, "projects", lambda ctx: (product, product, None))
    calls = dotnet_calls(monkeypatch, [(3, output)])
    assert sonar.dotnet_scan(make_context(tmp_path), CREDS, KEY) == (3, expected or dotnet.hint(3, output))
    tools = tmp_path / ".marestail" / "dotnet-tools"
    assert calls == [
        (
            ["tool", "install", "dotnet-sonarscanner", "--version", "11.3.0", "--tool-path", str(tools)],
            {"cwd": tmp_path, "network": True},
        )
    ]


def test_dotnet_scan_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    product = tmp_path / "App.csproj"
    monkeypatch.setattr(dotnet, "projects", lambda ctx: (product, product, None))
    calls = dotnet_calls(monkeypatch, [(0, "installed"), (0, "scanned")])
    (tmp_path / "One.sln").write_text("")
    ctx = make_context(tmp_path, {"sonar": {"project_name": "Nice"}})
    (tmp_path / ".marestail").mkdir()
    assert sonar.dotnet_scan(ctx, CREDS, KEY) == (0, "scanned")
    work = tmp_path / ".marestail"
    assert calls[1] == (
        [str(work / "sonar-dotnet.sh")],
        {"cwd": tmp_path, "timeout": 1800, "network": True, "extra": {"SONAR_TOKEN": "tok"}, "program": "sh"},
    )
    assert (work / "sonar-dotnet.sh").read_text() == "\n".join(
        [
            "set -e",
            f'export PATH="$PATH:{work}/dotnet-tools"',
            f'cd "{tmp_path}" && rm -rf .sonarqube',
            f'dotnet-sonarscanner begin /k:"proj" /n:"Nice" /s:"{work}/sonar-dotnet.xml" /d:sonar.host.url="http://sonar:9000" /d:sonar.token="$SONAR_TOKEN" /d:sonar.projectBaseDir="{tmp_path}"',
            f'dotnet build "{tmp_path}/One.sln" -t:Rebuild --nologo',
            'dotnet-sonarscanner end /d:sonar.token="$SONAR_TOKEN"',
            "",
        ]
    )


def test_dotnet_scan_builds_project_when_solutions_are_ambiguous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    product = tmp_path / "App.csproj"
    monkeypatch.setattr(dotnet, "projects", lambda ctx: (product, product, None))
    calls = dotnet_calls(monkeypatch, [(0, "scanned")])
    (tmp_path / ".marestail" / "dotnet-tools" / "dotnet-sonarscanner").mkdir(parents=True)
    (tmp_path / "A.sln").write_text("")
    (tmp_path / "B.sln").write_text("")
    assert sonar.dotnet_scan(make_context(tmp_path), CREDS, KEY) == (0, "scanned")
    script = (tmp_path / ".marestail" / "sonar-dotnet.sh").read_text()
    assert f'dotnet build "{product}" -t:Rebuild' in script
    assert '/n:"proj"' in script
    assert len(calls) == 1


def test_write_settings_minimal(tmp_path: Path) -> None:
    (tmp_path / ".marestail").mkdir()
    path = sonar.write_settings(make_context(tmp_path))
    work = tmp_path / ".marestail"
    assert path == work / "sonar-dotnet.xml"
    assert path.read_text() == "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8" ?>',
            '<SonarQubeAnalysisProperties xmlns="http://www.sonarsource.com/msbuild/integration/2015/1">',
            f'  <Property Name="sonar.exclusions">{",".join(sonar.DOTNET_EXCLUSIONS)}</Property>',
            f'  <Property Name="sonar.cs.opencover.reportsPaths">{work}/cs-tests/**/coverage.opencover.xml</Property>',
            '  <Property Name="sonar.scm.disabled">true</Property>',
            '  <Property Name="sonar.sourceEncoding">UTF-8</Property>',
            "</SonarQubeAnalysisProperties>",
            "",
        ]
    )


def test_write_settings_full(tmp_path: Path) -> None:
    (tmp_path / ".marestail").mkdir()
    (tmp_path / "app").mkdir()
    raw = {
        "sonar": {"exclusions": ["gen/**"]},
        "dotnet": {"root": "app", "coverage_exclude": ["/Migrations/", "Program.cs"]},
        "ts": {},
        "python": {},
    }
    text = sonar.write_settings(make_context(tmp_path, raw)).read_text()
    work = tmp_path / ".marestail"
    assert f'<Property Name="sonar.exclusions">{",".join(sonar.DOTNET_EXCLUSIONS)},gen/**</Property>' in text
    assert '<Property Name="sonar.coverage.exclusions">app/Migrations,app/Program.cs</Property>' in text
    assert text.endswith(
        f'  <Property Name="sonar.javascript.lcov.reportPaths">{work}/ts-coverage/lcov.info</Property>\n'
        f'  <Property Name="sonar.python.coverage.reportPaths">{work}/py-coverage.xml</Property>\n'
        "</SonarQubeAnalysisProperties>\n"
    )


def test_coverage_exclusions_at_root(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"dotnet": {"coverage_exclude": "Program.cs"}})
    assert sonar.coverage_exclusions(ctx) == "Program.cs"


@pytest.mark.parametrize(
    ("statuses", "expected", "slept"),
    [
        (["SUCCESS"], None, []),
        (["PENDING", "IN_PROGRESS", "FAILED"], "analysis FAILED", [5, 5]),
        (["CANCELED"], "analysis CANCELED", []),
        (["PENDING"] * 3, "analysis timed out", [5, 5, 5]),
    ],
)
def test_wait_for_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float], statuses: list[str], expected: str | None, slept: list[float]
) -> None:
    monkeypatch.setattr(sonar, "POLL_LIMIT", 3)
    task_file = tmp_path / "report-task.txt"
    task_file.write_text("projectKey=p\nceTaskId=abc=1\nnoise\n")
    pending = list(statuses)
    client: Any = FakeClient(lambda path, params: {"task": {"status": pending.pop(0)}})
    assert sonar.wait_for_analysis(client, task_file) == expected
    assert sleeps == slept
    assert client.gets[0] == ("api/ce/task", {"id": "abc=1"})


def test_wait_without_report(tmp_path: Path) -> None:
    client: Any = FakeClient(healthy)
    assert sonar.wait_for_analysis(client, tmp_path / "gone.txt") == f"no ceTaskId in {tmp_path / 'gone.txt'}"


def collect_responder(path: str, params: dict[str, Any]) -> dict[str, Any]:
    replies: dict[str, dict[str, Any]] = {
        "api/qualitygates/project_status": {"projectStatus": {"status": "ERROR"}},
        "api/hotspots/search": {"hotspots": [{"component": "p:src/a.py", "message": "look"}]},
        "api/measures/component": {
            "component": {"measures": [{"metric": "coverage"}, {"metric": "duplicated_lines_density", "value": "1.25"}]}
        },
        "api/measures/component_tree": {
            "components": [
                {"key": "p:src/a.py", "measures": [{"metric": "duplicated_lines_density", "value": "2"}]},
                {"path": "src/a.py", "measures": []},
                {"path": "src/b.py", "measures": [{"metric": "duplicated_lines_density", "value": "4"}]},
            ]
        },
    }
    if path == "api/issues/search":
        return {
            "issues": [
                {"key": "I1", "component": "p:src/a.py", "rule": "r1", "issueStatus": "ACCEPTED", "severity": "MAJOR", "message": "m"}
            ]
        }
    return replies[path]


def test_collect_unscoped() -> None:
    client: Any = FakeClient(collect_responder)
    findings, status = sonar.collect(make_context(Path("/tmp")), client, KEY)
    assert status == "ERROR"
    assert findings == [
        "src/a.py:0 r1 was marked ACCEPTED in Sonar instead of fixed; reopened. Fix the code, or a human adds an ignore rule to sonar-project.properties",
        "quality gate ERROR",
        "src/a.py:0 MAJOR r1: m",
        "src/a.py:0 hotspot: look",
        "sonar coverage 0.0% (need 100)",
        "sonar duplication 1.2% (need 0)",
    ]
    assert client.posts == [("api/issues/do_transition", {"issue": "I1", "transition": "reopen"})]
    assert ("api/issues/search", {"componentKeys": KEY, "issueStatuses": "ACCEPTED,FALSE_POSITIVE", "ps": 500}) in client.gets
    assert ("api/issues/search", {"componentKeys": KEY, "resolved": "false", "ps": 500}) in client.gets
    assert ("api/hotspots/search", {"project": KEY, "status": "TO_REVIEW", "ps": 500}) in client.gets
    assert client.gets[-1] == ("api/measures/component", {"component": KEY, "metricKeys": "coverage,duplicated_lines_density"})


def test_collect_scoped() -> None:
    client: Any = FakeClient(collect_responder)
    ctx = make_context(Path("/tmp"), scope_changed=True, changed={"src/b.py"})
    findings, status = sonar.collect(ctx, client, KEY)
    assert (findings, status) == (["src/b.py:1 sonar duplication 4.0% (need 0)"], "ERROR")
    assert client.posts == []
    assert client.gets[-1] == (
        "api/measures/component_tree",
        {"component": KEY, "metricKeys": "duplicated_lines_density", "qualifiers": "FIL", "ps": 500},
    )


def test_scoped_duplication_uses_key_when_path_missing() -> None:
    client: Any = FakeClient(collect_responder)
    ctx = make_context(Path("/tmp"), scope_changed=True, changed={"src/a.py"})
    assert sonar.scoped_duplication(ctx, client, KEY) == ["src/a.py:1 sonar duplication 2.0% (need 0)"]


def test_scoped_duplication_without_components() -> None:
    client: Any = FakeClient(lambda path, params: {})
    ctx = make_context(Path("/tmp"), scope_changed=True, changed={"src/a.py"})
    assert sonar.scoped_duplication(ctx, client, KEY) == []


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, []),
        ({"coverage": 100.0, "duplicated_lines_density": 0.0}, []),
        ({"coverage": 99.96}, ["sonar coverage 100.0% (need 100)"]),
    ],
)
def test_measure_findings(values: dict[str, float], expected: list[str]) -> None:
    assert sonar.measure_findings(values) == expected


def test_summarize_scoped(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"a"}, changed_lines_map={"a": {1, 2}})
    assert (
        sonar.summarize(ctx, ["x"], "ERROR") == "1 sonar findings in scope (global quality gate ERROR; scope: changed (1 files, 2 lines))"
    )
    assert sonar.summarize(make_context(tmp_path), [], "ERROR") == "sonar clean"


def test_sonar_constants() -> None:
    assert sonar.EQUALS == "="
    assert sonar.COLON == ":"
    assert sonar.SLASH == "/"
    assert sonar.COMMA == ","
    assert sonar.LINE == "line"
    assert sonar.COMPONENT == "component"
    assert sonar.PATH_KEY == "path"
    assert sonar.KEY == "key"
    assert sonar.EMPTY == ""
    assert sonar.EMPTY_LIST == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [("a=b", 1), ("a:b", 1), ("ab", -1), ("a=b:c", 1), (":x", 0), ("=x", 0)],
)
def test_separator_index(line: str, expected: int) -> None:
    assert sonar.separator_index(line) == expected


def test_issue_and_component_paths() -> None:
    assert sonar.issue_path({"component": "proj:src/A.cs"}) == "src/A.cs"
    assert sonar.issue_path({"component": "proj:src:A.cs"}) == "src:A.cs"
    assert sonar.issue_path({}) == ""
    assert sonar.component_path({"path": "src/A.cs"}) == "src/A.cs"
    assert sonar.component_path({"key": "proj:src/B.cs"}) == "src/B.cs"
    assert sonar.component_path({"key": "proj:src:B.cs"}) == "src:B.cs"
    assert sonar.component_path({}) == ""
    assert sonar.after_colon("proj:src:A.cs") == "src:A.cs"
    assert sonar.after_colon("leaf") == "leaf"
    assert sonar.COLON == ":"
    assert sonar.mapping_list({}, "issues") == []
    assert sonar.mapping_list({"issues": [{"k": 1}]}, "issues") == [{"k": 1}]
    assert sonar.line_of({"line": 7}) == 7
    assert sonar.line_of({}) == 0
    assert sonar.LINE == "line"
    client: Any = FakeClient(lambda path, params: {})
    issue = {"key": "I1", "component": "p:a.py", "rule": "r1", "issueStatus": "ACCEPTED", "line": 7}
    assert sonar.reopen(client, issue).startswith("a.py:7 ")
