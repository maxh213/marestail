import curses
import time
from pathlib import Path
from typing import Any, cast

from marestail.tui import app
from marestail.tui.model import Fleet, RepoState
from marestail.tui.panels import ConversationPanel, FleetPanel, WatchState
from marestail.tui.theme import mono_theme


class FakeScr:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.keys: list[int] = []
        self.erased = 0

    def timeout(self, _ms: int) -> None:
        return None

    def getch(self) -> int:
        return self.keys.pop(0) if self.keys else -1

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def erase(self) -> None:
        self.erased += 1

    def noutrefresh(self) -> None:
        return None

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        return None


def repo(root: Path, alive: bool = True) -> RepoState:
    return RepoState(name=root.name, root=root, branch="main", head="a", task="t", log_path=None, alive=alive)


def as_window(fake: FakeScr) -> curses.window:
    return cast(curses.window, fake)


def reply(value: str | None) -> Any:
    return lambda key, state: value


def test_run_wraps(monkeypatch: Any) -> None:
    monkeypatch.setattr("marestail.tui.app.locale.setlocale", lambda *args: None)
    monkeypatch.setattr("marestail.tui.app.curses.wrapper", lambda fn, *args: fn(FakeScr(), *args) or 0)
    monkeypatch.setattr(app, "_main", lambda *args: 0)
    assert app.run([Path(".")]) == 0


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
    monkeypatch.setattr("marestail.tui.app.curses.curs_set", lambda n: None)
    app.hide_cursor()

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
    assert "beds" in app.status_text(watch)
    assert "0 beds" in app.status_text(WatchState(fleet=None, theme=mono_theme(), tick=0))


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
