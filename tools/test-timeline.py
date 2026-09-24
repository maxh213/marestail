#!/usr/bin/env python3
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import runner, timeline
from marestail.config import Config
from marestail.perf import trees as perf_trees
from marestail.pipeline import Judge, find
from marestail.report import Result
from marestail.runner import Run, run_judge

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLI = ROOT / "marestail" / "cli.py"
STUB = HERE / "stub-claude"
FEATURE = "Feature: t\n  Scenario: Adds one\n    Given x\n  Scenario Outline: Rejects bad input\n    Given y\n"
AUDIT = "## Audit\n- Adds one -> tests/test_src.py::test_adds_one\n- Rejects bad input -> tests/test_src.py::test_rejects_bad_input\n"
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
ENV_KEYS = (
    "MARESTAIL_CLAUDE",
    "MARESTAIL_AGENT",
    "MARESTAIL_DANDELION",
    "MARESTAIL_LIMIT_WAIT_SECONDS",
    "MARESTAIL_LIMIT_WAITS",
    "STUB_PLAN",
    "PLAN",
    "CALLS",
    "PATH",
)


def expect(name: str, got: Any, wanted: Any) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_true(name: str, value: Any) -> None:
    if not value:
        raise SystemExit(f"{name}: {value!r}")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def seed_features(root: Path) -> None:
    write(root, "features/t.feature", FEATURE)
    write(root, "qa/t.md", "1. open\n")
    write(root, "src.py", "original\n")
    write(root, "tests/test_src.py", "def test_adds_one():\n    pass\ndef test_rejects_bad_input():\n    pass\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "seed")


def new_repo(folder: Path, seed: bool = True) -> Path:
    root = folder / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    write(root, "README.md", "# repo\n")
    write(root, "marestail.toml", '[git]\nbase = "main"\n')
    write(root, ".gitignore", ".marestail/\n")
    write(root, "tasks/t.md", "# Add one\n")
    write(root, "src.py", "original\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    if seed:
        seed_features(root)
    return root


def plan_file(folder: Path, lines: str) -> Path:
    path = folder / "plan.txt"
    path.write_text(lines if lines.endswith("\n") else lines + "\n")
    return path


def run_cli(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    merged["PATH"] = f"{ROOT / 'bin'}:{merged.get('PATH', '')}"
    for key in ("MARESTAIL_AGENT", "AGENT", "MODEL", "EFFORT"):
        merged.pop(key, None)
    return subprocess.run(
        [sys.executable, str(CLI), "run", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=merged,
    )


def load_timeline(root: Path) -> dict[str, Any]:
    return json.loads((root / ".marestail" / "runs" / "t" / "timeline.json").read_text())


def timeline_md(root: Path) -> str:
    return (root / ".marestail" / "runs" / "t" / "timeline.md").read_text()


def stub_env(folder: Path, plan: str) -> dict[str, str]:
    return {"MARESTAIL_CLAUDE": str(STUB), "STUB_PLAN": str(plan_file(folder, plan))}


@contextlib.contextmanager
def saved_env(keys: tuple[str, ...] = ENV_KEYS) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in keys}
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def coder_commits_timeline(folder: Path) -> None:
    root = new_repo(folder)
    stub = folder / "coder-stub"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, re, subprocess, sys\n"
        "from pathlib import Path\n"
        "prompt = sys.stdin.read()\n"
        "root = Path.cwd()\n"
        'Path("src.py").write_text("def add_one(x):\\n    return x + 1\\n")\n'
        'subprocess.run(["git", "add", "src.py"], cwd=root, check=True)\n'
        'subprocess.run(["git", "commit", "-qm", "add src\\n\\nBy coder."], cwd=root, check=True)\n'
        'path = root / re.search(r"Write (\\.marestail/\\S+?):", prompt).group(1)\n'
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        'path.write_text("coded the adder\\n\\n## Audit\\n'
        "- Adds one -> tests/test_src.py::test_adds_one\\n"
        '- Rejects bad input -> tests/test_src.py::test_rejects_bad_input\\n")\n'
        'print(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0.01, "result": "ok"}))\n'
    )
    stub.chmod(0o755)
    result = run_cli(
        root,
        "tasks/t.md",
        "--from",
        "coder",
        "--to",
        "coder",
        "--auto",
        "--retries",
        "1",
        env={"MARESTAIL_CLAUDE": str(stub)},
    )
    expect_true("coder-exit", result.returncode == 0)
    expect_true("stdout-==", bool(re.search(r"^== coder \(\d+-coder\) attempt 1$", result.stdout, re.M)))
    expect_true("stdout-finished", bool(re.search(r"^\s+\d+-coder finished in [0-9.]+ min:", result.stdout, re.M)))
    data = load_timeline(root)
    expect("task", data["task"], "t")
    expect("one-step", len(data["steps"]), 1)
    step = data["steps"][0]
    expect_true("id", bool(re.match(r"^\d+-coder$", step["id"])))
    expect("role", step["role"], "coder")
    expect("attempt", step["attempt"], 1)
    expect_true("started", bool(ISO_Z.match(step["started_at"])))
    expect_true("ended", bool(ISO_Z.match(step["ended_at"])))
    expect_true("gate-list", isinstance(step["gate"], list))
    expect_true("waits-list", isinstance(step["waits"], list))
    expect_true("agent-keys", set(step["agent"]) >= {"backend", "model", "effort", "account", "minutes", "summary"})
    expect_true("minutes", step["agent"]["minutes"] >= 0)
    expect_true("commits", len(step["commits"]) >= 1)
    expect_true("files-src", "src.py" in step["files"])
    expect("done", step["done"], "coded the adder")
    expect("no-verdict", "verdict" in step, False)
    expect_true("md-heading", f"## {step['id']}" in timeline_md(root))
    finished = re.search(r"finished in [0-9.]+ min: (.+)$", result.stdout, re.M)
    expect("summary", step["agent"]["summary"], cast(re.Match[str], finished).group(1))


def hardener_gate_fail(folder: Path) -> None:
    root = new_repo(folder)
    calls = {"gates": 0}
    failed = [Result(gate="sonar", ok=False, summary="fail", seconds=41.0)]

    def fake_gates(self: Run, tier: str) -> list[Result]:
        calls["gates"] += 1
        return failed

    with saved_env():
        os.environ.update(stub_env(folder, "judge PASS\n"))
        for key in ("MARESTAIL_AGENT", "AGENT", "MODEL"):
            os.environ.pop(key, None)
        state = Run(
            config=Config(root=root, raw={"git": {"base": "main"}}),
            task=root / "tasks" / "t.md",
            model=None,
            retries=1,
            agent="claude",
        )
        previous = runner.Run.gates
        runner.Run.gates = fake_gates  # type: ignore[method-assign]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                run_judge(state, cast(Judge, find("hardener")))
        finally:
            runner.Run.gates = previous  # type: ignore[method-assign]
        expect("gates-once", calls["gates"], 1)
        data = load_timeline(root)
        step = data["steps"][-1]
        expect("gate-entry", step["gate"], [{"name": "sonar", "seconds": 41.0, "ok": False}])
        expect("verdict-bounce", step["verdict"], "BOUNCE")
        expect_true("agent-present", "agent" in step)


def author_step(folder: Path) -> None:
    root = new_repo(folder, seed=False)
    config = Config(root=root, raw={"git": {"base": "main"}})
    perf_trees.record_start(config, "t")
    calls = {"n": 0}

    def fake_invoke(state: Run, label: str, prompt: str) -> None:
        calls["n"] += 1
        report = Path(re.search(r"Write your verdict to (\S+) and", prompt).group(1))
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("VERDICT: AUTHOR\n" if calls["n"] == 1 else "VERDICT: PASS\n")
        (state.folder / f"{label}.json").write_text(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0, "result": "ok"}))
        state.attempt_agent = {
            "backend": "claude",
            "model": "claude",
            "effort": None,
            "account": None,
            "minutes": 0.0,
            "summary": "ok",
        }

    state = Run(config=config, task=root / "tasks" / "t.md", model=None, retries=2, agent="claude")
    original = runner.invoke
    runner.invoke = fake_invoke  # type: ignore[assignment]
    saved_progress = runner.JudgeProgress

    @dataclass
    class ZeroAuthor(saved_progress):  # type: ignore[valid-type,misc]
        author_left: int = 0

    runner.JudgeProgress = ZeroAuthor  # type: ignore[misc,assignment]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run_judge(state, cast(Judge, find("perf")))
    finally:
        runner.invoke = original
        runner.JudgeProgress = saved_progress
    steps = load_timeline(root)["steps"]
    verdicts = [step.get("verdict") for step in steps]
    expect_true("author-then-pass", "AUTHOR" in verdicts and "PASS" in verdicts)
    expect_true("author-before-pass", verdicts.index("AUTHOR") < verdicts.index("PASS"))


