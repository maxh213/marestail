import curses
import locale
import time
from pathlib import Path

from .collect import collect_fleet
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
    state = WatchState(fleet=None, theme=init_theme(), tick=0)
    panels = [panel_cls() for panel_cls in PANELS]
    active = 0
    detail: ConversationPanel | None = None
    legend = False
    collected = 0.0
    while True:
        now = time.monotonic()
        if now - collected >= refresh:
            refresh_fleet(roots, state, show_all)
            if detail is not None:
                detail.sync(state.fleet)
            collected = now
        draw(stdscr, panels[active], detail, state, legend)
        key = stdscr.getch()
        if key == -1:
            state.tick += 1
            continue
        if key == curses.KEY_RESIZE:
            continue
        if key == ord("?"):
            legend = not legend
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
            if repo is not None:
                detail = ConversationPanel(repo)


def hide_cursor() -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass


def refresh_fleet(roots: list[Path], state: WatchState, show_all: bool) -> None:
    try:
        fleet = collect_fleet(roots)
        if not show_all:
            fleet.repos = [repo for repo in fleet.repos if repo.alive]
        state.fleet = fleet
        state.error = None
    except Exception as error:
        state.error = f"collect failed: {error}"[:60]
    state.selected = max(0, min(state.selected, max(0, len(worker_rows(state.fleet)) - 1)))


def draw(stdscr: curses.window, panel: Panel, detail: ConversationPanel | None, state: WatchState, legend: bool) -> None:
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
        if legend:
            draw_legend(stdscr, height, width, state)
    stdscr.noutrefresh()
    curses.doupdate()


def draw_legend(win: curses.window, height: int, width: int, state: WatchState) -> None:
    rows = [f" {glyph}  {meaning}" for glyph, meaning in LEGEND]
    inner = max(len(row) for row in rows + [" key "])
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
    if detail is not None:
        hints = "j/k scroll · PgUp/PgDn · q back"
        if detail.follow:
            hints += " ⇊"
    else:
        hints = "↑↓ select · enter open · tab panel · r refresh · ? key · q quit"
    put(win, height - 1, 1, hints, state.theme.secondary)
    if state.error:
        put(win, height - 1, width - len(state.error) - 1, state.error, state.theme.bounced)
