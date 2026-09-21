import curses
import locale
import time
from pathlib import Path
from typing import Any, cast

from marestail.tui import app
from marestail.tui.model import Fleet, RepoState
from marestail.tui.panels import ConversationPanel, FleetPanel, Rect, WatchState, draw_box
from marestail.tui.theme import GLYPH_FLOURISH, mono_theme


class FakeScr:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.keys: list[int] = []
        self.erased = 0
        self.cells: list[tuple[int, int, str, int]] = []
        self.timeouts: list[int | None] = []

    def timeout(self, ms: int) -> None:
        self.timeouts.append(ms)

    def getch(self) -> int:
        return self.keys.pop(0) if self.keys else -1

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def erase(self) -> None:
        self.erased += 1

    def noutrefresh(self) -> None:
        return None

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        self.cells.append((y, x, text, attr))


def repo(root: Path, alive: bool = True) -> RepoState:
    return RepoState(name=root.name, root=root, branch="main", head="a", task="t", log_path=None, alive=alive)


def as_window(fake: FakeScr) -> curses.window:
    return cast(curses.window, fake)


def reply(value: str | None) -> Any:
    return lambda key, state: value


class Tracker:
    def __init__(self, fn: Any = lambda *args, **kwargs: None) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.fn = fn

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self.fn(*args, **kwargs)


def tracker(fn: Any = lambda *args, **kwargs: None) -> Tracker:
    return Tracker(fn)


def test_surely_keeps_missing_values() -> None:
    assert app.surely("x") == "x"
    assert app.surely(None) is None
    assert app.is_code(3) is True
    assert app.is_code(None) is False


def test_run_session_repeats_a_finite_number_of_ticks(monkeypatch: Any) -> None:
    seen: list[int] = []

    def fake_repeat(item: object, times: int) -> list[object]:
        seen.append(times)
        return [item]

    class Session:
        def tick(self, stdscr: object) -> None:
            return None

    monkeypatch.setattr(app, "repeat", fake_repeat)
    assert app.run_session(Session(), object()) == 0  # type: ignore[arg-type]
    assert seen == [app.SESSION_TICKS]


