import json
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable

VALUES = 200
WARMUP = 5
TOML = "marestail.toml"
FALLBACK = {
    "git": {"base": "origin/main"},
    "python": {"root": ".", "venv": ".venv", "sources": ["marestail"], "crap_max": 4},
    "docs": {
        "files": ["README.md"],
        "routes_file": "README.md",
        "sources": ["marestail"],
        "path_prefixes": ["marestail/", "tools/", "tasks/", "roles/", "templates/", "guidance/"],
        "ignore_env": [],
    },
    "comments": {"paths": ["marestail"]},
    "sonar": {
        "project_key": "marestail",
        "project_name": "marestail",
        "exclusions": ["tools/samples/**", "templates/**", "roles/**"],
    },
}


class Discard:
    encoding = "utf-8"

    def write(self, _data: str) -> int:
        return 0

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def tree() -> Path:
    return Path(os.environ["MARESTAIL_PERF_TREE_PATH"]).resolve()


def use_tree() -> Path:
    root = tree()
    sys.path.insert(0, str(root))
    os.chdir(root)
    return root


def has_toml(root: Path) -> bool:
    return (root / TOML).is_file()


def make_config(root: Path) -> Any:
    from marestail.config import Config, load

    return load(root) if has_toml(root) else Config(root=root, raw=FALLBACK)


def emit(target: str, values: list[float]) -> None:
    print(json.dumps({"target": target, "unit": "ms", "better": "lower", "values": values}), flush=True)


def emit_one(target: str, value: float) -> None:
    print(json.dumps({"target": target, "unit": "ms", "better": "lower", "value": value}), flush=True)


def absent(target: str) -> None:
    print(json.dumps({"target": target, "absent": True}), flush=True)


def measure(fn: Callable[[], Any]) -> list[float]:
    sink = Discard()
    with redirect_stdout(sink), redirect_stderr(sink):
        for _ in range(WARMUP):
            fn()
        values: list[float] = []
        for _ in range(VALUES):
            started = time.perf_counter()
            fn()
            values.append((time.perf_counter() - started) * 1000)
    return values


def measure_one(fn: Callable[[], Any]) -> float:
    sink = Discard()
    with redirect_stdout(sink), redirect_stderr(sink):
        fn()
        started = time.perf_counter()
        fn()
        return (time.perf_counter() - started) * 1000
