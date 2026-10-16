import os
import re
import subprocess
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PARENTS = True
EXIST_OK = True


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=PARENTS, exist_ok=EXIST_OK)


def run(
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int | None = 3600,
    stdin: str | None = None,
) -> tuple[int, str]:
    merged = {**os.environ, **(env or {})}
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=merged,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        return 127, f"{command[0]}: not found ({error})"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(command)}: timed out after {timeout}s"
    return completed.returncode, clean(completed.stdout + completed.stderr)


def clean(text: str) -> str:
    return ANSI.sub("", text).replace("\r", "")


def tail(text: str, count: int = 30) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-count:]
