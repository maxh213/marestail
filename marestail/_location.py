import re
from itertools import pairwise, starmap

TOKEN_BREAKS = re.compile(r"[\s()]+")
DIGITS = "0123456789"


def location(finding: str) -> tuple[str, int] | None:
    return next(filter(None, map(token_location, TOKEN_BREAKS.split(finding))), None)


def token_location(token: str) -> tuple[str, int] | None:
    parts = token.split(":")
    pairs = starmap(path_and_digits, pairwise(parts))
    return next(((path, int(line)) for path, line in pairs if path and line), None)


def path_and_digits(path: str, after: str) -> tuple[str, str]:
    return path, leading_digits(after)


def leading_digits(text: str) -> str:
    return text[: len(text) - len(text.lstrip(DIGITS))]
