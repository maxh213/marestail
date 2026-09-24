import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import runner
from marestail.config import Config
from marestail.runner import Run

AGENT_VARS = (
    "MARESTAIL_AGENT",
    "MARESTAIL_CLAUDE",
    "MARESTAIL_AGY",
    "MARESTAIL_CURSOR",
    "MARESTAIL_GROK",
    "MARESTAIL_GROK_EFFORT",
    "MARESTAIL_KILO",
    "MARESTAIL_KILO_VARIANT",
    "MARESTAIL_KIMI",
    "MARESTAIL_JUNIE",
)
CLAUDE_BASE = ["claude", "-p", "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions", "--output-format", "json"]
KILO_BASE = ["kilo", "run", "--auto", "--format", "json", "--log-level", "ERROR", "--model"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AGENT_VARS:
        monkeypatch.delenv(name, raising=False)


def make_state(root: Path = Path("/work"), raw: dict[str, Any] | None = None, **fields: Any) -> Run:
    base: dict[str, Any] = {"model": None, "retries": 1}
    return Run(config=Config(root=root, raw=raw or {}), task=Path("/work/task.md"), **{**base, **fields})


@pytest.mark.parametrize(
    ("fields", "raw", "env", "expected"),
    [
        ({"agent": "Grok"}, {"agent": {"backend": "kimi"}}, "kilo", "grok"),
        ({}, {"agent": {"backend": "kimi"}}, "Kilo", "kilo"),
        ({}, {"agent": {"backend": "Kimi"}}, "", "kimi"),
        ({}, {}, "", "claude"),
    ],
)
def test_resolve_agent(monkeypatch: pytest.MonkeyPatch, fields: dict[str, Any], raw: dict[str, Any], env: str, expected: str) -> None:
    monkeypatch.setenv("MARESTAIL_AGENT", env)
    assert runner.resolve_agent(make_state(raw=raw, **fields)) == expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"model": "opus", "effort": "high"}, "opus high"),
        ({"route": "dandelion/route"}, "dandelion/route"),
        ({"route": "dandelion/route", "agent": "claude", "effort": "low"}, "dandelion/route low"),
        ({"agent": "kilo"}, f"{runner.KILO_DEFAULT_MODEL} high"),
        ({"agent": "kilo", "model": "other"}, "other"),
        ({"agent": "grok", "effort": "max"}, "grok max"),
        ({"agent": "grok"}, "grok"),
        ({}, "claude"),
    ],
)
def test_agent_label(fields: dict[str, Any], expected: str) -> None:
    assert runner.agent_label(make_state(**fields)) == expected


