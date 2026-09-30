from pathlib import Path

from marestail.config import Config
from marestail.gates import visual
from marestail.gates.visual import Reproduction

_SPECIFIER = "specifier"
_CRITIC = "critic"
_REPORTED = "Reported"
_OBSERVED = "Observed"
_OBSERVED_HEADING = "## Observed"
ASK_OBSERVED = (
    "Write ## Observed in your handoff: what the capture shows, then one line per cause the task states, marked holds or "
    "does not hold, with the measurement that says so."
)
_SPEC_ROLES = (_SPECIFIER, _CRITIC)
_NEXT_HEADING = "\n## "
_LAST = -1


def reproduce_first(config: Config, task: Path, roles: list[str]) -> tuple[Reproduction | None, bool]:
    first = _first_spec_role(roles)
    report = visual.bug_report(config, task) if first is not None else None
    if first is None or report is None:
        return None, True
    return _reproduced(config, report, first)


def _first_spec_role(roles: list[str]) -> str | None:
    return next((role for role in roles if role in _SPEC_ROLES), None)


def _reproduced(config: Config, report: visual.Report, first: str) -> tuple[Reproduction | None, bool]:
    reproduction = visual.reproduce(config, report)
    if reproduction is None:
        print(f"pipeline stopped before {first}: the bug could not be reproduced")
    return reproduction, reproduction is not None


def prompt_sections(config: Config, reproduction: Reproduction | None, role: str, handoffs: Path, blind_backend: str) -> dict[str, str]:
    if reproduction is None or role not in _SPEC_ROLES:
        return {}
    body = visual.reported_section(config, reproduction, blind_backend)
    if role == _CRITIC:
        return {_REPORTED: body, _OBSERVED: _latest_observed(handoffs)}
    return {_REPORTED: f"{body}\n{ASK_OBSERVED}"}


def handoff_problems(config: Config, reproduction: Reproduction | None, role: str, handoff: Path) -> list[str]:
    if role != _SPECIFIER or reproduction is None or _observed_in(handoff):
        return []
    return [f"missing {_OBSERVED_HEADING} section in {handoff.relative_to(config.root)}"]


def _latest_observed(handoffs: Path) -> str:
    written = sorted(handoffs.glob(f"*-{_SPECIFIER}.md"))
    return _observed_body(written[_LAST].read_text()) if written else ""


def _observed_body(text: str) -> str:
    if not _has_observed(text):
        return ""
    after = text[text.find(_OBSERVED_HEADING) + len(_OBSERVED_HEADING) :]
    return after.partition(_NEXT_HEADING)[0].strip()


def _has_observed(text: str) -> bool:
    return _OBSERVED_HEADING in (line.strip() for line in text.splitlines())


def _observed_in(path: Path) -> bool:
    return path.exists() and _has_observed(path.read_text())
