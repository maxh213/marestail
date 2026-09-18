#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import route, runner
from marestail.config import Config
from marestail.runner import Run, agent_env, agent_label, invoke, stamped

CLI = Path(__file__).resolve().parent.parent / "marestail" / "cli.py"
DANDELION_ROUTES = Path(os.environ.get("MARESTAIL_DANDELION_SRC", Path.home() / "workspace" / "dandelion" / "src" / "domain" / "route.ts"))
ENV_KEYS = [
    "MARESTAIL_DANDELION",
    "DANDELION_CLAUDE_WORK_CONFIG_DIR",
    "MARESTAIL_CLAUDE",
    "MARESTAIL_GROK",
    "MARESTAIL_CURSOR",
    "PLAN",
    "CALLS",
    "SEEN",
]


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_exit(name, action, fragment):
    try:
        action()
    except SystemExit as error:
        if fragment not in str(error):
            raise SystemExit(f"{name}: {error!r} lacks {fragment!r}") from error
        return
    raise SystemExit(f"{name}: no SystemExit")


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def parsed(line):
    choice = route.parse(line)
    return choice.backend, choice.model, choice.effort, choice.env


def parses_every_line():
    work = {"CLAUDE_CONFIG_DIR": str(Path("~/.claude-work").expanduser())}
    cases = {
        "claude-opus-5 high claude": ("claude", "claude-opus-5", "high", {}),
        "claude-opus-5 max claude-work": ("claude", "claude-opus-5", "max", work),
        "claude-fable-5-1 max claude-work": ("claude", "claude-fable-5-1", "max", work),
        "gemini-3.1-pro-high medium agy": ("agy", "gemini-3.1-pro-high", "medium", {}),
        "gemini-3.8-flash-high high agy": ("agy", "gemini-3.8-flash-high", "high", {}),
        "kimi-code/kimi-for-coding-highspeed kimi": ("kimi", "kimi-code/kimi-for-coding-highspeed", None, {}),
        "grok-4.6 grok": ("grok", "grok-4.6", None, {}),
        "grok-4.6 xhigh grok": ("grok", "grok-4.6", "xhigh", {}),
        "kimi-k3-max cursor": ("cursor", "kimi-k3-max", None, {}),
    }
    for line, wanted in cases.items():
        expect(f"parse {line}", parsed(line), wanted)
    os.environ["DANDELION_CLAUDE_WORK_CONFIG_DIR"] = "/srv/work-claude"
    expect("work-dir-env", parsed("claude-opus-5 high claude-work")[3], {"CLAUDE_CONFIG_DIR": "/srv/work-claude"})
    del os.environ["DANDELION_CLAUDE_WORK_CONFIG_DIR"]
    expect_exit("one-word", lambda: route.parse("claude"), "expected")
    expect_exit("four-words", lambda: route.parse("a b c claude"), "expected")
    expect_exit("unknown-account", lambda: route.parse("gpt-6 high codex"), "no backend for")
    expect(
        "routed-models",
        (
            route.is_routed("dandelion/route"),
            route.is_routed("dandelion/route-best"),
            route.is_routed("claude-opus-5"),
            route.is_routed(None),
        ),
        (True, True, False, False),
    )


def dandelion_source_lines() -> list[str]:
    source = DANDELION_ROUTES.read_text()
    claude = re.search(r"CLAUDE_LINES = \{ standard: '([^']+)', max: '([^']+)' \}", source)
    lines = []
    for entry in re.finditer(r"\{ id: '([\w-]+)'(.*?)\}", source):
        account, rest = entry.group(1), entry.group(2)
        pair = claude.groups() if "...CLAUDE_LINES" in rest else re.search(r"standard: '([^']+)', max: '([^']+)'", rest).groups()
        lines.extend(f"{line} {account}" for line in pair)
    for entry in re.finditer(r"providers: \[([^\]]*)\].*?line: '([^']+)'", source):
        lines.extend(f"{entry.group(2)} {account}" for account in re.findall(r"'([\w-]+)'", entry.group(1)))
    return lines


def matches_dandelion_source():
    if not DANDELION_ROUTES.exists():
        print(f"skipping dandelion source check: no {DANDELION_ROUTES}")
        return
    lines = dandelion_source_lines()
    if len(lines) < 12:
        raise SystemExit(f"dandelion source: found only {lines!r}; the route table format changed")
    for line in lines:
        choice = route.parse(line)
        expect(f"source {line} model", line.startswith(choice.model + " "), True)
    print(f"dandelion source: {len(lines)} route lines parse")


def stub_dandelion(folder: Path) -> None:
    os.environ["PLAN"] = str(folder / "plan")
    os.environ["CALLS"] = str(folder / "calls")
    os.environ["MARESTAIL_DANDELION"] = str(
        script(
            folder / "dandelion",
            ('echo "$*" >> "$CALLS"\nline=$(head -n 1 "$PLAN"); sed -i 1d "$PLAN"\nprintf "%s\\n" "${line#* }"; exit "${line%% *}"\n'),
        )
    )


def chooses(folder: Path):
    stub_dandelion(folder)
    (folder / "plan").write_text("0 grok-4.6 xhigh grok\n1 none\n3 boom\n")
    choice, reason = route.choose("dandelion/route-best", folder)
    expect("choose-line", (choice.line, reason), ("grok-4.6 xhigh grok", ""))
    expect("choose-none", route.choose("dandelion/route", folder), (None, "no subscription has quota left"))
    expect("choose-failure", route.choose("dandelion/route", folder)[1], "dandelion route failed with exit 3: boom")
    expect("choose-args", (folder / "calls").read_text(), "route --high\nroute\nroute\n")
    os.environ["MARESTAIL_DANDELION"] = str(folder / "absent")
    expect_exit("choose-missing", lambda: route.choose("dandelion/route", folder), route.REPO)