def test_run_wraps(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def setlocale(category: int, locale_name: str) -> None:
        seen["locale"] = (category, locale_name)

    def wrapper(fn: Any, *args: Any) -> int:
        seen["fn"] = fn
        seen["args"] = args
        return 0

    monkeypatch.setattr("marestail.tui.app.locale.setlocale", setlocale)
    monkeypatch.setattr("marestail.tui.app.curses.wrapper", wrapper)
    roots = [Path(".")]
    assert app.run(roots) == 0
    assert seen["locale"] == (locale.LC_ALL, "")
    assert seen["fn"] is app._main
    assert seen["args"] == (roots, 2.0, False)
    assert app.run(roots, 3.5, True) == 0
    assert seen["args"] == (roots, 3.5, True)


def test_session_keys(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    monkeypatch.setattr(app, "hide_cursor", lambda: None)
    monkeypatch.setattr(app, "refresh_fleet", lambda *args: None)
    monkeypatch.setattr("marestail.tui.app.curses.doupdate", lambda: None)
    session = app.WatchSession([tmp_path], 0.0, True)
    session.state.fleet = Fleet(repos=[repo(tmp_path)], scanned_at=0)
    scr = FakeScr()
    scr.keys = [-1]
    assert session.tick(as_window(scr)) is None
    assert session.state.tick == 1
    assert session.handle_key(curses.KEY_RESIZE) is None
    assert session.handle_key(ord("?")) is None
    assert session.legend is True
    assert session.handle_key(ord("\t")) is None
    assert session.handle_key(ord("r")) is None
    assert session.collected == 0.0


def test_session_panel_actions(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    monkeypatch.setattr(app, "refresh_fleet", lambda *args: None)
    session = app.WatchSession([tmp_path], 0.0, True)
    session.state.fleet = Fleet(repos=[repo(tmp_path)], scanned_at=0)
    session.detail = ConversationPanel(repo(tmp_path))
    monkeypatch.setattr(session.detail, "on_key", reply("handled"))
    assert session.handle_detail(ord("j")) is None
    assert session.detail is not None
    session.detail = None
    monkeypatch.setattr(session.panels[0], "on_key", reply(None))
    assert session.handle_panel(ord("z")) is None


def test_handle_key_forwards_to_detail(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    monkeypatch.setattr(app, "refresh_fleet", lambda *args: None)
    session = app.WatchSession([tmp_path], 0.0, True)
    session.detail = ConversationPanel(repo(tmp_path))
    monkeypatch.setattr(session.detail, "on_key", reply("handled"))
    assert session.handle_key(ord("j")) is None
    assert session.detail is not None


def test_session_quit_open_and_back(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    monkeypatch.setattr(app, "refresh_fleet", lambda *args: None)
    session = app.WatchSession([tmp_path], 0.0, True)
    session.state.fleet = Fleet(repos=[repo(tmp_path)], scanned_at=0)
    monkeypatch.setattr(session.panels[0], "on_key", reply("quit"))
    assert session.handle_key(ord("x")) == 0
    session = app.WatchSession([tmp_path], 0.0, True)
    session.state.fleet = Fleet(repos=[repo(tmp_path)], scanned_at=0)
    monkeypatch.setattr(session.panels[0], "on_key", reply("open"))
    assert session.handle_key(ord("x")) is None
    detail = session.detail
    assert detail is not None
    monkeypatch.setattr(detail, "on_key", reply("back"))
    session.handle_detail(ord("q"))
    assert session.detail is None
    session.open_detail()
    session.state.fleet = Fleet(repos=[], scanned_at=0)
    session.detail = None
    session.open_detail()
    assert session.detail is None


def test_main_returns_on_quit(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    monkeypatch.setattr(app, "hide_cursor", lambda: None)
    monkeypatch.setattr(app, "refresh_fleet", lambda *args: None)
    scr = FakeScr()
    calls = {"n": 0}

    def tick(self: app.WatchSession, stdscr: curses.window) -> int | None:
        calls["n"] += 1
        return 0 if calls["n"] > 1 else None

    monkeypatch.setattr(app.WatchSession, "tick", tick)
    assert app._main(as_window(scr), [tmp_path], 1.0, False) == 0


def test_hide_cursor(monkeypatch: Any) -> None:
    seen: list[int] = []
    monkeypatch.setattr("marestail.tui.app.curses.curs_set", lambda n: seen.append(n))
    app.hide_cursor()
    assert seen == [0]

    def boom(n: int) -> None:
        raise curses.error("no")

    monkeypatch.setattr("marestail.tui.app.curses.curs_set", boom)
    app.hide_cursor()


def test_refresh_and_draw(tmp_path: Path, monkeypatch: Any) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    fleet = Fleet(repos=[repo(tmp_path, alive=True), repo(tmp_path / "b", alive=False)], scanned_at=0)
    monkeypatch.setattr(app, "collect_fleet", lambda roots: fleet)
    app.refresh_fleet([tmp_path], watch, False)
    assert watch.fleet is not None
    assert len(watch.fleet.repos) == 1
    app.refresh_fleet([tmp_path], watch, True)
    monkeypatch.setattr(app, "collect_fleet", lambda roots: (_ for _ in ()).throw(RuntimeError("boom")))
    app.refresh_fleet([tmp_path], watch, True)
    assert watch.error is not None
    small = as_window(FakeScr(10, 10))
    monkeypatch.setattr("marestail.tui.app.curses.doupdate", lambda: None)
    app.draw(small, FleetPanel(), None, watch, False)
    scr = as_window(FakeScr(24, 80))
    app.draw(scr, FleetPanel(), None, watch, True)
    detail = ConversationPanel(repo(tmp_path))
    app.draw(scr, FleetPanel(), detail, watch, False)
    watch.error = "err"
    app.draw_footer(scr, 24, 80, detail, watch)
    detail.follow = False
    app.draw_footer(scr, 24, 80, detail, watch)
    app.draw_footer(scr, 24, 80, None, watch)
    app.draw_header(scr, 80, watch)
    app.draw_legend(scr, 24, 80, watch)
    monkeypatch.setattr("marestail.tui.app.time.strftime", lambda fmt: "12:00:00")
    assert "beds" in app.status_text(watch)
    assert app.status_text(WatchState(fleet=None, theme=mono_theme(), tick=0)) == "0 beds · 0 workers · 12:00:00 "


def cell_texts(screen: FakeScr) -> list[str]:
    return [text for _, _, text, _ in screen.cells]


def test_draw_header_and_legend_write_labels(tmp_path: Path) -> None:
    watch = WatchState(fleet=Fleet(repos=[repo(tmp_path)], scanned_at=0), theme=mono_theme(), tick=0)
    header = FakeScr(24, 80)
    app.draw_header(as_window(header), 80, watch)
    texts = cell_texts(header)
    banner_text = f" {GLYPH_FLOURISH}{app.HEADER}{GLYPH_FLOURISH}"
    assert banner_text in texts
    banner = next(cell for cell in header.cells if cell[2] == banner_text)
    assert banner[0] == 0
    assert banner[1] == 0
    assert banner[3] == watch.theme.heading
    vine_cell = next(cell for cell in header.cells if cell[0] == 1)
    assert vine_cell[1] == 0
    assert len(vine_cell[2]) == 80
    assert vine_cell[3] == watch.theme.border
    legend = FakeScr(24, 80)
    app.draw_legend(as_window(legend), 24, 80, watch)
    written = cell_texts(legend)
    assert app.KEY_LABEL in written
    for glyph, meaning in app.LEGEND:
        assert app.legend_row((glyph, meaning)) in written
    key = next(cell for cell in legend.cells if cell[2] == app.KEY_LABEL)
    rows = [app.legend_row(item) for item in app.LEGEND]
    inner = max(len(row) for row in [*rows, app.KEY_LABEL])
    top = max(1, (24 - len(rows) - 2) // 2)
    left = max(0, (80 - inner - 2) // 2)
    assert key[:3] == (top, left + 2, app.KEY_LABEL)
    assert key[3] == watch.theme.heading
    assert {cell[3] for cell in legend.cells} == {watch.theme.border_focus, watch.theme.heading, watch.theme.secondary}


def test_maybe_refresh_skips(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 100.0, True)
    session.collected = time.monotonic()
    session.maybe_refresh()
    session.refresh = 0.0
    session.detail = ConversationPanel(repo(tmp_path))
    monkeypatch.setattr(app, "refresh_fleet", lambda *args: None)
    session.maybe_refresh()


def test_filtered_fleet(tmp_path: Path) -> None:
    fleet = Fleet(repos=[repo(tmp_path, True), repo(tmp_path / "x", False)], scanned_at=0)
    assert len(app.filtered_fleet(fleet, True).repos) == 2
    assert len(app.filtered_fleet(fleet, False).repos) == 1


def test_caught_swallows_exceptions() -> None:
    box = app.Caught()
    assert box.__exit__(None, None, None) is False
    assert box.__exit__(ValueError, ValueError("boom"), None) is True
    assert isinstance(box.error, ValueError)


def test_app_helpers(tmp_path: Path, monkeypatch: Any) -> None:
    assert app.skip() is None
    assert app.surely("x") == "x"
    assert app.is_code(None) is False
    assert app.is_code(0) is True
    assert isinstance(app.instantiate(FleetPanel), FleetPanel)
    assert app.idle_handler(-1) is app.WatchSession.handle_idle
    assert app.idle_handler(ord("x")) is None
    assert app.detail_handler(None) is None
    assert app.detail_handler(ConversationPanel(repo(tmp_path))) is app.WatchSession.handle_detail
    assert app.key_action(None, 1, WatchState(fleet=None, theme=mono_theme(), tick=0)) is None
    assert app.repo_alive(repo(tmp_path, True)) is True
    fleet = Fleet(repos=[repo(tmp_path, True), repo(tmp_path / "d", False)], scanned_at=0)
    app.keep_alive_repos(fleet)
    assert len(fleet.repos) == 1
    app.keep_all_repos(fleet)
    assert app.legend_row(("⚘", "run")) == " ⚘  run"
    assert app.fleet_hints(None) == app.FLEET_HINT
    detail = ConversationPanel(repo(tmp_path))
    detail.follow = False
    assert app.detail_hints(detail) == app.DETAIL_HINT
    detail.follow = True
    assert app.detail_hints(detail) == app.DETAIL_HINT + app.DETAIL_FOLLOW
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    app.set_fleet(watch, fleet)
    assert watch.fleet is fleet
    app.apply_fleet(watch, (None, "err"))
    assert watch.error == "err"
    monkeypatch.setattr(app, "collect_fleet", lambda roots: (_ for _ in ()).throw(RuntimeError("x")))
    empty, error = app.collected_fleet([tmp_path], True)
    assert empty is None
    assert error is not None
    assert error == f"{app.COLLECT_PREFIX}{RuntimeError('x')}"[: app.ERROR_WIDTH]
    assert error.startswith(app.COLLECT_PREFIX)
    box = app.Caught()
    with box:
        pass
    assert box.error is None
    monkeypatch.setattr(app, "init_theme", mono_theme)
    monkeypatch.setattr(app, "refresh_fleet", lambda *args: None)
    session = app.WatchSession([tmp_path], 1.0, True)
    session.state.fleet = Fleet(repos=[repo(tmp_path)], scanned_at=0)
    app.set_detail(session, repo(tmp_path))
    opened = session.detail
    assert opened is not None
    session.close_detail()
    assert session.detail is not opened
    app.open_repo(session, None)
    assert session.detail is not opened
    assert session.quit_watch() == 0
    session.bump_tick()
    session.toggle_legend()
    session.cycle_panel(ord("\t"))
    session.request_refresh(ord("r"))
    assert session.collected == 0.0
    assert app.detail_backs(None, ord("q"), session.state) is False
    monkeypatch.setattr(app, "draw_legend", lambda *args: None)
    scr = as_window(FakeScr(10, 10))
    app.draw_too_small(scr, FleetPanel(), None, session.state, False, 10, 10)
    app.put_error(scr, 10, 10, WatchState(fleet=None, theme=mono_theme(), tick=0, error="e"))
    session.legend = True
    app.legend_put(scr, Rect(0, 0, 5, 20), session.state, 0, " row")
    assert app.footer_hints(None) == app.FLEET_HINT
    assert app.footer_hints(detail) == app.DETAIL_HINT + app.DETAIL_FOLLOW
    session.detail = ConversationPanel(repo(tmp_path))
    session.sync_detail()
    session.open_from_panel()
    assert session.detail is not None
    session.handle_nav(ord("r"))
    session.refresh_now(0.0)
    app.draw_body(scr, FleetPanel(), None, session.state, False, 24, 80)
    app.draw_frame(scr, FleetPanel(), None, session.state, False, 24, 80)
    session.detail = None
    monkeypatch.setattr("marestail.tui.app.curses.doupdate", lambda: None)
    scr_keys = FakeScr()
    scr_keys.keys = [ord("q")]
    monkeypatch.setattr(session.panels[0], "on_key", reply("quit"))
    assert app.run_session(session, as_window(scr_keys)) == 0
    assert scr_keys.erased == 1


def test_main_records_timeout_and_session_args(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    monkeypatch.setattr(app, "hide_cursor", lambda: None)
    seen: dict[str, Any] = {}

    def run_session(session: app.WatchSession, stdscr: curses.window) -> int:
        seen["session"] = session
        seen["stdscr"] = stdscr
        return 0

    monkeypatch.setattr(app, "run_session", run_session)
    scr = FakeScr()
    win = as_window(scr)
    roots = [tmp_path]
    assert app._main(win, roots, 1.5, False) == 0
    assert scr.timeouts == [app.TICK_MS]
    assert seen["stdscr"] is win
    session = seen["session"]
    assert session.roots is roots
    assert session.refresh == 1.5
    assert session.show_all is False


def test_watch_session_init_fields(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    roots = [tmp_path]
    session = app.WatchSession(roots, 2.5, False)
    assert session.roots is roots
    assert session.refresh == 2.5
    assert session.show_all is False
    assert session.collected == 0.0
    assert session.legend is False
    assert session.active == 0
    assert session.detail is None
    assert session.state.tick == 0


def test_tick_passes_detail(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    monkeypatch.setattr(app, "refresh_fleet", lambda *args: None)
    session = app.WatchSession([tmp_path], 100.0, True)
    session.collected = time.monotonic()
    session.detail = ConversationPanel(repo(tmp_path))
    drawn = tracker()
    monkeypatch.setattr(app, "draw", drawn)
    scr = FakeScr()
    scr.keys = [-1]
    assert session.tick(as_window(scr)) is None
    assert drawn.calls[0][0][2] is session.detail
    assert drawn.calls[0][0][4] is session.legend


def test_maybe_refresh_boundary_and_now(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 2.0, True)
    monkeypatch.setattr("marestail.tui.app.time.monotonic", lambda: 10.0)
    refreshed = tracker()
    monkeypatch.setattr(app.WatchSession, "refresh_now", refreshed)
    session.collected = 8.0
    session.maybe_refresh()
    assert refreshed.calls == [((session, 10.0), {})]
    refreshed.calls.clear()
    session.collected = 8.0
    session.refresh = 2.1
    session.maybe_refresh()
    assert refreshed.calls == []


def test_refresh_now_passes_session_fields(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 1.0, True)
    fleet_calls = tracker()
    monkeypatch.setattr(app, "refresh_fleet", fleet_calls)
    synced = tracker()
    session.detail = ConversationPanel(repo(tmp_path))
    monkeypatch.setattr(session.detail, "sync", synced)
    session.refresh_now(9.5)
    assert fleet_calls.calls == [((session.roots, session.state, session.show_all), {})]
    assert synced.calls == [((session.state.fleet,), {})]
    assert session.collected == 9.5


def test_handle_key_uses_detail_and_key(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 1.0, True)
    session.detail = ConversationPanel(repo(tmp_path))
    keys = tracker(lambda key, state: "handled")
    monkeypatch.setattr(session.detail, "on_key", keys)
    assert session.handle_key(ord("j")) is None
    assert keys.calls[0][0] == (ord("j"), session.state)
    assert session.detail is not None


def test_handle_nav_passes_key(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 1.0, True)
    panel_keys = tracker(lambda key, state: None)
    monkeypatch.setattr(session.panels[0], "on_key", panel_keys)
    assert session.handle_nav(ord("z")) is None
    assert panel_keys.calls[0][0] == (ord("z"), session.state)


def test_cycle_panel_steps_one(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 1.0, True)
    session.panels = [FleetPanel(), FleetPanel(), FleetPanel()]
    session.active = 0
    session.cycle_panel(ord("\t"))
    assert session.active == 1
    session.cycle_panel(ord("\t"))
    assert session.active == 2
    session.cycle_panel(ord("\t"))
    assert session.active == 0


def test_handle_detail_uses_key_and_state(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 1.0, True)
    session.detail = ConversationPanel(repo(tmp_path))
    keys = tracker(lambda key, state: "back")
    monkeypatch.setattr(session.detail, "on_key", keys)
    assert session.handle_detail(ord("q")) is None
    assert keys.calls[0][0] == (ord("q"), session.state)
    assert session.detail is None


def test_handle_panel_forwards_key_state(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 1.0, True)
    keys = tracker(lambda key, state: "open")
    monkeypatch.setattr(session.panels[0], "on_key", keys)
    session.state.fleet = Fleet(repos=[repo(tmp_path)], scanned_at=0)
    assert session.handle_panel(ord("x")) is None
    assert keys.calls[0][0] == (ord("x"), session.state)
    assert session.detail is not None


def test_detail_backs_and_key_action(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    detail = ConversationPanel(repo(tmp_path))
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    keys = tracker(lambda key, state: "back")
    monkeypatch.setattr(detail, "on_key", keys)
    assert app.detail_backs(detail, ord("q"), watch) is True
    assert keys.calls[0][0] == (ord("q"), watch)
    assert app.key_action(detail, ord("q"), watch) == "back"


def test_bump_tick_increments(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    session = app.WatchSession([tmp_path], 1.0, True)
    session.state.tick = 4
    session.bump_tick()
    assert session.state.tick == 5


def test_caught_starts_empty() -> None:
    box = app.Caught()
    assert box.error is None


def test_refresh_fleet_clamps_selected(tmp_path: Path, monkeypatch: Any) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0, selected=0)
    monkeypatch.setattr(app, "collect_fleet", lambda roots: Fleet(repos=[], scanned_at=0))
    app.refresh_fleet([tmp_path], watch, True)
    assert watch.selected == 0
    watch.selected = 9
    live = repo(tmp_path)
    monkeypatch.setattr(app, "collect_fleet", lambda roots: Fleet(repos=[live], scanned_at=0))
    app.refresh_fleet([tmp_path], watch, True)
    assert watch.selected == 0
    rows = [repo(tmp_path / f"r{index}") for index in range(3)]
    watch.selected = 2
    monkeypatch.setattr(app, "collect_fleet", lambda roots: Fleet(repos=rows, scanned_at=0))
    app.refresh_fleet([tmp_path], watch, True)
    assert watch.selected == 2
    watch.selected = 9
    app.refresh_fleet([tmp_path], watch, True)
    assert watch.selected == 2


def test_refresh_fleet_passes_roots(tmp_path: Path, monkeypatch: Any) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    seen: list[list[Path]] = []

    def collect(roots: list[Path]) -> Fleet:
        seen.append(roots)
        return Fleet(repos=[], scanned_at=0)

    monkeypatch.setattr(app, "collect_fleet", collect)
    roots = [tmp_path]
    app.refresh_fleet(roots, watch, True)
    assert seen == [roots]
    fleet, error = app.collected_fleet(roots, True)
    assert error is None
    assert fleet is not None


def test_collected_fleet_clips_error(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "collect_fleet", lambda roots: (_ for _ in ()).throw(RuntimeError("e" * 80)))
    empty, error = app.collected_fleet([tmp_path], True)
    assert empty is None
    assert error is not None
    assert len(error) == app.ERROR_WIDTH
    assert error == f"{app.COLLECT_PREFIX}{'e' * 80}"[:60]
    assert len(f"{app.COLLECT_PREFIX}{'e' * 80}") > 60


def test_draw_passes_detail(tmp_path: Path, monkeypatch: Any) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    body = tracker()
    monkeypatch.setattr(app, "draw_body", body)
    monkeypatch.setattr("marestail.tui.app.curses.doupdate", lambda: None)
    detail = ConversationPanel(repo(tmp_path))
    scr = FakeScr(24, 80)
    app.draw(as_window(scr), FleetPanel(), detail, watch, True)
    args = body.calls[0][0]
    assert args[2] is detail
    assert args[5:] == (24, 80)
    assert scr.erased == 1


def test_draw_body_size_boundary(tmp_path: Path, monkeypatch: Any) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    frames = tracker()
    small = tracker()
    monkeypatch.setattr(app, "draw_frame", frames)
    monkeypatch.setattr(app, "draw_too_small", small)
    panel = FleetPanel()
    detail = ConversationPanel(repo(tmp_path))
    scr = as_window(FakeScr(app.MIN_H, app.MIN_W))
    app.draw_body(scr, panel, detail, watch, False, app.MIN_H, app.MIN_W)
    assert frames.calls
    assert frames.calls[0][0][2] is detail
    assert small.calls == []
    frames.calls.clear()
    app.draw_body(scr, panel, detail, watch, False, app.MIN_H, app.MIN_W - 1)
    assert small.calls
    assert small.calls[0][0][2] is detail
    small.calls.clear()
    app.draw_body(scr, panel, detail, watch, False, app.MIN_H - 1, app.MIN_W)
    assert small.calls


def test_draw_too_small_cell(tmp_path: Path) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    scr = FakeScr(10, 10)
    notice = f"resize to at least {app.MIN_W}x{app.MIN_H}"
    app.draw_too_small(as_window(scr), FleetPanel(), None, watch, False, 10, 10)
    x = max(0, (10 - len(notice)) // 2)
    assert scr.cells == [(10 // 2, x, notice[: 10 - x], watch.theme.heading)]
    narrow = FakeScr(9, 4)
    app.draw_too_small(as_window(narrow), FleetPanel(), None, watch, False, 9, 4)
    assert narrow.cells[0][0] == 9 // 2
    assert narrow.cells[0][1] == 0
    assert narrow.cells[0][2] == notice[:4]
    assert narrow.cells[0][3] == watch.theme.heading
    wide = FakeScr(10, 40)
    app.draw_too_small(as_window(wide), FleetPanel(), None, watch, False, 10, 40)
    assert wide.cells[0][1] == max(0, (40 - len(notice)) // 2)
    assert type(wide.cells[0][1]) is int


def test_draw_frame_rect_and_detail(tmp_path: Path, monkeypatch: Any) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    panel = FleetPanel()
    rendered = tracker()
    monkeypatch.setattr(panel, "render", rendered)
    footer = tracker()
    monkeypatch.setattr(app, "draw_footer", footer)
    header = tracker()
    monkeypatch.setattr(app, "draw_header", header)
    scr = as_window(FakeScr(24, 80))
    app.draw_frame(scr, panel, None, watch, False, 24, 80)
    rect = rendered.calls[0][0][1]
    assert (rect.y, rect.x, rect.h, rect.w) == (2, 0, 21, 80)
    assert rendered.calls[0][0][2] is True
    assert footer.calls[0][0][3] is None
    detail = ConversationPanel(repo(tmp_path))
    detail_render = tracker()
    monkeypatch.setattr(detail, "render", detail_render)
    app.draw_frame(scr, panel, detail, watch, False, 24, 80)
    assert footer.calls[-1][0][3] is detail
    assert detail_render.calls[0][0][2] is True


def test_legend_put_and_draw_legend_geometry(tmp_path: Path) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    scr = FakeScr(24, 80)
    app.legend_put(as_window(scr), Rect(4, 6, 10, 30), watch, 2, " row")
    assert scr.cells == [(7, 7, " row", watch.theme.secondary)]
    legend = FakeScr(5, 8)
    app.draw_legend(as_window(legend), 5, 8, watch)
    rows = list(map(app.legend_row, app.LEGEND))
    inner = max(map(len, [*rows, app.KEY_LABEL]))
    top = max(1, (5 - len(rows) - 2) // 2)
    left = max(0, (8 - inner - 2) // 2)
    key = next(cell for cell in legend.cells if cell[2] == app.KEY_LABEL)
    assert key == (top, left + 2, app.KEY_LABEL, watch.theme.heading)
    assert any(cell[3] == watch.theme.border_focus for cell in legend.cells)


def test_draw_legend_box_on_odd_width(tmp_path: Path, monkeypatch: Any) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0)
    boxes: list[Rect] = []

    def capture(win: Any, rect: Rect, *args: Any) -> None:
        boxes.append(rect)
        draw_box(win, rect, *args)

    monkeypatch.setattr("marestail.tui.app.draw_box", capture)
    legend = FakeScr(24, 81)
    app.draw_legend(as_window(legend), 24, 81, watch)
    rows = list(map(app.legend_row, app.LEGEND))
    inner = 41
    assert max(map(len, [*rows, app.KEY_LABEL])) == inner
    assert boxes[0].y == 7
    assert boxes[0].x == 19
    assert boxes[0].h == 10
    assert boxes[0].w == 43
    assert max(0, (81 - inner - 3) // 2) != 19


def test_header_status_attr(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("marestail.tui.app.time.strftime", lambda fmt: "01:02:03")
    fleet = Fleet(repos=[repo(tmp_path)], scanned_at=0)
    watch = WatchState(fleet=fleet, theme=mono_theme(), tick=0)
    status = app.status_text(watch)
    assert status == "1 beds · 1 workers · 01:02:03 "
    header = FakeScr(24, 80)
    app.draw_header(as_window(header), 80, watch)
    status_cell = next(cell for cell in header.cells if cell[2] == status)
    assert status_cell == (0, 80 - len(status), status, watch.theme.secondary)


def test_footer_and_error_cells(tmp_path: Path) -> None:
    watch = WatchState(fleet=None, theme=mono_theme(), tick=0, error="boom")
    detail = ConversationPanel(repo(tmp_path))
    detail.follow = False
    footer = FakeScr(24, 80)
    app.draw_footer(as_window(footer), 24, 80, detail, watch)
    hints = next(cell for cell in footer.cells if cell[2] == app.DETAIL_HINT)
    assert hints == (23, 1, app.DETAIL_HINT, watch.theme.secondary)
    err = next(cell for cell in footer.cells if cell[2] == "boom")
    assert err == (23, 80 - 4 - 1, "boom", watch.theme.bounced)
    only = FakeScr(10, 20)
    app.put_error(as_window(only), 10, 20, watch)
    assert only.cells == [(9, 15, "boom", watch.theme.bounced)]
    none = FakeScr(24, 80)
    app.draw_footer(as_window(none), 24, 80, None, WatchState(fleet=None, theme=mono_theme(), tick=0))
    assert none.cells == [(23, 1, app.FLEET_HINT, mono_theme().secondary)]


def test_sync_detail_uses_the_fleet(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "init_theme", mono_theme)
    live = repo(tmp_path)
    session = app.WatchSession([tmp_path], 1.0, True)
    session.detail = ConversationPanel(live)
    other = repo(tmp_path)
    other.task = "changed"
    session.state.fleet = Fleet(repos=[other], scanned_at=0)
    session.sync_detail()
    assert session.detail.repo.task == "changed"
