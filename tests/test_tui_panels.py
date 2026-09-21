import curses
from pathlib import Path
from typing import Any, cast

import pytest

from marestail.tui import panels
from marestail.tui.model import Fleet, Process, RepoState, Step, Worker
from marestail.tui.panels import (
    ConversationPanel,
    FleetPanel,
    Panel,
    Rect,
    WatchState,
    alive_label,
    bed_height,
    bed_index,
    bed_span,
    build_lines,
    clamp,
    clean,
    clip_text,
    draw_bed,
    draw_box,
    draw_idle_row,
    draw_strip,
    draw_worker_row,
    fit_top,
    fleet_action,
    fmt_elapsed,
    gate_rows,
    marquee,
    matching_repo,
    offscreen,
    paint_box,
    paint_lines,
    put,
    selected_repo,
    shift_left,
    tail_lines_of,
    worker_rows,
    wrap_line,
)
from marestail.tui.theme import GLYPH_BOUNCED, GLYPH_FLOURISH, GLYPH_IDLE, GLYPH_RUNNING, GLYPH_SECTION, HEAVY, ROUND, mono_theme


class FakeWin:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.cells: list[tuple[int, int, str, int]] = []
        self.fail = False

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if self.fail:
            raise curses.error("edge")
        self.cells.append((y, x, text, attr))


def as_window(fake: FakeWin) -> curses.window:
    return cast(curses.window, fake)


def step(status: str = "running", verdict: str | None = None) -> Step:
    return Step(role="coder", label="01-coder", attempt=1, status=status, summary="hello world", verdict=verdict, minutes=1.0)


def make_repo(root: Path, alive: bool = True, worker: Worker | None = None, **fields: Any) -> RepoState:
    return RepoState(
        name=root.name,
        root=root,
        branch="main",
        head="abc",
        task="t",
        log_path=None,
        worker=worker,
        alive=alive,
        **fields,
    )


def state_of(fleet: Fleet | None = None) -> WatchState:
    return WatchState(fleet=fleet, theme=mono_theme(), tick=20)


def test_put_and_clip() -> None:
    win: Any = FakeWin(4, 10)
    put(win, 0, 0, "ab", 0)
    put(win, 0, 3, "z", 5)
    put(win, -1, 0, "x", 0)
    put(win, 0, 20, "x", 0)
    put(win, 0, -2, "hello", 0)
    put(win, 1, 0, "", 0)
    win.fail = True
    put(win, 2, 0, "nope", 0)
    assert (0, 0, "ab", 0) in win.cells
    assert (0, 3, "z", 5) in win.cells
    assert clip_text(0, 0, "", 4, 10) is None
    assert offscreen(-1, 0, 4, 10) is True
    assert offscreen(0, 0, 4, 10) is False
    assert offscreen(3, 0, 4, 10) is False
    assert offscreen(4, 0, 4, 10) is True
    assert offscreen(0, 9, 4, 10) is False
    assert offscreen(0, 10, 4, 10) is True
    assert shift_left(0, 2, "ab") == (0, 2, "ab")
    assert shift_left(0, 0, "ab") == (0, 0, "ab")
    assert shift_left(0, -1, "ab") == (0, 0, "b")
    assert shift_left(0, -2, "hello") == (0, 0, "llo")
    assert panels.shift_neg(0, -2, "hello") == (0, 0, "llo")
    with pytest.raises(KeyError):
        panels.shift_neg(0, 0, "hello")
    assert panels.clipped(0, 8, "abcdef", 10) == (0, 8, "ab")
    assert panels.surely("x") == "x"
    assert panels.surely(0) == 0
    assert panels.surely(None) is None
    with pytest.raises(KeyError):
        panels.int_attr(None)  # type: ignore[arg-type]
    assert panels.int_attr(5) == 5
    assert panels.first_text("", "later") == ""
    assert panels.first_text(None, "later") == "later"
    assert panels.first_text() == ""
    assert panels.first_text(None) == ""
    assert panels.skip() is None
    assert panels.none_of("a") is None
    assert panels.present("a") is True
    assert panels.present(None) is False
    assert panels.is_str("a") is True
    assert panels.is_str(None) is False
    assert panels.keep_pos(1, 2, "t") == (1, 2, "t")


def test_draw_box_and_helpers() -> None:
    win: Any = FakeWin()
    draw_box(win, Rect(0, 0, 1, 1), ROUND, 0)
    assert win.cells == []
    two: Any = FakeWin(5, 5)
    draw_box(two, Rect(0, 0, 2, 2), ROUND, 0)
    assert ROUND.tl in "".join(cell[2] for cell in two.cells)
    draw_box(win, Rect(0, 0, 3, 5), ROUND, 1)
    box: Any = FakeWin(10, 20)
    paint_box(box, Rect(1, 2, 4, 6), ROUND, 7)
    top = ROUND.tl + ROUND.top * 4 + ROUND.tr
    bottom = ROUND.bl + ROUND.top * 4 + ROUND.br
    assert box.cells[0] == (1, 2, top, 7)
    assert (4, 2, bottom, 7) in box.cells
    assert (2, 2, ROUND.side, 7) in box.cells
    assert (2, 7, ROUND.side, 7) in box.cells
    assert (3, 2, ROUND.side, 7) in box.cells
    assert (3, 7, ROUND.side, 7) in box.cells
    assert clean(" a  b ") == "a b"
    assert marquee("ab", 0, 0) == ""
    assert marquee("ab", 5, 0) == "ab"
    assert marquee("abcdef", 3, 0) == "abc"
    assert marquee("abcdef", 3, 20) != ""
    assert wrap_line("", 4) == [""]
    assert build_lines([], 8) == [("no conversation files found", False)]
    lines = build_lines([("prompt", "hello\nworld")], 10)
    assert lines[0][1] is True
    assert clamp(5, 0, 3) == 3
    assert bed_span([2, 2], 0, 1) == 5


