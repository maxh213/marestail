import os
import subprocess
from pathlib import Path

from marestail.config import Config

DEFAULT = 19
OOM_SCORE = 500
ENV = "MARESTAIL_NICE"


def level(config: Config | None = None) -> int | None:
    if ENV in os.environ:
        raw = os.environ[ENV]
    elif config is not None:
        raw = config.get("run", "nice", DEFAULT)
    else:
        raw = DEFAULT
    parsed = parse(raw)
    if parsed is None:
        return None
    return min(parsed, 19)


def parse(raw: object) -> int | None:
    if raw is True:
        return DEFAULT
    if raw in (False, None, ""):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw)
        except ValueError:
            raise SystemExit(f"nice must be an integer 0-19, got {raw!r}") from None
    else:
        raise SystemExit(f"nice must be an integer 0-19, got {raw!r}")
    if value < 0:
        raise SystemExit(f"nice must be an integer 0-19, got {value}")
    if value == 0:
        return None
    return value


def apply(config: Config | None = None) -> None:
    value = level(config)
    if value is None:
        return
    try:
        os.setpriority(os.PRIO_PROCESS, 0, value)
    except (AttributeError, OSError):
        pass
    idle_io()
    prefer_oom()


def idle_io() -> None:
    try:
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], check=False, capture_output=True)
    except OSError:
        pass


def prefer_oom() -> None:
    try:
        Path(f"/proc/{os.getpid()}/oom_score_adj").write_text(str(OOM_SCORE))
    except OSError:
        pass
