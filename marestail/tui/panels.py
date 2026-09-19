import contextlib
import curses
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .collect import conversation_for, fmt_seconds
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
    clipped = clip_text(y, x, text, height, width)
    if clipped is None:
        return
    write_cell(win, clipped[0], clipped[1], clipped[2], attr)


def clip_text(y: int, x: int, text: str, height: int, width: int) -> tuple[int, int, str] | None:
    if offscreen(y, x, height, width):
        return None
    y, x, text = shift_left(y, x, text)
    text = text[: max(0, width - x)]
    return None if not text else (y, x, text)


def offscreen(y: int, x: int, height: int, width: int) -> bool:
    return y < 0 or y >= height or x >= width


def shift_left(y: int, x: int, text: str) -> tuple[int, int, str]:
    if x >= 0:
        return y, x, text
    return y, 0, text[-x:]


def write_cell(win: curses.window, y: int, x: int, text: str, attr: int) -> None:
    with contextlib.suppress(curses.error):
        win.addstr(y, x, text, attr)


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


def fmt_elapsed(worker: Worker) -> str:
    if worker.process is not None:
        return fmt_seconds(worker.process.elapsed_s)
    if worker.step.minutes is not None:
        return f"{worker.step.minutes:.0f}m"
    return "--"


def worker_rows(fleet: Fleet | None) -> list[RepoState]:
    return [] if fleet is None else [repo for repo in fleet.repos if is_worker_row(repo)]


def is_worker_row(repo: RepoState) -> bool:
    return repo.worker is not None or repo.alive


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
    return BED_H + gate_rows(repo) + min(TAIL_ROWS, len(tail_lines_of(repo)))


def gate_rows(repo: RepoState) -> int:
    return 1 if repo.gate_activity is not None else 0


def bed_span(heights: list[int], first: int, last: int) -> int:
    return sum(heights[first : last + 1]) + BED_GAP * (last - first)


def fit_top(heights: list[int], top: int, current: int, height: int) -> int:
    top = min(top, current)
    while top < current and bed_span(heights, top, current) > height:
        top += 1
    return top


def draw_bed(win: curses.window, rect: Rect, repo: RepoState, selected: bool, state: WatchState) -> None:
    inner = rect.w - 4
    paint_bed_frame(win, rect, repo, selected, state, inner)
    draw_worker_row(win, rect.y + 2, rect.x + 2, inner, repo, selected, state)
    row = draw_gate_row(win, rect.y + 3, rect.x + 2, inner, repo, state)
    paint_tails(win, row, rect.x + 2, inner, repo, state)


def paint_bed_frame(win: curses.window, rect: Rect, repo: RepoState, selected: bool, state: WatchState, inner: int) -> None:
    draw_box(win, rect, HEAVY if selected else ROUND, state.theme.border_focus if selected else state.theme.border)
    put(win, rect.y, rect.x + 2, f" {repo.name} {GLYPH_FLOURISH} {repo.branch} @{repo.head} "[:inner], state.theme.heading)
    put(win, rect.y + 1, rect.x + 2, f"task: {repo.task or 'none'}"[:inner], state.theme.secondary)


def paint_tails(win: curses.window, row: int, x: int, inner: int, repo: RepoState, state: WatchState) -> None:
    tails = tail_lines_of(repo)
    for offset, line in enumerate(tails):
        put(win, row + offset, x, line[:inner], state.theme.secondary)
    draw_strip(win, row + len(tails), x, inner, repo.steps, state)


def draw_gate_row(win: curses.window, y: int, x: int, inner: int, repo: RepoState, state: WatchState) -> int:
    if repo.gate_activity is None:
        return y
    put(win, y, x, f"⚒ gate: {repo.gate_activity}"[:inner], state.theme.secondary)
    return y + 1


def tail_lines_of(repo: RepoState) -> list[str]:
    worker = repo.worker
    lines = worker.tail_lines if worker is not None else []
    if not lines:
        lines = repo.tail_lines
    return lines[:TAIL_ROWS]


def draw_worker_row(win: curses.window, y: int, x: int, width: int, repo: RepoState, selected: bool, state: WatchState) -> None:
    worker = repo.worker
    if worker is None:
        draw_idle_row(win, y, x, width, repo, selected, state)
        return
    draw_busy_row(win, y, x, width, worker, selected, state)


def draw_idle_row(win: curses.window, y: int, x: int, width: int, repo: RepoState, selected: bool, state: WatchState) -> None:
    if not repo.alive:
        put(win, y, x, f"{GLYPH_IDLE} idle", state.theme.idle)
        return
    text = f"{GLYPH_RUNNING} {alive_label(repo)}"
    if selected:
        put(win, y, x, text.ljust(width)[:width], state.theme.selected)
        return
    put(win, y, x, text[:width], state.theme.worker)


def alive_label(repo: RepoState) -> str:
    if repo.gate_activity is not None:
        return f"in gate: {repo.gate_activity}"
    if repo.runner_activity:
        return f"runner: {repo.runner_activity}"
    return "between steps"


def draw_busy_row(win: curses.window, y: int, x: int, width: int, worker: Worker, selected: bool, state: WatchState) -> None:
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
    lines = [line for name, body in sections for line in section_lines(name, body, width)]
    return lines if sections else [("no conversation files found", False)]


