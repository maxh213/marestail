import os
import re

from marestail.config import Config

ENV = "MARESTAIL_JOBS"
_KEY = "[run] jobs"
_WHOLE = re.compile(r"[0-9]+")


def parse(raw: object, name: str) -> int:
    value = _whole(raw)
    if value is None or value < 1:
        raise SystemExit(f"{name} must be a whole number 1 or more, got {raw!r}")
    return value


def _whole(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    return _whole_text(raw)


def _whole_text(raw: object) -> int | None:
    if isinstance(raw, str) and _WHOLE.fullmatch(raw):
        return int(raw)
    return None


def run_jobs(config: Config) -> int | None:
    chosen = _variable()
    configured = _run_key(config)
    if chosen is not None:
        return _capped(chosen)
    return configured


def pick(config: Config, section: str, key: str, default: int) -> int:
    chosen = _variable()
    if chosen is not None:
        _run_key(config)
        return _capped(chosen)
    if _present(config, section, key):
        own: int = config.get(section, key)
        return own
    configured = _run_key(config)
    return default if configured is None else configured


def _variable() -> int | None:
    raw = os.environ.get(ENV)
    if not raw:
        return None
    return parse(raw, ENV)


def _run_key(config: Config) -> int | None:
    raw = config.get("run", "jobs")
    if raw is None:
        return None
    return _capped(parse(raw, _KEY))


def _present(config: Config, section: str, key: str) -> bool:
    found = config.section(section)
    return found is not None and key in found


def _capped(value: int) -> int:
    return min(value, os.cpu_count() or 1)
