import curses
import locale
import time
from pathlib import Path

from .collect import collect_fleet
from .panels import PANELS, ConversationPanel, Panel, Rect, WatchState, put, selected_repo, worker_rows
from .theme import GLYPH_FLOURISH, init_theme, vine

MIN_W = 70
MIN_H = 20
TICK_MS = 125


def run(roots: list[Path], refresh: float = 2.0) -> int:
    locale.setlocale(locale.LC_ALL, "")
    return curses.wrapper(_main, roots, refresh)


def _main(stdscr: curses.window, roots: list[Path], refresh: float) -> int:
    hide_cursor()
    stdscr.timeout(TICK_MS)
    state = WatchState(fleet=None, theme=init_theme(), tick=0)
    panels = [panel_cls() for panel_cls in PANELS]
    active = 0
    detail: ConversationPanel | None = None
    collected = 0.0
    while True:
        now = time.monotonic()
        if now - collected >= refresh:
            refresh_fleet(roots, state)
            collected = now
        draw(stdscr, panels[active], detail, state)
        key = stdscr.getch()
        if key == -1:
            state.tick += 1
            continue
        if key == curses.KEY_RESIZE:
            continue
        if detail is not None:
            if detail.on_key(key, state) == "back":
                detail = None
            continue
        if key == ord("\t"):
            active = (active + 1) % len(panels)
            continue
        if key == ord("r"):
            collected = 0.0
            continue
        action = panels[active].on_key(key, state)
        if action == "quit":
            return 0
        if action == "open":
            repo = selected_repo(state)
            if repo is not None and repo.worker is not None:
                detail = ConversationPanel(repo.worker)


def hide_cursor() -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass


def refresh_fleet(roots: list[Path], state: WatchState) -> None:
    try:
        state.fleet = collect_fleet(roots)
        state.error = None
    except Exception as error:
        state.error = f"collect failed: {error}"[:60]
    state.selected = max(0, min(state.selected, max(0, len(worker_rows(state.fleet)) - 1)))


def draw(stdscr: curses.window, panel: Panel, detail: ConversationPanel | None, state: WatchState) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if width < MIN_W or height < MIN_H:
        notice = f"resize to at least {MIN_W}x{MIN_H}"
        put(stdscr, height // 2, max(0, (width - len(notice)) // 2), notice, state.theme.heading)
    else:
        draw_header(stdscr, width, state)
        draw_footer(stdscr, height, width, detail, state)
        target = detail if detail is not None else panel
        target.render(stdscr, Rect(2, 0, height - 3, width), True, state)
    stdscr.noutrefresh()
    curses.doupdate()


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
    if detail is not None:
        hints = "j/k scroll · PgUp/PgDn · q back"
    else:
        hints = "↑↓ select · enter open · tab panel · r refresh · q quit"
    put(win, height - 1, 1, hints, state.theme.secondary)
    if state.error:
        put(win, height - 1, width - len(state.error) - 1, state.error, state.theme.bounced)
