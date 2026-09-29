from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Viewport:
    name: str
    width: int
    height: int
    scale: float
    touch: bool


@dataclass(frozen=True)
class Block:
    route: str
    selector: str
    scroll: bool
    wait: str
    styles: list[str]
    inside: str
    unchanged: list[str]
    must_not_change: list[str]
    symptom: str


@dataclass(frozen=True)
class Settings:
    start: str
    setup: str
    ready: str
    port: int
    env: dict[str, str]
    viewports: list[Viewport]
    hide: list[str]
    block: list[str]
    tolerance: int
    capture_timeout: int
    setup_timeout: int
    ready_timeout: int


@dataclass(frozen=True)
class Spec:
    task: str
    block: Block
    settings: Settings


@dataclass(frozen=True)
class Tree:
    folder: str
    label: str
    path: Path


@dataclass
class Shot:
    geometries: list[dict[str, Any]]
    failure: str | None


@dataclass
class TreeRun:
    tree: Tree
    problems: list[str] = field(default_factory=list)
    shots: dict[str, Shot] = field(default_factory=dict)
