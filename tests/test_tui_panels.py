import curses
from pathlib import Path
from typing import Any

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
    paint_lines,
    put,
    selected_repo,
    shift_left,
    tail_lines_of,
    worker_rows,
    wrap_line,
)
from marestail.tui.theme import ROUND, mono_theme


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
    put(win, -1, 0, "x")
    put(win, 0, 20, "x")
    put(win, 0, -2, "hello")
    put(win, 1, 0, "")
    win.fail = True
    put(win, 2, 0, "nope")
    assert clip_text(0, 0, "", 4, 10) is None
    assert offscreen(-1, 0, 4, 10) is True
    assert shift_left(0, 2, "ab") == (0, 2, "ab")
    assert shift_left(0, -1, "ab") == (0, 0, "b")


def test_draw_box_and_helpers() -> None:
    win: Any = FakeWin()
    draw_box(win, Rect(0, 0, 1, 1), ROUND, 0)
    draw_box(win, Rect(0, 0, 3, 5), ROUND, 1)
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
    assert panels.present(0) is True
    assert panels.present(None) is False
    assert panels.task_label(None) == "none"
    assert panels.task_label("t") == "t"
    assert panels.selected_border(True) is panels.HEAVY
    assert panels.selected_border(False) is panels.ROUND
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
    assert panels.just_text("abc", 2, 0) == "abc"
    assert panels.scrolled_text("abcdef", 3, 0) == "abc"
    assert panels.fit_or_scroll("ab", 5, 0) == "ab"
    assert panels.pick_marquee(0) is panels.blank_marquee
    assert panels.worker_tails(None) == []
    assert panels.blank_tail(Worker(step=step(), process=None, result_path=None, prompt_path=None, handoff_path=None), 1, state_of()) == ""
    assert panels.skip_gate_row(FakeWin(), 4, 0, 10, live, state_of()) == 4
    assert panels.is_target(live, (0, live)) is True
    win: Any = FakeWin()
    panels.paint_dead(win, 0, 0, 10, live, False, state_of())
    panels.paint_alive(win, 1, 0, 20, make_repo(tmp_path, alive=True), False, state_of())
    panels.paint_idle_selected(win, 2, 0, 20, make_repo(tmp_path, alive=True), True, state_of())
    panels.paint_idle_plain(win, 3, 0, 20, make_repo(tmp_path, alive=True), False, state_of())
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
