import contextlib
import curses
import locale
import time
from functools import partial
from itertools import starmap
from pathlib import Path

from .collect import collect_fleet
from .model import Fleet
from .panels import PANELS, ConversationPanel, Panel, Rect, WatchState, draw_box, put, selected_repo, worker_rows
from .theme import GLYPH_FLOURISH, ROUND, init_theme, vine

MIN_W = 70
MIN_H = 20
TICK_MS = 125
NEVER = object()
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
IDLE_KEYS = frozenset({-1, curses.KEY_RESIZE, ord("?")})


class Caught:
    def __init__(self) -> None:
        self.error: BaseException | None = None

    def __enter__(self) -> "Caught":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> bool:
        self.error = exc
        return isinstance(exc, Exception)


def skip(*_args: object, **_kwargs: object) -> None:
    return None


def is_code(value: int | None) -> bool:
    return value is not None


def instantiate(panel_cls: type[Panel]) -> Panel:
    return panel_cls()


def run(roots: list[Path], refresh: float = 2.0, show_all: bool = False) -> int:
    locale.setlocale(locale.LC_ALL, "")
    return curses.wrapper(_main, roots, refresh, show_all)


def _main(stdscr: curses.window, roots: list[Path], refresh: float, show_all: bool) -> int:
    hide_cursor()
    stdscr.timeout(TICK_MS)
    return run_session(WatchSession(roots, refresh, show_all), stdscr)


def run_session(session: "WatchSession", stdscr: curses.window) -> int:
    return next(filter(is_code, iter(partial(session.tick, stdscr), NEVER)))


class WatchSession:
    def __init__(self, roots: list[Path], refresh: float, show_all: bool) -> None:
        self.roots = roots
        self.refresh = refresh
        self.show_all = show_all
        self.state = WatchState(fleet=None, theme=init_theme(), tick=0)
        self.panels = list(map(instantiate, PANELS))
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
        chosen = (skip, WatchSession.refresh_now)[now - self.collected >= self.refresh]
        chosen(self, now)

    def refresh_now(self, now: float) -> None:
        refresh_fleet(self.roots, self.state, self.show_all)
        chosen = (skip, WatchSession.sync_detail)[self.detail is not None]
        chosen(self)
        self.collected = now

    def sync_detail(self) -> None:
        self.detail.sync(self.state.fleet)

    def handle_key(self, key: int) -> int | None:
        chosen = next(filter(None, (idle_handler(key), detail_handler(self.detail), WatchSession.handle_nav)))
        return chosen(self, key)

    def handle_idle(self, key: int) -> int | None:
        IDLE_ACTIONS.get(key, skip)(self)
        return None

    def bump_tick(self) -> None:
        self.state.tick += 1

    def toggle_legend(self) -> None:
        self.legend = not self.legend

    def handle_nav(self, key: int) -> int | None:
        chosen = NAV_ACTIONS.get(key, WatchSession.handle_panel)
        return chosen(self, key)

    def cycle_panel(self, _key: int) -> None:
        self.active = (self.active + 1) % len(self.panels)

    def request_refresh(self, _key: int) -> None:
        self.collected = 0.0

    def handle_detail(self, key: int) -> int | None:
        chosen = (skip, WatchSession.close_detail)[detail_backs(self.detail, key, self.state)]
        chosen(self)
        return None

    def close_detail(self) -> None:
        self.detail = None

    def handle_panel(self, key: int) -> int | None:
        chosen = PANEL_ACTIONS.get(self.panels[self.active].on_key(key, self.state), skip)
        return chosen(self)

    def quit_watch(self) -> int:
        return 0

    def open_from_panel(self) -> None:
        self.open_detail()

    def open_detail(self) -> None:
        open_repo(self, selected_repo(self.state))


def idle_handler(key: int) -> object:
    return {True: WatchSession.handle_idle}.get(key in IDLE_KEYS)


def detail_handler(detail: ConversationPanel | None) -> object:
    return {True: WatchSession.handle_detail}.get(detail is not None)


def detail_backs(detail: ConversationPanel | None, key: int, state: WatchState) -> bool:
    return False not in (detail is not None, key_action(detail, key, state) == "back")


def key_action(detail: ConversationPanel | None, key: int, state: WatchState) -> str | None:
    return getattr(detail, "on_key", skip)(key, state)


def open_repo(session: WatchSession, repo: object) -> None:
    chosen = (skip, set_detail)[repo is not None]
    chosen(session, repo)


def set_detail(session: WatchSession, repo: object) -> None:
    session.detail = ConversationPanel(repo)


def hide_cursor() -> None:
    with contextlib.suppress(curses.error):
        curses.curs_set(0)


def refresh_fleet(roots: list[Path], state: WatchState, show_all: bool) -> None:
    apply_fleet(state, collected_fleet(roots, show_all))
    state.selected = max(0, min(state.selected, max(0, len(worker_rows(state.fleet)) - 1)))


