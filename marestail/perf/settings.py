import os
import re

from marestail.config import Config

DEFAULT_ROWS = 50000000
ROWS_ENV = "MARESTAIL_PERF_DB_ROWS"


def threshold_percent(config: Config) -> float:
    return float(config.get("perf", "threshold_percent", 10))


def min_runs(config: Config) -> int:
    return int(config.get("perf", "min_runs", 10))


def sample_timeout(config: Config) -> int:
    return int(config.get("perf", "sample_timeout", 600))


def db(config: Config) -> dict:
    section = config.get("perf", "db", {})
    return section if isinstance(section, dict) else {}


def effective_rows(config: Config) -> tuple[int, str]:
    if ROWS_ENV in os.environ:
        return parse_rows(os.environ[ROWS_ENV]), ROWS_ENV
    if "rows" in db(config):
        return parse_rows(db(config)["rows"]), "marestail.toml"
    return DEFAULT_ROWS, "default"


def parse_rows(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value)
    raise ValueError(f"[perf.db] rows must be a whole number ≥ 0, got {value}")
