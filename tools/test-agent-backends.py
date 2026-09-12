#!/usr/bin/env python3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.config import Config
from marestail.runner import (
    KILO_DEFAULT_MODEL,
    KILO_DEFAULT_VARIANT,
    Run,
    agent_command,
    agent_label,
    grok_command,
    kilo_command,
    kilo_events,
    kilo_rate_limited,
    kilo_summary,
    kimi_command,
    kimi_events,
    kimi_rate_limited,
    kimi_summary,
    parse_verdict,
    resolve_agent,
    stamped,
)

ROOT = Path("/tmp/marestail-agent-test")
TASK = Path("/tmp/t.md")
PROMPT = Path("/tmp/p.md")


def state(agent=None, model="mymodel", raw=None, effort=None):
    return Run(config=Config(root=ROOT, raw=raw or {}), task=TASK, model=model, retries=0, agent=agent, effort=effort)


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def restore(keys, previous):
    for key in keys:
        value = previous.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def snapshot_existing():
    expect(
        "claude",
        agent_command(state("claude")),
        ["claude", "-p", "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions", "--output-format", "json", "--model", "mymodel"],
    )
    expect(
        "agy",
        agent_command(state("agy")),
        ["agy", "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "4h", "--model", "mymodel"],
    )
    expect(
        "cursor",
        agent_command(state("cursor")),
        ["cursor-agent", "-p", "--output-format", "json", "--force", "--trust", "--sandbox", "disabled", "--model", "mymodel"],
    )
    expect(
        "grok",
        grok_command(state("grok"), PROMPT),
        ["grok", "--prompt-file", str(PROMPT.resolve()), "--output-format", "json", "--always-approve", "--no-plan", "--trust", "--model", "mymodel"],
    )
    expect("default", resolve_agent(state(None, model=None)), "claude")
    expect("default-command", agent_command(state(None)), agent_command(state("claude")))


def kilo_defaults():
    expect(
        "kilo-default",
        kilo_command(state("kilo", model=None)),
        ["kilo", "run", "--auto", "--format", "json", "--log-level", "ERROR", "--model", KILO_DEFAULT_MODEL, "--variant", KILO_DEFAULT_VARIANT],
    )
    expect("kilo-default-model", KILO_DEFAULT_MODEL, "kilo/stepfun/step-3.7-flash:free")
    expect("kilo-default-variant", KILO_DEFAULT_VARIANT, "high")
    expect(
        "kilo-other-model",
        kilo_command(state("kilo", model="kilo/other")),
        ["kilo", "run", "--auto", "--format", "json", "--log-level", "ERROR", "--model", "kilo/other"],
    )
    expect("kilo-via-agent-command", agent_command(state("kilo", model=None)), kilo_command(state("kilo", model=None)))


def kimi_backend():
    expect(
        "kimi-command",
        kimi_command(state("kimi"), "do the thing"),
        ["kimi", "-p", "do the thing", "--output-format", "stream-json", "-m", "mymodel"],
    )
    expect(
        "kimi-command-no-model",
        kimi_command(state("kimi", model=None), "do the thing"),
        ["kimi", "-p", "do the thing", "--output-format", "stream-json"],
    )
    expect("kimi-resolve", resolve_agent(state("kimi", model=None)), "kimi")
    expect("kimi-config-backend", resolve_agent(state(None, model=None, raw={"agent": {"backend": "kimi"}})), "kimi")


def env_overrides():
    keys = ["MARESTAIL_AGENT", "MARESTAIL_KILO", "MARESTAIL_KILO_VARIANT", "MARESTAIL_CLAUDE", "MARESTAIL_AGY", "MARESTAIL_CURSOR", "MARESTAIL_GROK", "MARESTAIL_GROK_EFFORT", "MARESTAIL_KIMI"]
    previous = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["MARESTAIL_AGENT"] = "kilo"
        expect("env-agent", resolve_agent(state(None, model=None)), "kilo")
        os.environ["MARESTAIL_KILO"] = "/opt/kilo"
        expect("env-binary", kilo_command(state("kilo", model=None))[0], "/opt/kilo")
        os.environ["MARESTAIL_CLAUDE"] = "/opt/claude"
        expect("claude-binary", agent_command(state("claude"))[0], "/opt/claude")
        os.environ["MARESTAIL_AGY"] = "/opt/agy"
        expect("agy-binary", agent_command(state("agy"))[0], "/opt/agy")
        os.environ["MARESTAIL_CURSOR"] = "/opt/cursor-agent"
        expect("cursor-binary", agent_command(state("cursor"))[0], "/opt/cursor-agent")
        os.environ["MARESTAIL_GROK"] = "/opt/grok"
        expect("grok-binary", grok_command(state("grok"), PROMPT)[0], "/opt/grok")
        os.environ["MARESTAIL_GROK_EFFORT"] = "xhigh"
        grok = grok_command(state("grok"), PROMPT)
        if grok[-2:] != ["--reasoning-effort", "xhigh"]:
            raise SystemExit(f"grok-effort: {grok!r}")
        os.environ["MARESTAIL_KILO_VARIANT"] = "high"
        kilo = kilo_command(state("kilo", model="kilo/other"))
        if kilo[-2:] != ["--variant", "high"]:
            raise SystemExit(f"kilo-variant-env: {kilo!r}")
        os.environ["MARESTAIL_KIMI"] = "/opt/kimi"
        expect("kimi-binary", kimi_command(state("kimi"), "p")[0], "/opt/kimi")
        os.environ["MARESTAIL_AGENT"] = "kimi"
        expect("env-agent-kimi", resolve_agent(state(None, model=None)), "kimi")
    finally:
        restore(keys, previous)
    expect("config-backend", resolve_agent(state(None, model=None, raw={"agent": {"backend": "kilo"}})), "kilo")
    expect("flag-wins", resolve_agent(state("cursor", model=None, raw={"agent": {"backend": "kilo"}})), "cursor")


def labels():
    expect("label-model-only", agent_label(state("claude")), "mymodel")
    expect("label-with-effort", agent_label(state("claude", effort="high")), "mymodel high")
    expect("label-no-model", agent_label(state("claude", model=None)), "claude")
    expect("label-kilo-default", agent_label(state("kilo", model=None)), f"{KILO_DEFAULT_MODEL} {KILO_DEFAULT_VARIANT}")
    expect("label-kilo-plain", agent_label(state("kilo", model="kilo/other")), "kilo/other")
    effortful = state("grok", effort="xhigh")
    expect("label-grok", agent_label(effortful), "mymodel xhigh")
    expect("grok-effort-flag", grok_command(effortful, PROMPT)[-2:], ["--reasoning-effort", "xhigh"])
    expect("claude-effort-flag", agent_command(state("claude", effort="xhigh"))[-2:], ["--effort", "xhigh"])
    expect("agy-effort-flag", agent_command(state("agy", effort="high"))[-2:], ["--effort", "high"])
    expect("cursor-effort-unflagged", agent_command(state("cursor", effort="high"))[-2:], ["--model", "mymodel"])
    expect("kilo-effort-flag", kilo_command(state("kilo", model="kilo/other", effort="low"))[-2:], ["--variant", "low"])
    expect("kilo-effort-off", kilo_command(state("kilo", model=None, effort=""))[-2:], ["--model", KILO_DEFAULT_MODEL])
    expect("label-kimi", agent_label(state("kimi")), "mymodel")
    expect("label-kimi-no-model", agent_label(state("kimi", model=None)), "kimi")
    expect("label-kimi-effort", agent_label(state("kimi", effort="high")), "mymodel high")
    expect(
        "kimi-effort-unflagged",
        kimi_command(state("kimi", effort="high"), "p"),
        ["kimi", "-p", "p", "--output-format", "stream-json", "-m", "mymodel"],
    )
    expect("stamp", stamped("coder handoff", "mymodel high"), "[mymodel high] coder handoff")
    expect("stamp-once", stamped("[mymodel high] coder handoff", "mymodel high"), "[mymodel high] coder handoff")
    expect("stamp-unlabelled", stamped("coder handoff", ""), "coder handoff")


def kilo_output():
    output = "\n".join(
        [
            "INFO ignore this",
            '{"type":"text","part":{"text":"hello"}}',
            '{"type":"step_finish","part":{"tokens":12,"cost":0.5}}',
            '{"type":"error","error":{"message":"too many requests"}}',
        ]
    )
    events = kilo_events(output)
    expect("event-count", len(events), 3)
    expect("rate-limited", kilo_rate_limited(1, output), True)
    expect("not-limited-ok", kilo_rate_limited(0, '{"type":"text","part":{"text":"ok"}}'), False)
    expect(
        "not-limited-generation-529",
        kilo_rate_limited(0, '{"type":"step_finish","part":{"metrics":{"generation":529}}}'),
        False,
    )
    summary = kilo_summary(output)
    if "hello" not in summary and "too many requests" not in summary:
        raise SystemExit(f"kilo_summary missing text: {summary!r}")
    if "tokens=12" not in summary:
        raise SystemExit(f"kilo_summary missing tokens: {summary!r}")


def kimi_output():
    output = "\n".join(
        [
            "notice: not json",
            '{"role":"meta","type":"system.version","version":"0.42.0"}',
            '{"role":"assistant","content":"working on it"}',
            "{malformed json",
            '{"role":"assistant","content":"all done"}',
            '{"role":"meta","type":"session.stats","num_turns":3,"total_cost_usd":0.25,"usage":{"total_tokens":42}}',
        ]
    )
    events = kimi_events(output)
    expect("kimi-event-count", len(events), 4)
    expect("kimi-not-limited", kimi_rate_limited(0, output), False)
    summary = kimi_summary(output)
    if "all done" not in summary or "turns=3" not in summary or "tokens=42" not in summary or "api-equivalent=$0.25" not in summary:
        raise SystemExit(f"kimi_summary: {summary!r}")
    expect("kimi-summary-tail", kimi_summary("plain text failure"), "plain text failure")
    limited = "\n".join(
        [
            '{"role":"assistant","content":"trying"}',
            '{"role":"meta","type":"error","error":{"message":"rate limit exceeded, retry later"}}',
        ]
    )
    expect("kimi-rate-limited-event", kimi_rate_limited(1, limited), True)
    expect("kimi-error-summary", "rate limit exceeded" in kimi_summary(limited), True)
    stderr_failure = '{"role":"meta","type":"system.version","version":"0.42.0"}\nerror: failed to run prompt: usage limit reached'
    expect("kimi-rate-limited-stderr", kimi_rate_limited(1, stderr_failure), True)
    expect("kimi-not-limited-clean-exit", kimi_rate_limited(0, "usage limit mentioned in passing"), False)
    expect("kimi-not-limited-nonquota-error", kimi_rate_limited(1, '{"role":"meta","type":"error","error":{"message":"model not configured"}}'), False)


def verdict_parse():
    report = Path("/tmp/marestail-verdict-test.md")
    report.write_text("Here is my judgement.\n\nVERDICT: BOUNCE specifier\n1. fix it\n")
    parsed = parse_verdict(report)
    expect("verdict-anywhere", parsed, ("BOUNCE", "specifier"))
    parsed = parse_verdict(Path("/tmp/missing-verdict.md"), '{"type":"text","part":{"text":"VERDICT: PASS"}}')
    expect("verdict-from-json", parsed, ("PASS", None))
    parsed = parse_verdict(Path("/tmp/missing-verdict.md"), '{"role":"assistant","content":"VERDICT: BOUNCE coder"}')
    expect("verdict-from-kimi-json", parsed, ("BOUNCE", "coder"))


if __name__ == "__main__":
    snapshot_existing()
    kilo_defaults()
    kimi_backend()
    env_overrides()
    labels()
    kilo_output()
    kimi_output()
    verdict_parse()
    print("agent backends ok")