def test_draw_bed_writes_title_and_task(tmp_path: Path) -> None:
    win: Any = FakeWin(20, 40)
    live = make_repo(tmp_path, alive=True)
    live.name = "bed-" + "n" * 40
    live.branch = "main"
    live.head = "abc"
    live.task = "task-one"
    watch = state_of()
    rect = Rect(2, 1, 8, 30)
    draw_bed(win, rect, live, True, watch)
    inner = 26
    title_text = f" {live.name} {GLYPH_FLOURISH} {live.branch} @{live.head} "[:inner]
    title = next(cell for cell in win.cells if cell[2] == title_text)
    task = next(cell for cell in win.cells if "task: task-one" in cell[2])
    assert title == (2, 3, title_text, watch.theme.heading)
    assert task[:3] == (3, 3, "task: task-one")
    assert task[3] == watch.theme.secondary
    assert HEAVY.tl in "".join(cell[2] for cell in win.cells)


def test_worker_rows_and_selection(tmp_path: Path) -> None:
    idle = make_repo(tmp_path / "a", alive=False)
    live = make_repo(tmp_path / "b", alive=True)
    busy = make_repo(tmp_path / "c", worker=Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None))
    fleet = Fleet(repos=[idle, live, busy], scanned_at=0)
    assert worker_rows(None) == []
    assert worker_rows(fleet) == [live, busy]
    watch = state_of(fleet)
    watch.selected = 9
    assert selected_repo(watch) is busy
    assert selected_repo(state_of(Fleet(repos=[], scanned_at=0))) is None
    assert bed_index(fleet, busy) == 2
    assert bed_index(fleet, None) == 0
    assert gate_rows(live) == 0
    live.gate_activity = "pytest"
    assert gate_rows(live) == 1
    assert bed_height(live) >= 5
    assert fit_top([10, 10, 10], 0, 2, 5) == 2
    assert tail_lines_of(busy) == []
    busy.tail_lines = ["a", "b", "c", "d"]
    assert len(tail_lines_of(busy)) == 3
    worker = busy.worker
    assert worker is not None
    worker.tail_lines = ["from-worker"]
    busy.tail_lines = ["from-repo"]
    assert tail_lines_of(busy) == ["from-worker"]
    worker.tail_lines = []
    assert tail_lines_of(busy) == ["from-repo"]
    process = Process(1, 3, "m", "claude")
    busy.worker = Worker(step=step(), process=process, result_path=None, prompt_path=None, handoff_path=None)
    assert fmt_elapsed(busy.worker).endswith("s")
    busy.worker.process = None
    assert fmt_elapsed(busy.worker) == "1m"
    busy.worker.step.minutes = None
    assert fmt_elapsed(busy.worker) == "--"


def test_draw_rows(tmp_path: Path) -> None:
    win: Any = FakeWin()
    watch = state_of()
    idle = make_repo(tmp_path, alive=False)
    draw_idle_row(win, 0, 0, 20, idle, False, watch)
    live = make_repo(tmp_path, alive=True, gate_activity="pytest")
    draw_idle_row(win, 1, 0, 20, live, True, watch)
    draw_idle_row(win, 2, 0, 20, make_repo(tmp_path, alive=True, runner_activity="run"), False, watch)
    draw_idle_row(win, 3, 0, 20, make_repo(tmp_path, alive=True), False, watch)
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None, tail_lines=["x"])
    draw_worker_row(win, 4, 0, 40, make_repo(tmp_path, worker=worker), True, watch)
    worker.tail_lines = []
    draw_worker_row(win, 5, 0, 40, make_repo(tmp_path, worker=worker), False, watch)
    draw_strip(win, 6, 0, 4, [step(), step("done", "PASS")], watch)
    draw_strip(win, 6, 0, 1, [step(), step(), step()], watch)
    draw_bed(win, Rect(0, 0, 10, 40), make_repo(tmp_path, alive=True, gate_activity="g", tail_lines=["t"]), True, watch)
    assert alive_label(make_repo(tmp_path, gate_activity="g")).startswith("in gate")
    assert alive_label(make_repo(tmp_path, runner_activity="r")).startswith("runner")
    assert alive_label(make_repo(tmp_path)) == "between steps"


