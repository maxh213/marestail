#!/usr/bin/env python3
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import audit
from marestail.config import Config
from marestail.pipeline import find
from marestail.runner import Run, problem_shape, run_worker

FEATURE = "Feature: adding\n  Scenario: Adds one\n    Then it adds\n  Scenario: Probes the live session\n    Then it probes\n"
HANDOFF = "## Audit\n- Adds one -> tests/test_src.py::test_adds_one\n- Probes the live session -> qa/t.e2e.mjs::probing\n"


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def new_repo(folder: Path) -> Path:
    root = folder / "repo"
    root.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@marestail"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    write(root, ".gitignore", ".marestail/\n")
    write(root, "marestail.toml", '[git]\nbase = "main"\n')
    write(root, "tasks/t.md", "# Add one\n")
    write(root, "features/t.feature", FEATURE)
    write(root, "tests/test_src.py", "def test_adds_one():\n    assert 1 + 1 == 2\n")
    write(root, "qa/t.e2e.mjs", "export async function probing() {}\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def audit_rules(root: Path) -> None:
    config = Config(root=root, raw={})
    found = audit.problems(config, "t", HANDOFF)
    expect("frozen-trace-count", len(found), 1)
    expect("frozen-trace-names-qa-role", "cannot edit" in found[0] and "QA role" in found[0] and "qa/t.e2e.mjs::probing" in found[0], True)
    expect("frozen-trace-not-reported-missing", any("not found" in problem for problem in found), False)
    expect("writable-trace-passes", audit.problems(config, "t", "## Audit\n- Adds one -> tests/test_src.py::test_adds_one\n- Probes the live session -> tests/test_src.py::test_adds_one\n"), [])
    expect("missing-test-still-reported", audit.problems(config, "t", "- Adds one -> tests/test_src.py::test_gone\n- Probes the live session -> tests/test_src.py::test_adds_one\n"), ["audit: tests/test_src.py::test_gone not found"])
    expect("specifier-may-trace-qa", audit.problems(config, "t", HANDOFF, "specifier"), [])
    instructions = audit.instructions(config, "t")
    expect("instructions-warn-about-qa", "`qa/`" in instructions and "QA role" in instructions, True)


def stub_agent(folder: Path, body: str) -> None:
    stub = folder / "agent"
    stub.write_text("#!/bin/sh\ncat > /dev/null\n" + body + 'echo "x" >> "$CALLS"\nprintf \'{"result": "done", "num_turns": 1, "total_cost_usd": 0}\'\n')
    stub.chmod(0o755)
    os.environ["MARESTAIL_CLAUDE"] = str(stub)
    os.environ["CALLS"] = str(folder / "calls")


def calls(folder: Path) -> int:
    path = folder / "calls"
    count = len(path.read_text().splitlines()) if path.exists() else 0
    path.unlink(missing_ok=True)
    return count


def loop_guard(folder: Path, root: Path) -> None:
    config = Config(root=root, raw={"git": {"base": "main"}})
    state = Run(config=config, task=root / "tasks" / "t.md", model=None, retries=0, agent="claude")
    stub_agent(folder, "")
    with contextlib.redirect_stdout(io.StringIO()) as out:
        passed = run_worker(state, find("specifier"), "")
    expect("same-problems-stop", (passed, calls(folder)), (False, 3))
    expect("stop-message", "same problems back 3 times in a row" in out.getvalue(), True)
    state = Run(config=config, task=root / "tasks" / "t.md", model=None, retries=5, agent="claude")
    stub_agent(folder, f'if [ -f "{root}/alpha.txt" ]; then rm "{root}/alpha.txt"; else echo a > "{root}/alpha.txt"; fi\n')
    with contextlib.redirect_stdout(io.StringIO()):
        passed = run_worker(state, find("specifier"), "")
    expect("changing-problems-keep-retrying", (passed, calls(folder)), (False, 5))
    (root / "alpha.txt").unlink(missing_ok=True)
    expect("shape-ignores-numbers", problem_shape("missing handoff 01-coder.md (12.3s)"), problem_shape("missing handoff 02-coder.md  (9.8s)"))
    expect("shape-keeps-words", problem_shape("missing handoff") == problem_shape("uncommitted changes"), False)


if __name__ == "__main__":
    saved = {key: os.environ.get(key) for key in ("MARESTAIL_CLAUDE", "MARESTAIL_AGENT", "CALLS")}
    os.environ.pop("MARESTAIL_AGENT", None)
    try:
        with tempfile.TemporaryDirectory(prefix="marestail-audit-test-") as temp:
            folder = Path(temp)
            root = new_repo(folder)
            audit_rules(root)
            loop_guard(folder, root)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("audit ok")
