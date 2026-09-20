import argparse
import io
import json
import os
import runpy
import sys
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import cli, depth, graph, install, route, runner
from marestail import config as config_module
from marestail import context as context_module
from marestail import gates as gates_module
from marestail.context import Context
from marestail.gates import Gate
from marestail.perf import db, samples
from marestail.report import Result
from marestail.sonar import setup
from tests.conftest import make_context

PASS = Result("lint", True, "clean")
FAIL = Result("tests", False, "2 failing")
Gates = tuple[list[Result], Context]


class Recorder:
    def __init__(self, reply: Any = 0) -> None:
        self.reply = reply
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((*args, *kwargs.items()))
        return self.reply


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "marestail.toml").write_text('[sonar]\nproject_key = "key"\n')
    monkeypatch.chdir(root)
    for name in ("MARESTAIL_FOCUS", "MARESTAIL_SCOPE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MARESTAIL_GATE_ACTIVE", "unset")
    monkeypatch.setattr(cli, "render", lambda results, line: f"render {[result.gate for result in results]} {line}")
    return root


@pytest.fixture
def gates(monkeypatch: pytest.MonkeyPatch, repo: Path) -> Callable[..., Recorder]:
    def install_gates(*results: Result, scoped: bool = False) -> Recorder:
        fake = Recorder((list(results), make_context(repo, scope_changed=scoped)))
        monkeypatch.setattr(cli, "run_gates_with_context", fake)
        return fake

    return install_gates


