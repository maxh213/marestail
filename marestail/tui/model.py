from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Step:
    role: str
    label: str
    attempt: int
    status: str
    summary: str
    verdict: str | None
    minutes: float | None


@dataclass
class Process:
    pid: int
    elapsed_s: int
    model: str
    backend: str


@dataclass
class Worker:
    step: Step
    process: Process | None
    result_path: Path | None
    prompt_path: Path | None
    handoff_path: Path | None
    tail_lines: list[str] = field(default_factory=list)


@dataclass
class RepoState:
    name: str
    root: Path
    branch: str
    head: str
    task: str | None
    log_path: Path | None
    steps: list[Step] = field(default_factory=list)
    worker: Worker | None = None
    alive: bool = False
    tail_lines: list[str] = field(default_factory=list)
    gate_activity: str | None = None
    runner_activity: str | None = None


@dataclass
class Fleet:
    repos: list[RepoState]
    scanned_at: float
