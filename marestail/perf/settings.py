from marestail.config import Config


def threshold_percent(config: Config) -> float:
    return float(config.get("perf", "threshold_percent", 10))


def min_runs(config: Config) -> int:
    return int(config.get("perf", "min_runs", 10))


def sample_timeout(config: Config) -> int:
    return int(config.get("perf", "sample_timeout", 600))