def rate_limit_wait(folder: Path) -> None:
    root = new_repo(folder)
    stub = folder / "rate-stub"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, re, subprocess, sys\n"
        "from pathlib import Path\n"
        "prompt = sys.stdin.read()\n"
        "count = Path(os.environ['CALLS'])\n"
        "n = int(count.read_text()) if count.exists() else 0\n"
        "count.write_text(str(n + 1))\n"
        "if n == 0:\n"
        '    print(json.dumps({"is_error": True, "result": "rate limit exceeded"}))\n'
        "    raise SystemExit(0)\n"
        "root = Path.cwd()\n"
        'Path("src.py").write_text("def add_one(x):\\n    return x + 1\\n")\n'
        'Path("tests").mkdir(exist_ok=True)\n'
        'Path("tests/test_src.py").write_text('
        '"def test_adds_one():\\n    pass\\ndef test_rejects_bad_input():\\n    pass\\n")\n'
        'subprocess.run(["git", "add", "src.py", "tests"], cwd=root, check=True)\n'
        'subprocess.run(["git", "commit", "-qm", "code\\n\\nBy coder."], cwd=root, check=True)\n'
        'path = root / re.search(r"Write (\\.marestail/\\S+?):", prompt).group(1)\n'
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        'path.write_text("coded\\n## Audit\\n'
        "- Adds one -> tests/test_src.py::test_adds_one\\n"
        '- Rejects bad input -> tests/test_src.py::test_rejects_bad_input\\n")\n'
        'print(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0.01, "result": "ok"}))\n'
    )
    stub.chmod(0o755)
    (folder / "calls").write_text("0")
    result = run_cli(
        root,
        "tasks/t.md",
        "--from",
        "coder",
        "--to",
        "coder",
        "--auto",
        "--retries",
        "1",
        env={
            "MARESTAIL_CLAUDE": str(stub),
            "MARESTAIL_LIMIT_WAIT_SECONDS": "0",
            "CALLS": str(folder / "calls"),
        },
    )
    expect_true("rate-exit", result.returncode == 0)
    expect_true("rate-stdout", "rate limited; waiting" in result.stdout)
    step = load_timeline(root)["steps"][0]
    expect("rate-wait", {"reason": "rate-limit", "seconds": 0} in step["waits"], True)