def test_backend_effort_for_grok_and_others(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_GROK_EFFORT", "low")
    grok = make_state(agent="grok", effort="")
    other = make_state(agent="claude", effort="")
    assert runner.backend_effort(grok) == "low"
    assert runner.backend_effort(other) == ""


def test_agent_env_for_claude_and_others() -> None:
    claude = make_state(account_env={"A": "1"})
    other = make_state(agent="grok", account_env={"A": "1"})
    assert runner.agent_env(claude) == {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0", "A": "1"}
    assert runner.agent_env(other) == {"A": "1"}
    assert runner.agent_env(other) is not other.account_env


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, CLAUDE_BASE),
        ({"model": "m", "effort": "e"}, [*CLAUDE_BASE, "--model", "m", "--effort", "e"]),
        (
            {"agent": "agy", "model": "m", "effort": "e"},
            ["agy", "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "4h", "--model", "m", "--effort", "e"],
        ),
        ({"agent": "agy"}, ["agy", "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "4h"]),
        (
            {"agent": "cursor", "model": "m", "effort": "e"},
            ["cursor-agent", "-p", "--output-format", "json", "--force", "--trust", "--sandbox", "disabled", "--model", "m"],
        ),
        ({"agent": "kilo"}, [*KILO_BASE, runner.KILO_DEFAULT_MODEL, "--variant", "high"]),
        (
            {"agent": "junie", "model": "m", "effort": "high"},
            ["junie", "--skip-update-check", "--input-format=json", "--output-format=json", "-p", "/work", "--model=m", "--effort=high"],
        ),
        ({"agent": "junie"}, ["junie", "--skip-update-check", "--input-format=json", "--output-format=json", "-p", "/work"]),
    ],
)
def test_agent_command(fields: dict[str, Any], expected: list[str]) -> None:
    assert runner.agent_command(make_state(**fields)) == expected


def test_agent_command_binaries_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_CLAUDE", "/bin/c")
    monkeypatch.setenv("MARESTAIL_AGY", "/bin/a")
    monkeypatch.setenv("MARESTAIL_CURSOR", "/bin/u")
    monkeypatch.setenv("MARESTAIL_JUNIE", "/bin/j")
    assert runner.agent_command(make_state())[0] == "/bin/c"
    assert runner.agent_command(make_state(agent="agy"))[0] == "/bin/a"
    assert runner.agent_command(make_state(agent="cursor"))[0] == "/bin/u"
    assert runner.agent_command(make_state(agent="junie"))[0] == "/bin/j"


def test_grok_command(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = Path("/work/p.md")
    base = ["grok", "--prompt-file", "/work/p.md", "--output-format", "json", "--always-approve", "--no-plan", "--trust"]
    assert runner.grok_command(make_state(), prompt) == base
    assert runner.grok_command(make_state(model="m", effort="e"), prompt) == [*base, "--model", "m", "--reasoning-effort", "e"]
    monkeypatch.setenv("MARESTAIL_GROK", "/bin/g")
    monkeypatch.setenv("MARESTAIL_GROK_EFFORT", "low")
    assert runner.grok_command(make_state(), prompt) == ["/bin/g", *base[1:], "--reasoning-effort", "low"]


@pytest.mark.parametrize(
    ("fields", "env", "expected"),
    [
        ({"effort": ""}, "low", None),
        ({}, None, "high"),
        ({"model": runner.KILO_DEFAULT_MODEL}, None, "high"),
        ({"model": "other"}, None, None),
        ({}, "low", "low"),
        ({}, "", None),
        ({"effort": "max"}, "low", "max"),
    ],
)
def test_kilo_variant(monkeypatch: pytest.MonkeyPatch, fields: dict[str, Any], env: str | None, expected: str | None) -> None:
    if env is not None:
        monkeypatch.setenv("MARESTAIL_KILO_VARIANT", env)
    assert runner.kilo_variant(make_state(**fields)) == expected


def test_kilo_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_KILO", "/bin/k")
    assert runner.kilo_command(make_state(model="m", effort="")) == ["/bin/k", *KILO_BASE[1:], "m"]


def test_kimi_command(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = Path("/work/p.md")
    expected_prompt = "Your instructions are in /work/p.md. Read that whole file first, then follow it exactly."
    assert runner.kimi_prompt(prompt) == expected_prompt
    assert runner.kimi_command(make_state(), prompt) == ["kimi", "-p", expected_prompt, "--output-format", "stream-json"]
    monkeypatch.setenv("MARESTAIL_KIMI", "/bin/k")
    assert runner.kimi_command(make_state(model="m"), prompt)[-2:] == ["-m", "m"]
    assert runner.kimi_command(make_state(model="m"), prompt)[0] == "/bin/k"


def test_junie_command(monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.junie_command(make_state(agent="junie", model="m", effort="high")) == [
        "junie",
        "--skip-update-check",
        "--input-format=json",
        "--output-format=json",
        "-p",
        "/work",
        "--model=m",
        "--effort=high",
    ]
    assert runner.junie_command(make_state(agent="junie", model="m", effort="low"))[-1] == "--effort=low"
    assert runner.junie_command(make_state(agent="junie", model="m", effort="medium"))[-1] == "--effort=medium"
    assert runner.junie_command(make_state(agent="junie", model="m", effort="xhigh")) == [
        "junie",
        "--skip-update-check",
        "--input-format=json",
        "--output-format=json",
        "-p",
        "/work",
        "--model=m",
    ]
    assert runner.junie_command(make_state(agent="junie")) == [
        "junie",
        "--skip-update-check",
        "--input-format=json",
        "--output-format=json",
        "-p",
        "/work",
    ]
    monkeypatch.setenv("MARESTAIL_JUNIE", "/opt/junie")
    assert runner.junie_command(make_state(agent="junie"))[0] == "/opt/junie"


def test_junie_run_passes_json_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install_subprocess(monkeypatch, (0, '{"result":"ok"}', "err"))
    assert runner.junie_run(make_state(root=Path("/repo"), agent="junie"), "the prompt") == (0, '{"result":"ok"}')
    call = fake.calls[0]
    assert call["command"] == runner.junie_command(make_state(root=Path("/repo"), agent="junie"))
    assert json.loads(call["input"]) == {"task": "the prompt"}
    assert call["cwd"] == Path("/repo")
    assert call["env"] is os.environ


def test_junie_run_appends_stderr_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install_subprocess(monkeypatch, (1, "out", "err"))
    assert runner.junie_run(make_state(agent="junie"), "p") == (1, "out\nerr")
    assert fake.calls[0]["input"] == json.dumps({"task": "p"})


def test_junie_run_errors_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    install_subprocess(monkeypatch, FileNotFoundError("nope"))
    assert runner.junie_run(make_state(agent="junie"), "p") == (127, "junie: not found (nope)")
    install_subprocess(monkeypatch, subprocess.TimeoutExpired("x", 1))
    assert runner.junie_run(make_state(agent="junie"), "p") == (124, "junie: timed out after 14400s")


class FakeSubprocess:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(self, command: list[str], **options: Any) -> Any:
        self.calls.append({"command": command, **options})
        if isinstance(self.result, BaseException):
            raise self.result
        return subprocess.CompletedProcess(command, self.result[0], self.result[1], self.result[2])


def install_subprocess(monkeypatch: pytest.MonkeyPatch, result: Any) -> FakeSubprocess:
    fake = FakeSubprocess(result)
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_grok_run_timeout_shows_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    install_subprocess(monkeypatch, subprocess.TimeoutExpired("x", 1))
    state = make_state()
    prompt = Path("/work/p.md")
    shown = runner.SPACE.join(runner.grok_command(state, prompt))
    assert runner.grok_run(state, prompt) == (124, f"{shown}: timed out after 14400s")


def test_grok_run_passes_env_and_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install_subprocess(monkeypatch, (0, "\x1b[1mout\r\n", "err"))
    state = make_state(root=Path("/repo"))
    assert runner.grok_run(state, Path("/work/p.md")) == (0, "out\n")
    call = fake.calls[0]
    assert call["command"][0] == "grok"
    assert call["cwd"] == Path("/repo")
    assert call["input"] == ""
    assert call["timeout"] == 14400
    assert call["env"]["GROK_MEMORY"] == "0"
    assert (call["capture_output"], call["text"], call["check"]) == (True, True, False)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ((1, "  ", "bad"), (1, "bad")),
        ((1, "partial", "bad"), (1, "partial")),
        ((0, "", "bad"), (0, "")),
    ],
)
def test_kilo_run_output_choice(monkeypatch: pytest.MonkeyPatch, result: tuple[int, str, str], expected: tuple[int, str]) -> None:
    fake = install_subprocess(monkeypatch, result)
    assert runner.kilo_run(make_state(), "the prompt") == expected
    assert fake.calls[0]["input"] == "the prompt"
    assert fake.calls[0]["env"] is os.environ


def test_run_errors_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    install_subprocess(monkeypatch, FileNotFoundError("nope"))
    assert runner.kilo_run(make_state(model="m", effort=""), "p") == (127, "kilo: not found (nope)")
    install_subprocess(monkeypatch, subprocess.TimeoutExpired("x", 1))
    command = " ".join(runner.kilo_command(make_state(model="m", effort="")))
    assert runner.kilo_run(make_state(model="m", effort=""), "p") == (124, f"{command}: timed out after 14400s")
    assert runner.kimi_run(make_state(), Path("/w/p.md")) == (124, "kimi: timed out after 14400s")


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ((1, "out", "err"), (1, "out\nerr")),
        ((1, "", "err"), (1, "err")),
        ((0, "out", "err"), (0, "out")),
    ],
)
def test_kimi_run_output(monkeypatch: pytest.MonkeyPatch, result: tuple[int, str, str], expected: tuple[int, str]) -> None:
    fake = install_subprocess(monkeypatch, result)
    assert runner.kimi_run(make_state(), Path("/w/p.md")) == expected
    assert fake.calls[0]["input"] == ""
    assert fake.calls[0]["command"] == runner.kimi_command(make_state(), Path("/w/p.md"))
    assert fake.calls[0]["env"] is os.environ


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ('noise {"type": "text"} tail\n[1]\n{bad\nnothing\n  {"a": 1}', [{"type": "text"}, {"a": 1}]),
        ('{"a": [1]}', [{"a": [1]}]),
        ("x [1, 2]", []),
    ],
)
def test_json_events(output: str, expected: list[dict[str, Any]]) -> None:
    assert runner.kilo_events(output) == expected
    assert runner.kimi_events(output) == expected


