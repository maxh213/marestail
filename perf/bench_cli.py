#!/usr/bin/env python3
import subprocess
import sys

import harness

root = harness.use_tree()
cli = root / "marestail" / "cli.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run([sys.executable, str(cli), *args], cwd=root, capture_output=True, text=True)
    if completed.returncode not in (0, 1, 2):
        raise RuntimeError(completed.stderr[-200:] or completed.stdout[-200:] or f"exit {completed.returncode}")
    return completed


def help_text() -> None:
    run("--help")


def gate_help() -> None:
    run("gate", "--help")


harness.emit_one("CLI startup", harness.measure_one(help_text))
harness.emit("marestail --help", harness.measure(help_text))
harness.emit("marestail gate --help", harness.measure(gate_help))