def stdin(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_main_forwards_route(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Recorder(127)
    monkeypatch.setattr(route, "command", fake)
    assert cli.main(["route", "--high", "-h"]) == 127
    assert fake.calls == [(["--high", "-h"],)]


def test_main_reads_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Recorder(5)
    monkeypatch.setattr(route, "command", fake)
    monkeypatch.setattr(sys, "argv", ["marestail", "route"])
    assert cli.main() == 5
    assert fake.calls == [([],)]


def test_script_exits_with_the_command_code(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Recorder(9)
    monkeypatch.setattr(route, "command", fake)
    monkeypatch.setattr(sys, "argv", ["cli.py", "route", "--high"])
    monkeypatch.setattr(sys, "path", list(sys.path))
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(cli.__file__, run_name="__main__")
    assert raised.value.code == 9
    assert fake.calls == [(["--high"],)]
    assert sys.path[0] == str(Path(cli.__file__).resolve().parent.parent)


def test_main_requires_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main([])
    assert raised.value.code == 2
    assert "the following arguments are required: command" in capsys.readouterr().err


def test_help_lists_commands_in_order() -> None:
    text = cli.build_parser().format_help()
    assert "{gate,run,install,sonar,watch,perf,route,graph,depth}" in text
    assert "print the subscription to use now: runs dandelion" in " ".join(text.split())


def test_graph_command(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(graph, "render", lambda config: f"graph of {config.root.name}")
    assert cli.main(["graph"]) == 0
    assert capsys.readouterr().out == "graph of repo\n"


def test_depth_command(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(depth, "analyse", lambda config: [config.root.name])
    monkeypatch.setattr(depth, "report", lambda modules: f"depth {modules}")
    assert cli.main(["depth"]) == 0
    assert capsys.readouterr().out == "depth ['repo']\n"


@pytest.mark.parametrize(("argv", "generated"), [(["install"], False), (["install", "sub", "--gitignore-generated"], True)])
def test_install_command(repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], generated: bool) -> None:
    fake = Recorder(None)
    monkeypatch.setattr(install, "install", fake)
    assert cli.main(argv) == 0
    target = repo / argv[1] if len(argv) > 1 else repo
    assert fake.calls == [(target, ("gitignore_generated", generated))]


@pytest.mark.parametrize("action", ["up", "down"])
def test_sonar_up_and_down(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    fakes = {name: Recorder(None) for name in ("up", "down", "setup")}
    for name, fake in fakes.items():
        monkeypatch.setattr(setup, name, fake)
    assert cli.main(["sonar", action]) == 0
    assert {name: fake.calls for name, fake in fakes.items()} == {"up": [], "down": [], "setup": [], action: [()]}


@pytest.mark.parametrize(
    ("toml", "expected"), [('[sonar]\nproject_key = "key"\n', ("key", "repo")), ("[sonar]\nproject_name = 'N'\n", (None, "N"))]
)
def test_sonar_setup(repo: Path, monkeypatch: pytest.MonkeyPatch, toml: str, expected: tuple[str | None, str]) -> None:
    (repo / "marestail.toml").write_text(toml)
    fake = Recorder(None)
    monkeypatch.setattr(setup, "setup", fake)
    assert cli.main(["sonar", "setup"]) == 0
    assert fake.calls == [expected]


def test_run_command(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Recorder(3)
    monkeypatch.setattr(runner, "run_pipeline", fake)
    argv = ["run", "t.md", "--from", "coder", "--to", "qa", "--auto", "--model", "m", "--retries", "2", "--agent", "grok"]
    assert cli.main([*argv, "--effort", "high", "--scope", "hard", "--focus", "a", "--focus", "b"]) == 3
    assert fake.calls == [(Path("t.md"), "coder", "qa", True, "m", 2, "grok", "high", "hard", ["a", "b"])]


def test_run_command_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Recorder(0)
    monkeypatch.setattr(runner, "run_pipeline", fake)
    assert cli.main(["run", "t.md"]) == 0
    assert fake.calls == [(Path("t.md"), None, None, False, None, 0, None, None, None, [])]


def fake_tui(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    fake = Recorder(4)
    app = types.ModuleType(cli.TUI_APP)
    vars(app)["run"] = fake
    monkeypatch.setitem(sys.modules, cli.TUI_APP, app)
    return fake


def test_watch_command_with_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fake_tui(monkeypatch)
    assert cli.main(["watch", "a", "b", "--refresh", "0.5", "--all"]) == 4
    assert fake.calls == [([Path("a"), Path("b")], 0.5, True)]


def test_watch_command_defaults_to_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = fake_tui(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "workspace").mkdir()
    assert cli.main(["watch"]) == 4
    assert fake.calls == [([tmp_path / "workspace"], 2.0, False)]


def test_default_watch_roots_fall_back_to_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert cli.default_watch_roots() == [tmp_path]


def test_perf_run_command(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Recorder(6)
    monkeypatch.setattr(samples, "run_command", fake)
    assert cli.main(["perf", "run", "perf/bench_x.py", "--tree", "base", "--samples", "3", "--db"]) == 6
    assert cli.main(["perf", "run", "perf/bench_y.py", "--tree", "head"]) == 6
    assert fake.calls == [("perf/bench_x.py", "base", 3, True), ("perf/bench_y.py", "head", 1, False)]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["golden", "--tree", "t", "--wait"], ("golden", "t", True)),
        (["golden", "--tree", "t"], ("golden", "t", False)),
        (["status"], ("status", None, False)),
        (["url", "--tree", "u"], ("url", "u", False)),
        (["prune"], ("prune", None, False)),
        (["down"], ("down", None, False)),
    ],
)
def test_perf_db_command(monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: tuple[str, str | None, bool]) -> None:
    fake = Recorder(7)
    monkeypatch.setattr(db, "command", fake)
    assert cli.main(["perf", "db", *argv]) == 7
    assert fake.calls == [expected]


def test_gate_rejects_focus_with_scope_all(gates: Callable[..., Recorder], capsys: pytest.CaptureFixture[str]) -> None:
    fake = gates(PASS)
    assert cli.main(["gate", "--scope", "all", "--focus", "src"]) == 2
    assert capsys.readouterr().err == "--focus cannot be combined with --scope all\n"
    assert fake.calls == []


def test_gate_reports_no_gates(gates: Callable[..., Recorder], capsys: pytest.CaptureFixture[str]) -> None:
    gates()
    assert cli.main(["gate", "--tier", "full", "--only", "x"]) == 2
    assert capsys.readouterr().err == "no gate ran: nothing in the full tier matches --only and marestail.toml\n"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["gate"], ("fast", False, None, set(), False)),
        (["gate", "--scope", "all", "--focus", " "], ("fast", False, None, set(), False)),
        (["gate", "--scope", "changed", "--tier", "sonar"], ("sonar", True, None, set(), False)),
        (["gate", "--scope", "hard", "--only", "a, b"], ("fast", True, {"a", "b"}, set(), True)),
        (["gate", "--focus", "src", "--focus", "src"], ("fast", True, None, {"src"}, False)),
    ],
)
def test_gate_passes_scope_to_the_gates(gates: Callable[..., Recorder], argv: list[str], expected: tuple[Any, ...]) -> None:
    fake = gates(PASS)
    assert cli.main(argv) == 0
    assert fake.calls == [expected]


def test_gate_renders_results(gates: Callable[..., Recorder], capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    gates(PASS, FAIL, scoped=True)
    assert cli.main(["gate"]) == 1
    assert capsys.readouterr().out == f"render ['lint', 'tests'] {make_context(repo, scope_changed=True).scope_summary()}\n"


def test_gate_prints_json(gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    gates(PASS)
    monkeypatch.setattr(cli, "to_json", lambda results, scope, focus: f"json {len(results)} {scope} {focus}")
    assert cli.main(["gate", "--json"]) == 0
    assert capsys.readouterr().out == "json 1 all set()\n"


@pytest.mark.parametrize(
    ("scope", "focus", "expected"),
    [(None, set(), False), ("all", set(), False), ("changed", set(), True), ("hard", set(), True), (None, {"a"}, True)],
)
def test_wants_changed(scope: str | None, focus: set[str], expected: bool) -> None:
    assert cli.wants_changed(scope, focus) is expected


def test_passed() -> None:
    assert cli.passed([PASS, PASS]) is True
    assert cli.passed([PASS, FAIL]) is False
    assert cli.passed([]) is True


def fake_gate(name: str, section: str | None, result: Result) -> Gate:
    return Gate(name, "fast", section, lambda ctx: result)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch, repo: Path) -> Recorder:
    (repo / "src").mkdir()
    (repo / "marestail.toml").write_text('[python]\n[focus]\npaths = ["src", "gone"]\n')
    selected = [fake_gate("lint", None, PASS), fake_gate("py", "python", FAIL), fake_gate("ts", "ts", FAIL)]
    select = Recorder(selected)
    monkeypatch.setattr(gates_module, "select", select)
    monkeypatch.setattr(context_module, "build", lambda config, changed, focus, hard: make_context(config.root, focus=focus, hard=hard))
    return select


def test_run_gates_with_context(registry: Recorder, repo: Path) -> None:
    results, ctx = gates_module.run_gates_with_context("full", True, {"lint"}, {"src"})
    assert (results, ctx.root, ctx.focus, ctx.hard) == ([PASS, FAIL], repo, {"src"}, False)
    assert registry.calls == [("full", {"lint"})]
    assert os.environ["MARESTAIL_GATE_ACTIVE"] == "true"


def test_run_gates_hard_adds_configured_focus(registry: Recorder, capsys: pytest.CaptureFixture[str]) -> None:
    _, ctx = gates_module.run_gates_with_context("fast", True, None, None, True)
    assert (ctx.focus, ctx.hard) == ({"src"}, True)
    assert capsys.readouterr().err == "warning: ignoring missing focus path: gone\n"
    assert gates_module.run_gates("fast", False, None) == [PASS, FAIL]


def test_resolve_focus(repo: Path) -> None:
    (repo / "src").mkdir()
    (repo / "b.py").write_text("")
    config = config_module.load(repo)
    assert context_module.resolve_focus(config, {" src ", str(repo / "b.py")}) == {"src", "b.py"}
    with pytest.raises(SystemExit) as raised:
        context_module.resolve_focus(config, {"src", "zz", "aa"})
    assert str(raised.value) == f"focus path not found under {repo}: aa, zz"


def test_locate_focus_outside_the_repo(repo: Path, tmp_path: Path) -> None:
    config = config_module.load(repo)
    assert context_module.locate_focus(config, str(tmp_path)) is None
    assert context_module.locate_focus(config, "..") is None
    assert context_module.locate_focus(config, "missing") is None
    assert context_module.locate_focus(config, ".") == "."


def test_hook_scope_reads_the_environment(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("a", "b"):
        (repo / name).mkdir()
    monkeypatch.setenv("MARESTAIL_FOCUS", os.pathsep.join(["a", "", "missing", str(repo / "b")]))
    monkeypatch.setenv("MARESTAIL_SCOPE", "hard")
    assert cli.hook_scope(config_module.load(repo)) == ({"a", "b"}, True)
    monkeypatch.setenv("MARESTAIL_SCOPE", "changed")
    assert cli.hook_scope(config_module.load(repo))[1] is False


def test_scope_line(repo: Path) -> None:
    assert cli.scope_line(make_context(repo)) is None
    assert cli.scope_line(make_context(repo, focus={"a"})) == make_context(repo, focus={"a"}).scope_summary()


def crashing(error: BaseException) -> Gate:
    def run(ctx: Context) -> Result:
        raise error

    return Gate("boom", "fast", None, run)


@pytest.mark.parametrize(
    ("error", "summary"),
    [
        (ValueError("bad\n  value " + "x" * 300), "boom crashed: ValueError bad value " + "x" * 190),
        (SystemExit("stop"), "boom crashed: SystemExit stop"),
    ],
)
def test_run_one_reports_crashes(repo: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException, summary: str) -> None:
    clock = iter([10.0, 12.5])
    monkeypatch.setattr(time, "time", lambda: next(clock))
    result = gates_module.run_one(crashing(error), make_context(repo))
    assert (result.gate, result.ok, result.summary, result.seconds) == ("boom", False, summary, 2.5)
    assert any(line.startswith(f"{type(error).__name__}: ") for line in result.findings)
    assert 1 < len(result.findings) <= 6


def test_run_one_returns_the_result(repo: Path) -> None:
    assert gates_module.run_one(fake_gate("lint", None, PASS), make_context(repo)) is PASS


def test_run_one_lets_interrupts_through(repo: Path) -> None:
    gate = crashing(KeyboardInterrupt())
    ctx = make_context(repo)
    with pytest.raises(KeyboardInterrupt):
        gates_module.run_one(gate, ctx)


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("", None), ("a", {"a"}), (" a , b,a", {"a", "b"})])
def test_parse_only(value: str | None, expected: set[str] | None) -> None:
    assert cli.parse_only(value) == expected


def counter(repo: Path, session: str) -> Path:
    return repo / ".marestail" / f"hook-{session}.count"


BLOCK = "render ['lint', 'tests'] None\nFix these before stopping.\n"


def test_hook_passing_claude_is_silent(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], repo: Path
) -> None:
    fake = gates(PASS)
    stdin(monkeypatch, "")
    counter(repo, "default").parent.mkdir(exist_ok=True)
    counter(repo, "default").write_text("2")
    assert cli.main(["gate", "--hook"]) == 0
    assert capsys.readouterr() == ("", "")
    assert not counter(repo, "default").exists()
    assert fake.calls == [("fast", True, None, set(), False)]


def test_hook_failing_claude_blocks(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], repo: Path
) -> None:
    gates(PASS, FAIL)
    stdin(monkeypatch, {"session_id": "s1", "cwd": str(repo)})
    assert cli.main(["gate", "--hook"]) == 2
    assert capsys.readouterr() == ("", BLOCK)
    assert counter(repo, "s1").read_text() == "1"