def test_fleet_panel(tmp_path: Path) -> None:
    panel = FleetPanel()
    win: Any = FakeWin()
    empty = state_of()
    panel.render(win, Rect(0, 0, 10, 40), True, empty)
    fleet = Fleet(repos=[make_repo(tmp_path / "a"), make_repo(tmp_path / "b")], scanned_at=0)
    watch = state_of(fleet)
    panel.render(win, Rect(0, 0, 3, 40), True, watch)
    panel.render_beds(win, Rect(0, 0, 20, 40), state_of())
    assert panel.on_key(curses.KEY_UP, watch) == "handled"
    assert panel.on_key(ord("j"), watch) == "handled"
    assert panel.on_key(10, watch) == "open"
    assert fleet_action(10, 0) == "handled"
    assert panel.on_key(ord("q"), watch) == "quit"
    assert panel.on_key(ord("x"), watch) is None


def test_conversation_panel(tmp_path: Path, monkeypatch: Any) -> None:
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None)
    current = make_repo(tmp_path, worker=worker)
    panel = ConversationPanel(current)
    assert "01-coder" in panel.heading()
    panel.repo.worker = None
    assert "live" in panel.heading()
    win: Any = FakeWin()
    watch = state_of(Fleet(repos=[current], scanned_at=0))
    panel.render(win, Rect(0, 0, 8, 40), True, watch)
    panel.render(win, Rect(0, 0, 8, 40), True, watch)
    panel.follow = False
    panel.place_scroll()
    assert panel.on_key(ord("q"), watch) == "back"
    assert panel.on_key(curses.KEY_END, watch) == "handled"
    assert panel.on_key(curses.KEY_UP, watch) == "handled"
    assert panel.on_key(ord("j"), watch) == "handled"
    assert panel.on_key(curses.KEY_PPAGE, watch) == "handled"
    assert panel.on_key(curses.KEY_NPAGE, watch) == "handled"
    assert panel.on_key(curses.KEY_HOME, watch) == "handled"
    assert panel.on_key(ord("x"), watch) is None
    panel.sync(None)
    panel.sync(Fleet(repos=[], scanned_at=0))
    other = make_repo(tmp_path)
    other.root = tmp_path
    panel.sync(Fleet(repos=[other], scanned_at=0))
    assert matching_repo(None, tmp_path) is None
    paint_lines(win, Rect(0, 0, 5, 20), [("h", True), ("b", False)], watch)
    base = Panel()
    assert base.on_key(1, watch) is None
    try:
        base.render(win, Rect(0, 0, 1, 1), True, watch)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("render")