def kilo(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event) for event in events)


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (1, "rate limit hit", True),
        (0, "rate limit hit", False),
        (1, "boom", False),
        (0, kilo({"type": "error", "error": {"message": "quota exceeded"}}), True),
        (0, kilo({"type": "error", "error": "Too many requests"}), True),
        (0, kilo({"type": "error", "note": "overloaded"}), True),
        (0, kilo({"type": "error", "error": "broken"}), False),
        (1, kilo({"type": "text", "text": "rate limit"}), False),
    ],
)
def test_kilo_rate_limited(code: int, output: str, expected: bool) -> None:
    assert runner.kilo_rate_limited(code, output) is expected


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("x" * 190 + "\nend" + "y" * 20, "x" * 176 + " end" + "y" * 20),
        (
            kilo(
                {"type": "text", "part": {"text": " first "}},
                {"type": "text", "text": "second"},
                {"type": "text", "part": {"text": ""}},
                {"type": "step_finish", "part": {"tokens": 10, "cost": 0.5}},
                {"type": "step_finish", "part": {"total_tokens": 12, "costUSD": "n/a"}},
                {"type": "step_finish", "part": {}},
            ),
            "tokens=12 cost=n/a 'second'",
        ),
        (
            kilo(
                {"type": "error", "error": {"message": "E" * 130}},
                {"type": "text", "text": "hello"},
                {"type": "step_finish", "part": "x", "cost": 3},
            ),
            f"{'E' * 120!r}",
        ),
        (kilo({"type": "error", "error": "boom"}, {"type": "error"}, {"type": "text", "text": "t"}), "'t'"),
        (kilo({"type": "step_finish", "part": {"cost": 1.234}}), "api-equivalent=$1.23 ''"),
        (kilo({"type": "text", "text": "z" * 130}), repr("z" * 120)),
    ],
)
def test_kilo_summary(output: str, expected: str) -> None:
    assert runner.kilo_summary(output) == expected


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (0, kilo({"error": {"message": "rate limited"}}), True),
        (1, kilo({"error": {"message": "nope"}}), False),
        (1, "usage limit", True),
        (0, "usage limit", False),
        (0, kilo({"role": "assistant", "content": "quota"}), False),
        (1, kilo({"role": "assistant", "content": "quota"}), True),
    ],
)
def test_kimi_rate_limited(code: int, output: str, expected: bool) -> None:
    assert runner.kimi_rate_limited(code, output) is expected


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"error": {"message": "m"}}, "m"),
        ({"error": {"code": 1}}, "{'code': 1}"),
        ({"error": "text"}, "text"),
        ({"type": "error", "message": "msg"}, "msg"),
        ({"role": "error_role", "content": "c"}, "c"),
        ({"type": "fatal_error"}, "{'type': 'fatal_error'}"),
        ({"type": "text", "error": ""}, None),
        ({"role": None, "type": None}, None),
    ],
)
def test_kimi_error(event: dict[str, Any], expected: str | None) -> None:
    assert runner.kimi_error(event) == expected


