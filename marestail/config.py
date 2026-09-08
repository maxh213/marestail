import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILENAME = "marestail.toml"


@dataclass(frozen=True)
class Config:
    root: Path
    raw: dict[str, Any]

    def section(self, name: str) -> dict[str, Any] | None:
        value = self.raw.get(name)
        return value if isinstance(value, dict) else None

    def get(self, section: str, key: str, default: Any = None) -> Any:
        found = self.section(section)
        return default if found is None else found.get(key, default)

    @property
    def work(self) -> Path:
        return self.root / ".marestail"


def find_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / FILENAME).exists():
            return candidate
    raise SystemExit(f"no {FILENAME} found above {start}")


def load(start: Path) -> Config:
    root = find_root(start.resolve())
    with (root / FILENAME).open("rb") as handle:
        raw = tomllib.load(handle)
    config = Config(root=root, raw=raw)
    config.work.mkdir(exist_ok=True)
    return config
