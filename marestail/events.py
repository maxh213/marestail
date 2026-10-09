import json
import os
import sys
from pathlib import Path
from typing import Any

from marestail.shell import ensure_dir

__all__ = ["Log"]


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
        if not self._warned:
            self._warned = True
            sys.stderr.write(f"warning: cannot write run events to {self.path}: {err}\n")


def _encoded_line(event: str, fields: dict[str, Any], at: str, pid: int) -> bytes:
    data = {"v": 1, "at": at, "event": event, "pid": pid, **fields}
    return (json.dumps(data) + "\n").encode("utf-8")


def _write_line(path: Path, payload: bytes) -> None:
    ensure_dir(path.parent)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    fd = os.open(path, flags, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
