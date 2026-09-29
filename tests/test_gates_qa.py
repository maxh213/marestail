import email.message
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from marestail.gates import _serve, qa
from marestail.report import Result
from tests.conftest import FakeRun, checked, make_context, untimed


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_skips_without_command(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(qa)
    assert untimed(qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "", "start": "python3 app.py"}})), "qa") == Result(
        "qa", True, "skipped: no [qa] cmd configured", [], 0.0
    )
    assert fake.calls == []
    assert not (tmp_path / ".marestail" / "qa-app.log").exists()


def test_passes(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(qa, [(0, "all\ngood\n")])
    result = checked(qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "make qa"}})), "qa")
    assert (result.gate, result.ok, result.summary, result.findings) == ("qa", True, "qa passed", [])
    assert fake.calls == [["bash", "-lc", "make qa"]]
    assert fake.options == [{"cwd": tmp_path / ".", "timeout": 3600}]
    assert result.seconds >= 0


def test_fails_with_tail_and_scope_note(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    output = "\n".join(f"line {number}" for number in range(50))
    fake = fake_run(qa, [(2, output)])
    ctx = make_context(tmp_path, {"qa": {"cmd": "npm test", "cwd": "web"}}, scope_changed=True)
    result = checked(qa.run_gate(ctx), "qa")
    assert (result.ok, result.summary) == (False, "qa failed (exit 2) (global gate — scope: changed)")
    assert result.findings == [f"line {number}" for number in range(10, 50)]
    assert fake.options[0]["cwd"] == tmp_path / "web"


def test_cmd_timeout_from_env(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_QA_CMD_TIMEOUT", "9")
    fake = fake_run(qa, [(0, "")])
    checked(qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "true"}})), "qa")
    assert fake.options[0]["timeout"] == 9


def test_cmd_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_QA_CMD_TIMEOUT", raising=False)
    assert qa._cmd_timeout() == 3600


def test_qa_env_and_empty(tmp_path: Path) -> None:
    assert qa._qa_env(make_context(tmp_path, {"qa": {}})) == {}
    assert qa._qa_env(make_context(tmp_path, {"qa": {"env": {"A": 1}}})) == {"A": "1"}


def test_app_log_path_bare_and_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_TASK", raising=False)
    assert qa._app_log_path(tmp_path) == tmp_path / ".marestail" / "qa-app.log"
    monkeypatch.setenv("MARESTAIL_TASK", "t")
    assert qa._app_log_path(tmp_path) == tmp_path / ".marestail" / "runs" / "t" / "qa-app.log"


def test_log_tail_missing_and_present(tmp_path: Path) -> None:
    missing = tmp_path / "gone.log"
    assert qa._log_tail(missing) == []
    path = write(tmp_path, "app.log", "\n".join(f"l{i}" for i in range(15)) + "\n")
    assert qa._log_tail(path) == [f"l{i}" for i in range(5, 15)]


def test_chosen_port_prefers_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_serve, "_can_bind", lambda port: port == 7)
    monkeypatch.setattr(_serve, "_free_port", lambda: 99)
    assert _serve._chosen_port(7) == 7
    assert _serve._chosen_port(8) == 99


def test_can_bind_and_free_port() -> None:
    port = _serve._free_port()
    assert _serve._can_bind(port) is True
    binder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    binder.bind(("127.0.0.1", 0))
    taken = binder.getsockname()[1]
    try:
        assert _serve._can_bind(taken) is False
    finally:
        binder.close()


def test_sockets_are_ipv4_streams_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.socket
    made: list[tuple[Any, ...]] = []
    bound: list[tuple[str, int]] = []

    class Recording(real):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any) -> None:
            made.append(args)
            super().__init__(*args)

        def bind(self, address: Any) -> None:
            bound.append(address)
            super().bind(address)

    monkeypatch.setattr(socket, "socket", Recording)
    port = _serve._free_port()
    assert _serve._can_bind(port) is True
    assert made == [(socket.AF_INET, socket.SOCK_STREAM)] * 2
    assert bound == [("127.0.0.1", 0), ("127.0.0.1", port)]


class Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", email.message.Message(), None)


@pytest.mark.parametrize(("status", "answers"), [(200, True), (499, True), (500, False), (503, False)])
def test_answers_by_status(monkeypatch: pytest.MonkeyPatch, status: int, answers: bool) -> None:
    calls: list[tuple[Any, ...]] = []

    def respond(*args: Any, **kwargs: Any) -> Response:
        calls.append((args, kwargs))
        return Response(status)

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    assert _serve._answers("http://localhost:9/up") is answers
    assert calls == [(("http://localhost:9/up",), {"timeout": 2})]


@pytest.mark.parametrize(("code", "answers"), [(404, True), (499, True), (500, False), (503, False)])
def test_answers_by_http_error(monkeypatch: pytest.MonkeyPatch, code: int, answers: bool) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise http_error(code)

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert _serve._answers("http://localhost/") is answers


def test_answers_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert _serve._answers("http://localhost/") is False


def test_exited_early() -> None:
    alive = MagicMock()
    alive.poll.return_value = None
    assert _serve._exited_early(alive) is None
    dead = MagicMock()
    dead.poll.return_value = 3
    assert _serve._exited_early(dead) == "app exited with 3 before answering"


def test_wait_ready_success(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    process.poll.return_value = None
    answers = MagicMock(return_value=True)
    stopped: list[Any] = []
    monkeypatch.setattr(_serve, "_answers", answers)
    monkeypatch.setattr(_serve, "_stop", stopped.append)
    assert _serve._wait_ready("http://x/", process, 1) is None
    answers.assert_called_once_with("http://x/")
    assert stopped == []


def test_wait_ready_polls_until_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    process.poll.return_value = None
    answers = MagicMock(return_value=False)
    naps: list[float] = []
    stopped: list[Any] = []
    monkeypatch.setattr(_serve, "_answers", answers)
    monkeypatch.setattr(time, "time", MagicMock(side_effect=[100, 100, 100.5, 101]))
    monkeypatch.setattr(time, "sleep", naps.append)
    monkeypatch.setattr(_serve, "_stop", stopped.append)
    assert _serve._wait_ready("http://x/", process, 1) == "app did not answer on http://x/ within 1s"
    assert answers.call_args_list == [call("http://x/"), call("http://x/")]
    assert naps == [0.2, 0.2]
    assert stopped == [process]


def test_wait_ready_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    monkeypatch.setattr(_serve, "_exited_early", lambda proc: "app exited with 1 before answering")
    assert _serve._wait_ready("http://x/", process, 5) == "app exited with 1 before answering"


def test_stop_already_dead() -> None:
    dead = MagicMock()
    dead.poll.return_value = 0
    _serve._stop(dead)
    dead.wait.assert_not_called()


def test_stop_group_already_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    live = MagicMock()
    live.poll.return_value = None
    live.pid = 123
    monkeypatch.setattr(os, "killpg", MagicMock(side_effect=ProcessLookupError))
    _serve._stop(live)
    live.wait.assert_not_called()


def recorded_kills(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: kills.append((pid, sig)))
    return kills


def test_stop_terminates_and_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    kills = recorded_kills(monkeypatch)
    live = MagicMock()
    live.poll.return_value = None
    live.pid = 7
    _serve._stop(live)
    assert kills == [(7, signal.SIGTERM)]
    live.wait.assert_called_once_with(timeout=15)


def test_stop_kills_when_wait_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    kills = recorded_kills(monkeypatch)
    stuck = MagicMock()
    stuck.poll.return_value = None
    stuck.pid = 7
    stuck.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=15)
    _serve._stop(stuck)
    assert kills == [(7, signal.SIGTERM), (7, signal.SIGKILL)]


def test_force_kill_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "killpg", MagicMock(side_effect=ProcessLookupError))
    _serve._force_kill(MagicMock(pid=4242))