def test_panel_helpers(tmp_path: Path) -> None:
    assert panels.skip() is None
    assert panels.none_of() is None
    assert panels.surely("x") == "x"
    assert panels.surely(None) is None
    assert panels.missing_fleet(0) is None
    assert panels.present(0) is True
    assert panels.present(None) is False
    assert panels.task_label(None) == "none"
    assert panels.task_label("t") == "t"
    assert panels.selected_border(True) is HEAVY
    assert panels.selected_border(False) is ROUND
    theme = mono_theme()
    assert panels.selected_border_attr(theme, True) == theme.border_focus
    assert panels.selected_border_attr(theme, False) == theme.border
    assert panels.pick_selected([], 0) is None
    live = make_repo(tmp_path)
    assert panels.pick_row([live], 9) is live
    assert panels.enter_action(0) == "handled"
    assert panels.enter_action(1) == "open"
    assert panels.quit_action(1) == "quit"
    assert panels.same_root(tmp_path, live) is True
    assert panels.add_gap(3) == 4
    assert panels.in_strip(1, (1, step())) is False
    assert panels.in_strip(4, (0, step())) is True
    assert panels.plain_line("x") == ("x", False)
    assert panels.wrap_flagged(10, "hi")[0] == ("hi", False)
    assert panels.zero_index(None, None) == 0
    assert panels.index_or_zero(None, live) == 0
    assert panels.blank_marquee("abc", 2, 0) == ""
    assert panels.scrolled_text("abcdef", 3, 0) == "abc"
    assert panels.scrolled_text("ab", 5, 0) == "ab"
    assert panels.scrolled_text("abcdef", 3, 16) == "abc"
    assert panels.scrolled_text("abcdef", 3, 13) == "bcd"
    assert panels.pick_marquee(0) is panels.blank_marquee
    assert panels.worker_tails(None) == []
    assert panels.blank_tail(Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None), 1, state_of()) == ""
    gate_win: Any = FakeWin()
    assert panels.skip_gate_row(gate_win, 4, 0, 10, live, state_of()) == 4
    assert panels.is_target(live, (0, live)) is True
    win: Any = FakeWin()
    panels.paint_dead(win, 0, 0, 10, live, False, state_of())
    panels.paint_alive(win, 1, 0, 20, make_repo(tmp_path, alive=True), False, state_of())
    panels.paint_idle_selected(win, 2, 0, 20, make_repo(tmp_path, alive=True), state_of())
    panels.paint_idle_plain(win, 3, 0, 20, make_repo(tmp_path, alive=True), state_of())
    assert panels.gate_label_text(None) is None
    assert panels.gate_label_text("g") == "in gate: g"
    assert panels.runner_label_text("") is None
    assert panels.runner_label_text("r") == "runner: r"
    assert panels.in_gate_text("g").startswith("in gate")
    assert panels.runner_text("r").startswith("runner")
    assert panels.seconds_of(Process(1, 3, "m", "claude")).endswith("s")
    assert panels.fmt_from_process(None) is None
    assert panels.minutes_label(None) is None
    assert panels.format_minutes(2.0) == "2m"
    tiny = Rect(0, 0, 1, 1)
    panels.tiny_box(win, tiny, ROUND, 0)
    panels.paint_sides(win, 0, 4, "|", 0, 1)
    assert panels.bed_fits(Rect(0, 0, 5, 10), (0, 10)) is False
    assert panels.bed_fits(Rect(0, 0, 5, 10), (0, 1)) is True
    assert panels.empty_repos(None) is True
    assert panels.empty_repos(Fleet(repos=[], scanned_at=0)) is True
    panel = ConversationPanel(live)
    assert panels.live_heading(live).endswith("live")
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None)
    busy = make_repo(tmp_path, worker=worker)
    assert "01-coder" in panels.worker_heading(busy)
    panels.follow_bottom(panel, 4)
    assert panel.scroll == 4
    assert panels.back_key(panel, ord("q")) == "back"
    assert panels.end_key(panel, 0) == "handled"
    assert panel.follow is True
    assert panels.apply_scroll(panel, None) is None
    assert panels.scrolled(panel, ord("z")) is None
    assert panels.section_lines_for(8, "p", "hi")[0][1] is True
    panels.apply_clip(win, None, 0)
    panels.write_clipped(win, (0, 0, "x"), 0)
    panels.apply_repo(panel, None)
    panel.rebuild_lines(12)
    assert panel.built_for == 12
    assert panels.keep_pos(1, 2, "ab") == (1, 2, "ab")
    assert panels.shift_neg(1, -1, "ab") == (1, 0, "b")
    panels.write_cell(win, 0, 0, "ok", 0)
    panels.paint_box(win, Rect(0, 0, 3, 5), ROUND, 0)
    assert panels.is_worker_row(live) is True
    assert panels.placed([5, 5], 0, 10, 0) is True
    assert list(panels.fit_range(2, 4)) == [2, 3, 4]
    busy_watch = state_of()
    panels.paint_bed_frame(win, Rect(0, 0, 6, 20), live, False, busy_watch, 16)
    panels.put_tail(win, 0, 0, 10, busy_watch, 0, "tail")
    panels.paint_tails(win, 1, 0, 20, live, busy_watch)
    assert panels.paint_gate_row(win, 2, 0, 10, make_repo(tmp_path, gate_activity="g"), busy_watch) == 3
    assert panels.draw_gate_row(win, 3, 0, 10, live, busy_watch) == 3
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None)
    busy = make_repo(tmp_path, worker=worker)
    panels.draw_busy_from_repo(win, 4, 0, 40, busy, False, busy_watch)
    assert panels.marquee_summary(worker, 8, busy_watch)
    panels.paint_busy_plain(win, 5, 0, 40, worker, busy_watch, "h ", "t")
    panels.paint_busy_selected(win, 6, 0, 40, worker, busy_watch, "h ", "t")
    panels.draw_busy_row(win, 7, 0, 40, worker, False, busy_watch)
    panels.put_step(win, 8, 0, busy_watch, (0, step()))
    assert panels.wrapped_body("hello", 8)
    assert panels.flatten_sections([("n", "b")], 8)
    fleet_panel = FleetPanel()
    panels.empty_fleet(fleet_panel, win, Rect(0, 0, 5, 20), busy_watch)
    assert panels.visible_beds(Rect(0, 0, 20, 20), [5, 5], 0)
    panels.paint_one_bed(win, Rect(0, 0, 20, 40), [live], [5], live, busy_watch, 0, 0)
    panels.paint_beds(win, Rect(0, 0, 20, 40), [live], [5], 0, live, busy_watch)
    watch = state_of(Fleet(repos=[live], scanned_at=0))
    assert panels.move_up(watch, 1, curses.KEY_UP) == "handled"
    assert panels.move_down(watch, 2, curses.KEY_DOWN) == "handled"
    assert panels.no_move(watch, 0, ord("x")) is None
    assert panels.run_scroll(panel, panels.scroll_up) == "handled"
    panels.scroll_down(panel)
    panels.scroll_page_up(panel)
    panels.scroll_page_down(panel)
    panels.scroll_home(panel)
    panel.take_repo(live)
    panel.ensure_lines(10)
    assert panel.built_for == 10


class Tracker:
    def __init__(self, fn: Any = lambda *args, **kwargs: None) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.fn = fn

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self.fn(*args, **kwargs)


def tracker(fn: Any = lambda *args, **kwargs: None) -> Tracker:
    return Tracker(fn)


def test_clipped_zero_width() -> None:
    assert panels.clipped(0, 5, "abc", 5) is None
    assert panels.clipped(0, 5, "abc", 6) == (0, 5, "a")
    assert panels.shift_left(0, 0, "ab") == (0, 0, "ab")


def test_paint_box_side_range() -> None:
    box: Any = FakeWin(10, 20)
    paint_box(box, Rect(1, 2, 4, 6), ROUND, 7)
    side_ys = sorted(cell[0] for cell in box.cells if cell[2] == ROUND.side)
    assert side_ys == [2, 2, 3, 3]


