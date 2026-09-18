from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import erlang
from tests.conftest import FakeRun, make_context

HINT = "erlang unavailable: install Erlang/OTP 25+ (erl, erlc, escript), or docker with `docker pull erlang:27`"


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(erlang, "host_checks", {})
    monkeypatch.setattr("marestail.erlang.os.getuid", lambda: 1000)
    monkeypatch.setattr("marestail.erlang.os.getgid", lambda: 100)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, []), ("erl", ["erl"]), (["a", 2], ["a", "2"]), (7, ["7"]), ([], [])],
)
def test_listify(value: Any, expected: list[str]) -> None:
    assert erlang.listify(value) == expected


def test_host_erlang_probes_once(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, "")])
    ctx = make_context(tmp_path)
    assert erlang.host_erlang(ctx) is True
    assert erlang.host_erlang(ctx) is True
    assert fake.calls == [["erl", "-noshell", "-eval", "halt()."]]
    assert fake.options == [{"cwd": tmp_path, "timeout": 60}]


def test_host_erlang_missing(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, [(127, "erl: not found (x)")])
    assert erlang.host_erlang(make_context(tmp_path)) is False


def test_erlang_bin_configured(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang)
    ctx = make_context(tmp_path, {"erlang": {"erlc": ["docker", "exec", "erlc"]}})
    assert erlang.erlang_bin(ctx, tmp_path, "erlc") == ["docker", "exec", "erlc"]
    assert fake.calls == []


def test_erlang_bin_host(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, [(0, "")])
    assert erlang.erlang_bin(make_context(tmp_path), tmp_path, "erlc") == ["erlc"]


def test_erlang_bin_docker_mounts_marestail(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, [(1, "")])
    ctx = make_context(tmp_path, {"erlang": {"image": "erlang:26"}})
    mount = f"{erlang.MARESTAIL_ROOT}:{erlang.MARESTAIL_ROOT}"
    expected = ["docker", "run", "--rm", "--user", "1000:100", "-v", f"{tmp_path}:{tmp_path}", "-v", mount]
    assert erlang.erlang_bin(ctx, tmp_path / "w", "erlc") == [*expected, "-w", str(tmp_path / "w"), "erlang:26", "erlc"]


def test_erlang_bin_docker_inside_root(fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, [(1, "")])
    root = erlang.MARESTAIL_ROOT.parent
    command = erlang.erlang_bin(make_context(root), root, "escript")
    assert command == ["docker", "run", "--rm", "--user", "1000:100", "-v", f"{root}:{root}", "-w", str(root), "erlang:27", "escript"]


def test_escript_runs_script_in_erlang_root(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, ""), (0, "done")])
    ctx = make_context(tmp_path, {"erlang": {"root": "app"}})
    assert erlang.escript(ctx, "deps.escript", ["a.beam"]) == (0, "done")
    assert fake.calls[1] == ["escript", str(erlang.SCRIPT_DIR / "deps.escript"), "a.beam"]
    assert fake.options[1] == {"cwd": tmp_path / "app", "timeout": 600}


def test_erlc_uses_given_cwd_and_timeout(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, ""), (1, "bad")])
    ctx = make_context(tmp_path)
    assert erlang.erlc(ctx, ["-o", "x"], tmp_path / "sub", 5) == (1, "bad")
    assert fake.calls[1] == ["erlc", "-o", "x"]
    assert fake.options[1] == {"cwd": tmp_path / "sub", "timeout": 5}


def test_erlc_default_timeout(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, ""), (0, "")])
    erlang.erlc(make_context(tmp_path), [])
    assert fake.options[1] == {"cwd": tmp_path, "timeout": 900}


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (127, "erlc: not found (No such file)", HINT),
        (127, "\nerlc: not found (No such file)", None),
        (1, "erlc: not found (No such file)", None),
        (127, "", None),
        (127, "   ", None),
        (1, "Cannot connect to the Docker daemon at unix:///x", HINT),
        (125, "Unable to find image 'erlang:27' locally", "docker image missing: docker pull erlang:27"),
        (1, "src/a.erl:3: syntax error", None),
        (0, "", None),
    ],
)
def test_hint(code: int, output: str, expected: str | None) -> None:
    assert erlang.hint(code, output) == expected


@pytest.mark.parametrize(
    ("code", "output", "lines", "expected"),
    [
        (0, "fine", None, None),
        (127, "erlc: not found (x)", None, (HINT, [HINT])),
        (1, "a\n\nb\n", None, ("broke", ["a", "b"])),
        (2, "a\nb", ["z"], ("broke", ["z"])),
    ],
)
def test_trouble(code: int, output: str, lines: list[str] | None, expected: tuple[str, list[str]] | None) -> None:
    assert erlang.trouble(code, output, "broke", lines) == expected


