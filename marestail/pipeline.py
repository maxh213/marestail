from dataclasses import dataclass, replace


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


HYPER = "hyper"
HYPER_DROPPED = ("cleaner", "practices", "perf")
HYPER_FULL = ("coder", "architect")


def steps(mode: str | None = None) -> list[Step]:
    if mode != HYPER:
        return PIPELINE
    return [hyper_step(step) for step in PIPELINE if step.name not in HYPER_DROPPED]


def hyper_step(step: Step) -> Step:
    return replace(step, tier="full") if step.name in HYPER_FULL else step


def names(mode: str | None = None) -> list[str]:
    return [step.name for step in steps(mode)]


def find(name: str, mode: str | None = None) -> Step:
    for step in steps(mode):
        if step.name == name:
            return step
    raise SystemExit(f"unknown role {name}; choose from {', '.join(names(mode))}")


def check_role(name: str | None, mode: str | None) -> None:
    if name is not None:
        find(name, mode)


def started(taking: bool, step: Step, start: str | None) -> bool:
    return taking or step.name == start


def taken(taking: bool, step: Step) -> list[Step]:
    return [step] if taking else []


def stop_here(taking: bool, step: Step, stop: str | None) -> bool:
    return taking and stop is not None and step.name == stop


def window(start: str | None, stop: str | None, mode: str | None = None) -> list[Step]:
    check_role(start, mode)
    check_role(stop, mode)
    taking = start is None
    chosen: list[Step] = []
    for step in steps(mode):
        taking = started(taking, step, start)
        chosen.extend(taken(taking, step))
        if stop_here(taking, step, stop):
            break
    return chosen
