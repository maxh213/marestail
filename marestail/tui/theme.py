import contextlib
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
STATUS_RUNNING = "running"
VERDICT_BOUNCE = "BOUNCE"
VERDICT_PASS = "PASS"
MISSING = ""
VERDICT_GLYPHS = {VERDICT_BOUNCE: GLYPH_BOUNCED, VERDICT_PASS: GLYPH_PASSED}

PAIR_HEADING = 1
PAIR_WORKER = 2
PAIR_JUDGE = 3
PAIR_SECONDARY = 4
PAIR_SELECTED = 5
PAIR_BORDER = 6
PAIR_BOUNCED = 7

RICH_PALETTE = (178, 70, 141, 108, 65, 167, 96)
VINE_SEGMENT = "─∙❧"


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
    with contextlib.suppress(curses.error):
        curses.use_default_colors()
        return -1
    return curses.COLOR_BLACK


def color_theme() -> Theme:
    bg = default_bg()
    palette = color_palette(curses.COLORS >= 256)
    curses.init_pair(PAIR_HEADING, palette[0], bg)
    curses.init_pair(PAIR_WORKER, palette[1], bg)
    curses.init_pair(PAIR_JUDGE, palette[2], bg)
    curses.init_pair(PAIR_SECONDARY, palette[3], bg)
    curses.init_pair(PAIR_SELECTED, curses.COLOR_WHITE, palette[6])
    curses.init_pair(PAIR_BORDER, palette[4], bg)
    curses.init_pair(PAIR_BOUNCED, palette[5], bg)
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


def basic_palette() -> tuple[int, int, int, int, int, int, int]:
    return (
        curses.COLOR_YELLOW,
        curses.COLOR_GREEN,
        curses.COLOR_MAGENTA,
        curses.COLOR_WHITE,
        curses.COLOR_GREEN,
        curses.COLOR_RED,
        curses.COLOR_MAGENTA,
    )


def color_palette(rich: bool) -> tuple[int, int, int, int, int, int, int]:
    chosen = (basic_palette, rich_palette)[rich]
    return chosen()


def rich_palette() -> tuple[int, int, int, int, int, int, int]:
    return RICH_PALETTE


def init_theme() -> Theme:
    curses.start_color()
    chosen = (try_color, mono_theme)[not curses.has_colors()]
    return chosen()


def try_color() -> Theme:
    with contextlib.suppress(curses.error):
        return color_theme()
    return mono_theme()


def running_glyph(_verdict: str | None) -> str:
    return GLYPH_RUNNING


def named_or_missing(verdict: str | None) -> str:
    return (MISSING, str(verdict))[verdict is not None]


def verdict_glyph(verdict: str | None) -> str:
    return VERDICT_GLYPHS.get(named_or_missing(verdict), GLYPH_DONE)


def step_glyph(status: str, verdict: str | None) -> str:
    chosen = (verdict_glyph, running_glyph)[status == STATUS_RUNNING]
    return chosen(verdict)


def running_attr(theme: Theme, _verdict: str | None) -> int:
    return theme.worker


def verdict_attr(theme: Theme, verdict: str | None) -> int:
    return {VERDICT_BOUNCE: theme.bounced, VERDICT_PASS: theme.passed}.get(named_or_missing(verdict), theme.done)


def step_attr(theme: Theme, status: str, verdict: str | None) -> int:
    chosen = (verdict_attr, running_attr)[status == STATUS_RUNNING]
    return chosen(theme, verdict)


def role_attr(theme: Theme, role: str) -> int:
    return (theme.worker, theme.judge)[role in JUDGE_ROLES]


def blank_vine(_width: int) -> str:
    return ""


def full_vine(width: int) -> str:
    return (VINE_SEGMENT * (width // len(VINE_SEGMENT) + 1))[:width]


def vine(width: int) -> str:
    chosen = (full_vine, blank_vine)[width <= 0]
    return chosen(width)
