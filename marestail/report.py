import json
import time
from dataclasses import asdict, dataclass, field

MAX_FINDINGS_SHOWN = 40
FINDING_CAP = 60


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
    seconds: float = 0.0

    @classmethod
    def skipped(cls, gate: str, why: str) -> "Result":
        return cls(gate=gate, ok=True, summary=f"skipped: {why}")


def mutation_summary(failing: int, total: int, note: str, timed_out: int = 0) -> str:
    summary = f"{failing} of {total} mutants not killed" if failing else f"all {total} mutants killed"
    summary += f" ({timed_out} killed by timeout)" if timed_out else ""
    return summary + (f" {note}" if note else "")


def render(results: list[Result], scope: str | None = None) -> str:
    lines = [render_one(result) for result in results]
    header = [f"scope: {scope}", ""] if scope else []
    return "\n".join([*header, *lines, "", verdict(results)])


def verdict(results: list[Result]) -> str:
    failed = [result.gate for result in results if not result.ok]
    return "GATE PASSED" if not failed else "GATE FAILED: " + ", ".join(failed)


def render_one(result: Result) -> str:
    mark = "ok  " if result.ok else "FAIL"
    head = f"[{mark}] {result.gate:<14} {result.summary}  ({result.seconds:.1f}s)"
    shown = result.findings[:MAX_FINDINGS_SHOWN]
    hidden = len(result.findings) - len(shown)
    body = [f"       {finding}" for finding in shown]
    if hidden > 0:
        body.append(f"       ... {hidden} more")
    return "\n".join([head, *body])


def to_json(results: list[Result], scope: str = "all", focus: set[str] | None = None) -> str:
    payload = {"scope": scope, "focus": sorted(focus or set()), "results": [asdict(result) for result in results]}
    return json.dumps(payload, indent=2)