@pytest.mark.parametrize("pid", [MagicMock(), 1, 0, -1])
def test_signal_group_refuses_init_and_everyone(monkeypatch: pytest.MonkeyPatch, pid: Any) -> None:
    kills = recorded_kills(monkeypatch)
    process = MagicMock(pid=pid)
    with pytest.raises(ProcessLookupError, match="refusing to signal process group"):
        _serve._signal_group(process, signal.SIGTERM)
    assert kills == []


def test_signal_group_signals_a_real_group(monkeypatch: pytest.MonkeyPatch) -> None:
    kills = recorded_kills(monkeypatch)
    _serve._signal_group(MagicMock(pid=2), signal.SIGTERM)
    assert kills == [(2, signal.SIGTERM)]


def test_stop_never_signals_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    kills = recorded_kills(monkeypatch)
    unnumbered = MagicMock()
    unnumbered.poll.return_value = None
    _serve._stop(unnumbered)
    assert kills == []


def test_spawn_sets_env_and_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return MagicMock()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    handle = (tmp_path / "log").open("w")
    try:
        _serve._spawn("echo hi", tmp_path, 3400, {"CMS_URL": "u", "PORT": "1"}, handle)
    finally:
        handle.close()
    assert seen["args"] == (["bash", "-lc", "echo hi"],)
    assert seen["kwargs"]["cwd"] == tmp_path
    assert seen["kwargs"]["start_new_session"] is True
    assert seen["kwargs"]["stdout"] is handle
    assert seen["kwargs"]["stderr"] == subprocess.STDOUT
    assert seen["kwargs"]["env"]["PORT"] == "3400"
    assert seen["kwargs"]["env"]["CMS_URL"] == "u"


def test_end_app_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = MagicMock()
    process = MagicMock()
    stopped: list[Any] = []
    monkeypatch.setattr(_serve, "_stop", stopped.append)
    _serve._end_app(process, handle)
    assert stopped == [process]
    handle.close.assert_called_once()
    _serve._end_app(None, handle)
    assert handle.close.call_count == 2


def test_run_with_app_failure_and_success(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    process.pid = 4242
    process.poll.return_value = None
    monkeypatch.setattr(_serve, "_chosen_port", lambda preferred: 3456)
    monkeypatch.setattr(_serve, "_stop", lambda proc: None)

    def write_log(start: str, cwd: Path, port: int, extra: dict[str, str], handle: Any) -> MagicMock:
        handle.write("one\ntwo\n")
        handle.flush()
        return process

    monkeypatch.setattr(_serve, "_spawn", write_log)
    monkeypatch.setattr(_serve, "_wait_ready", lambda url, proc, seconds: "app did not answer on http://localhost:3456/ within 2s")
    result = checked(
        qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "true", "start": "python3 app.py", "ready_timeout": 2}})),
        "qa",
    )
    assert result.ok is False
    assert result.summary == "qa: app did not answer on http://localhost:3456/ within 2s"
    assert result.findings == ["one", "two"]

    fake = fake_run(qa, [(0, "")])
    monkeypatch.setattr(_serve, "_spawn", lambda *a, **k: process)
    monkeypatch.setattr(_serve, "_wait_ready", lambda url, proc, seconds: None)
    result = checked(qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "true", "start": "python3 app.py"}})), "qa")
    assert result.ok is True
    assert result.summary == "qa passed"
    assert fake.options[0]["env"] == {"MARESTAIL_APP_URL": "http://localhost:3456", "PORT": "3456"}


def recorded_app(monkeypatch: pytest.MonkeyPatch, failure: str | None, port: int) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    @contextmanager
    def fake_ready_app(*args: Any) -> Iterator[tuple[str | None, int]]:
        calls.append(args)
        yield failure, port

    monkeypatch.setattr(_serve, "ready_app", fake_ready_app)
    return calls


def test_run_with_app_passes_defaults(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_TASK", raising=False)
    calls = recorded_app(monkeypatch, None, 3400)
    fake_run(qa, [(0, "")])
    checked(qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "true", "start": "yarn dev"}})), "qa")
    assert calls == [("yarn dev", tmp_path / ".", 3400, "/", 180, {}, tmp_path / ".marestail" / "qa-app.log")]


