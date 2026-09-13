import curses
from dataclasses import dataclass

GLYPH_FLOURISH = "❧"
GLYPH_RUNNING = "⚘"
GLYPH_DONE = "✿"
GLYPH_BOUNCED = "✶"
GLYPH_PASSED = "✔"
GLYPH_IDLE = "○"
GLYPH_SECTION = "◆"

JUDGE_ROLES = frozenset({"critic", "practices", "perf", "hardener"})

PAIR_HEADING = 1
PAIR_WORKER = 2
PAIR_JUDGE = 3
PAIR_SECONDARY = 4
PAIR_SELECTED = 5
PAIR_BORDER = 6
PAIR_BOUNCED = 7


@dataclass(frozen=True)
class Border:
    tl: str
    top: str
    tr: str
    side: str
    bl: str
    br: str


ROUND = Border("╭", "─", "╮", "│", "╰", "╯")
HEAVY = Border("┏", "━", "┓", "┃", "┗", "┛")


@dataclass(frozen=True)
class Theme:
    heading: int
    worker: int
    judge: int
    secondary: int
    selected: int
    border: int
    border_focus: int
    done: int
    bounced: int
    passed: int
    idle: int
    colors: bool


def mono_theme() -> Theme:
    return Theme(
        heading=curses.A_BOLD,
        worker=curses.A_NORMAL,
        judge=curses.A_BOLD,
        secondary=curses.A_DIM,
        selected=curses.A_REVERSE | curses.A_BOLD,
        border=curses.A_DIM,
        border_focus=curses.A_BOLD,
        done=curses.A_NORMAL,
        bounced=curses.A_BOLD,
        passed=curses.A_BOLD,
        idle=curses.A_DIM,
        colors=False,
    )


def default_bg() -> int:
    try:
        curses.use_default_colors()
        return -1
    except curses.error:
        return curses.COLOR_BLACK


def color_theme() -> Theme:
    bg = default_bg()
    rich = curses.COLORS >= 256
    amber = 178 if rich else curses.COLOR_YELLOW
    fern = 70 if rich else curses.COLOR_GREEN
    heather = 141 if rich else curses.COLOR_MAGENTA
    sage = 108 if rich else curses.COLOR_WHITE
    moss = 65 if rich else curses.COLOR_GREEN
    ember = 167 if rich else curses.COLOR_RED
    plum = 96 if rich else curses.COLOR_MAGENTA
    curses.init_pair(PAIR_HEADING, amber, bg)
    curses.init_pair(PAIR_WORKER, fern, bg)
    curses.init_pair(PAIR_JUDGE, heather, bg)
    curses.init_pair(PAIR_SECONDARY, sage, bg)
    curses.init_pair(PAIR_SELECTED, curses.COLOR_WHITE, plum)
    curses.init_pair(PAIR_BORDER, moss, bg)
    curses.init_pair(PAIR_BOUNCED, ember, bg)
    return Theme(
        heading=curses.color_pair(PAIR_HEADING) | curses.A_BOLD,
        worker=curses.color_pair(PAIR_WORKER),
        judge=curses.color_pair(PAIR_JUDGE),
        secondary=curses.color_pair(PAIR_SECONDARY) | curses.A_DIM,
        selected=curses.color_pair(PAIR_SELECTED) | curses.A_BOLD,
        border=curses.color_pair(PAIR_BORDER) | curses.A_DIM,
        border_focus=curses.color_pair(PAIR_JUDGE) | curses.A_BOLD,
        done=curses.color_pair(PAIR_WORKER),
        bounced=curses.color_pair(PAIR_BOUNCED),
        passed=curses.color_pair(PAIR_WORKER) | curses.A_BOLD,
        idle=curses.color_pair(PAIR_SECONDARY) | curses.A_DIM,
        colors=True,
    )


def init_theme() -> Theme:
    curses.start_color()
    if not curses.has_colors():
        return mono_theme()
    try:
        return color_theme()
    except curses.error:
        return mono_theme()


def step_glyph(status: str, verdict: str | None) -> str:
    if status == "running":
        return GLYPH_RUNNING
    if verdict == "BOUNCE":
        return GLYPH_BOUNCED
    if verdict == "PASS":
        return GLYPH_PASSED
    return GLYPH_DONE


def step_attr(theme: Theme, status: str, verdict: str | None) -> int:
    if status == "running":
        return theme.worker
    if verdict == "BOUNCE":
        return theme.bounced
    if verdict == "PASS":
        return theme.passed
    return theme.done


def role_attr(theme: Theme, role: str) -> int:
    if role in JUDGE_ROLES:
        return theme.judge
    return theme.worker


def vine(width: int) -> str:
    segment = "─∙❧"
    if width <= 0:
        return ""
    return (segment * (width // len(segment) + 1))[:width]
