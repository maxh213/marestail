#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.tui import collect, panels

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLI = ROOT / "marestail" / "cli.py"
STUB = HERE / "stub-claude"
FEATURE = "Feature: t\n  Scenario: Adds one\n    Given x\n"
HARNESS_LINE = "pipeline complete, NOT verified against the running app (qa ran against a harness)"
NOTHING_LINE = "pipeline complete, NOT verified against the running app (qa ran against nothing)"


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


def new_repo(folder: Path) -> Path:
    root = folder / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    write(root, "README.md", "# repo\n")
    write(root, "marestail.toml", '[git]\nbase = "main"\n')
    write(root, ".gitignore", ".marestail/\n")
    write(root, "tasks/t.md", "# Add one\n")
    write(root, "features/t.feature", FEATURE)
    write(root, "qa/t.md", "1. open\n")
    write(root, "src.py", "ok\n")
    write(root, "tests/test_src.py", "def test_adds_one():\n    pass\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def plan_file(folder: Path, lines: str) -> Path:
    path = folder / "plan.txt"
    path.write_text(lines if lines.endswith("\n") else lines + "\n")
    return path


def stub_env(folder: Path, plan: str) -> dict[str, str]:
    return {"MARESTAIL_CLAUDE": str(STUB), "STUB_PLAN": str(plan_file(folder, plan))}


def last_nonempty(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


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


def qa_window(folder: Path, plan: str) -> subprocess.CompletedProcess[str]:
    root = new_repo(folder)
    return run_cli(root, "tasks/t.md", "--from", "qa", "--to", "qa", "--auto", "--retries", "2", env=stub_env(folder, plan))


def expect_ending(name: str, result: subprocess.CompletedProcess[str], line: str, code: int) -> None:
    expect(f"{name}-exit", result.returncode, code)
    expect(f"{name}-line", last_nonempty(result.stdout), line)


def ran_against_app(folder: Path) -> None:
    expect_ending("app", qa_window(folder, "worker qa app\n"), "pipeline complete", 0)


def ran_against_harness(folder: Path) -> None:
    expect_ending("harness", qa_window(folder, "worker qa harness\n"), HARNESS_LINE, 3)


def ran_against_nothing(folder: Path) -> None:
    expect_ending("nothing", qa_window(folder, "worker qa nothing\n"), NOTHING_LINE, 3)


def missing_then_still_missing(folder: Path) -> None:
    result = qa_window(folder, "worker qa omit\nworker qa omit\n")
    expect_true("missing-retry", result.stdout.count("== qa (") == 2)
    expect_ending("missing-still", result, NOTHING_LINE, 3)


def missing_then_app(folder: Path) -> None:
    expect_ending("missing-app", qa_window(folder, "worker qa omit\nworker qa app\n"), "pipeline complete", 0)


def sentence_counts_as_missing(folder: Path) -> None:
    expect_ending("sentence", qa_window(folder, "worker qa sentence\nworker qa omit\n"), NOTHING_LINE, 3)


def to_hardener_unchanged(folder: Path) -> None:
    root = new_repo(folder)
    result = run_cli(
        root,
        "tasks/t.md",
        "--from",
        "hardener",
        "--to",
        "hardener",
        "--auto",
        "--retries",
        "1",
        env=stub_env(folder, "judge PASS\n"),
    )
    expect_ending("hardener", result, "pipeline complete", 0)
    expect_true("hardener-no-not-verified", "NOT verified" not in result.stdout)


def overnight_stops_on_exit_3(folder: Path) -> None:
    root = new_repo(folder)
    env = {
        **os.environ,
        **stub_env(folder, "worker qa harness\n"),
        "PATH": f"{ROOT / 'bin'}:{os.environ.get('PATH', '')}",
        "START_FROM": "qa",
        "STOP_AT": "qa",
    }
    for key in ("MARESTAIL_AGENT", "AGENT", "MODEL", "EFFORT", "SCOPE", "FOCUS"):
        env.pop(key, None)
    result = subprocess.run(
        ["bash", str(HERE / "overnight.sh"), "tasks/t.md", "tasks/t.md"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    expect("overnight-exit", result.returncode, 3)
    summaries = list((root / ".marestail" / "runs").glob("overnight-*.md"))
    expect_true("overnight-summary", bool(summaries))
    text = summaries[0].read_text()
    expect_true("overnight-not-verified", "NOT verified" in text)
    expect_true("overnight-exit-line", bool(re.search(r"- exit 3 after", text)))
    expect_true("overnight-stopped", text.count("### tasks/t.md") == 1)


def watch_dead_bed_not_verified(folder: Path) -> None:
    root = folder / "bed"
    root.mkdir(parents=True)
    runs = root / ".marestail" / "runs"
    runs.mkdir(parents=True)
    (runs / "overnight-20200101T0000.log").write_text(f"== qa (01-qa) attempt 1\n{HARNESS_LINE}\n")
    state = collect.collect_repo(root)
    expect_true("dead", state.alive is False)
    expect_true("activity", state.runner_activity is not None and "NOT verified" in state.runner_activity)
    row = panels.dead_row_text(state)
    expect_true("row-not-verified", "NOT verified" in row)
    expect_true("row-not-idle-only", row != panels.IDLE_TEXT)


def roles_and_readme() -> None:
    qa = (ROOT / "roles" / "qa.md").read_text()
    expect_true("roles-app", "ran-against: app" in qa)
    expect_true("roles-harness", "ran-against: harness" in qa)
    expect_true("roles-nothing", "ran-against: nothing" in qa)
    expect_true("roles-no-fake-app", "must not claim" in qa and "app" in qa and "stand-in" in qa)
    pipeline = (ROOT / "README.md").read_text().split("## Pipeline", 1)[1].split("## ", 1)[0]
    expect_true("readme-app", "app" in pipeline and "ran-against" in pipeline)
    expect_true("readme-harness", "harness" in pipeline)
    expect_true("readme-nothing", "nothing" in pipeline)
    expect_true("readme-exit-3", "exit code 3" in pipeline or "exit 3" in pipeline)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ran_against_app(base / "app")
        ran_against_harness(base / "harness")
        ran_against_nothing(base / "nothing")
        missing_then_still_missing(base / "missing")
        missing_then_app(base / "missing-app")
        sentence_counts_as_missing(base / "sentence")
        to_hardener_unchanged(base / "hardener")
        overnight_stops_on_exit_3(base / "overnight")
        watch_dead_bed_not_verified(base / "watch")
        roles_and_readme()
    print("ok")


if __name__ == "__main__":
    main()
