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
    grok_command,
    kilo_command,
    kilo_events,
    kilo_rate_limited,
    kilo_summary,
    parse_verdict,
    resolve_agent,
)

ROOT = Path("/tmp/marestail-agent-test")
TASK = Path("/tmp/t.md")
PROMPT = Path("/tmp/p.md")


def state(agent=None, model="mymodel", raw=None):
    return Run(config=Config(root=ROOT, raw=raw or {}), task=TASK, model=model, retries=0, agent=agent)


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


def env_overrides():
    keys = ["MARESTAIL_AGENT", "MARESTAIL_KILO", "MARESTAIL_KILO_VARIANT", "MARESTAIL_CLAUDE", "MARESTAIL_AGY", "MARESTAIL_CURSOR", "MARESTAIL_GROK", "MARESTAIL_GROK_EFFORT"]
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
    finally:
        restore(keys, previous)
    expect("config-backend", resolve_agent(state(None, model=None, raw={"agent": {"backend": "kilo"}})), "kilo")
    expect("flag-wins", resolve_agent(state("cursor", model=None, raw={"agent": {"backend": "kilo"}})), "cursor")


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


def verdict_parse():
    report = Path("/tmp/marestail-verdict-test.md")
    report.write_text("Here is my judgement.\n\nVERDICT: BOUNCE specifier\n1. fix it\n")
    parsed = parse_verdict(report)
    expect("verdict-anywhere", parsed, ("BOUNCE", "specifier"))
    parsed = parse_verdict(Path("/tmp/missing-verdict.md"), '{"type":"text","part":{"text":"VERDICT: PASS"}}')
    expect("verdict-from-json", parsed, ("PASS", None))


if __name__ == "__main__":
    snapshot_existing()
    kilo_defaults()
    env_overrides()
    kilo_output()
    verdict_parse()
    print("agent backends ok")
