from collections.abc import Callable
from pathlib import Path

from marestail.gates import qa
from marestail.report import Result
from tests.conftest import FakeRun, make_context


def test_skips_without_command(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(qa)
    assert qa.run_gate(make_context(tmp_path, {"qa": {"cmd": ""}})) == Result("qa", True, "skipped: no [qa] cmd configured")
    assert fake.calls == []


def test_passes(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(qa, [(0, "all\ngood\n")])
    result = qa.run_gate(make_context(tmp_path, {"qa": {"cmd": "make qa"}}))
    assert (result.gate, result.ok, result.summary, result.findings) == ("qa", True, "qa passed", [])
    assert fake.calls == [["bash", "-lc", "make qa"]]
    assert fake.options == [{"cwd": tmp_path / ".", "timeout": 3600}]
    assert result.seconds >= 0


def test_fails_with_tail_and_scope_note(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    output = "\n".join(f"line {number}" for number in range(50))
    fake = fake_run(qa, [(2, output)])
    ctx = make_context(tmp_path, {"qa": {"cmd": "npm test", "cwd": "web"}}, scope_changed=True)
    result = qa.run_gate(ctx)
    assert (result.ok, result.summary) == (False, "qa failed (exit 2) (global gate — scope: changed)")
    assert result.findings == [f"line {number}" for number in range(10, 50)]
    assert fake.options[0]["cwd"] == tmp_path / "web"
