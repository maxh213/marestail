from pathlib import Path
from typing import Any

import pytest

from marestail.config import Config
from marestail.perf import settings
from marestail.perf.results import Policy


def config(raw: dict[str, Any] | None = None) -> Config:
    return Config(root=Path("/tmp"), raw=raw or {})


def test_db_default_is_an_empty_table(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    def get(_self: Config, section: str, key: str, default: Any = None) -> Any:
        seen.append((section, key, default))
        return default

    monkeypatch.setattr(Config, "get", get)
    assert settings.db(config()) == {}
    assert seen == [(settings.SECTION, "db", {})]


def test_control_default_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    def get(_self: Config, section: str, key: str, default: Any = None) -> bool:
        seen.append((section, key, default))
        return False

    monkeypatch.setattr(Config, "get", get)
    assert settings.control(config()) is False
    assert seen == [(settings.SECTION, "control", False)]


def test_defaults() -> None:
    plain = config()
    assert settings.threshold_percent(plain) == 10.0
    assert settings.min_runs(plain) == 10
    assert settings.values_per_sample(plain) == 200
    assert settings.p95_min_values(plain) == 200
    assert settings.bootstrap_rounds(plain) == 500
    assert settings.control(plain) is False
    assert settings.min_change(plain) == (("ms", 1.0),)
    assert settings.sample_timeout(plain) == 600
    assert settings.db(plain) == {}
    assert settings.policy(plain) == Policy()


def test_configured() -> None:
    raw = {
        "perf": {
            "threshold_percent": "5",
            "min_runs": 3,
            "values_per_sample": 250,
            "p95_min_values": 500,
            "bootstrap": 100,
            "control": True,
            "min_change": {"rows": 10, "ms": 2},
            "sample_timeout": 30,
            "db": {"migrate": "make db"},
        }
    }
    configured = config(raw)
    assert settings.policy(configured) == Policy(3, 250, 500, 5.0, (("ms", 2.0), ("rows", 10.0)), 100)
    assert settings.control(configured) is True
    assert settings.sample_timeout(configured) == 30
    assert settings.db(configured) == {"migrate": "make db"}


@pytest.mark.parametrize("value", ["yes", 1])
def test_control_needs_true(value: object) -> None:
    assert settings.control(config({"perf": {"control": value}})) is False


def test_db_must_be_table() -> None:
    assert settings.db(config({"perf": {"db": "nope"}})) == {}


def test_min_change_must_be_table() -> None:
    stated = config({"perf": {"min_change": 5}})
    with pytest.raises(ValueError, match=r"^\[perf\] min_change must be a table of unit = amount, e.g. \{ ms = 1 \}, got 5$"):
        settings.min_change(stated)


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (7, 7), ("12", 12), (" 3 ", 3), ("0", 0)])
def test_parse_rows(value: object, expected: int) -> None:
    assert settings.parse_rows(value) == expected


@pytest.mark.parametrize("value", [-1, True, False, "-1", "1.5", "", "x", 2.0, None])
def test_parse_rows_rejects(value: object) -> None:
    with pytest.raises(ValueError, match="^" + "\\[perf.db\\] rows must be a whole number ≥ 0, got " + str(value) + "$"):
        settings.parse_rows(value)


def test_effective_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(settings.ROWS_ENV, raising=False)
    assert settings.effective_rows(config()) == (50000000, "default")
    configured = config({"perf": {"db": {"rows": 1000}}})
    assert settings.effective_rows(configured) == (1000, "marestail.toml")
    monkeypatch.setenv("MARESTAIL_PERF_DB_ROWS", "0")
    assert settings.effective_rows(configured) == (0, "MARESTAIL_PERF_DB_ROWS")
    monkeypatch.setenv("MARESTAIL_PERF_DB_ROWS", "-1")
    with pytest.raises(ValueError, match=r"got -1$"):
        settings.effective_rows(configured)