def test_hook_gives_up_after_the_limit(gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    gates(FAIL)
    counter(repo, "s").parent.mkdir(exist_ok=True)
    counter(repo, "s").write_text("4")
    stdin(monkeypatch, {"session_id": "s"})
    assert cli.main(["gate", "--hook"]) == 2
    assert counter(repo, "s").read_text() == "5"
    stdin(monkeypatch, {"session_id": "s"})
    assert cli.main(["gate", "--hook"]) == 0
    assert not counter(repo, "s").exists()


@pytest.mark.parametrize(("results", "reply"), [((PASS,), {}), ((PASS, FAIL), {"decision": "continue", "reason": BLOCK})])
def test_hook_for_agy(
    gates: Callable[..., Recorder],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo: Path,
    results: tuple[Result, ...],
    reply: dict[str, str],
) -> None:
    gates(*results)
    stdin(monkeypatch, {"conversationId": "c9", "workspacePaths": [str(repo / ".marestail")]})
    assert cli.main(["gate", "--hook"]) == 0
    assert json.loads(capsys.readouterr().out) == reply
    assert counter(repo, "c9").exists() is bool(reply)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"cwd": "a", "workspacePaths": ["b"]}, "a"),
        ({"workspacePaths": ["b", "c"]}, "b"),
        ({"workspacePaths": []}, None),
        ({"cwd": ""}, None),
    ],
)
def test_claude_root(repo: Path, payload: dict[str, Any], expected: str | None) -> None:
    assert cli.claude_root(payload) == (expected or repo)


