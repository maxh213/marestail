import os
import subprocess
from pathlib import Path
from typing import NoReturn

from marestail.config import Config

DEFAULT = 19
OOM_SCORE = 500
ENV = "MARESTAIL_NICE"


def level(config: Config | None = None) -> int | None:
    parsed = parse(_setting(config))
    if parsed is None:
        return None
    return min(parsed, DEFAULT)


def parse(raw: object) -> int | None:
    if raw is True:
        return DEFAULT
    if raw in (False, None, ""):
        return None
    return _accepted(_as_int(raw))


def apply(config: Config | None = None) -> None:
    value = level(config)
    if value is None:
        return
    _drop_priority(value)
    _idle_io()
    _prefer_oom()


def _setting(config: Config | None) -> object:
    if os.environ.get("MARESTAIL_NICE") is not None:
        return os.environ[ENV]
    return _configured(config)


def _configured(config: Config | None) -> object:
    if config is None:
        return DEFAULT
    return config.get("run", "nice", DEFAULT)


def _as_int(raw: object) -> int:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        return _text_int(raw)
    _refuse(repr(raw))


def _text_int(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        _refuse(repr(raw))


def _accepted(value: int) -> int | None:
    if value < 0:
        _refuse(value)
    if value == 0:
        return None
    return value


def _refuse(shown: object) -> NoReturn:
    raise SystemExit(f"nice must be an integer 0-19, got {shown}")


def _drop_priority(value: int) -> None:
    try:
        os.setpriority(os.PRIO_PROCESS, 0, value)
    except (AttributeError, OSError):
        return


def _idle_io() -> None:
    try:
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], check=False, capture_output=True)
    except OSError:
        return


def _prefer_oom() -> None:
    try:
        _score_path().write_text(str(OOM_SCORE))
    except OSError:
        return


def _score_path() -> Path:
    return Path(f"/proc/{os.getpid()}/oom_score_adj")