def section_lines(name: str, body: str, width: int) -> list[tuple[str, bool]]:
    wrapped = [(text, False) for raw in body.splitlines() for text in wrap_line(raw, width)]
    return [(f"{GLYPH_SECTION} {name}", True), *wrapped, ("", False)]


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
        self.render_beds(win, rect, state)

    def render_beds(self, win: curses.window, rect: Rect, state: WatchState) -> None:
        repos = state.fleet.repos if state.fleet is not None else []
        heights = [bed_height(repo) for repo in repos]
        current = selected_repo(state)
        self.top = fit_top(heights, self.top, bed_index(state.fleet, current) if state.fleet is not None else 0, rect.h)
        paint_beds(win, rect, repos, heights, self.top, current, state)

    def on_key(self, key: int, state: WatchState) -> str | None:
        rows = len(worker_rows(state.fleet))
        if key in (curses.KEY_UP, ord("k")):
            state.selected = max(0, state.selected - 1)
            return "handled"
        if key in (curses.KEY_DOWN, ord("j")):
            state.selected = min(max(0, rows - 1), state.selected + 1)
            return "handled"
        return fleet_action(key, rows)


def paint_beds(
    win: curses.window, rect: Rect, repos: list[RepoState], heights: list[int], top: int, current: RepoState | None, state: WatchState
) -> None:
    y = rect.y
    for index in range(top, len(repos)):
        if y >= rect.y + rect.h:
            return
        draw_bed(win, Rect(y, rect.x, heights[index], rect.w), repos[index], repos[index] is current, state)
        y += heights[index] + BED_GAP


def fleet_action(key: int, rows: int) -> str | None:
    if key in (curses.KEY_ENTER, 10, 13):
        return "open" if rows else "handled"
    return "quit" if key == ord("q") else None


class ConversationPanel(Panel):
    title = "conversation"

    def __init__(self, repo: RepoState) -> None:
        self.repo = repo
        self.sections = conversation_for(repo)
        self.follow = True
        self.scroll = 0
        self.page = 1
        self.built_for = -1
        self.lines: list[tuple[str, bool]] = []

    def sync(self, fleet: Fleet | None) -> None:
        repo = matching_repo(fleet, self.repo.root)
        if repo is None:
            return
        self.repo = repo
        self.sections = conversation_for(repo)
        self.built_for = -1

    def heading(self) -> str:
        worker = self.repo.worker
        if worker is None:
            return f"{GLYPH_SECTION} {self.repo.name} · live"
        return f"{GLYPH_SECTION} {worker.step.label} · {worker.step.role}"

    def render(self, win: curses.window, rect: Rect, focused: bool, state: WatchState) -> None:
        self.ensure_lines(rect.w - 1)
        put(win, rect.y, rect.x, self.heading()[: rect.w], state.theme.heading)
        self.page = max(1, rect.h - 1)
        self.place_scroll()
        paint_lines(win, rect, self.lines[self.scroll : self.scroll + self.page], state)

    def ensure_lines(self, width: int) -> None:
        if width == self.built_for:
            return
        self.lines = build_lines(self.sections, width)
        self.built_for = width

    def place_scroll(self) -> None:
        bottom = max(0, len(self.lines) - self.page)
        if self.follow:
            self.scroll = bottom
        self.scroll = clamp(self.scroll, 0, bottom)

    def on_key(self, key: int, state: WatchState) -> str | None:
        if key in (ord("q"), 27):
            return "back"
        if key == curses.KEY_END:
            self.follow = True
            return "handled"
        return scrolled(self, key)


def scrolled(panel: ConversationPanel, key: int) -> str | None:
    handler = SCROLL_KEYS.get(key)
    if handler is None:
        return None
    handler(panel)
    panel.scroll = max(0, panel.scroll)
    return "handled"


def scroll_up(panel: ConversationPanel) -> None:
    panel.follow = False
    panel.scroll -= 1


def scroll_down(panel: ConversationPanel) -> None:
    panel.scroll += 1


def scroll_page_up(panel: ConversationPanel) -> None:
    panel.follow = False
    panel.scroll -= panel.page


def scroll_page_down(panel: ConversationPanel) -> None:
    panel.scroll += panel.page


def scroll_home(panel: ConversationPanel) -> None:
    panel.follow = False
    panel.scroll = 0


SCROLL_KEYS = {
    curses.KEY_UP: scroll_up,
    ord("k"): scroll_up,
    curses.KEY_DOWN: scroll_down,
    ord("j"): scroll_down,
    curses.KEY_PPAGE: scroll_page_up,
    curses.KEY_NPAGE: scroll_page_down,
    curses.KEY_HOME: scroll_home,
}


def matching_repo(fleet: Fleet | None, root: Path) -> RepoState | None:
    if fleet is None:
        return None
    return next((repo for repo in fleet.repos if repo.root == root), None)


def paint_lines(win: curses.window, rect: Rect, rows: list[tuple[str, bool]], state: WatchState) -> None:
    for row, (text, head) in enumerate(rows):
        put(win, rect.y + 1 + row, rect.x, text, state.theme.heading if head else 0)


PANELS: list[type[Panel]] = [FleetPanel]