def test_kimi_texts() -> None:
    events: list[dict[str, Any]] = [
        {"role": "assistant", "content": " a "},
        {"type": "assistant", "message": {"content": [{"text": " b "}, {"text": ""}, "raw", {"other": 1}]}},
        {"role": "assistant", "content": "   ", "message": "not a dict"},
        {"role": "assistant", "content": 5},
        {"role": "user", "content": "ignored"},
    ]
    assert runner.kimi_texts(events) == ["a", "b"]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("plain\ntext", "plain text"),
        (
            kilo(
                {"role": "assistant", "content": "hi"},
                {"num_turns": 3, "usage": {"input_tokens": 5, "output_tokens": None}},
                {"usage": {"total_tokens": 9}, "total_cost_usd": 0.126},
            ),
            "turns=3 tokens=9 api-equivalent=$0.13 'hi'",
        ),
        (kilo({"role": "assistant", "content": "hi"}, {"error": "bad" * 50}), f"{('bad' * 50)[:120]!r}"),
        (kilo({"usage": {"output_tokens": 4}, "total_cost_usd": "free"}), "tokens=4 cost=free ''"),
        (kilo({"usage": {"other": 1}}, {"usage": "x"}), "''"),
        (kilo({"usage": {"total_tokens": 7}}, {"usage": {"input_tokens": 1}}), "tokens=7 ''"),
    ],
)
def test_kimi_summary(output: str, expected: str) -> None:
    assert runner.kimi_summary(output) == expected


