import signal
import socket
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from marestail.gates import qa
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
    assert qa.cmd_timeout() == 3600


def test_qa_env_and_empty(tmp_path: Path) -> None:
    assert qa.qa_env(make_context(tmp_path, {"qa": {}})) == {}
    assert qa.qa_env(make_context(tmp_path, {"qa": {"env": {"A": 1}}})) == {"A": "1"}


def test_app_log_path_bare_and_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_TASK", raising=False)
    assert qa.app_log_path(tmp_path) == tmp_path / ".marestail" / "qa-app.log"
    monkeypatch.setenv("MARESTAIL_TASK", "t")
    assert qa.app_log_path(tmp_path) == tmp_path / ".marestail" / "runs" / "t" / "qa-app.log"


def test_log_tail_missing_and_present(tmp_path: Path) -> None:
    missing = tmp_path / "gone.log"
    assert qa.log_tail(missing) == []
    path = write(tmp_path, "app.log", "\n".join(f"l{i}" for i in range(15)) + "\n")
    assert qa.log_tail(path) == [f"l{i}" for i in range(5, 15)]


def test_chosen_port_prefers_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa, "can_bind", lambda port: port == 7)
    monkeypatch.setattr(qa, "free_port", lambda: 99)
    assert qa.chosen_port(7) == 7
    assert qa.chosen_port(8) == 99


def test_can_bind_and_free_port() -> None:
    port = qa.free_port()
    assert qa.can_bind(port) is True
    binder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    binder.bind(("127.0.0.1", 0))
    taken = binder.getsockname()[1]
    try:
        assert qa.can_bind(taken) is False
    finally:
        binder.close()


def test_answers_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class Ok:
        status = 200

        def __enter__(self) -> "Ok":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Ok())
    assert qa.answers("http://localhost/") is True


def test_answers_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError("u", 503, "x", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert qa.answers("http://localhost/") is False

    def client(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError("u", 404, "x", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", client)
    assert qa.answers("http://localhost/") is True


def test_answers_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert qa.answers("http://localhost/") is False


def test_exited_early() -> None:
    alive = MagicMock()
    alive.poll.return_value = None
    assert qa.exited_early(alive) is None
    dead = MagicMock()
    dead.poll.return_value = 3
    assert qa.exited_early(dead) == "qa: app exited with 3 before answering"


def test_wait_ready_success_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    process.poll.return_value = None
    monkeypatch.setattr(qa, "answers", lambda url: True)
    assert qa.wait_ready("http://x/", process, 1) is None
    monkeypatch.setattr(qa, "answers", lambda url: False)
    monkeypatch.setattr(qa.time, "time", MagicMock(side_effect=[0, 0, 10]))
    monkeypatch.setattr(qa, "stop", lambda proc: None)
    assert qa.wait_ready("http://x/", process, 1) == "qa: app did not answer on http://x/ within 1s"


def test_wait_ready_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    monkeypatch.setattr(qa, "exited_early", lambda proc: "qa: app exited with 1 before answering")
    assert qa.wait_ready("http://x/", process, 5) == "qa: app exited with 1 before answering"


def test_stop_already_dead_and_force(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = MagicMock()
    dead.poll.return_value = 0
    qa.stop(dead)
    dead.wait.assert_not_called()

    live = MagicMock()
    live.poll.return_value = None
    live.pid = 123
    monkeypatch.setattr(qa.os, "killpg", MagicMock(side_effect=ProcessLookupError))
    qa.stop(live)

    again = MagicMock()
    again.poll.return_value = None
    again.pid = 7
    again.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=1)
    kills: list[int] = []

    def killpg(pid: int, sig: int) -> None:
        kills.append(sig)
        if sig == signal.SIGKILL:
            raise ProcessLookupError

    monkeypatch.setattr(qa.os, "killpg", killpg)
    qa.stop(again)
    assert signal.SIGTERM in kills
    assert signal.SIGKILL in kills


def test_force_kill_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa.os, "killpg", MagicMock(side_effect=ProcessLookupError))
    qa.force_kill(MagicMock(pid=1))


def test_spawn_sets_env_and_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return MagicMock()

    monkeypatch.setattr(qa.subprocess, "Popen", fake_popen)
    handle = (tmp_path / "log").open("w")
    try:
        qa.spawn("echo hi", tmp_path, 3400, {"CMS_URL": "u"}, handle)
    finally:
        handle.close()
    assert seen["args"][0] == ["bash", "-lc", "echo hi"]
    assert seen["kwargs"]["cwd"] == tmp_path
    assert seen["kwargs"]["start_new_session"] is True
    assert seen["kwargs"]["env"]["PORT"] == "3400"
    assert seen["kwargs"]["env"]["CMS_URL"] == "u"


def test_end_app_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = MagicMock()
    process = MagicMock()
    stopped: list[Any] = []
    monkeypatch.setattr(qa, "stop", stopped.append)
    qa.end_app(process, handle)
    assert stopped == [process]
    handle.close.assert_called_once()
    qa.end_app(None, handle)
    assert handle.close.call_count == 2


def test_run_with_app_failure_and_success(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    process.pid = 1
    process.poll.return_value = None
    monkeypatch.setattr(qa, "chosen_port", lambda preferred: 3456)
    monkeypatch.setattr(qa, "stop", lambda proc: None)

    def write_log(start: str, cwd: Path, port: int, extra: dict[str, str], handle: Any) -> MagicMock:
        handle.write("one\ntwo\n")
        handle.flush()
        return process

    monkeypatch.setattr(qa, "spawn", write_log)
    monkeypatch.setattr(qa, "wait_ready", lambda url, proc, seconds: "qa: app did not answer on http://localhost:3456/ within 2s")
    result = checked(
        qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "true", "start": "python3 app.py", "ready_timeout": 2}})),
        "qa",
    )
    assert result.ok is False
    assert result.summary == "qa: app did not answer on http://localhost:3456/ within 2s"
    assert result.findings == ["one", "two"]

    fake = fake_run(qa, [(0, "")])
    monkeypatch.setattr(qa, "spawn", lambda *a, **k: process)
    monkeypatch.setattr(qa, "wait_ready", lambda url, proc, seconds: None)
    result = checked(qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "true", "start": "python3 app.py"}})), "qa")
    assert result.ok is True
    assert result.summary == "qa passed"
    assert fake.options[0]["env"] == {"MARESTAIL_APP_URL": "http://localhost:3456", "PORT": "3456"}


def test_run_cmd_and_qa_result(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(qa, [(1, "nope\n")])
    ctx = make_context(tmp_path, {"qa": {"cmd": "x"}})
    result = qa.run_cmd(ctx, "x", tmp_path, 0.0, {"MARESTAIL_APP_URL": "http://localhost:9", "PORT": "9"})
    assert result.ok is False
    assert "qa failed" in result.summary
    assert fake.options[0]["env"]["MARESTAIL_APP_URL"] == "http://localhost:9"


def test_qa_cwd(tmp_path: Path) -> None:
    assert qa.qa_cwd(make_context(tmp_path, {"qa": {"cwd": "web"}})) == tmp_path / "web"
    assert qa.qa_cwd(make_context(tmp_path, {"qa": {}})) == tmp_path / "."
