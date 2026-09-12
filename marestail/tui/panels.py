import curses
import textwrap
from dataclasses import dataclass

from .collect import conversation_for
from .model import Fleet, RepoState, Step, Worker
from .theme import (
    GLYPH_FLOURISH,
    GLYPH_IDLE,
    GLYPH_RUNNING,
    GLYPH_SECTION,
    HEAVY,
    ROUND,
    Border,
    Theme,
    role_attr,
    step_attr,
    step_glyph,
)

BED_H = 5
BED_GAP = 1
STRIP_STEPS = 12
MARQUEE_PAUSE_TICKS = 12
TAIL_ROWS = 3


@dataclass(frozen=True)
class Rect:
    y: int
    x: int
    h: int
    w: int


@dataclass
class WatchState:
    fleet: Fleet | None
    theme: Theme
    tick: int
    selected: int = 0
    error: str | None = None


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def put(win: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = win.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    text = text[: max(0, width - x)]
    if not text:
        return
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def draw_box(win: curses.window, rect: Rect, border: Border, attr: int) -> None:
    if rect.h < 2 or rect.w < 2:
        return
    right = rect.x + rect.w - 1
    bottom = rect.y + rect.h - 1
    edge = border.top * (rect.w - 2)
    put(win, rect.y, rect.x, border.tl + edge + border.tr, attr)
    put(win, bottom, rect.x, border.bl + edge + border.br, attr)
    for y in range(rect.y + 1, bottom):
        put(win, y, rect.x, border.side, attr)
        put(win, y, right, border.side, attr)


def clean(text: str) -> str:
    return " ".join(text.split())


def marquee(text: str, width: int, tick: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    span = len(text) - width
    cycle = MARQUEE_PAUSE_TICKS + span + 1
    moment = tick % cycle
    offset = 0 if moment < MARQUEE_PAUSE_TICKS else min(moment - MARQUEE_PAUSE_TICKS, span)
    return text[offset : offset + width]


def fmt_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def fmt_elapsed(worker: Worker) -> str:
    if worker.process is not None:
        return fmt_seconds(worker.process.elapsed_s)
    if worker.step.minutes is not None:
        return f"{worker.step.minutes:.0f}m"
    return "--"


def worker_rows(fleet: Fleet | None) -> list[RepoState]:
    if fleet is None:
        return []
    return [repo for repo in fleet.repos if repo.worker is not None]


def selected_repo(state: WatchState) -> RepoState | None:
    rows = worker_rows(state.fleet)
    if not rows:
        return None
    return rows[min(state.selected, len(rows) - 1)]


def bed_index(fleet: Fleet, target: RepoState | None) -> int:
    for index, repo in enumerate(fleet.repos):
        if repo is target:
            return index
    return 0


def bed_height(repo: RepoState) -> int:
    worker = repo.worker
    extra = min(TAIL_ROWS, len(worker.tail_lines)) if worker is not None else 0
    return BED_H + extra


def bed_span(heights: list[int], first: int, last: int) -> int:
    return sum(heights[first : last + 1]) + BED_GAP * (last - first)


def fit_top(heights: list[int], top: int, current: int, height: int) -> int:
    top = min(top, current)
    while top < current and bed_span(heights, top, current) > height:
        top += 1
    return top


def draw_bed(win: curses.window, rect: Rect, repo: RepoState, selected: bool, state: WatchState) -> None:
    border = HEAVY if selected else ROUND
    attr = state.theme.border_focus if selected else state.theme.border
    draw_box(win, rect, border, attr)
    inner = rect.w - 4
    put(win, rect.y, rect.x + 2, f" {repo.name} {GLYPH_FLOURISH} {repo.branch} @{repo.head} "[:inner], state.theme.heading)
    put(win, rect.y + 1, rect.x + 2, f"task: {repo.task or 'none'}"[:inner], state.theme.secondary)
    draw_worker_row(win, rect.y + 2, rect.x + 2, inner, repo, selected, state)
    tails = tail_lines_of(repo)
    for offset, line in enumerate(tails):
        put(win, rect.y + 3 + offset, rect.x + 2, line[:inner], state.theme.secondary)
    draw_strip(win, rect.y + 3 + len(tails), rect.x + 2, inner, repo.steps, state)


def tail_lines_of(repo: RepoState) -> list[str]:
    if repo.worker is None:
        return []
    return repo.worker.tail_lines[:TAIL_ROWS]


def draw_worker_row(win: curses.window, y: int, x: int, width: int, repo: RepoState, selected: bool, state: WatchState) -> None:
    worker = repo.worker
    if worker is None:
        put(win, y, x, f"{GLYPH_IDLE} idle", state.theme.idle)
        return
    head = f"{GLYPH_RUNNING} {worker.step.role} {worker.step.label} {fmt_elapsed(worker)} "
    tail = "" if worker.tail_lines else marquee(clean(worker.step.summary), width - len(head), state.tick)
    if selected:
        put(win, y, x, (head + tail).ljust(width)[:width], state.theme.selected)
        return
    put(win, y, x, head, role_attr(state.theme, worker.step.role))
    put(win, y, x + len(head), tail, state.theme.secondary)


def draw_strip(win: curses.window, y: int, x: int, width: int, steps: list[Step], state: WatchState) -> None:
    col = x
    for step in steps[-STRIP_STEPS:]:
        if col >= x + width:
            break
        put(win, y, col, step_glyph(step.status, step.verdict), step_attr(state.theme, step.status, step.verdict))
        col += 2


def wrap_line(raw: str, width: int) -> list[str]:
    wrapped = textwrap.wrap(raw, max(1, width), replace_whitespace=False, drop_whitespace=False)
    return wrapped or [""]


def build_lines(sections: list[tuple[str, str]], width: int) -> list[tuple[str, bool]]:
    lines: list[tuple[str, bool]] = []
    for name, body in sections:
        lines.append((f"{GLYPH_SECTION} {name}", True))
        lines.extend((wrapped, False) for raw in body.splitlines() for wrapped in wrap_line(raw, width))
        lines.append(("", False))
    if not sections:
        lines.append(("no conversation files found", False))
    return lines


class Panel:
    title: str = "panel"

    def render(self, win: curses.window, rect: Rect, focused: bool, state: WatchState) -> None:
        raise NotImplementedError

    def on_key(self, key: int, state: WatchState) -> str | None:
        return None


class FleetPanel(Panel):
    title = "fleet"

    def __init__(self) -> None:
        self.top = 0

    def render(self, win: curses.window, rect: Rect, focused: bool, state: WatchState) -> None:
        if state.fleet is None or not state.fleet.repos:
            put(win, rect.y, rect.x + 2, "no beds found — waiting for pipelines", state.theme.secondary)
            return
        repos = state.fleet.repos
        heights = [bed_height(repo) for repo in repos]
        current_repo = selected_repo(state)
        self.top = fit_top(heights, self.top, bed_index(state.fleet, current_repo), rect.h)
        y = rect.y
        for index in range(self.top, len(repos)):
            if y >= rect.y + rect.h:
                break
            bed = Rect(y, rect.x, heights[index], rect.w)
            draw_bed(win, bed, repos[index], repos[index] is current_repo, state)
            y += heights[index] + BED_GAP

    def on_key(self, key: int, state: WatchState) -> str | None:
        rows = len(worker_rows(state.fleet))
        if key in (curses.KEY_UP, ord("k")):
            state.selected = max(0, state.selected - 1)
            return "handled"
        if key in (curses.KEY_DOWN, ord("j")):
            state.selected = min(max(0, rows - 1), state.selected + 1)
            return "handled"
        if key in (curses.KEY_ENTER, 10, 13):
            return "open" if rows else "handled"
        if key == ord("q"):
            return "quit"
        return None


class ConversationPanel(Panel):
    title = "conversation"

    def __init__(self, worker: Worker) -> None:
        self.worker = worker
        self.sections = conversation_for(worker)
        self.scroll = 0
        self.page = 1
        self.built_for = -1
        self.lines: list[tuple[str, bool]] = []

    def render(self, win: curses.window, rect: Rect, focused: bool, state: WatchState) -> None:
        width = rect.w - 1
        if width != self.built_for:
            self.lines = build_lines(self.sections, width)
            self.built_for = width
        put(win, rect.y, rect.x, f"{GLYPH_SECTION} {self.worker.step.label} · {self.worker.step.role}"[:rect.w], state.theme.heading)
        self.page = max(1, rect.h - 1)
        self.scroll = clamp(self.scroll, 0, max(0, len(self.lines) - self.page))
        for row, (text, head) in enumerate(self.lines[self.scroll : self.scroll + self.page]):
            put(win, rect.y + 1 + row, rect.x, text, state.theme.heading if head else 0)

    def on_key(self, key: int, state: WatchState) -> str | None:
        if key in (ord("q"), 27):
            return "back"
        if key in (curses.KEY_UP, ord("k")):
            self.scroll -= 1
        elif key in (curses.KEY_DOWN, ord("j")):
            self.scroll += 1
        elif key == curses.KEY_PPAGE:
            self.scroll -= self.page
        elif key == curses.KEY_NPAGE:
            self.scroll += self.page
        elif key == curses.KEY_HOME:
            self.scroll = 0
        elif key == curses.KEY_END:
            self.scroll = len(self.lines)
        else:
            return None
        self.scroll = max(0, self.scroll)
        return "handled"


PANELS: list[type[Panel]] = [FleetPanel]
