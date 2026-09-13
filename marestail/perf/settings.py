import os
import re

from marestail.config import Config
from marestail.perf.results import Policy

DEFAULT_ROWS = 50000000
ROWS_ENV = "MARESTAIL_PERF_DB_ROWS"
DEFAULT_MIN_CHANGE = {"ms": 1.0}


def threshold_percent(config: Config) -> float:
    return float(config.get("perf", "threshold_percent", 10))


def min_runs(config: Config) -> int:
    return int(config.get("perf", "min_runs", 10))


def values_per_sample(config: Config) -> int:
    return int(config.get("perf", "values_per_sample", 200))


def p95_min_values(config: Config) -> int:
    return int(config.get("perf", "p95_min_values", 200))


def bootstrap_rounds(config: Config) -> int:
    return int(config.get("perf", "bootstrap", 500))


def control(config: Config) -> bool:
    return config.get("perf", "control", False) is True


def min_change(config: Config) -> tuple[tuple[str, float], ...]:
    configured = config.get("perf", "min_change", DEFAULT_MIN_CHANGE)
    if not isinstance(configured, dict):
        raise ValueError(f"[perf] min_change must be a table of unit = amount, e.g. {{ ms = 1 }}, got {configured}")
    return tuple(sorted((str(unit), float(amount)) for unit, amount in configured.items()))


def policy(config: Config) -> Policy:
    return Policy(
        min_runs=min_runs(config),
        values_per_sample=values_per_sample(config),
        p95_min_values=p95_min_values(config),
        threshold_percent=threshold_percent(config),
        min_change=min_change(config),
        bootstrap=bootstrap_rounds(config),
    )


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