def test_rel(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert erlang.rel(ctx, tmp_path / "src" / "a.erl") == "src/a.erl"
    assert erlang.rel(ctx, str(tmp_path / "b.erl")) == "b.erl"
    assert erlang.rel(ctx, "/elsewhere/c.erl") == "/elsewhere/c.erl"


def test_in_scope_findings(tmp_path: Path) -> None:
    findings = ["src/a.erl:3 bad", "src/b.erl:4 worse"]
    assert erlang.in_scope_findings(make_context(tmp_path), findings) == findings
    scoped = make_context(tmp_path, scope_changed=True, changed={"src/b.erl"})
    assert erlang.in_scope_findings(scoped, findings) == ["src/b.erl:4 worse"]


def test_fresh_dir(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    target.mkdir(parents=True)
    (target / "old").write_text("x")
    assert erlang.fresh_dir(target) == target
    assert list(target.iterdir()) == []


def test_compile_with_tests_success(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, ""), (0, ""), (0, "")])
    ebin, test_ebin = tmp_path / "e", tmp_path / "t"
    result = erlang.compile_with_tests(make_context(tmp_path), [Path("a.erl")], [Path("a_tests.erl")], ebin, test_ebin)
    assert result is None
    assert fake.calls[1:] == [
        ["erlc", "+debug_info", "-o", str(ebin), "a.erl"],
        ["erlc", "-DTEST", "+debug_info", "-pa", str(ebin), "-o", str(test_ebin), "a_tests.erl"],
    ]
    assert fake.options[2]["timeout"] == 900
    assert ebin.is_dir()
    assert test_ebin.is_dir()


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        ([(0, ""), (127, "erlc: not found (x)")], (HINT, [HINT])),
        ([(0, ""), (1, "src/a.erl:1: oops")], ("sources failed to compile", ["src/a.erl:1: oops"])),
        ([(0, ""), (0, ""), (1, "test/a.erl:1: oops")], ("tests failed to compile", ["test/a.erl:1: oops"])),
    ],
)
def test_compile_with_tests_failures(
    tmp_path: Path, fake_run: Callable[..., FakeRun], replies: list[tuple[int, str]], expected: tuple[str, list[str]]
) -> None:
    fake_run(erlang, replies)
    ctx = make_context(tmp_path)
    assert erlang.compile_with_tests(ctx, [Path("a.erl")], [Path("t.erl")], tmp_path / "e", tmp_path / "t") == expected


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


def test_source_and_test_files(tmp_path: Path) -> None:
    for name in ["src/a.erl", "src/deep/b.erl", "src/a_tests.erl", "src/_build/c.erl", "src/x.hrl", "test/t_SUITE.erl", "tests/u.erl"]:
        touch(tmp_path / name)
    touch(tmp_path / "test" / "node_modules" / "n.erl")
    ctx = make_context(tmp_path)
    assert erlang.source_files(ctx) == [tmp_path / "src/a.erl", tmp_path / "src/deep/b.erl"]
    assert erlang.test_files(ctx) == [tmp_path / "src/a_tests.erl", tmp_path / "test/t_SUITE.erl", tmp_path / "tests/u.erl"]


def test_configured_dirs(tmp_path: Path) -> None:
    touch(tmp_path / "app" / "lib" / "m.erl")
    touch(tmp_path / "app" / "lib" / "m_tests.erl")
    touch(tmp_path / "app" / "spec" / "s.erl")
    touch(tmp_path / "app" / "src" / "ignored.erl")
    ctx = make_context(tmp_path, {"erlang": {"root": "app", "sources": "lib", "test_dirs": ["spec", "missing"]}})
    assert erlang.source_dirs(ctx) == [tmp_path / "app" / "lib"]
    assert erlang.test_dirs(ctx) == [tmp_path / "app" / "spec", tmp_path / "app" / "missing"]
    assert erlang.source_files(ctx) == [tmp_path / "app" / "lib" / "m.erl"]
    assert erlang.test_files(ctx) == [tmp_path / "app" / "lib" / "m_tests.erl", tmp_path / "app" / "spec" / "s.erl"]


def test_generated(tmp_path: Path) -> None:
    assert erlang.generated(tmp_path, tmp_path / ".git" / "a.erl") is True
    assert erlang.generated(tmp_path, tmp_path / "src" / "a.erl") is False


def test_found_skips_files_and_missing(tmp_path: Path) -> None:
    touch(tmp_path / "plain.erl")
    assert erlang.found([tmp_path / "plain.erl", tmp_path / "nope"], "*.erl") == []