def test_error_typed_reads_type_and_role() -> None:
    assert runner.error_typed({"type": "error"}) is True
    assert runner.error_typed({"role": "error"}) is True
    assert runner.error_typed({}) is False


def test_kimi_usage_keeps_the_previous_cost() -> None:
    events: list[dict[str, Any]] = [{runner.TOTAL_COST: 1.5, runner.NUM_TURNS: 2}, {"text": "later"}]
    assert runner.kimi_usage(events) == (2, None, 1.5)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ("[1]", None),
        ('log line {"b": 2} trailing', {"b": 2}),
        ("no json", None),
        ("{broken", None),
        ("x [1] {1}", None),
    ],
)
def test_grok_parse_json(output: str, expected: dict[str, Any] | None) -> None:
    assert runner.grok_parse_json(output) == expected


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (1, "HTTP 503", True),
        (0, "HTTP 503", False),
        (1, "plain", False),
        (0, '{"type": "error", "message": "rate limit"}', True),
        (1, '{"type": "done", "stopReason": "overloaded"}', True),
        (0, '{"type": "done", "stopReason": "overloaded"}', False),
        (1, '{"type": "done"} capacity', True),
        (1, '{"type": "done", "text": "fine"}', False),
    ],
)
def test_grok_rate_limited(code: int, output: str, expected: bool) -> None:
    assert runner.grok_rate_limited(code, output) is expected


@pytest.mark.parametrize(
    ("data", "output", "expected"),
    [
        ({runner.MESSAGE: "rate limit"}, "clean", True),
        ({"text": "usage limit"}, "clean", True),
        ({"type": "overloaded"}, "clean", True),
        ({"stopReason": "capacity"}, "clean", True),
        ({}, "too many requests", True),
        ({}, "HTTP 529", True),
        ({}, "HTTP 503", True),
        ({runner.MESSAGE: "fine"}, "clean", False),
        ({}, "clean", False),
    ],
)
def test_grok_limit_text(data: dict[str, str], output: str, expected: bool) -> None:
    assert runner.grok_limit_text(data, output) is expected


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("tail\nend", "tail end"),
        ('{"text": "t", "num_turns": 2, "total_cost_usd": 1.5}', "turns=2 api-equivalent=$1.50 't'"),
        (
            '{"message": "m", "modelUsage": {"a": {"costUSD": 1}, "b": {"costUSD": 0.25}, "c": {}, "d": 3}}',
            "turns=? api-equivalent=$1.25 'm'",
        ),
        ('{"modelUsage": {"c": {}}, "usage": {"total_tokens": 8}}', "turns=? tokens=8 ''"),
        ('{"modelUsage": "x", "usage": {"total_tokens": 0}}', "turns=? ''"),
        ('{"usage": "x", "text": "' + "q" * 130 + '"}', f"turns=? {'q' * 120!r}"),
    ],
)
def test_grok_summary(output: str, expected: str) -> None:
    assert runner.grok_summary(output) == expected


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (0, "always-approve disabled", False),
        (1, "always-approve disabled", True),
        (1, '{"message": "yolo mode is locked"}', True),
        (1, '{"message": ""} bypassPermissions', True),
        (1, '{"message": "other"} bypassPermissions', False),
        (1, "fine", False),
    ],
)
def test_grok_always_approve_locked(code: int, output: str, expected: bool) -> None:
    assert runner.grok_always_approve_locked(code, output) is expected


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (1, "rate limit", True),
        (0, "rate limit", False),
        (1, "nope", False),
        (0, '{"is_error": true, "result": "usage limit reached"}', True),
        (0, '{"is_error": false, "result": "usage limit reached"}', False),
        (0, '{"is_error": true, "result": "other"}', False),
        (0, '{"status": "ERROR", "response": "quota"}', True),
        (1, '{"error": "Too Many Requests"}', True),
        (0, '{"error": "Too Many Requests"}', False),
        (1, '{"response": "", "error": "fine"}', False),
    ],
)
def test_rate_limited(code: int, output: str, expected: bool) -> None:
    assert runner.rate_limited(code, output) is expected


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("not\njson", "not json"),
        ('{"total_cost_usd": 0.5, "num_turns": 4, "result": "done"}', "turns=4 api-equivalent=$0.50 'done'"),
        ('{"total_cost_usd": 1, "result": "' + "r" * 130 + '"}', f"turns=None api-equivalent=$1.00 {'r' * 120!r}"),
        ('{"type": "result", "result": "ok", "usage": {"total_tokens": 0}}', "tokens=0 'ok'"),
        ('{"type": "result"}', "''"),
        ('{"usage": {"inputTokens": 2, "outputTokens": 3}, "result": "r"}', "tokens=5 'r'"),
        ('{"usage": {"outputTokens": null}}', "tokens=0 ''"),
        ('{"num_turns": 2, "response": "resp", "usage": {"total_tokens": 6}}', "turns=2 tokens=6 'resp'"),
        ('{"usage": "x"}', "turns=? ''"),
    ],
)
def test_summary(output: str, expected: str) -> None:
    assert runner.summary(output) == expected


