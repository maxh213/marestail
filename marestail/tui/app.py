import contextlib
import curses
import locale
import time
from pathlib import Path

from .collect import collect_fleet
from .model import Fleet
from .panels import PANELS, ConversationPanel, Panel, Rect, WatchState, draw_box, put, selected_repo, worker_rows
from .theme import GLYPH_FLOURISH, ROUND, init_theme, vine

MIN_W = 70
MIN_H = 20
TICK_MS = 125
LEGEND = [
    ("⚘", "running: worker, gate, or runner work"),
    ("✿", "step done"),
    ("✶", "bounced by a judge"),
    ("✔", "passed the judge"),
    ("○", "idle (no live pipeline)"),
    ("💭", "agent thought"),
    ("⚒", "agent tool call"),
    ("⇊", "conversation following, tail -f style"),
]


def run(roots: list[Path], refresh: float = 2.0, show_all: bool = False) -> int:
    locale.setlocale(locale.LC_ALL, "")
    return curses.wrapper(_main, roots, refresh, show_all)


def _main(stdscr: curses.window, roots: list[Path], refresh: float, show_all: bool) -> int:
    hide_cursor()
    stdscr.timeout(TICK_MS)
    session = WatchSession(roots, refresh, show_all)
    while True:
        code = session.tick(stdscr)
        if code is not None:
            return code


class WatchSession:
    def __init__(self, roots: list[Path], refresh: float, show_all: bool) -> None:
        self.roots = roots
        self.refresh = refresh
        self.show_all = show_all
        self.state = WatchState(fleet=None, theme=init_theme(), tick=0)
        self.panels = [panel_cls() for panel_cls in PANELS]
        self.active = 0
        self.detail: ConversationPanel | None = None
        self.legend = False
        self.collected = 0.0

    def tick(self, stdscr: curses.window) -> int | None:
        self.maybe_refresh()
        draw(stdscr, self.panels[self.active], self.detail, self.state, self.legend)
        return self.handle_key(stdscr.getch())

    def maybe_refresh(self) -> None:
        now = time.monotonic()
        if now - self.collected < self.refresh:
            return
        refresh_fleet(self.roots, self.state, self.show_all)
        if self.detail is not None:
            self.detail.sync(self.state.fleet)
        self.collected = now

    def handle_key(self, key: int) -> int | None:
        if key in (-1, curses.KEY_RESIZE, ord("?")):
            return self.handle_idle(key)
        if self.detail is not None:
            return self.handle_detail(key)
        return self.handle_nav(key)

    def handle_idle(self, key: int) -> None:
        if key == -1:
            self.state.tick += 1
        if key == ord("?"):
            self.legend = not self.legend
        return None

    def handle_nav(self, key: int) -> int | None:
        if key == ord("\t"):
            self.active = (self.active + 1) % len(self.panels)
            return None
        if key == ord("r"):
            self.collected = 0.0
            return None
        return self.handle_panel(key)

    def handle_detail(self, key: int) -> None:
        if self.detail is not None and self.detail.on_key(key, self.state) == "back":
            self.detail = None
        return None

    def handle_panel(self, key: int) -> int | None:
        action = self.panels[self.active].on_key(key, self.state)
        if action == "quit":
            return 0
        if action == "open":
            self.open_detail()
        return None

    def open_detail(self) -> None:
        repo = selected_repo(self.state)
        if repo is not None:
            self.detail = ConversationPanel(repo)


def hide_cursor() -> None:
    with contextlib.suppress(curses.error):
        curses.curs_set(0)


def refresh_fleet(roots: list[Path], state: WatchState, show_all: bool) -> None:
    try:
        state.fleet = filtered_fleet(collect_fleet(roots), show_all)
        state.error = None
    except Exception as error:
        state.error = f"collect failed: {error}"[:60]
    state.selected = max(0, min(state.selected, max(0, len(worker_rows(state.fleet)) - 1)))


def filtered_fleet(fleet: Fleet, show_all: bool) -> Fleet:
    if not show_all:
        fleet.repos = [repo for repo in fleet.repos if repo.alive]
    return fleet


def draw(stdscr: curses.window, panel: Panel, detail: ConversationPanel | None, state: WatchState, legend: bool) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    draw_body(stdscr, panel, detail, state, legend, height, width)
    stdscr.noutrefresh()
    curses.doupdate()


def draw_body(
    stdscr: curses.window, panel: Panel, detail: ConversationPanel | None, state: WatchState, legend: bool, height: int, width: int
) -> None:
    if width < MIN_W or height < MIN_H:
        notice = f"resize to at least {MIN_W}x{MIN_H}"
        put(stdscr, height // 2, max(0, (width - len(notice)) // 2), notice, state.theme.heading)
        return
    draw_frame(stdscr, panel, detail, state, legend, height, width)


def draw_frame(
    stdscr: curses.window, panel: Panel, detail: ConversationPanel | None, state: WatchState, legend: bool, height: int, width: int
) -> None:
    draw_header(stdscr, width, state)
    draw_footer(stdscr, height, width, detail, state)
    target = detail if detail is not None else panel
    target.render(stdscr, Rect(2, 0, height - 3, width), True, state)
    if legend:
        draw_legend(stdscr, height, width, state)


def draw_legend(win: curses.window, height: int, width: int, state: WatchState) -> None:
    rows = [f" {glyph}  {meaning}" for glyph, meaning in LEGEND]
    inner = max(len(row) for row in [*rows, " key "])
    rect = Rect(max(1, (height - len(rows) - 2) // 2), max(0, (width - inner - 2) // 2), len(rows) + 2, inner + 2)
    draw_box(win, rect, ROUND, state.theme.border_focus)
    put(win, rect.y, rect.x + 2, " key ", state.theme.heading)
    for i, row in enumerate(rows):
        put(win, rect.y + 1 + i, rect.x + 1, row, state.theme.secondary)


def draw_header(win: curses.window, width: int, state: WatchState) -> None:
    put(win, 0, 0, f" {GLYPH_FLOURISH} M A R E S T A I L {GLYPH_FLOURISH}", state.theme.heading)
    status = status_text(state)
    put(win, 0, width - len(status), status, state.theme.secondary)
    put(win, 1, 0, vine(width), state.theme.border)


def status_text(state: WatchState) -> str:
    fleet = state.fleet
    beds = len(fleet.repos) if fleet is not None else 0
    return f"{beds} beds · {len(worker_rows(fleet))} workers · {time.strftime('%H:%M:%S')} "


def draw_footer(win: curses.window, height: int, width: int, detail: ConversationPanel | None, state: WatchState) -> None:
    put(win, height - 1, 1, footer_hints(detail), state.theme.secondary)
    if state.error:
        put(win, height - 1, width - len(state.error) - 1, state.error, state.theme.bounced)


def footer_hints(detail: ConversationPanel | None) -> str:
    if detail is None:
        return "↑↓ select · enter open · tab panel · r refresh · ? key · q quit"
    return "j/k scroll · PgUp/PgDn · q back" + (" ⇊" if detail.follow else "")