def test_hook_verdict_honours_loop_count_and_status(gates: Callable[..., Recorder], repo: Path) -> None:
    gates(FAIL)
    config = config_module.load(repo)
    assert cli.hook_verdict(config, "a", loop_count=5) is None
    assert cli.hook_verdict(config, "b", loop_count=4, finished=False) is None
    assert not counter(repo, "b").exists()
    assert cli.hook_verdict(config, "c", loop_count=4) == "render ['tests'] None\nFix these before stopping.\n"
    assert counter(repo, "c").read_text() == "1"


def test_load_hook_config(repo: Path, tmp_path: Path) -> None:
    (repo / "sub").mkdir()
    config = cli.load_hook_config(str(repo / "sub"))
    assert config is not None
    assert config.root == repo
    assert cli.load_hook_config(tmp_path) is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"cursor_version": "1"}, True), ({"hook_event_name": "stop"}, True), ({"hook_event_name": "afterEdit"}, False), ({}, False)],
)
def test_is_cursor_hook(payload: dict[str, Any], expected: bool) -> None:
    assert cli.is_cursor_hook(payload) is expected


def cursor_hook(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: dict[str, Any]) -> str:
    stdin(monkeypatch, {"cursor_version": "1", **payload})
    assert cli.main(["gate", "--hook"]) == 0
    return capsys.readouterr().out


