import json
import time
from dataclasses import asdict, dataclass, field

MAX_FINDINGS_SHOWN = 40
FINDING_CAP = 60
GATE_FIELD = "gate"
SECONDS_FIELD = "seconds"
FINDINGS_FIELD = "findings"


def elapsed(started: float) -> float:
    return time.time() - started


def capped(findings: list[str]) -> list[str]:
    return findings[:FINDING_CAP]


@dataclass
class Result:
    gate: str
    ok: bool
    summary: str
    findings: list[str] = field(default_factory=list)
    seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.gate, str):
            raise TypeError(GATE_FIELD)
        if type(self.findings) is not list:
            raise TypeError(FINDINGS_FIELD)
        if self.seconds is None:
            raise TypeError(SECONDS_FIELD)

    @classmethod
    def skipped(cls, gate: str, why: str) -> "Result":
        return cls(gate=gate, ok=True, summary=f"skipped: {why}", seconds=0.0)


def render(results: list[Result], scope: str | None = None) -> str:
    lines = [render_one(result) for result in results]
    header = [f"scope: {scope}", ""] if scope else []
    return "\n".join([*header, *lines, "", verdict(results)])


def verdict(results: list[Result]) -> str:
    failed = [result.gate for result in results if not result.ok]
    return "GATE PASSED" if not failed else "GATE FAILED: " + ", ".join(failed)


def result_seconds(result: Result) -> float:
    if result.seconds is None:
        raise TypeError(SECONDS_FIELD)
    return result.seconds


def render_one(result: Result) -> str:
    mark = "ok  " if result.ok else "FAIL"
    head = f"[{mark}] {result.gate:<14} {result.summary}  ({result_seconds(result):.1f}s)"
    shown = result.findings[:MAX_FINDINGS_SHOWN]
    hidden = len(result.findings) - len(shown)
    body = [f"       {finding}" for finding in shown]
    if hidden > 0:
        body.append(f"       ... {hidden} more")
    return "\n".join([head, *body])


def to_json(results: list[Result], scope: str = "all", focus: set[str] | None = None) -> str:
    payload = {"scope": scope, "focus": sorted(focus or set()), "results": [asdict(result) for result in results]}
    return json.dumps(payload, indent=2)
