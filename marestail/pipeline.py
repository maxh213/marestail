from dataclasses import dataclass


@dataclass(frozen=True)
class Worker:
    name: str
    tier: str | None
    audit: bool = False
    pause_after: bool = False


@dataclass(frozen=True)
class Judge:
    name: str
    tier: str | None
    bounce_to: str
    bounces: int = 3
    pause_after: bool = False


Step = Worker | Judge

PIPELINE: list[Step] = [
    Worker("specifier", None),
    Judge("critic", None, bounce_to="specifier", bounces=2, pause_after=True),
    Worker("coder", "fast", audit=True),
    Worker("cleaner", "sonar"),
    Worker("architect", "sonar"),
    Judge("hardener", "full", bounce_to="coder", bounces=3),
    Worker("qa", "qa"),
]


def names() -> list[str]:
    return [step.name for step in PIPELINE]


def find(name: str) -> Step:
    for step in PIPELINE:
        if step.name == name:
            return step
    raise SystemExit(f"unknown role {name}; choose from {', '.join(names())}")


def window(start: str | None, stop: str | None) -> list[Step]:
    first = names().index(start) if start else 0
    last = names().index(stop) if stop else len(PIPELINE) - 1
    return PIPELINE[first : last + 1]