def test_cursor_hook_follows_up(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], repo: Path, tmp_path: Path
) -> None:
    gates(PASS, FAIL)
    seen: list[str] = []

    def hook_scope(config: Any) -> tuple[set[str], bool]:
        seen.append(os.getcwd())
        return set(), False

    monkeypatch.setattr(cli, "hook_scope", hook_scope)
    monkeypatch.chdir(tmp_path)
    output = cursor_hook(monkeypatch, capsys, {"workspace_roots": [str(repo)], "conversation_id": "k", "status": "completed"})
    assert json.loads(output) == {"followup_message": BLOCK}
    assert (seen, Path.cwd(), counter(repo, "k").read_text()) == ([str(repo)], tmp_path, "1")


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "aborted", "session_id": "q"},
        {"loop_count": "5", "session_id": "q"},
        {"hook_event_name": "stop", "session_id": "q", "status": ""},
    ],
)
def test_cursor_hook_allows(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], repo: Path, payload: dict[str, Any]
) -> None:
    gates(PASS if "hook_event_name" in payload else FAIL)
    assert json.loads(cursor_hook(monkeypatch, capsys, {"cwd": str(repo), **payload})) == {}
    assert not counter(repo, "q").exists()


@pytest.mark.parametrize("payload", [{"hook_event_name": "afterFileEdit"}, {"cwd": "/"}])
def test_cursor_hook_ignores(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: dict[str, Any]
) -> None:
    fake = gates(FAIL)
    assert cursor_hook(monkeypatch, capsys, payload) == ""
    assert fake.calls == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"workspace_roots": ["", "b"], "cwd": "c"}, ""), ({"workspace_roots": [], "cwd": "c"}, "c"), ({}, None)],
)
def test_cursor_root(repo: Path, payload: dict[str, Any], expected: str | None) -> None:
    assert cli.cursor_root(payload) == (repo if expected is None else expected)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"conversation_id": "a", "session_id": "b"}, "a"), ({"session_id": "b"}, "b"), ({"conversation_id": ""}, "default")],
)
def test_cursor_session(payload: dict[str, Any], expected: str) -> None:
    assert cli.cursor_session(payload) == expected


def grok_hook(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: dict[str, Any]) -> str:
    stdin(monkeypatch, {"hookEventName": "Stop", **payload})
    assert cli.main(["gate", "--hook"]) == 0
    return capsys.readouterr().out


