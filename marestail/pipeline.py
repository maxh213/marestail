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
    bounces: int = 0
    pause_after: bool = False
    writes: tuple[str, ...] = ()
    pinned_bounce: bool = False
    optional: bool = False


Step = Worker | Judge

PIPELINE: list[Step] = [
    Worker("specifier", None),
    Judge("critic", None, bounce_to="specifier", pause_after=True),
    Worker("coder", "fast", audit=True),
    Worker("cleaner", "sonar"),
    Worker("architect", "sonar"),
    Judge("practices", None, bounce_to="coder", pinned_bounce=True, optional=True),
    Judge("perf", None, bounce_to="coder", writes=("perf/**",), pinned_bounce=True, optional=True),
    Judge("hardener", "full", bounce_to="coder"),
    Worker("qa", "qa"),
]


def names() -> list[str]:
    return [step.name for step in PIPELINE]


def find(name: str) -> Step:
    for step in PIPELINE:
        if step.name == name:
            return step
    raise SystemExit(f"unknown role {name}; choose from {', '.join(names())}")


def started(taking: bool, step: Step, start: str | None) -> bool:
    return taking or step.name == start


def taken(taking: bool, step: Step) -> list[Step]:
    return [step] if taking else []


def stop_here(taking: bool, step: Step, stop: str | None) -> bool:
    return taking and stop is not None and step.name == stop


def window(start: str | None, stop: str | None) -> list[Step]:
    taking = start is None
    chosen: list[Step] = []
    for step in PIPELINE:
        taking = started(taking, step, start)
        chosen.extend(taken(taking, step))
        if stop_here(taking, step, stop):
            break
    return chosen
