#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import config as config_module
from marestail import context, prompts, rust
from marestail.config import Config
from marestail.pipeline import find
from marestail.runner import Run

CLI = Path(__file__).resolve().parent.parent / "marestail" / "cli.py"
TOML = '[git]\nbase = "main"\n\n[comments]\npaths = ["."]\n'


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(folder: Path) -> Path:
    repo = folder / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@marestail")
    git(repo, "config", "user.name", "test")
    (repo / "marestail.toml").write_text(TOML)
    (repo / ".gitignore").write_text(".marestail/\n")
    (repo / "t.md").write_text("task\n")
    for name in ("a", "b", "clean"):
        (repo / f"{name}.py").write_text(f"def {name}():\n    return 1\n")
    (repo / "old.py").write_text("# an old comment\nvalue = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "work")
    for name in ("a", "b"):
        (repo / f"{name}.py").write_text(f"# new comment in {name}\ndef {name}():\n    return 2\n")
    git(repo, "commit", "-qam", "work")
    return repo


def cli(repo: Path, *args: str, stdin: str = "", env: dict[str, str] | None = None) -> tuple[int, str]:
    merged = {key: value for key, value in os.environ.items() if key not in ("MARESTAIL_SCOPE", "MARESTAIL_FOCUS")}
    completed = subprocess.run(
        [sys.executable, str(CLI), *args], cwd=repo, input=stdin, capture_output=True, text=True, env={**merged, **(env or {})}, check=False
    )
    return completed.returncode, completed.stdout + completed.stderr


def mentions(output: str, *names: str) -> tuple[bool, ...]:
    return tuple(f"{name}:1" in output for name in names)


def gate_scopes(repo: Path) -> None:
    code, output = cli(repo, "gate", "--only", "comments")
    expect("all", (code, *mentions(output, "a.py", "b.py", "old.py")), (1, True, True, True))
    code, output = cli(repo, "gate", "--only", "comments", "--scope", "changed")
    expect("changed", (code, *mentions(output, "a.py", "b.py", "old.py")), (1, True, True, False))
    code, output = cli(repo, "gate", "--only", "comments", "--scope", "hard", "--focus", "a.py")
    expect("hard-changed-file", (code, *mentions(output, "a.py", "b.py", "old.py")), (1, True, False, False))
    code, output = cli(repo, "gate", "--only", "comments", "--scope", "hard", "--focus", "old.py")
    expect("hard-untouched-file", (code, *mentions(output, "a.py", "b.py", "old.py")), (1, False, False, True))
    code, output = cli(repo, "gate", "--only", "comments", "--scope", "hard", "--focus", "clean.py")
    expect("hard-clean", (code, "scope: hard: clean.py" in output), (0, True))
    code, output = cli(repo, "gate", "--only", "comments", "--scope", "hard", "--focus", "clean.py", "--json")
    expect("hard-json", (code, json.loads(output)["scope"]), (0, "hard"))
    code, output = cli(repo, "gate", "--only", "comments", "--scope", "hard")
    expect("hard-needs-focus", (code != 0, "needs at least one focus path" in output), (True, True))
    (repo / "marestail.toml").write_text(TOML + '\n[focus]\npaths = ["clean.py"]\n')
    code, output = cli(repo, "gate", "--only", "comments", "--scope", "hard")
    expect("hard-config-focus", code, 0)
    (repo / "marestail.toml").write_text(TOML)


def hooks(repo: Path) -> None:
    def hook(session: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        return cli(repo, "gate", "--hook", stdin=json.dumps({"cwd": str(repo), "session_id": session}), env=env)

    code, output = hook("plain")
    expect("hook-soft", (code, *mentions(output, "a.py", "old.py")), (2, True, False))
    code, output = hook("hard", {"MARESTAIL_SCOPE": "hard", "MARESTAIL_FOCUS": "clean.py"})
    expect("hook-hard", (code, output), (0, ""))
    code, output = hook("hard-old", {"MARESTAIL_SCOPE": "hard", "MARESTAIL_FOCUS": os.pathsep.join(["clean.py", "old.py"])})
    expect("hook-hard-focus", (code, *mentions(output, "a.py", "old.py")), (2, False, True))
    code, output = hook("soft-focus", {"MARESTAIL_SCOPE": "changed", "MARESTAIL_FOCUS": "old.py"})
    expect("hook-soft-run-focus", (code, *mentions(output, "a.py", "old.py")), (2, True, True))


def context_rules(repo: Path) -> None:
    config = config_module.load(repo)
    ctx = context.build(config, True, {"old.py"}, hard=True)
    expect("ctx-changed", (ctx.changed, ctx.changed_lines_map), (set(), {}))
    expect("ctx-in-scope", (ctx.in_scope("a.py"), ctx.in_scope("old.py")), (False, True))
    expect("ctx-gated-lines", (ctx.gated_lines("old.py"), ctx.gated_lines("a.py")), ({1, 2}, None))
    expect("ctx-mutation", ctx.mutation_files("python", repo, (".py",)).files, ["old.py"])
    expect("ctx-names", (ctx.scope_name, ctx.scope_summary()), ("hard", "hard: old.py"))
    soft = context.build(config, True, {"old.py"})
    expect("soft-still-diff", (soft.in_scope("a.py"), soft.scope_name), (True, "changed"))
    focused = context.Context(config=Config(root=repo, raw={"rust": {"root": "."}}), scope_changed=True, focus={"src/lib.rs"})
    expect("rust-focus", rust.in_scope(focused, [repo / "src" / "lib.rs", repo / "src" / "other.rs"]), [repo / "src" / "lib.rs"])


def prompt_text(repo: Path) -> None:
    config = config_module.load(repo)
    task = repo / "t.md"
    report = repo / ".marestail" / "handoffs" / "t" / "01-coder.md"
    hard = prompts.worker_prompt(config, find("coder"), task, "t", report, "", "m", " --scope hard --focus old.py", {"old.py"})
    expect("worker-scope", ("# Scope" in hard, "hard scope: `old.py`" in hard, "--scope hard --focus old.py" in hard), (True, True, True))
    expect("worker-no-scope", "# Scope" in prompts.worker_prompt(config, find("coder"), task, "t", report, ""), False)
    judged = prompts.judge_prompt(config, find("hardener"), task, "t", report, "", "", "", {"old.py"})
    expect("judge-scope", "bounce only when the diff goes beyond" in judged, True)
    hard_run = Run(config=config, task=task, model=None, retries=0, scope_changed=True, focus={"old.py"}, hard=True)
    soft_run = Run(config=config, task=task, model=None, retries=0, scope_changed=True, focus={"old.py"})
    expect("run-flags", (hard_run.gate_flags, hard_run.hard_focus), (" --scope hard --focus old.py", {"old.py"}))
    expect("soft-run-flags", (soft_run.gate_flags, soft_run.hard_focus), (" --scope changed --focus old.py", None))


def run_validation(repo: Path) -> None:
    code, output = cli(repo, "run", "t.md", "--scope", "hard")
    expect("run-hard-needs-focus", (code, "needs at least one focus path" in output), (1, True))
    code, output = cli(repo, "run", "t.md", "--scope", "all", "--focus", "old.py")
    expect("run-all-focus", (code, "cannot be combined with --scope all" in output), (1, True))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="marestail-hard-scope-") as temp:
        repo = make_repo(Path(temp))
        gate_scopes(repo)
        hooks(repo)
        context_rules(repo)
        prompt_text(repo)
        run_validation(repo)
    print("hard scope ok")