def test_draw_box_passes_attr() -> None:
    win: Any = FakeWin(10, 20)
    draw_box(win, Rect(0, 0, 3, 5), ROUND, 9)
    assert all(cell[3] == 9 for cell in win.cells)


def test_scrolled_text_boundaries() -> None:
    assert panels.scrolled_text("ab", 5, 0) == "ab"
    assert panels.scrolled_text("abcdef", 3, 0) == "abc"
    assert panels.scrolled_text("", 0, 0) == ""
    assert panels.scrolled_text("abcdef", 3, 14) == "cde"
    assert panels.scrolled_text("abc", 3, 0) == "abc"
    assert panels.pick_marquee(1) is panels.scrolled_text
    assert panels.pick_marquee(0) is panels.blank_marquee


def test_fmt_elapsed_zero_minutes() -> None:
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None)
    worker.step.minutes = 0
    assert fmt_elapsed(worker) == "0m"


def test_bed_height_and_span() -> None:
    live = make_repo(Path("/x"), alive=True, tail_lines=["a", "b"])
    assert bed_height(live) == panels.BED_H + 0 + 2
    live.gate_activity = "g"
    assert bed_height(live) == panels.BED_H + 1 + 2
    assert bed_span([3, 4, 5], 0, 1) == 3 + 4 + panels.BED_GAP
    assert bed_span([3, 4, 5], 1, 2) == 4 + 5 + panels.BED_GAP
    assert panels.placed([5, 5], 1, 11, 0) is True
    assert panels.placed([5, 5], 1, 10, 0) is False
    assert fit_top([8, 8, 8], 1, 2, 20) == 1


def test_draw_bed_positions(tmp_path: Path) -> None:
    win: Any = FakeWin(20, 40)
    live = make_repo(tmp_path, alive=True, gate_activity="g", tail_lines=["tail-line"])
    watch = state_of()
    rect = Rect(2, 4, 10, 30)
    draw_bed(win, rect, live, False, watch)
    worker_cells = [cell for cell in win.cells if cell[0] == 4 and cell[1] == 6]
    assert worker_cells
    gate_cells = [cell for cell in win.cells if cell[2].startswith("⚒ gate:")]
    assert gate_cells
    assert gate_cells[0][1] == 6
    assert gate_cells[0][0] == rect.y + panels.BED_GATE_ROW
    tail_cells = [cell for cell in win.cells if cell[2].startswith("tail-line")]
    assert tail_cells
    assert tail_cells[0][1] == 6


def test_paint_bed_frame_attr(tmp_path: Path) -> None:
    win: Any = FakeWin(10, 30)
    live = make_repo(tmp_path)
    watch = state_of()
    panels.paint_bed_frame(win, Rect(0, 0, 6, 20), live, True, watch, 16)
    assert any(cell[3] == watch.theme.border_focus for cell in win.cells)


def test_put_tail_and_paint_tails(tmp_path: Path, monkeypatch: Any) -> None:
    win: Any = FakeWin(10, 40)
    watch = state_of()
    panels.put_tail(win, 3, 2, 10, watch, 1, "hello-tail")
    assert (4, 2, "hello-tail", watch.theme.secondary) in win.cells
    short: Any = FakeWin(10, 40)
    panels.put_tail(short, 0, 0, 4, watch, 0, "hello-tail")
    assert any(cell[2] == "hell" for cell in short.cells)
    with pytest.raises(KeyError):
        panels.need_int(None)  # type: ignore[arg-type]
    strip = tracker()
    monkeypatch.setattr(panels, "draw_strip", strip)
    live = make_repo(tmp_path, tail_lines=["t"])
    panels.paint_tails(win, 5, 3, 12, live, watch)
    assert strip.calls[0][0] == (win, 6, 3, 12, live.steps, watch)


def test_render_beds_scrolls_to_the_selected_bed(tmp_path: Path) -> None:
    repos = [make_repo(tmp_path / name) for name in ("a", "b", "c")]
    fleet = Fleet(repos=repos, scanned_at=0)
    watch = WatchState(fleet=fleet, theme=mono_theme(), tick=20, selected=2)
    panel = FleetPanel()
    win: Any = FakeWin(30, 40)
    panel.render_beds(win, Rect(0, 0, 8, 40), watch)
    assert list(map(bed_height, repos)) == [5, 5, 5]
    assert panel.top == 2
    assert panels.index_or_zero(fleet, repos[2]) == 2


def test_conversation_render_shows_the_last_page_of_lines(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "a")
    panel = ConversationPanel(repo)
    panel.sections = [("one", "a\nb\nc\nd\ne")]
    panel.built_for = -1
    watch = state_of(Fleet(repos=[repo], scanned_at=0))
    win: Any = FakeWin(20, 40)
    panel.render(win, Rect(0, 0, 4, 40), True, watch)
    assert (panel.page, panel.scroll) == (3, 4)
    assert [(cell[0], cell[2]) for cell in win.cells] == [(0, panel.heading()), (1, "d"), (2, "e")]