def collected_fleet(roots: list[Path], show_all: bool) -> tuple[Fleet | None, str | None]:
    box = Caught()
    with box:
        return filtered_fleet(collect_fleet(roots), show_all), None
    return None, f"collect failed: {box.error}"[:60]


def apply_fleet(state: WatchState, result: tuple[Fleet | None, str | None]) -> None:
    fleet, error = result
    chosen = (set_fleet, skip)[fleet is None]
    chosen(state, fleet)
    state.error = error


def set_fleet(state: WatchState, fleet: Fleet | None) -> None:
    state.fleet = fleet


def keep_all_repos(_fleet: Fleet) -> None:
    return None


def repo_alive(repo: object) -> bool:
    return repo.alive


def keep_alive_repos(fleet: Fleet) -> None:
    fleet.repos = list(filter(repo_alive, fleet.repos))


FILTERS = {True: keep_all_repos, False: keep_alive_repos}


def filtered_fleet(fleet: Fleet, show_all: bool) -> Fleet:
    FILTERS[show_all](fleet)
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
    chosen = (draw_frame, draw_too_small)[True in (width < MIN_W, height < MIN_H)]
    chosen(stdscr, panel, detail, state, legend, height, width)


def draw_too_small(
    stdscr: curses.window, panel: Panel, detail: ConversationPanel | None, state: WatchState, legend: bool, height: int, width: int
) -> None:
    notice = f"resize to at least {MIN_W}x{MIN_H}"
    put(stdscr, height // 2, max(0, (width - len(notice)) // 2), notice, state.theme.heading)


def draw_frame(
    stdscr: curses.window, panel: Panel, detail: ConversationPanel | None, state: WatchState, legend: bool, height: int, width: int
) -> None:
    draw_header(stdscr, width, state)
    draw_footer(stdscr, height, width, detail, state)
    (panel, detail)[detail is not None].render(stdscr, Rect(2, 0, height - 3, width), True, state)
    chosen = (skip, draw_legend)[legend]
    chosen(stdscr, height, width, state)


def legend_row(item: tuple[str, str]) -> str:
    glyph, meaning = item
    return f" {glyph}  {meaning}"


def legend_put(win: curses.window, rect: Rect, state: WatchState, index: int, row: str) -> None:
    put(win, rect.y + 1 + index, rect.x + 1, row, state.theme.secondary)


def draw_legend(win: curses.window, height: int, width: int, state: WatchState) -> None:
    rows = list(map(legend_row, LEGEND))
    inner = max(map(len, [*rows, " key "]))
    rect = Rect(max(1, (height - len(rows) - 2) // 2), max(0, (width - inner - 2) // 2), len(rows) + 2, inner + 2)
    draw_box(win, rect, ROUND, state.theme.border_focus)
    put(win, rect.y, rect.x + 2, " key ", state.theme.heading)
    list(starmap(partial(legend_put, win, rect, state), enumerate(rows)))


def draw_header(win: curses.window, width: int, state: WatchState) -> None:
    put(win, 0, 0, f" {GLYPH_FLOURISH} M A R E S T A I L {GLYPH_FLOURISH}", state.theme.heading)
    status = status_text(state)
    put(win, 0, width - len(status), status, state.theme.secondary)
    put(win, 1, 0, vine(width), state.theme.border)


def status_text(state: WatchState) -> str:
    return f"{len(getattr(state.fleet, 'repos', []))} beds · {len(worker_rows(state.fleet))} workers · {time.strftime('%H:%M:%S')} "


def draw_footer(win: curses.window, height: int, width: int, detail: ConversationPanel | None, state: WatchState) -> None:
    put(win, height - 1, 1, footer_hints(detail), state.theme.secondary)
    chosen = (skip, put_error)[bool(state.error)]
    chosen(win, height, width, state)


def put_error(win: curses.window, height: int, width: int, state: WatchState) -> None:
    put(win, height - 1, width - len(state.error) - 1, state.error, state.theme.bounced)


def fleet_hints(_detail: ConversationPanel | None) -> str:
    return "↑↓ select · enter open · tab panel · r refresh · ? key · q quit"


def detail_hints(detail: ConversationPanel) -> str:
    return "j/k scroll · PgUp/PgDn · q back" + ("", " ⇊")[detail.follow]


def footer_hints(detail: ConversationPanel | None) -> str:
    chosen = (fleet_hints, detail_hints)[detail is not None]
    return chosen(detail)


IDLE_ACTIONS = {-1: WatchSession.bump_tick, ord("?"): WatchSession.toggle_legend}
NAV_ACTIONS = {ord("\t"): WatchSession.cycle_panel, ord("r"): WatchSession.request_refresh}
PANEL_ACTIONS = {"quit": WatchSession.quit_watch, "open": WatchSession.open_from_panel}
