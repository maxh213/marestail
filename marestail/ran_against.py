__all__ = ["MISSING", "NOTE", "finish", "parse"]

_LINES = {
    "ran-against: app": "app",
    "ran-against: harness": "harness",
    "ran-against: nothing": "nothing",
}
_PHRASE = {"harness": "a harness", "nothing": "nothing"}
_COMPLETE = "pipeline complete"
_TEMPLATE = "pipeline complete, NOT verified against the running app (qa ran against {what})"
_OK = 0
_NOT_VERIFIED = 3

MISSING = "Your handoff has no whole line that is exactly `ran-against: app`, `ran-against: harness`, or `ran-against: nothing`."
NOTE = "NOT verified"


def parse(text: str) -> str | None:
    return next((_LINES[line] for line in text.splitlines() if line in _LINES), None)


def finish(against: str) -> tuple[str, int]:
    if against == "app":
        return _COMPLETE, _OK
    return _TEMPLATE.format(what=_PHRASE[against]), _NOT_VERIFIED
