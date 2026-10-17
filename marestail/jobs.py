import os
import re

from marestail.config import Config

ENV = "MARESTAIL_JOBS"
_RUN_JOBS = "[run] jobs"
_WHOLE = re.compile(r"\d+", re.ASCII)


def parse(raw: object, name: str) -> int:
    value = _as_int(raw)
    if value is None or value < 1:
        raise SystemExit(f"{name} must be a whole number 1 or more, got {raw!r}")
    return value


def _as_int(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    return _digits(raw)


def _digits(raw: object) -> int | None:
    if isinstance(raw, str) and _WHOLE.fullmatch(raw):
        return int(raw)
    return None


def run_jobs(config: Config) -> int | None:
    from_env = _from_env()
    from_file = _from_file(config)
    return from_env if from_env is not None else from_file


def worker_args(config: Config, flag: str) -> list[str]:
    count = run_jobs(config)
    return [] if count is None else [flag, str(count)]


def pick(config: Config, section: str, key: str, default: int) -> int:
    from_env = _from_env()
    if from_env is not None:
        _reject_bad_file(config)
        return from_env
    if _present(config, section, key):
        raw: int = config.get(section, key)
        return raw
    from_file = _from_file(config)
    return default if from_file is None else from_file


def _from_env() -> int | None:
    raw = os.environ.get(ENV)
    if not raw:
        return None
    return _capped(parse(raw, ENV))


def _from_file(config: Config) -> int | None:
    raw = config.get("run", "jobs")
    if raw is None:
        return None
    return _capped(parse(raw, _RUN_JOBS))


def _reject_bad_file(config: Config) -> None:
    _from_file(config)


def _present(config: Config, section: str, key: str) -> bool:
    found = config.section(section)
    return found is not None and key in found


def _capped(value: int) -> int:
    return min(value, os.cpu_count() or 1)