def turn_file(repo: Path, session: str) -> Path:
    return repo / ".marestail" / f"hook-{session}.turn"


def test_grok_hook_blocks_and_replays(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], repo: Path
) -> None:
    fake = gates(PASS, FAIL)
    payload = {"cwd": str(repo), "sessionId": "g", "promptId": 7, "reason": "end_turn"}
    first = grok_hook(monkeypatch, capsys, payload)
    assert json.loads(first) == {"decision": "block", "reason": BLOCK}
    assert turn_file(repo, "g").read_text() == f"7\nblock\n{BLOCK}"
    assert grok_hook(monkeypatch, capsys, payload) == first
    assert len(fake.calls) == 1


def test_grok_hook_allows_and_remembers(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], repo: Path
) -> None:
    fake = gates(PASS)
    assert grok_hook(monkeypatch, capsys, {"workspaceRoot": str(repo), "promptId": "p"}) == ""
    assert turn_file(repo, "default").read_text() == "p\nallow\n"
    assert grok_hook(monkeypatch, capsys, {"workspaceRoot": str(repo), "promptId": "p"}) == ""
    assert len(fake.calls) == 1


def test_grok_hook_without_turn_id_forgets(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], repo: Path
) -> None:
    fake = gates(FAIL)
    assert json.loads(grok_hook(monkeypatch, capsys, {"sessionId": "n"}))["decision"] == "block"
    assert not turn_file(repo, "n").exists()
    assert grok_hook(monkeypatch, capsys, {"sessionId": "n"}) != ""
    assert len(fake.calls) == 2


@pytest.mark.parametrize("payload", [{"reason": "max_tokens"}, {"cwd": "/"}])
def test_grok_hook_ignores(
    gates: Callable[..., Recorder], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: dict[str, Any]
) -> None:
    fake = gates(FAIL)
    assert grok_hook(monkeypatch, capsys, payload) == ""
    assert fake.calls == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"cwd": "a", "workspaceRoot": "b"}, "a"), ({"workspaceRoot": "b"}, "b"), ({"cwd": ""}, None)],
)
def test_grok_root(repo: Path, payload: dict[str, Any], expected: str | None) -> None:
    assert cli.grok_root(payload) == (expected or repo)


@pytest.mark.parametrize(
    ("stamp", "turn", "expected"),
    [
        (None, "t", None),
        ("t\nallow\n", "", None),
        ("t\nallow\n", "u", None),
        ("t\nallow\n", "t", (True, "")),
        ("t\nblock\nfix\nit", "t", (False, "fix\nit")),
        ("t", "t", (False, "")),
    ],
)
def test_grok_replay_hook(tmp_path: Path, stamp: str | None, turn: str, expected: tuple[bool, str] | None) -> None:
    if stamp is not None:
        (tmp_path / "hook-s.turn").write_text(stamp)
    assert cli.grok_replay_hook(tmp_path, "s", turn) == expected


def test_grok_remember_hook(tmp_path: Path) -> None:
    work = tmp_path / "work"
    cli.grok_remember_hook(work, "s", "", False, "x")
    assert not work.exists()
    cli.grok_remember_hook(work, "s", "t", False, "fix")
    assert (work / "hook-s.turn").read_text() == "t\nblock\nfix"
    cli.grok_remember_hook(work, "s", "u", True, "ignored")
    assert (work / "hook-s.turn").read_text() == "u\nallow\n"


def test_sweep_counters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["hook-old.count", "hook-keep.count", "hook-new.count", "hook-edge.count", "hook-old.turn"]
    for name in names:
        (tmp_path / name).write_text("1")
        os.utime(tmp_path / name, (1000, 1000))
    os.utime(tmp_path / "hook-new.count", (90000, 90000))
    os.utime(tmp_path / "hook-edge.count", (13600, 13600))
    monkeypatch.setattr(time, "time", lambda: 100000.0)
    cli.sweep_counters(tmp_path, keep=tmp_path / "hook-keep.count")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["hook-edge.count", "hook-keep.count", "hook-new.count", "hook-old.turn"]