def test_paint_and_draw_gate_row(tmp_path: Path) -> None:
    win: Any = FakeWin(10, 40)
    watch = state_of()
    live = make_repo(tmp_path, gate_activity="pytest")
    assert panels.paint_gate_row(win, 2, 4, 20, live, watch) == 3
    assert (2, 4, "⚒ gate: pytest", watch.theme.secondary) in win.cells
    idle = make_repo(tmp_path)
    assert panels.draw_gate_row(win, 7, 1, 8, idle, watch) == 7
    busy = make_repo(tmp_path, gate_activity="g")
    assert panels.draw_gate_row(win, 8, 1, 8, busy, watch) == 9
    assert (8, 1, "⚒ gate: ", watch.theme.secondary) in win.cells


def test_idle_and_busy_attrs(tmp_path: Path) -> None:
    win: Any = FakeWin(10, 40)
    watch = state_of()
    live = make_repo(tmp_path, alive=True)
    panels.paint_dead(win, 0, 1, 10, live, False, watch)
    assert (0, 1, f"{GLYPH_IDLE} idle", watch.theme.idle) in win.cells
    panels.paint_idle_selected(win, 1, 0, 20, live, watch)
    selected = [cell for cell in win.cells if cell[0] == 1]
    idle_text = f"{GLYPH_RUNNING} {alive_label(live)}"
    assert selected[0][3] == watch.theme.selected
    assert selected[0][2] == idle_text.ljust(20)[:20]
    assert selected[0][2] != idle_text.rjust(20)[:20]
    panels.paint_idle_plain(win, 2, 0, 10, live, watch)
    plain = [cell for cell in win.cells if cell[0] == 2]
    assert plain[0][3] == watch.theme.worker
    panels.paint_alive(win, 3, 0, 10, live, True, watch)
    alive_sel = [cell for cell in win.cells if cell[0] == 3]
    assert alive_sel[0][3] == watch.theme.selected
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None)
    panels.paint_busy_selected(win, 4, 0, 8, worker, watch, "ab", "cd")
    busy_sel = [cell for cell in win.cells if cell[0] == 4]
    assert busy_sel[0][2] == "abcd".ljust(8)[:8]
    assert busy_sel[0][2] != "abcd".rjust(8)[:8]
    assert busy_sel[0][3] == watch.theme.selected
    panels.paint_busy_plain(win, 5, 2, 20, worker, watch, "HEAD ", "TAIL")
    heads = [cell for cell in win.cells if cell[0] == 5 and cell[2] == "HEAD "]
    tails = [cell for cell in win.cells if cell[0] == 5 and cell[2] == "TAIL"]
    assert heads[0][1] == 2
    assert tails[0][1] == 7
    assert tails[0][3] == watch.theme.secondary


def test_draw_busy_row_tail_choice(tmp_path: Path) -> None:
    win: Any = FakeWin(10, 40)
    watch = state_of()
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None, tail_lines=["x"])
    panels.draw_busy_row(win, 0, 0, 60, worker, False, watch)
    assert all(cell[2].find("hello") < 0 for cell in win.cells)
    worker.tail_lines = []
    panels.draw_busy_row(win, 1, 0, 60, worker, False, watch)
    assert any("hello" in cell[2] for cell in win.cells)


def test_draw_busy_row_fits_the_tail_beside_the_head(tmp_path: Path) -> None:
    win: Any = FakeWin(10, 80)
    watch = state_of()
    worker = Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None)
    panels.draw_busy_row(win, 0, 0, 30, worker, False, watch)
    assert [(cell[1], cell[2]) for cell in win.cells] == [(0, f"{GLYPH_RUNNING} coder 01-coder 1m "), (20, "hello worl")]


def test_put_step_reads_the_status_and_the_verdict(tmp_path: Path) -> None:
    win: Any = FakeWin(10, 40)
    watch = state_of()
    panels.put_step(win, 2, 5, watch, (2, step("running", "PASS")))
    panels.put_step(win, 3, 1, watch, (1, step("done", "BOUNCE")))
    assert [(cell[1], cell[2], cell[3]) for cell in win.cells] == [
        (9, GLYPH_RUNNING, watch.theme.worker),
        (3, GLYPH_BOUNCED, watch.theme.bounced),
    ]


def test_in_strip_and_put_step(tmp_path: Path) -> None:
    assert panels.in_strip(3, (1, step())) is True
    assert panels.in_strip(2, (1, step())) is False
    win: Any = FakeWin(10, 40)
    watch = state_of()
    running = step("running")
    panels.put_step(win, 2, 5, watch, (2, running))
    glyph = [cell for cell in win.cells if cell[0] == 2]
    assert glyph[0][1] == 9
    assert glyph[0][2] == GLYPH_RUNNING
    assert glyph[0][3] == watch.theme.worker
    bounced = step("done", "BOUNCE")
    panels.put_step(win, 3, 1, watch, (1, bounced))
    bounced_cell = [cell for cell in win.cells if cell[0] == 3]
    assert bounced_cell[0][1] == 3
    assert bounced_cell[0][2] != glyph[0][2]