def stub_dandelion(folder: Path) -> Path:
    binary = folder / "dandelion"
    binary.write_text(
        '#!/bin/sh\necho "$*" >> "$CALLS"\nline=$(head -n 1 "$PLAN"); sed -i 1d "$PLAN"\nprintf "%s\\n" "${line#* }"; exit "${line%% *}"\n'
    )
    binary.chmod(0o755)
    return binary


def dandelion_wait(folder: Path) -> None:
    root = new_repo(folder)
    stub_dandelion(folder)
    (folder / "plan").write_text("1 none\n0 claude-opus-5 high claude\n")
    (folder / "calls").write_text("")
    result = run_cli(
        root,
        "tasks/t.md",
        "--from",
        "coder",
        "--to",
        "coder",
        "--auto",
        "--retries",
        "1",
        "--model",
        "dandelion/route",
        env={
            **stub_env(folder, "code\n"),
            "MARESTAIL_DANDELION": str(folder / "dandelion"),
            "MARESTAIL_LIMIT_WAIT_SECONDS": "0",
            "PLAN": str(folder / "plan"),
            "CALLS": str(folder / "calls"),
        },
    )
    expect_true("dandelion-exit", result.returncode == 0)
    expect_true("dandelion-stdout", bool(re.search(r"dandelion/route:", result.stdout)))
    step = load_timeline(root)["steps"][0]
    expect("dandelion-wait", {"reason": "dandelion-unrouted", "seconds": 0} in step["waits"], True)


def two_attempts(folder: Path) -> None:
    root = new_repo(folder)
    result = run_cli(
        root,
        "tasks/t.md",
        "--from",
        "coder",
        "--to",
        "coder",
        "--auto",
        "--retries",
        "2",
        env=stub_env(folder, "code bad-audit\ncode\n"),
    )
    expect_true("two-exit", result.returncode == 0)
    data = load_timeline(root)
    expect("two-steps", len(data["steps"]), 2)
    expect("attempts", [step["attempt"] for step in data["steps"]], [1, 2])
    md = timeline_md(root)
    expect("two-headings", len(re.findall(r"^## \d+\-", md, re.M)), 2)
    expect("ids-match", [step["id"] for step in data["steps"]], re.findall(r"^## (\S+)", md, re.M))


