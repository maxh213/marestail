import os
import re

ENV = "MARESTAIL_RUN_SUFFIX"
SONAR_KEY_REQUIRED = "marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set"
_PATTERN = re.compile(r"[a-z0-9-]{1,32}")


def current() -> str | None:
    raw = os.environ.get(ENV)
    if raw is None:
        return None
    if _matches(raw):
        return raw
    raise SystemExit(_rejected(raw))


def named(name: str) -> str:
    suffix = current()
    if suffix is None:
        return name
    return f"{name}_{suffix}"


def titled(name: str) -> str:
    suffix = current()
    if suffix is None:
        return name
    return f"{name} ({suffix})"


def argument(value: str) -> str:
    import argparse

    if _matches(value):
        return value
    raise argparse.ArgumentTypeError(_flag_text(value))


def _matches(value: str) -> bool:
    return _PATTERN.fullmatch(value) is not None


def _rejected(raw: str) -> str:
    return f"{ENV} {_flag_text(raw)}"


def _flag_text(raw: str) -> str:
    return f"must be 1 to 32 characters from a-z, 0-9 and -, got {raw!r}"