def test_summary_without_a_result_key() -> None:
    assert runner.summary('{"total_cost_usd": 2}') == "turns=None api-equivalent=$2.00 ''"


def test_kilo_text_reads_part_then_text() -> None:
    assert runner.kilo_text({"type": "text", "part": {"text": "from-part"}, "text": "fallback"}) == "from-part"
    assert runner.kilo_text({"type": "text", "text": "plain"}) == "plain"
    assert runner.kilo_text({"type": "other", "text": "nope"}) == ""


def test_object_or_none_keeps_objects() -> None:
    assert runner.object_or_none({"a": 1}) == {"a": 1}
    assert runner.object_or_none([1]) is None
    assert runner.json_object("[1, 2]") is None


def test_stdout_or_stderr_uses_stderr_when_stdout_is_blank() -> None:
    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="  ", stderr="err")
    assert runner.stdout_or_stderr(failed) == "err"
    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="out", stderr="err")
    assert runner.stdout_or_stderr(ok) == "out"


def test_turns_summary_prefers_result_over_response() -> None:
    data = {runner.NUM_TURNS: 2, runner.RESULT: "hello", "response": "other"}
    text = runner.turns_summary(data, {})
    assert "hello" in text
    assert "other" not in text


JUNIE_VERIFIED_OUTPUT = '{"sessionId":"s","taskName":"t","result":"### Summary\\n- pong\\n\\n### Changes\\n- No files were created or modified as requested.\\n\\n### Verification\\n- Verified that the repository remains empty and untouched.","changes":[],"llmUsage":[{"model":"gemini-3.8-flash","calls":17,"cost":0.06395658750000001,"inputTokens":91729,"cacheInputTokens":261869,"cacheCreateTokens":0,"outputTokens":10527},{"model":"gpt-5.4-nano","calls":13,"cost":0.01091355,"inputTokens":44499,"cacheInputTokens":0,"cacheCreateTokens":0,"outputTokens":1611},{"model":"gpt-4.1-mini-2025-04-14","calls":16,"cost":0.004483599999999999,"inputTokens":10037,"cacheInputTokens":0,"cacheCreateTokens":0,"outputTokens":293},{"model":"gpt-4.1-2025-04-14","calls":1,"cost":0.0015019999999999999,"inputTokens":743,"cacheInputTokens":0,"cacheCreateTokens":0,"outputTokens":2},{"model":"gemini-3.5-flash-lite","calls":1,"cost":4.9225E-4,"inputTokens":1969,"cacheInputTokens":0,"cacheCreateTokens":0,"outputTokens":0}]}'


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (1, "Your balance is exhausted.", True),
        (1, '{"errors":[{"level":"ERROR","message":"InsufficientAccountBalance"}]}', True),
        (1, "insufficient balance", True),
        (1, "rate limit exceeded", True),
        (1, "Junie failed with the message: Invalid model: no-such-model-xyz", False),
        (0, "the quota gate passed", False),
        (0, JUNIE_VERIFIED_OUTPUT, False),
        (1, '{"result": "ok"}', False),
        (1, "plain error", False),
        (1, '{"errors":["not a dict"]}', False),
        (1, '{"errors":[{}]}', False),
        (1, '{"errors":[{"message":"rate limit exceeded"}]}', True),
        (1, '{"errors":[{"message":"rate\\u0020limit exceeded"}]}', True),
    ],
)
def test_junie_rate_limited(code: int, output: str, expected: bool) -> None:
    assert runner.junie_rate_limited(code, output) is expected


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            JUNIE_VERIFIED_OUTPUT,
            "calls=48 tokens=423279 cost=$0.08 '### Summary - pong  ### Changes - No files were created or modified as requested.  ### Verification '",
        ),
        ('{"llmUsage": []}', "calls=0 tokens=0 cost=$0.00 ''"),
        ("not json", "not json"),
        ("x" * 190 + "\nend" + "y" * 20, "x" * 176 + " end" + "y" * 20),
    ],
)
def test_junie_summary(output: str, expected: str) -> None:
    assert runner.junie_summary(output) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"llmUsage": [{"calls": 2, "inputTokens": 10, "cacheInputTokens": 5, "outputTokens": 3, "cost": 1.5}]}, (2, 18, 1.5)),
        ({"llmUsage": [{}, {"calls": None, "inputTokens": None, "cost": None}]}, (0, 0, 0.0)),
        ({"llmUsage": ["not a dict", {"calls": 1, "inputTokens": 2, "outputTokens": 3, "cost": 0.1}]}, (1, 5, 0.1)),
        ("not a dict", (0, 0, 0.0)),
        ({}, (0, 0, 0.0)),
    ],
)
def test_junie_usage(data: Any, expected: tuple[int, int, float]) -> None:
    assert runner.junie_usage(data) == expected


