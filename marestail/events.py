import json
import os
import sys
from pathlib import Path
from typing import Any

from marestail.shell import ensure_dir

__all__ = ["Log"]

_FIELDS = {
    "run-start": (
        "task",
        "task_path",
        "branch",
        "base",
        "start_commit",
        "from",
        "to",
        "scope",
        "agent",
        "model",
        "effort",
    ),
    "step-start": ("step", "role", "kind", "attempt"),
    "step-end": ("step", "role", "attempt", "outcome", "bounce_to", "head", "handoff", "seconds"),
    "wait": ("reason", "until"),
    "proposal": ("step", "path", "files"),
    "run-end": ("result", "exit_code", "head", "stopped_at", "reason", "archive"),
}


class Log:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._warned = False

    def append(self, event: str, fields: dict[str, Any], at: str, pid: int) -> None:
        payload = _encoded_line(event, fields, at, pid)
        try:
            _write_line(self.path, payload)
        except OSError as err:
            self._warn(err)

    def _warn(self, err: OSError) -> None:
        if self._warned is False:
            self._warned = True
            sys.stderr.write(f"warning: cannot write run events to {self.path}: {err}\n")


def _encoded_line(event: str, fields: dict[str, Any], at: str, pid: int) -> bytes:
    data = {"v": 1, "at": at, "event": event, "pid": pid, **_payload(event, fields)}
    return (json.dumps(data) + "\n").encode()


def _payload(event: str, fields: dict[str, Any]) -> dict[str, Any]:
    keys = _FIELDS.get(event)
    if keys is None:
        return fields
    return {key: fields.get(key) for key in keys}


def _write_line(path: Path, payload: bytes) -> None:
    ensure_dir(path.parent)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    fd = os.open(path, flags, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
