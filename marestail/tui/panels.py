import contextlib
import curses
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from itertools import accumulate, chain, starmap
from pathlib import Path
from typing import Any, TypeGuard, cast

from .collect import conversation_for, fmt_seconds
from .model import Fleet, Process, RepoState, Step, Worker
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
REPOS_ATTR = "repos"
EMPTY_BEDS = "no beds found — waiting for pipelines"
BETWEEN_STEPS = "between steps"
NO_CONVERSATION = "no conversation files found"
IDLE_TEXT = "idle"
NONE_TASK = "none"
GATE_PREFIX = "⚒ gate: "
IN_GATE = "in gate: "
RUNNER_PREFIX = "runner: "


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


def skip(*_args: object, **_kwargs: object) -> Any:
    return None


def none_of(*_args: object, **_kwargs: object) -> Any:
    return None


def present[T](value: T | None) -> TypeGuard[T]:
    return value is not None


def surely[T](value: T | None) -> T:
    return cast(T, value)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def put(win: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    apply_clip(win, clip_text(y, x, text, *win.getmaxyx()), attr)


def apply_clip(win: curses.window, clipped: tuple[int, int, str] | None, attr: int) -> None:
    chosen = (skip, write_clipped)[clipped is not None]
    chosen(win, surely(clipped), attr)


def write_clipped(win: curses.window, clipped: tuple[int, int, str], attr: int) -> None:
    write_cell(win, clipped[0], clipped[1], clipped[2], attr)


def clip_text(y: int, x: int, text: str, height: int, width: int) -> tuple[int, int, str] | None:
    chosen = (clipped, none_of)[offscreen(y, x, height, width)]
    return chosen(y, x, text, width)


def clipped(y: int, x: int, text: str, width: int) -> tuple[int, int, str] | None:
    y, x, text = shift_left(y, x, text)
    text = text[: max(0, width - x)]
    return {True: None}.get(not text, (y, x, text))


def offscreen(y: int, x: int, height: int, width: int) -> bool:
    return True in (y < 0, y >= height, x >= width)


def keep_pos(y: int, x: int, text: str) -> tuple[int, int, str]:
    return y, x, text


def shift_neg(y: int, x: int, text: str) -> tuple[int, int, str]:
    return y, 0, text[-x:]


def shift_left(y: int, x: int, text: str) -> tuple[int, int, str]:
    chosen = (shift_neg, keep_pos)[x >= 0]
    return chosen(y, x, text)


def write_cell(win: curses.window, y: int, x: int, text: str, attr: int) -> None:
    with contextlib.suppress(curses.error):
        win.addstr(y, x, text, attr)


def tiny_box(*_args: object) -> None:
    return None


def paint_sides(win: curses.window, left: int, right: int, side: str, attr: int, y: int) -> None:
    put(win, y, left, side, attr)
    put(win, y, right, side, attr)


def paint_box(win: curses.window, rect: Rect, border: Border, attr: int) -> None:
    right = rect.x + rect.w - 1
    bottom = rect.y + rect.h - 1
    edge = border.top * (rect.w - 2)
    put(win, rect.y, rect.x, border.tl + edge + border.tr, attr)
    put(win, bottom, rect.x, border.bl + edge + border.br, attr)
    list(map(partial(paint_sides, win, rect.x, right, border.side, attr), range(rect.y + 1, bottom)))


def draw_box(win: curses.window, rect: Rect, border: Border, attr: int) -> None:
    chosen = (tiny_box, paint_box)[False not in (rect.h >= 2, rect.w >= 2)]
    chosen(win, rect, border, attr)


def clean(text: str) -> str:
    return " ".join(text.split())


def blank_marquee(_text: str, _width: int, _tick: int) -> str:
    return ""


def just_text(text: str, _width: int, _tick: int) -> str:
    return text


def scrolled_text(text: str, width: int, tick: int) -> str:
    span = max(0, len(text) - width)
    cycle = MARQUEE_PAUSE_TICKS + span + 1
    moment = tick % cycle
    offset = min(max(0, moment - MARQUEE_PAUSE_TICKS), span)
    return text[offset : offset + max(0, width)]


def fit_or_scroll(text: str, width: int, tick: int) -> str:
    chosen = (scrolled_text, just_text)[len(text) <= width]
    return chosen(text, width, tick)


def pick_marquee(width: int) -> Callable[[str, int, int], str]:
    return {True: blank_marquee}.get(width <= 0, fit_or_scroll)


def marquee(text: str, width: int, tick: int) -> str:
    chosen = pick_marquee(width)
    return chosen(text, width, tick)


def seconds_of(process: Process) -> str:
    return fmt_seconds(process.elapsed_s)


def fmt_from_process(process: Process | None) -> str | None:
    chosen = (seconds_of, none_of)[process is None]
    return chosen(surely(process))


def format_minutes(minutes: float) -> str:
    return f"{minutes:.0f}m"


def minutes_label(minutes: float | None) -> str | None:
    chosen = (none_of, format_minutes)[minutes is not None]
    return chosen(surely(minutes))


def fmt_elapsed(worker: Worker) -> str:
    return surely(next(filter(present, (fmt_from_process(worker.process), minutes_label(worker.step.minutes), "--"))))


def is_worker_row(repo: RepoState) -> bool:
    return True in (repo.worker is not None, repo.alive)


def worker_rows(fleet: Fleet | None) -> list[RepoState]:
    return list(filter(is_worker_row, getattr(fleet, REPOS_ATTR, [])))


def selected_repo(state: WatchState) -> RepoState | None:
    return pick_selected(worker_rows(state.fleet), state.selected)


def pick_row(rows: list[RepoState], selected: int) -> RepoState:
    return rows[min(selected, max(0, len(rows) - 1))]


def pick_selected(rows: list[RepoState], selected: int) -> RepoState | None:
    chosen = (pick_row, none_of)[not rows]
    return chosen(rows, selected)


def is_target(target: RepoState | None, pair: tuple[int, RepoState]) -> bool:
    return pair[1] is target


def bed_index(fleet: Fleet, target: RepoState | None) -> int:
    return next(filter(partial(is_target, target), enumerate(fleet.repos)), (0, None))[0]


def bed_height(repo: RepoState) -> int:
    return BED_H + gate_rows(repo) + min(TAIL_ROWS, len(tail_lines_of(repo)))


def gate_rows(repo: RepoState) -> int:
    return (0, 1)[repo.gate_activity is not None]


def bed_span(heights: list[int], first: int, last: int) -> int:
    return sum(heights[first : last + 1]) + BED_GAP * (last - first)


def placed(heights: list[int], current: int, height: int, top: int) -> bool:
    return True in (top == current, bed_span(heights, top, current) <= height)


def fit_top(heights: list[int], top: int, current: int, height: int) -> int:
    start = min(top, current)
    return next(filter(partial(placed, heights, current, height), range(start, current + 1)))


def selected_border(selected: bool) -> Border:
    return (ROUND, HEAVY)[selected]


def selected_border_attr(theme: Theme, selected: bool) -> int:
    return (theme.border, theme.border_focus)[selected]


def task_label(task: str | None) -> str:
    return next(filter(None, (task, NONE_TASK)))


def draw_bed(win: curses.window, rect: Rect, repo: RepoState, selected: bool, state: WatchState) -> None:
    inner = rect.w - 4
    paint_bed_frame(win, rect, repo, selected, state, inner)
    draw_worker_row(win, rect.y + 2, rect.x + 2, inner, repo, selected, state)
    row = draw_gate_row(win, rect.y + 3, rect.x + 2, inner, repo, state)
    paint_tails(win, row, rect.x + 2, inner, repo, state)


def paint_bed_frame(win: curses.window, rect: Rect, repo: RepoState, selected: bool, state: WatchState, inner: int) -> None:
    draw_box(win, rect, selected_border(selected), selected_border_attr(state.theme, selected))
    put(win, rect.y, rect.x + 2, f" {repo.name} {GLYPH_FLOURISH} {repo.branch} @{repo.head} "[:inner], state.theme.heading)
    put(win, rect.y + 1, rect.x + 2, f"task: {task_label(repo.task)}"[:inner], state.theme.secondary)


def put_tail(win: curses.window, row: int, x: int, inner: int, state: WatchState, offset: int, line: str) -> None:
    put(win, row + offset, x, line[:inner], state.theme.secondary)


def paint_tails(win: curses.window, row: int, x: int, inner: int, repo: RepoState, state: WatchState) -> None:
    tails = tail_lines_of(repo)
    list(starmap(partial(put_tail, win, row, x, inner, state), enumerate(tails)))
    draw_strip(win, row + len(tails), x, inner, repo.steps, state)


def skip_gate_row(_win: curses.window, y: int, _x: int, _inner: int, _repo: RepoState, _state: WatchState) -> int:
    return y


def paint_gate_row(win: curses.window, y: int, x: int, inner: int, repo: RepoState, state: WatchState) -> int:
    put(win, y, x, f"{GATE_PREFIX}{repo.gate_activity}"[:inner], state.theme.secondary)
    return y + 1


def draw_gate_row(win: curses.window, y: int, x: int, inner: int, repo: RepoState, state: WatchState) -> int:
    chosen = (paint_gate_row, skip_gate_row)[repo.gate_activity is None]
    return chosen(win, y, x, inner, repo, state)


def worker_tails(worker: Worker | None) -> list[str]:
    return getattr(worker, "tail_lines", [])


def tail_lines_of(repo: RepoState) -> list[str]:
    return next(filter(None, (worker_tails(repo.worker), repo.tail_lines)), [])[:TAIL_ROWS]


def draw_worker_row(win: curses.window, y: int, x: int, width: int, repo: RepoState, selected: bool, state: WatchState) -> None:
    chosen = (draw_idle_row, draw_busy_from_repo)[repo.worker is not None]
    chosen(win, y, x, width, repo, selected, state)


def draw_busy_from_repo(win: curses.window, y: int, x: int, width: int, repo: RepoState, selected: bool, state: WatchState) -> None:
    draw_busy_row(win, y, x, width, surely(repo.worker), selected, state)


def paint_dead(win: curses.window, y: int, x: int, _width: int, _repo: RepoState, _selected: bool, state: WatchState) -> None:
    put(win, y, x, f"{GLYPH_IDLE} {IDLE_TEXT}", state.theme.idle)


def paint_idle_selected(win: curses.window, y: int, x: int, width: int, repo: RepoState, _selected: bool, state: WatchState) -> None:
    put(win, y, x, f"{GLYPH_RUNNING} {alive_label(repo)}".ljust(width)[:width], state.theme.selected)


def paint_idle_plain(win: curses.window, y: int, x: int, width: int, repo: RepoState, _selected: bool, state: WatchState) -> None:
    put(win, y, x, f"{GLYPH_RUNNING} {alive_label(repo)}"[:width], state.theme.worker)


def paint_alive(win: curses.window, y: int, x: int, width: int, repo: RepoState, selected: bool, state: WatchState) -> None:
    chosen = (paint_idle_plain, paint_idle_selected)[selected]
    chosen(win, y, x, width, repo, selected, state)


def draw_idle_row(win: curses.window, y: int, x: int, width: int, repo: RepoState, selected: bool, state: WatchState) -> None:
    chosen = (paint_dead, paint_alive)[repo.alive]
    chosen(win, y, x, width, repo, selected, state)


def in_gate_text(activity: str) -> str:
    return f"{IN_GATE}{activity}"


def runner_text(activity: str) -> str:
    return f"{RUNNER_PREFIX}{activity}"


def gate_label_text(activity: str | None) -> str | None:
    chosen = (none_of, in_gate_text)[activity is not None]
    return chosen(surely(activity))


def runner_label_text(activity: str | None) -> str | None:
    chosen = (none_of, runner_text)[bool(activity)]
    return chosen(surely(activity))


def alive_label(repo: RepoState) -> str:
    return surely(next(filter(present, (gate_label_text(repo.gate_activity), runner_label_text(repo.runner_activity), BETWEEN_STEPS))))


def marquee_summary(worker: Worker, width: int, state: WatchState) -> str:
    return marquee(clean(worker.step.summary), width, state.tick)


def blank_tail(_worker: Worker, _width: int, _state: WatchState) -> str:
    return ""


def paint_busy_selected(win: curses.window, y: int, x: int, width: int, _worker: Worker, _state: WatchState, head: str, tail: str) -> None:
    put(win, y, x, (head + tail).ljust(width)[:width], _state.theme.selected)


def paint_busy_plain(win: curses.window, y: int, x: int, _width: int, worker: Worker, state: WatchState, head: str, tail: str) -> None:
    put(win, y, x, head, role_attr(state.theme, worker.step.role))
    put(win, y, x + len(head), tail, state.theme.secondary)


def draw_busy_row(win: curses.window, y: int, x: int, width: int, worker: Worker, selected: bool, state: WatchState) -> None:
    head = f"{GLYPH_RUNNING} {worker.step.role} {worker.step.label} {fmt_elapsed(worker)} "
    chosen_tail = (marquee_summary, blank_tail)[bool(worker.tail_lines)]
    tail = chosen_tail(worker, width - len(head), state)
    chosen = (paint_busy_plain, paint_busy_selected)[selected]
    chosen(win, y, x, width, worker, state, head, tail)


def in_strip(width: int, item: tuple[int, Step]) -> bool:
    index, _step = item
    return index * 2 < width


def put_step(win: curses.window, y: int, x: int, state: WatchState, item: tuple[int, Step]) -> None:
    index, step = item
    put(win, y, x + index * 2, step_glyph(step.status, step.verdict), step_attr(state.theme, step.status, step.verdict))


def draw_strip(win: curses.window, y: int, x: int, width: int, steps: list[Step], state: WatchState) -> None:
    list(map(partial(put_step, win, y, x, state), filter(partial(in_strip, width), enumerate(steps[-STRIP_STEPS:]))))


def wrap_line(raw: str, width: int) -> list[str]:
    return next(filter(None, (textwrap.wrap(raw, max(1, width), replace_whitespace=False, drop_whitespace=False), [""])))


def plain_line(text: str) -> tuple[str, bool]:
    return text, False


def wrap_flagged(width: int, raw: str) -> list[tuple[str, bool]]:
    return list(map(plain_line, wrap_line(raw, width)))


def wrapped_body(body: str, width: int) -> list[tuple[str, bool]]:
    return list(chain.from_iterable(map(partial(wrap_flagged, width), body.splitlines())))


def section_lines(name: str, body: str, width: int) -> list[tuple[str, bool]]:
    return [(f"{GLYPH_SECTION} {name}", True), *wrapped_body(body, width), ("", False)]


def section_lines_for(width: int, name: str, body: str) -> list[tuple[str, bool]]:
    return section_lines(name, body, width)


def flatten_sections(sections: list[tuple[str, str]], width: int) -> list[tuple[str, bool]]:
    return list(chain.from_iterable(starmap(partial(section_lines_for, width), sections)))


def build_lines(sections: list[tuple[str, str]], width: int) -> list[tuple[str, bool]]:
    return next(filter(None, (flatten_sections(sections, width), [(NO_CONVERSATION, False)])))


class Panel:
    title: str = "panel"

    def render(self, win: curses.window, rect: Rect, focused: bool, state: WatchState) -> None:
        raise NotImplementedError

    def on_key(self, key: int, state: WatchState) -> str | None:
        skip(key, state)
        return None


def empty_repos(fleet: Fleet | None) -> bool:
    return True in (fleet is None, not getattr(fleet, REPOS_ATTR, []))


def empty_fleet(self: "FleetPanel", win: curses.window, rect: Rect, state: WatchState) -> None:
    put(win, rect.y, rect.x + 2, EMPTY_BEDS, state.theme.secondary)


def zero_index(_fleet: Fleet | None, _current: RepoState | None) -> int:
    return 0


def index_or_zero(fleet: Fleet | None, current: RepoState | None) -> int:
    chosen = {True: bed_index}.get(fleet is not None, zero_index)
    return chosen(surely(fleet), current)


def add_gap(height: int) -> int:
    return height + BED_GAP


def bed_fits(rect: Rect, item: tuple[int, int]) -> bool:
    _index, y = item
    return y < rect.y + rect.h


def visible_beds(rect: Rect, heights: list[int], top: int) -> list[tuple[int, int]]:
    ys = list(accumulate([rect.y, *map(add_gap, heights[top:])]))[:-1]
    return list(filter(partial(bed_fits, rect), enumerate(ys, start=top)))


def paint_one_bed(
    win: curses.window,
    rect: Rect,
    repos: list[RepoState],
    heights: list[int],
    current: RepoState | None,
    state: WatchState,
    index: int,
    y: int,
) -> None:
    draw_bed(win, Rect(y, rect.x, heights[index], rect.w), repos[index], repos[index] is current, state)


def paint_beds(
    win: curses.window, rect: Rect, repos: list[RepoState], heights: list[int], top: int, current: RepoState | None, state: WatchState
) -> None:
    list(starmap(partial(paint_one_bed, win, rect, repos, heights, current, state), visible_beds(rect, heights, top)))


def move_up(state: WatchState, _rows: int, _key: int) -> str:
    state.selected = max(0, state.selected - 1)
    return "handled"


def move_down(state: WatchState, rows: int, _key: int) -> str:
    state.selected = min(max(0, rows - 1), state.selected + 1)
    return "handled"


def no_move(_state: WatchState, rows: int, key: int) -> str | None:
    return fleet_action(key, rows)


FLEET_MOVE = {curses.KEY_UP: move_up, ord("k"): move_up, curses.KEY_DOWN: move_down, ord("j"): move_down}


class FleetPanel(Panel):
    title = "fleet"

    def __init__(self) -> None:
        self.top = 0

    def render(self, win: curses.window, rect: Rect, focused: bool, state: WatchState) -> None:
        chosen = (FleetPanel.render_beds, empty_fleet)[empty_repos(state.fleet)]
        chosen(self, win, rect, state)

    def render_beds(self, win: curses.window, rect: Rect, state: WatchState) -> None:
        repos = getattr(state.fleet, REPOS_ATTR, [])
        heights = list(map(bed_height, repos))
        current = selected_repo(state)
        self.top = fit_top(heights, self.top, index_or_zero(state.fleet, current), rect.h)
        paint_beds(win, rect, repos, heights, self.top, current, state)

    def on_key(self, key: int, state: WatchState) -> str | None:
        chosen = FLEET_MOVE.get(key, no_move)
        return chosen(state, len(worker_rows(state.fleet)), key)


def enter_action(rows: int) -> str:
    return ("handled", "open")[bool(rows)]


def quit_action(_rows: int) -> str:
    return "quit"


def missing_fleet(_rows: int) -> str | None:
    return None


def fleet_action(key: int, rows: int) -> str | None:
    chosen = FLEET_KEYS.get(key, missing_fleet)
    return chosen(rows)


FLEET_KEYS = {curses.KEY_ENTER: enter_action, 10: enter_action, 13: enter_action, ord("q"): quit_action}


def same_root(root: Path, repo: RepoState) -> bool:
    return repo.root == root


def matching_repo(fleet: Fleet | None, root: Path) -> RepoState | None:
    return next(filter(partial(same_root, root), getattr(fleet, REPOS_ATTR, [])), None)


def paint_line(win: curses.window, rect: Rect, state: WatchState, row: int, item: tuple[str, bool]) -> None:
    text, head = item
    put(win, rect.y + 1 + row, rect.x, text, (0, state.theme.heading)[head])


def paint_lines(win: curses.window, rect: Rect, rows: list[tuple[str, bool]], state: WatchState) -> None:
    list(starmap(partial(paint_line, win, rect, state), enumerate(rows)))


def apply_repo(panel: "ConversationPanel", repo: RepoState | None) -> None:
    chosen = (skip, ConversationPanel.take_repo)[repo is not None]
    chosen(panel, surely(repo))


def live_heading(repo: RepoState) -> str:
    return f"{GLYPH_SECTION} {repo.name} · live"


def worker_heading(repo: RepoState) -> str:
    worker = surely(repo.worker)
    return f"{GLYPH_SECTION} {worker.step.label} · {worker.step.role}"


def follow_bottom(panel: "ConversationPanel", bottom: int) -> None:
    panel.scroll = bottom


def back_key(_panel: "ConversationPanel", _key: int) -> str:
    return "back"


def end_key(panel: "ConversationPanel", _key: int) -> str:
    panel.follow = True
    return "handled"


def apply_scroll(panel: "ConversationPanel", handler: Callable[["ConversationPanel"], None] | None) -> str | None:
    chosen = (skip, run_scroll)[handler is not None]
    return chosen(panel, surely(handler))


def run_scroll(panel: "ConversationPanel", handler: Callable[["ConversationPanel"], None]) -> str:
    handler(panel)
    panel.scroll = max(0, panel.scroll)
    return "handled"


def scrolled(panel: "ConversationPanel", key: int) -> str | None:
    return apply_scroll(panel, SCROLL_KEYS.get(key))


def scroll_up(panel: "ConversationPanel") -> None:
    panel.follow = False
    panel.scroll -= 1


def scroll_down(panel: "ConversationPanel") -> None:
    panel.scroll += 1


def scroll_page_up(panel: "ConversationPanel") -> None:
    panel.follow = False
    panel.scroll -= panel.page


def scroll_page_down(panel: "ConversationPanel") -> None:
    panel.scroll += panel.page


def scroll_home(panel: "ConversationPanel") -> None:
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

CONV_KEYS = {ord("q"): back_key, 27: back_key, curses.KEY_END: end_key}


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
        apply_repo(self, matching_repo(fleet, self.repo.root))

    def take_repo(self, repo: RepoState) -> None:
        self.repo = repo
        self.sections = conversation_for(repo)
        self.built_for = -1

    def heading(self) -> str:
        chosen = (live_heading, worker_heading)[self.repo.worker is not None]
        return chosen(self.repo)

    def render(self, win: curses.window, rect: Rect, focused: bool, state: WatchState) -> None:
        self.ensure_lines(rect.w - 1)
        put(win, rect.y, rect.x, self.heading()[: rect.w], state.theme.heading)
        self.page = max(1, rect.h - 1)
        self.place_scroll()
        paint_lines(win, rect, self.lines[self.scroll : self.scroll + self.page], state)

    def ensure_lines(self, width: int) -> None:
        chosen = (ConversationPanel.rebuild_lines, skip)[width == self.built_for]
        chosen(self, width)

    def rebuild_lines(self, width: int) -> None:
        self.lines = build_lines(self.sections, width)
        self.built_for = width

    def place_scroll(self) -> None:
        bottom = max(0, len(self.lines) - self.page)
        chosen = (skip, follow_bottom)[self.follow]
        chosen(self, bottom)
        self.scroll = clamp(self.scroll, 0, bottom)

    def on_key(self, key: int, state: WatchState) -> str | None:
        chosen = CONV_KEYS.get(key, scrolled)
        return chosen(self, key)


PANELS: list[type[Panel]] = [FleetPanel]
