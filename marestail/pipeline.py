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
    targets: tuple[str, ...] = ()


Step = Worker | Judge

_PIPELINE: list[Step] = [
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
_HYPER_DROPPED = ("cleaner", "practices", "perf")
_HYPER_FULL_TIER = ("coder", "architect")
_BLAST = Judge("blast", None, bounce_to="coder")
_BLAST_BEFORE = "hardener"
_VISUAL = Judge("visual", "visual", bounce_to="coder", optional=True, targets=("coder", "specifier"))
_VISUAL_AFTER = "hardener"


def steps(mode: str | None = None, visual: bool = False) -> list[Step]:
    chosen = _hyper_pipeline() if mode == HYPER else _PIPELINE
    return _with_visual(chosen) if visual else chosen


def _with_visual(chosen: list[Step]) -> list[Step]:
    return [added for step in chosen for added in _visual_steps(step)]


def _visual_steps(step: Step) -> list[Step]:
    return [step, _VISUAL] if step.name == _VISUAL_AFTER else [step]


def _hyper_pipeline() -> list[Step]:
    return [added for step in _PIPELINE if step.name not in _HYPER_DROPPED for added in _hyper_steps(step)]


def _hyper_steps(step: Step) -> list[Step]:
    return [_BLAST, step] if step.name == _BLAST_BEFORE else [_hyper_step(step)]


def _hyper_step(step: Step) -> Step:
    return replace(step, tier="full") if step.name in _HYPER_FULL_TIER else step


def names(mode: str | None = None, visual: bool = False) -> list[str]:
    return [step.name for step in steps(mode, visual)]


def find(name: str, mode: str | None = None, visual: bool = False) -> Step:
    for step in steps(mode, visual):
        if step.name == name:
            return step
    raise SystemExit(f"unknown role {name}; choose from {', '.join(names(mode, visual))}")


def _check_role(name: str | None, mode: str | None, visual: bool) -> None:
    if name is not None:
        find(name, mode, visual)


def _started(taking: bool, step: Step, start: str | None) -> bool:
    return taking or step.name == start


def _taken(taking: bool, step: Step) -> list[Step]:
    return [step] if taking else []


def _stop_here(taking: bool, step: Step, stop: str | None) -> bool:
    return taking and stop is not None and step.name == stop


def window(start: str | None, stop: str | None, mode: str | None = None, visual: bool = False) -> list[Step]:
    _check_role(start, mode, visual)
    _check_role(stop, mode, visual)
    taking = start is None
    chosen: list[Step] = []
    for step in steps(mode, visual):
        taking = _started(taking, step, start)
        chosen.extend(_taken(taking, step))
        if _stop_here(taking, step, stop):
            break
    return chosen