def test_draw_strip_limits_and_slice(tmp_path: Path) -> None:
    win: Any = FakeWin(10, 10)
    watch = state_of()
    steps = [step() for _ in range(20)]
    draw_strip(win, 0, 0, 4, steps, watch)
    assert len([cell for cell in win.cells if cell[0] == 0]) == 2
    draw_strip(win, 1, 0, 100, steps, watch)
    assert len([cell for cell in win.cells if cell[1] == 0 and cell[0] == 1]) == 1


def test_wrap_line_flags() -> None:
    assert wrap_line("a  b", 10) == ["a  b"]
    assert wrap_line("  x", 10) == ["  x"]
    assert wrap_line("x  ", 10) == ["x  "]
    assert wrap_line("ab", 1) == ["a", "b"]
    assert wrap_line("abcd", 2) == ["ab", "cd"]
    assert panels.as_false(False) is False


def test_as_false_rejects_none() -> None:
    with pytest.raises(KeyError):
        panels.as_false(None)  # type: ignore[arg-type]


def test_first_text_skips_missing_and_keeps_a_string() -> None:
    assert panels.first_text(None, "--") == "--"
    assert panels.first_text("in gate: x", "runner: y") == "in gate: x"


def test_shift_left_keeps_column_zero() -> None:
    assert shift_left(1, 0, "ab") == (1, 0, "ab")
    assert shift_left(1, -1, "ab") == (1, 0, "b")
    assert shift_left(1, 2, "ab") == (1, 2, "ab")


def test_section_lines_blank_and_flag() -> None:
    lines = panels.section_lines("prompt", "hi", 10)
    assert lines[-1] == ("", False)
    assert lines[0] == (f"{GLYPH_SECTION} prompt", True)
    assert panels.section_lines_for(8, "prompt", "body")[0] == (f"{GLYPH_SECTION} prompt", True)


def test_panel_on_key_calls_skip(monkeypatch: Any) -> None:
    watch = state_of()
    skipped = tracker()
    monkeypatch.setattr(panels, "skip", skipped)
    assert Panel().on_key(7, watch) is None
    assert skipped.calls[0][0] == (7, watch)


def test_empty_repos_and_empty_fleet(tmp_path: Path) -> None:
    live = make_repo(tmp_path)
    assert panels.empty_repos(None) is True
    assert panels.empty_repos(Fleet(repos=[], scanned_at=0)) is True
    assert panels.empty_repos(Fleet(repos=[live], scanned_at=0)) is False
    win: Any = FakeWin(10, 80)
    watch = state_of()
    panels.empty_fleet(FleetPanel(), win, Rect(3, 5, 8, 60), watch)
    assert any(cell[0] == 3 and cell[1] == 7 and cell[2].startswith("no beds found") for cell in win.cells)


def test_index_or_zero_uses_current(tmp_path: Path) -> None:
    a = make_repo(tmp_path / "a")
    b = make_repo(tmp_path / "b")
    fleet = Fleet(repos=[a, b], scanned_at=0)
    assert panels.index_or_zero(fleet, b) == 1
    assert panels.index_or_zero(None, b) == 0


def test_bed_fits_and_visible_beds() -> None:
    rect = Rect(0, 0, 5, 10)
    assert panels.bed_fits(rect, (0, 4)) is True
    assert panels.bed_fits(rect, (0, 5)) is False
    visible = panels.visible_beds(Rect(0, 0, 12, 10), [5, 5, 5], 0)
    assert visible[0][0] == 0
    assert len(visible) == 2
    sliced = panels.visible_beds(Rect(0, 0, 12, 10), [5, 5], 1)
    assert sliced[0][0] == 1


def test_paint_one_bed_selected_flag(tmp_path: Path, monkeypatch: Any) -> None:
    drawn = tracker()
    monkeypatch.setattr(panels, "draw_bed", drawn)
    live = make_repo(tmp_path)
    other = make_repo(tmp_path / "o")
    win: Any = FakeWin()
    watch = state_of()
    panels.paint_one_bed(win, Rect(0, 0, 20, 40), [live, other], [5, 5], live, watch, 0, 2)
    assert live in drawn.calls[0][0]
    assert True in drawn.calls[0][0]
    panels.paint_one_bed(win, Rect(0, 0, 20, 40), [live, other], [5, 5], live, watch, 1, 2)
    assert other in drawn.calls[1][0]
    assert False in drawn.calls[1][0]
    beds = tracker()
    monkeypatch.setattr(panels, "paint_one_bed", beds)
    panels.paint_beds(win, Rect(0, 0, 20, 40), [live], [5], 0, live, watch)
    assert live in beds.calls[0][0]


def test_move_up_down_boundaries() -> None:
    watch = state_of()
    watch.selected = 0
    assert panels.move_up(watch, 3, 0) == "handled"
    assert watch.selected == 0
    watch.selected = 2
    assert panels.move_up(watch, 3, 0) == "handled"
    assert watch.selected == 1
    watch.selected = 0
    assert panels.move_down(watch, 0, 0) == "handled"
    assert watch.selected == 0
    watch.selected = 0
    assert panels.move_down(watch, 3, 0) == "handled"
    assert watch.selected == 1
    watch.selected = 2
    assert panels.move_down(watch, 3, 0) == "handled"
    assert watch.selected == 2