def test_run_with_app_passes_configured_keys(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_TASK", "t")
    calls = recorded_app(monkeypatch, None, 5001)
    fake = fake_run(qa, [(0, "")])
    raw = {
        "qa": {
            "cmd": "npx playwright test",
            "cwd": "web",
            "start": "yarn dev",
            "ready": "/health",
            "port": "5000",
            "ready_timeout": "7",
            "env": {"LOCALE": "en-gb"},
        }
    }
    checked(qa.run_gate(make_context(tmp_path, raw)), "qa")
    log_path = tmp_path / ".marestail" / "runs" / "t" / "qa-app.log"
    assert calls == [("yarn dev", tmp_path / "web", 5000, "/health", 7, {"LOCALE": "en-gb"}, log_path)]
    assert fake.calls == [["bash", "-lc", "npx playwright test"]]
    assert fake.options[0]["cwd"] == tmp_path / "web"
    assert fake.options[0]["env"] == {"MARESTAIL_APP_URL": "http://localhost:5001", "PORT": "5001"}


def test_run_with_app_not_ready_skips_cmd(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_TASK", raising=False)
    write(tmp_path, ".marestail/qa-app.log", "booting\n")
    recorded_app(monkeypatch, "app did not answer on http://localhost:3400/ within 180s", 3400)
    fake = fake_run(qa, [])
    result = checked(qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "true", "start": "yarn dev"}})), "qa")
    assert (result.ok, result.summary, result.findings) == (
        False,
        "qa: app did not answer on http://localhost:3400/ within 180s",
        ["booting"],
    )
    assert fake.calls == []


def test_run_cmd_and_qa_result(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(qa, [(1, "nope\n")])
    ctx = make_context(tmp_path, {"qa": {"cmd": "x"}})
    result = qa._run_cmd(ctx, "x", tmp_path, 0.0, {"MARESTAIL_APP_URL": "http://localhost:9", "PORT": "9"})
    assert result.ok is False
    assert "qa failed" in result.summary
    assert fake.options[0]["env"]["MARESTAIL_APP_URL"] == "http://localhost:9"


def test_qa_cwd(tmp_path: Path) -> None:
    assert qa._qa_cwd(make_context(tmp_path, {"qa": {"cwd": "web"}})) == tmp_path / "web"
    assert qa._qa_cwd(make_context(tmp_path, {"qa": {}})) == tmp_path / "."


@pytest.fixture
def bash_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    real = subprocess.Popen
    seen: list[list[str]] = []

    def popen(args: list[str], *rest: Any, **options: Any) -> Any:
        seen.append(list(args))
        return real(["sh", *args[1:]], *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", popen)
    return seen


def test_run_logged_merges_env_and_stderr_into_the_log(tmp_path: Path, bash_calls: list[list[str]]) -> None:
    log = tmp_path / "logs" / "setup.log"
    command = "echo out; echo err >&2; echo $RUN_LOGGED_PROBE; exit 3"
    assert _serve.run_logged(command, tmp_path, {"RUN_LOGGED_PROBE": "probe"}, log, 30) == 3
    assert log.read_text() == "out\nerr\nprobe\n"
    assert bash_calls == [["bash", "-lc", command]]


@pytest.mark.usefixtures("bash_calls")
def test_run_logged_leads_its_own_session(tmp_path: Path) -> None:
    log = tmp_path / "setup.log"
    command = f'exec {sys.executable} -c "import os; print(os.getsid(0) == os.getpid())"'
    assert _serve.run_logged(command, tmp_path, {}, log, 30) == 0
    assert log.read_text() == "True\n"


@pytest.mark.usefixtures("bash_calls")
def test_run_logged_times_out_and_kills_the_group(tmp_path: Path) -> None:
    log = tmp_path / "setup.log"
    pid_file = tmp_path / "pid"
    sleeper = f'{sys.executable} -c "import time; time.sleep(60)"'
    assert _serve.run_logged(f"{sleeper} & echo $! > {pid_file}; echo started; wait", tmp_path, {}, log, 1) is None
    assert log.read_text() == "started\n"
    pid = int(pid_file.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