def overnight_embeds(folder: Path) -> None:
    root = new_repo(folder)
    env = {
        **os.environ,
        **stub_env(folder, "code\n"),
        "PATH": f"{ROOT / 'bin'}:{os.environ.get('PATH', '')}",
        "START_FROM": "coder",
        "STOP_AT": "coder",
    }
    for key in ("MARESTAIL_AGENT", "AGENT", "MODEL", "EFFORT", "SCOPE", "FOCUS"):
        env.pop(key, None)
    result = subprocess.run(
        ["bash", str(HERE / "overnight.sh"), "tasks/t.md"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    expect_true("overnight-exit", result.returncode == 0)
    summaries = list((root / ".marestail" / "runs").glob("overnight-*.md"))
    expect_true("overnight-summary", bool(summaries))
    text = summaries[0].read_text()
    expect_true("overnight-section", "### tasks/t.md" in text)
    expect_true("overnight-exit-line", "exit 0 after" in text)
    expect_true("overnight-timeline", "timeline.md" in text or bool(re.search(r"\d+-coder", text)))


def freeze_lists_timeline() -> None:
    text = (ROOT / "marestail" / "freeze.py").read_text()
    expect_true("freeze-md", "timeline.md" in text)
    expect_true("freeze-json", "timeline.json" in text)


def readme_documents() -> None:
    text = (ROOT / "README.md").read_text()
    overnight = text.split("## Overnight", 1)[1].split("## ", 1)[0]
    expect_true("readme-md", "timeline.md" in overnight)
    expect_true("readme-json", "timeline.json" in overnight)
    expect("no-pipeline-log", ".marestail/runs/<task>/pipeline.log" in overnight, False)


def schema_helpers() -> None:
    results = [Result(gate="sonar", ok=False, summary="fail", seconds=41.0)]
    expect("gate-entries", timeline.gate_entries(results), [{"name": "sonar", "seconds": 41.0, "ok": False}])
    expect("verdict-pass", timeline.verdict_text("PASS", None), "PASS")
    expect("verdict-bounce-target", timeline.verdict_text("BOUNCE", "specifier"), "BOUNCE specifier")
    expect("verdict-bounce", timeline.verdict_text("BOUNCE", None), "BOUNCE")
    expect("verdict-author", timeline.verdict_text("AUTHOR", None), "AUTHOR")
    expect("first-paragraph", timeline.first_paragraph("one\n\ntwo"), "one")
    expect("utc-z", bool(ISO_Z.match(timeline.utc_now())), True)
    step = timeline.build_step(
        "01-coder",
        "coder",
        1,
        "2026-01-01T00:00:00.000Z",
        "2026-01-01T00:01:00.000Z",
        [],
        [],
        {"backend": "claude", "model": "claude", "effort": None, "account": None, "minutes": 0.0, "summary": "ok"},
        None,
        [{"hash": "abc", "subject": "add"}],
        ["src.py"],
        "coded",
    )
    expect(
        "key-order",
        list(step.keys()),
        ["id", "role", "attempt", "started_at", "ended_at", "gate", "waits", "agent", "commits", "files", "done"],
    )
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        timeline.append_step(folder, "t", step)
        loaded = timeline.load_document(folder, "t")
        expect("load-task", loaded["task"], "t")
        expect("load-steps", len(loaded["steps"]), 1)
        expect_true("md-written", (folder / "timeline.md").exists())
        expect("done-handoff", timeline.done_line(folder / "missing", [{"hash": "a", "subject": "subj"}], None), "subj")
        handoff = folder / "h.md"
        handoff.write_text("para one\n\npara two\n")
        expect("done-para", timeline.done_line(handoff, [], {"summary": "sum"}), "para one")
        expect("done-summary", timeline.done_line(folder / "gone", [], {"summary": "sum"}), "sum")
        expect("format-list", timeline.format_value([1]), "[1]")
        expect("format-dict", timeline.format_value({"a": 1}), '{"a": 1}')
        expect("format-str", timeline.format_value("x\ny"), "x y")
        expect("ordered", timeline.ordered_step({"files": [], "id": "1", "role": "coder"}), {"id": "1", "role": "coder", "files": []})


def timeline_diagnostic_passes() -> None:
    schema_helpers()
    freeze_lists_timeline()
    readme_documents()
    with tempfile.TemporaryDirectory(prefix="marestail-timeline-") as temp:
        base = Path(temp)
        coder_commits_timeline(base / "coder")
        hardener_gate_fail(base / "hardener")
        author_step(base / "author")
        rate_limit_wait(base / "rate")
        dandelion_wait(base / "dandelion")
        two_attempts(base / "two")
        overnight_embeds(base / "overnight")


if __name__ == "__main__":
    timeline_diagnostic_passes()
    print("timeline ok")