def proxies(folder: Path):
    stub_dandelion(folder)
    (folder / "plan").write_text("0 claude-opus-5 high claude\n1 none\n")
    first = subprocess.run([sys.executable, str(CLI), "route", "--high"], capture_output=True, text=True, check=False)
    expect("proxy-output", (first.returncode, first.stdout), (0, "claude-opus-5 high claude\n"))
    second = subprocess.run([sys.executable, str(CLI), "route"], capture_output=True, text=True, check=False)
    expect("proxy-none", (second.returncode, second.stdout), (1, "none\n"))
    expect("proxy-args", (folder / "calls").read_text(), "route --high\nroute\n")
    os.environ["MARESTAIL_DANDELION"] = str(folder / "absent")
    missing = subprocess.run([sys.executable, str(CLI), "route"], capture_output=True, text=True, check=False)
    expect("proxy-missing-code", missing.returncode, 127)
    for fragment in (route.REPO, "npm link", "not installed"):
        expect(f"proxy-missing-hint {fragment}", fragment in missing.stderr, True)
    listed = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True, text=True, check=False)
    expect("help-lists-route", "route" in listed.stdout, True)


def reroutes(folder: Path):
    stub_dandelion(folder)
    os.environ["DANDELION_CLAUDE_WORK_CONFIG_DIR"] = "/srv/work-claude"
    os.environ["SEEN"] = str(folder / "seen")
    (folder / "plan").write_text("0 grok-4.6 xhigh grok\n1 none\n0 claude-opus-5 high claude-work\n0 kimi-k3-max cursor\n")
    os.environ["MARESTAIL_GROK"] = str(script(folder / "grok", 'echo \'{"type":"error","message":"rate limit exceeded"}\'; exit 1\n'))
    os.environ["MARESTAIL_CURSOR"] = str(
        script(folder / "cursor-agent", 'cat > /dev/null; echo \'{"type":"result","result":"cursor stub"}\'\n')
    )
    os.environ["MARESTAIL_CLAUDE"] = str(
        script(
            folder / "claude",
            ('cat > "$SEEN"\nprintf \'{"total_cost_usd": 0, "num_turns": 1, "result": "config=%s args=%s"}\' "$CLAUDE_CONFIG_DIR" "$*"\n'),
        )
    )
    previous_wait = runner.LIMIT_WAIT_SECONDS
    runner.LIMIT_WAIT_SECONDS = 0
    try:
        state = Run(
            config=Config(root=folder, raw={"agent": {"backend": "kilo", "effort": "low"}}),
            task=folder / "t.md",
            model=None,
            retries=0,
            route="dandelion/route",
        )
        expect("label-before-routing", agent_label(state), "dandelion/route")
        invoke(state, "01-coder", f"Commit everything with a message starting with `[{agent_label(state)}] ` and ending in `By coder.`")
        expect("routed-state", (state.agent, state.model, state.effort), ("claude", "claude-opus-5", "high"))
        expect("labels", state.labels, {"grok-4.6 xhigh", "claude-opus-5 high"})
        expect(
            "prompt-relabelled",
            (folder / "seen").read_text(),
            "Commit everything with a message starting with `[claude-opus-5 high] ` and ending in `By coder.`",
        )
        expect("prompt-file", (state.folder / "01-coder.prompt.md").read_text(), (folder / "seen").read_text())
        output = (state.folder / "01-coder.json").read_text()
        expect("claude-work-config", "config=/srv/work-claude " in output, True)
        expect("claude-model-effort", "--model claude-opus-5 --effort high" in output, True)
        expect("claude-work-env", agent_env(state)["CLAUDE_CONFIG_DIR"], "/srv/work-claude")
        invoke(state, "02-cleaner", "no stamp here")
        expect("cursor-state", (state.agent, state.model, state.effort, agent_env(state)), ("cursor", "kimi-k3-max", None, {}))
        expect("route-calls", (folder / "calls").read_text(), "route\nroute\nroute\nroute\n")
    finally:
        runner.LIMIT_WAIT_SECONDS = previous_wait
    expect(
        "stamp-keeps-earlier-route",
        stamped("[grok-4.6 xhigh] coder work", "claude-opus-5 high", {"grok-4.6 xhigh"}),
        "[grok-4.6 xhigh] coder work",
    )
    expect(
        "stamp-unknown-bracket",
        stamped("[WIP] coder work", "claude-opus-5 high", {"grok-4.6 xhigh"}),
        "[claude-opus-5 high] [WIP] coder work",
    )


def rejects_flags(folder: Path):
    repo = folder / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "marestail.toml").write_text('[git]\nbase = "main"\n')
    (repo / "t.md").write_text("task\n")
    stub_dandelion(folder)

    def run_cli(*args):
        return subprocess.run([sys.executable, str(CLI), "run", "t.md", *args], cwd=repo, capture_output=True, text=True, check=False)

    for flags in (["--agent", "claude"], ["--effort", "high"]):
        result = run_cli("--model", "dandelion/route", *flags)
        expect(f"reject {flags}", (result.returncode, "drop --agent and --effort" in result.stderr), (1, True))
    os.environ["MARESTAIL_DANDELION"] = str(folder / "absent")
    result = run_cli("--model", "dandelion/route-best")
    expect("run-missing-dandelion", (result.returncode, route.REPO in result.stderr), (1, True))


if __name__ == "__main__":
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    try:
        parses_every_line()
        matches_dandelion_source()
        for check in (chooses, proxies, reroutes, rejects_flags):
            with tempfile.TemporaryDirectory(prefix="marestail-route-test-") as temp:
                check(Path(temp))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("route ok")
