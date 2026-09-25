#!/usr/bin/env python3
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import install
from marestail.freeze import GATE_CONFIG

CLI = Path(__file__).resolve().parent.parent / "marestail" / "cli.py"
FULL_DONE = "edit marestail.toml and sonar-project.properties"
HARD_DONE = "left CLAUDE.md and AGENTS.md alone; edit marestail.toml and sonar-project.properties"


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def quiet() -> contextlib.AbstractContextManager[None]:
    return contextlib.redirect_stdout(io.StringIO())


def with_grok(tmp: Path) -> None:
    os.environ["GROK_HOME"] = str(tmp / "grok")


def run_cli(target: Path, *args: str) -> tuple[int, str]:
    completed = subprocess.run([sys.executable, str(CLI), "install", *args, str(target)], capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout + completed.stderr


def empty_target(tmp: Path) -> Path:
    target = tmp / "target"
    target.mkdir(parents=True)
    return target


def full_install_creates_gate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        target = empty_target(root)
        with quiet():
            install.install(target)
        expect("full-claude", install.GATE_MARKER in (target / "CLAUDE.md").read_text(), True)
        expect("full-agents", install.GATE_MARKER in (target / "AGENTS.md").read_text(), True)


def hard_install_creates_neither() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        target = empty_target(root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            install.install(target, hard=True)
        expect("hard-no-claude", (target / "CLAUDE.md").exists(), False)
        expect("hard-no-agents", (target / "AGENTS.md").exists(), False)
        expect("hard-line", HARD_DONE in buf.getvalue(), True)
        expect("hard-line-has-claude", "CLAUDE.md" in buf.getvalue().splitlines()[-1], True)


def hard_install_keeps_claude() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        target = empty_target(root)
        (target / "CLAUDE.md").write_text("team rules\n")
        with quiet():
            install.install(target, hard=True)
        expect("keep-claude", (target / "CLAUDE.md").read_text(), "team rules\n")
        expect("no-agents", (target / "AGENTS.md").exists(), False)


def hard_install_implies_gitignore() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        target = empty_target(root)
        with quiet():
            install.install(target, hard=True, gitignore_generated=False)
        text = (target / ".gitignore").read_text()
        for line in install.GITIGNORE_GENERATED_LINES:
            expect(f"gi-{line}", line in text, True)


def hard_install_writes_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        target = empty_target(root)
        with quiet():
            install.install(target, hard=True)
        expect("toml", (target / "marestail.toml").is_file(), True)
        expect("guidance", (target / "guidance" / "ts.md").is_file(), True)
        expect("hook", "marestail gate --hook" in (target / ".claude" / "settings.json").read_text(), True)


def cli_scope_hard_matches_api() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        api = empty_target(root / "api")
        cli_target = empty_target(root / "cli")
        with quiet():
            install.install(api, hard=True)
        code, out = run_cli(cli_target, "--scope", "hard")
        expect("cli-hard-exit", code, 0)
        expect("cli-hard-no-claude", (cli_target / "CLAUDE.md").exists(), False)
        expect("cli-hard-no-agents", (cli_target / "AGENTS.md").exists(), False)
        expect("cli-hard-line", HARD_DONE in out, True)
        expect("api-hard-no-claude", (api / "CLAUDE.md").exists(), False)


def cli_scope_all_matches_full() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        target = empty_target(root)
        code, out = run_cli(target, "--scope", "all")
        expect("cli-all-exit", code, 0)
        expect("cli-all-claude", install.GATE_MARKER in (target / "CLAUDE.md").read_text(), True)
        expect("cli-all-agents", install.GATE_MARKER in (target / "AGENTS.md").read_text(), True)
        expect("cli-all-line", out.rstrip().endswith(f"installed into {target.resolve()}; {FULL_DONE}"), True)
        expect("cli-all-no-claude-in-line", "CLAUDE.md" in out.splitlines()[-1], False)


def cli_scope_changed_is_full() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        target = empty_target(root)
        code, out = run_cli(target, "--scope", "changed")
        expect("cli-changed-exit", code, 0)
        expect("cli-changed-gate", install.GATE_MARKER in (target / "CLAUDE.md").read_text(), True)
        expect("cli-changed-line", "CLAUDE.md" in out.splitlines()[-1], False)


def gitignore_generated_still_appends_gate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with_grok(root)
        target = empty_target(root)
        with quiet():
            install.install(target, gitignore_generated=True)
        expect("gi-claude", install.GATE_MARKER in (target / "CLAUDE.md").read_text(), True)
        expect("gi-agents", install.GATE_MARKER in (target / "AGENTS.md").read_text(), True)


def freeze_still_lists_agent_docs() -> None:
    for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
        expect(f"freeze-{name}", name in GATE_CONFIG, True)


def readme_documents_hard() -> None:
    text = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    expect("readme-scope-hard", "install --scope hard" in text or "install . --scope hard" in text, True)
    expect("readme-leaves-alone", "leaves `CLAUDE.md` and `AGENTS.md` alone" in text, True)
    expect("readme-implies", "implies `--gitignore-generated`" in text, True)
    expect("readme-full-only", "On a full install" in text, True)


if __name__ == "__main__":
    full_install_creates_gate()
    hard_install_creates_neither()
    hard_install_keeps_claude()
    hard_install_implies_gitignore()
    hard_install_writes_tree()
    cli_scope_hard_matches_api()
    cli_scope_all_matches_full()
    cli_scope_changed_is_full()
    gitignore_generated_still_appends_gate()
    freeze_still_lists_agent_docs()
    readme_documents_hard()
    print("install-hard ok")