def test_blocked_count(tmp_path: Path) -> None:
    assert cli.blocked_count(tmp_path / "missing") == 0
    (tmp_path / "c").write_text("3")
    assert cli.blocked_count(tmp_path / "c") == 3


def subparsers_action(parser: argparse.ArgumentParser) -> Any:
    group = parser._subparsers
    assert group is not None
    action: Any = group._group_actions[0]
    return action


def subparser(name: str) -> argparse.ArgumentParser:
    chosen: argparse.ArgumentParser = subparsers_action(cli.build_parser()).choices[name]
    return chosen


def nested_parser(parent: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    chosen: argparse.ArgumentParser = subparsers_action(parent).choices[name]
    return chosen


def option_help(parser: argparse.ArgumentParser) -> dict[str, str | None]:
    return {flag: action.help for action in parser._actions for flag in action.option_strings}


def positional_help(parser: argparse.ArgumentParser) -> dict[str, str | None]:
    return {action.dest: action.help for action in parser._actions if not action.option_strings}


def parse_argv(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    parser.parse_args(argv)


def choice_help(parser: argparse.ArgumentParser) -> dict[str, str | None]:
    return {choice.dest: choice.help for choice in subparsers_action(parser)._choices_actions}


def test_root_parser_help_and_subcommands() -> None:
    parser = cli.build_parser()
    assert parser.prog == "marestail"
    assert parser.description == "deterministic gates for coding agents"
    action = subparsers_action(parser)
    assert action.dest == "command"
    assert action.required is True
    assert list(action.choices) == ["gate", "run", "install", "sonar", "watch", "perf", "route", "graph", "depth"]
    help_by_name = choice_help(parser)
    assert help_by_name["gate"] == "run the gates against the current repo"
    assert help_by_name["run"] == "run the role pipeline on a task"
    assert help_by_name["install"] == "install thin config into a target repo"
    assert help_by_name["sonar"] == "manage the local SonarQube"
    assert help_by_name["watch"] == "live TUI of every marestail pipeline on this machine"
    assert help_by_name["perf"] == "take performance samples during a perf run"
    assert help_by_name["route"] == "print the subscription to use now: runs dandelion route with the same arguments, e.g. --high"
    assert help_by_name["graph"] == "print the module dependency graph"
    assert help_by_name["depth"] == "print module interface width and depth"
    assert nested_parser(parser, "route").add_help is False


def test_gate_parser_defaults_and_help() -> None:
    parser = subparser("gate")
    args = parser.parse_args([])
    assert (args.tier, args.scope, args.focus, args.only, args.json, args.hook) == ("fast", None, [], None, False, False)
    helped = option_help(parser)
    assert helped["--tier"] is None
    assert helped["--scope"] == "all (default); changed: the diff against [git] base plus the focus paths; hard: only the focus paths"
    assert helped["--focus"] == "add a file or directory to the gate scope (repeatable); implies --scope changed"
    assert helped["--only"] == "comma separated gate names"
    assert helped["--json"] is None
    assert helped["--hook"] == "behave as a Claude Code Stop hook"
    tier = next(action for action in parser._actions if "--tier" in action.option_strings)
    assert list(tier.choices or []) == ["fast", "sonar", "full", "qa", "all"]
    focus = next(action for action in parser._actions if "--focus" in action.option_strings)
    assert focus.metavar == "PATH"


def test_run_parser_defaults_and_help() -> None:
    parser = subparser("run")
    args = parser.parse_args(["tasks/x.md"])
    assert args.task == "tasks/x.md"
    assert (args.start, args.stop, args.auto, args.retries, args.effort, args.agent, args.model) == (None, None, False, 0, None, None, None)
    helped = option_help(parser)
    assert helped["--auto"] == "skip the approval pause after the critic"
    assert helped["--scope"] == (
        "changed is a soft scope: gate the diff against [git] base plus the focus paths; workers may still edit any file, "
        "and it joins the diff. hard gates only the focus paths and tells every role to leave the rest alone apart from "
        "the smallest supporting edits"
    )
    assert next(action.dest for action in parser._actions if "--from" in action.option_strings) == "start"
    assert next(action.dest for action in parser._actions if "--to" in action.option_strings) == "stop"
    assert helped["--model"] == "the model, or dandelion/route or dandelion/route-best to ask dandelion before every session"
    assert helped["--retries"] == "attempts per role; 0 means unlimited (default)"
    assert (
        helped["--effort"]
        == "reasoning effort (claude and agy: low|medium|high|xhigh|max; grok: reasoning effort; kilo: variant); stamped on every commit"
    )
    assert helped["--agent"] == "agent backend (claude, agy, grok, cursor, kilo, or kimi)"
    retries = next(action for action in parser._actions if "--retries" in action.option_strings)
    assert (retries.metavar, retries.type) == ("N", int)
    agent = next(action for action in parser._actions if "--agent" in action.option_strings)
    assert list(agent.choices or []) == list(cli.AGENT_CHOICES)
    assert positional_help(parser)["task"] == cli.HELP_TASK
    scope = next(action for action in parser._actions if "--scope" in action.option_strings)
    assert list(scope.choices or []) == list(cli.SCOPE_CHOICES)


def test_watch_install_sonar_defaults() -> None:
    watch = subparser("watch").parse_args([])
    assert (watch.paths, watch.refresh, watch.all) == ([], 2.0, False)
    assert option_help(subparser("watch"))["--refresh"] == "seconds between redraws"
    assert option_help(subparser("watch"))["--all"] == "show every repo with a .marestail directory, not just those with a running pipeline"
    install_args = subparser("install").parse_args([])
    assert (install_args.target, install_args.gitignore_generated) == (".", False)
    assert option_help(subparser("install"))["--gitignore-generated"] == "add the files marestail generates to the target's .gitignore"
    sonar = subparser("sonar")
    action = next(item for item in sonar._actions if item.dest == "action")
    assert list(action.choices or []) == list(cli.SONAR_ACTIONS)
    assert positional_help(subparser("watch"))["paths"] == cli.HELP_WATCH_PATHS


def test_unknown_scope_is_rejected() -> None:
    parser = subparser("gate")
    with pytest.raises(SystemExit):
        parse_argv(parser, ["--scope", "nope"])


def test_unknown_tier_is_rejected() -> None:
    parser = subparser("gate")
    with pytest.raises(SystemExit):
        parse_argv(parser, ["--tier", "nope"])


def test_perf_parser_defaults_and_help() -> None:
    perf = subparser("perf")
    action = subparsers_action(perf)
    assert (action.dest, action.required) == ("perf_command", True)
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parse_argv(parser, ["perf"])
    assert choice_help(perf) == {
        "run": "take samples of one perf/bench_* script on one tree",
        "db": "manage the local performance database",
    }
    run = nested_parser(perf, "run").parse_args(["script.py", "--tree", "head"])
    assert (run.script, run.tree, run.samples, run.db) == ("script.py", "head", 1, False)
    assert option_help(nested_parser(perf, "run"))["--db"] == "reset the tree's performance database before every sample"
    database = nested_parser(perf, "db")
    names = list(subparsers_action(database).choices)
    assert names == ["golden", "status", "url", "prune", "down"]
    golden = nested_parser(database, "golden")
    assert option_help(golden)["--wait"] == "block until the build finishes instead of detaching"
    helped = choice_help(database)
    assert helped["status"] == "print the golden status of every tree in the perf run"
    assert helped["url"] == "print the database URL for one tree"
    assert helped["prune"] == "delete this repo's goldens the current perf run does not need"
    assert helped["down"] == "remove every performance database container, keeping the volume"
    assert helped["golden"] == "build the seeded golden data directory for one tree"


def require_tree(parser: argparse.ArgumentParser) -> None:
    assert next(action.required for action in parser._actions if "--tree" in action.option_strings) is True
    argv = parser_args_without_tree(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(argv)


def parser_args_without_tree(parser: argparse.ArgumentParser) -> list[str]:
    return ["script.py"] if any(action.dest == "script" for action in parser._actions) else []


def test_perf_tree_and_db_commands_are_required() -> None:
    perf = subparser("perf")
    require_tree(nested_parser(perf, "run"))
    database = nested_parser(perf, "db")
    assert subparsers_action(database).required is True
    with pytest.raises(SystemExit):
        parse_argv(database, [])
    require_tree(nested_parser(database, "golden"))
    require_tree(nested_parser(database, "url"))