def test_fleet_panel_init_and_render(tmp_path: Path, monkeypatch: Any) -> None:
    panel = FleetPanel()
    assert panel.top == 0
    live = make_repo(tmp_path)
    fleet = Fleet(repos=[live], scanned_at=0)
    watch = state_of(fleet)
    empty = tracker()
    monkeypatch.setattr(panels, "empty_fleet", empty)
    panel.render(as_window(FakeWin()), Rect(0, 0, 10, 40), True, state_of())
    assert empty.calls
    beds = tracker()
    monkeypatch.setattr(FleetPanel, "render_beds", beds)
    panel.render(as_window(FakeWin()), Rect(0, 0, 10, 40), True, watch)
    assert beds.calls


def test_fleet_render_beds_passes_repos(tmp_path: Path, monkeypatch: Any) -> None:
    live = make_repo(tmp_path)
    watch = state_of(Fleet(repos=[live], scanned_at=0))
    painted = tracker()
    monkeypatch.setattr(panels, "paint_beds", painted)
    FleetPanel().render_beds(as_window(FakeWin()), Rect(0, 0, 20, 40), watch)
    assert painted.calls
    args = painted.calls[0][0]
    assert args[2] == [live]
    assert live in args


def test_matching_repo_filters_root(tmp_path: Path) -> None:
    a = make_repo(tmp_path / "a")
    b = make_repo(tmp_path / "b")
    fleet = Fleet(repos=[a, b], scanned_at=0)
    assert matching_repo(fleet, b.root) is b
    assert matching_repo(fleet, tmp_path / "missing") is None
    assert matching_repo(None, b.root) is None


def test_paint_line_coords_and_attr() -> None:
    win: Any = FakeWin(10, 40)
    watch = state_of()
    panels.paint_line(win, Rect(2, 3, 8, 20), watch, 1, ("hello", False))
    assert win.cells == [(4, 3, "hello", 0)]
    panels.paint_line(win, Rect(2, 3, 8, 20), watch, 0, ("HEAD", True))
    assert (3, 3, "HEAD", watch.theme.heading) in win.cells


def test_scroll_handlers_values() -> None:
    live = make_repo(Path("/x"))
    panel = ConversationPanel(live)
    panel.follow = True
    panel.scroll = 5
    panel.page = 3
    assert panels.run_scroll(panel, panels.scroll_home) == "handled"
    assert panel.scroll == 0
    panel.scroll = 5
    panel.follow = True
    panels.scroll_up(panel)
    assert panel.follow is False
    assert panel.scroll == 4
    panel.scroll = 5
    panels.scroll_down(panel)
    assert panel.scroll == 6
    panel.follow = True
    panel.scroll = 10
    panel.page = 3
    panels.scroll_page_up(panel)
    assert panel.follow is False
    assert panel.scroll == 7
    panel.scroll = 10
    panels.scroll_page_down(panel)
    assert panel.scroll == 13
    panel.follow = True
    panels.scroll_home(panel)
    assert panel.follow is False
    assert panel.scroll == 0


def test_conversation_init_and_sync(tmp_path: Path, monkeypatch: Any) -> None:
    live = make_repo(tmp_path)
    panel = ConversationPanel(live)
    assert panel.follow is True
    assert panel.scroll == 0
    assert panel.page == 1
    assert panel.built_for == -1
    assert panel.lines == []
    assert panel.repo is live
    other = make_repo(tmp_path / "o")
    other.root = tmp_path
    apply = tracker()
    monkeypatch.setattr(panels, "apply_repo", apply)
    panel.sync(Fleet(repos=[other], scanned_at=0))
    assert apply.calls[0][0][1] is other
    panel.take_repo(other)
    assert panel.repo is other
    assert panel.built_for == -1


def test_conversation_render_geometry(tmp_path: Path, monkeypatch: Any) -> None:
    live = make_repo(tmp_path)
    panel = ConversationPanel(live)
    panel.sections = [("prompt", "hello world")]
    win: Any = FakeWin(10, 20)
    watch = state_of()
    panel.render(win, Rect(1, 2, 5, 12), True, watch)
    assert panel.page == 4
    heading = [cell for cell in win.cells if cell[0] == 1]
    assert heading[0][1] == 2
    assert heading[0][3] == watch.theme.heading
    assert panel.built_for == 11
    panel.follow = False
    panel.scroll = 0
    panel.lines = [("a", False)]
    panel.page = 3
    panel.place_scroll()
    assert panel.scroll == 0
    panel.lines = []
    panel.page = 2
    panel.place_scroll()
    assert panel.scroll == 0
    panel.follow = True
    panel.lines = [("a", False)]
    panel.page = 3
    panel.place_scroll()
    assert panel.scroll == 0
    tiny = ConversationPanel(live)
    tiny.sections = [("prompt", "hello")]
    tiny.render(win, Rect(0, 0, 2, 12), True, watch)
    assert tiny.page == 1
    ensured = tracker(lambda width: None)
    monkeypatch.setattr(panel, "ensure_lines", ensured)
    panel.render(win, Rect(0, 0, 4, 10), True, watch)
    assert ensured.calls[0][0] == (9,)
