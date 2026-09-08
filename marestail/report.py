import json
from dataclasses import asdict, dataclass, field

MAX_FINDINGS_SHOWN = 40


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


def render(results: list[Result]) -> str:
    lines = [render_one(result) for result in results]
    failed = [result.gate for result in results if not result.ok]
    verdict = "GATE PASSED" if not failed else "GATE FAILED: " + ", ".join(failed)
    return "\n".join([*lines, "", verdict])


def render_one(result: Result) -> str:
    mark = "ok  " if result.ok else "FAIL"
    head = f"[{mark}] {result.gate:<14} {result.summary}  ({result.seconds:.1f}s)"
    shown = result.findings[:MAX_FINDINGS_SHOWN]
    hidden = len(result.findings) - len(shown)
    body = [f"       {finding}" for finding in shown]
    if hidden > 0:
        body.append(f"       ... {hidden} more")
    return "\n".join([head, *body])


def to_json(results: list[Result]) -> str:
    return json.dumps([asdict(result) for result in results], indent=2)