@pytest.mark.parametrize(
    ("backend", "readers"),
    [
        ("grok", (runner.grok_rate_limited, runner.grok_summary)),
        ("kilo", (runner.kilo_rate_limited, runner.kilo_summary)),
        ("kimi", (runner.kimi_rate_limited, runner.kimi_summary)),
        ("junie", (runner.junie_rate_limited, runner.junie_summary)),
        ("claude", (runner.rate_limited, runner.summary)),
        ("agy", (runner.rate_limited, runner.summary)),
    ],
)
def test_outcome_readers(backend: str, readers: tuple[Any, Any]) -> None:
    assert runner.outcome_readers(backend) == readers


def test_run_backend_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(root=Path("/repo"), model="m", account_env={"K": "v"})
    prompt = Path("/f")

    def make_run(name: str, arg: str) -> Callable[[Run, Any], tuple[int, str]]:
        def run_fn(s: Run, p: Any) -> tuple[int, str]:
            assert s is state
            return 1, f"{name} {arg}"

        return run_fn

    monkeypatch.setattr(runner, "grok_run", make_run("grok", str(prompt)))
    monkeypatch.setattr(runner, "kilo_run", make_run("kilo", "p"))
    monkeypatch.setattr(runner, "kimi_run", make_run("kimi", str(prompt)))
    monkeypatch.setattr(runner, "junie_run", make_run("junie", "p"))
    assert runner.run_backend(state, "grok", "p", prompt) == (1, "grok /f")
    assert runner.run_backend(state, "kilo", "p", prompt) == (1, "kilo p")
    assert runner.run_backend(state, "kimi", "p", prompt) == (1, "kimi /f")
    assert runner.run_backend(state, "junie", "p", prompt) == (1, "junie p")


def test_run_backend_generic_uses_agent_command(fake_run: Any) -> None:
    fake = fake_run(runner, [(4, "claude out")])
    state = make_state(root=Path("/repo"), model="m", account_env={"K": "v"})
    assert runner.run_backend(state, "claude", "p", Path("/f")) == (4, "claude out")
    assert fake.calls == [[*CLAUDE_BASE, "--model", "m"]]
    assert fake.options == [
        {"cwd": Path("/repo"), "stdin": "p", "timeout": 14400, "env": {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0", "K": "v"}}
    ]
