import contextlib
import html
from bisect import bisect
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import partial
from operator import attrgetter
from pathlib import Path
from typing import Any

from marestail.tui.model import Process, RepoState, Step
from marestail.tui.web_sources import Dandelion, Host, Record, blank, dict_of, first_text, is_dict, list_of, none_of

ASSETS = Path(__file__).parent
CSS = (ASSETS / "web_page.css").read_text()
HEAD_JS = (ASSETS / "web_head.js").read_text()
TAIL_JS = (ASSETS / "web_tail.js").read_text()
BAR_CELLS = 24
HOST_CELLS = 18
TAIL_CHARS = 220
RECENT_STEPS = 14
TAIL_LINES = 3
RUN_ROWS = 40
HOUR = 3600
DAY = 86400
AGO_LIMITS = (90, 5400)
TONE_LIMITS = (50, 80, 95)
TONES = ("calm", "warm", "hot", "crit")
BYTE_STEPS = (1024, 1024**2, 1024**3, 1024**4)
BYTE_UNITS = "BKMGT"
GLYPHS = {"BOUNCE": "↩", "PASS": "✓", "FAIL": "✗"}
FIRST_PROBE = "first probe running, give it a minute"
ROUTING_OFF = ' <span class="dim">routing off</span>'
RUN_COLUMNS = ("repo", "task", "state", "where it stands", "verdict", "last activity")
SWITCHES = {
    "watch": ("active only", "Hide repos that are not running"),
    "runs": ("active only", "Hide runs that are not running"),
    "dandelion": ("usage only", "Hide providers with no usage window"),
}
POP = """<div id="task-pop" class="pop" hidden>
<div class="pop-frame" role="dialog" aria-modal="true" aria-labelledby="pop-title">
<div class="pop-bar"><span id="pop-title">task</span><button type="button" class="switch" id="pop-close">close</button></div>
<div class="pop-body" id="pop-body"></div>
</div></div>"""


def esc(value: Any) -> str:
    return html.escape(("", str(value))[value is not None])


def seconds_ago(seconds: int) -> str:
    return f"{seconds}s ago"


def minutes_ago(seconds: int) -> str:
    return f"{seconds // 60}m ago"


def hours_ago(seconds: int) -> str:
    return f"{seconds // HOUR}h ago"


AGO = (seconds_ago, minutes_ago, hours_ago)


def since(ts: float, now: float) -> str:
    seconds = int(now - ts)
    return AGO[bisect(AGO_LIMITS, seconds)](seconds)


def ago(ts: float | None, now: float) -> str:
    chosen = (blank, since)[bool(ts)]
    return chosen(ts, now)


def seconds_until(iso: str, now: datetime) -> int | None:
    with contextlib.suppress(ValueError):
        return int((datetime.fromisoformat(iso.replace("Z", "+00:00")) - now).total_seconds())
    return None


def reset_due(_seconds: int) -> str:
    return "reset due"


def countdown(seconds: int) -> str:
    days, hours, minutes = seconds // DAY, seconds % DAY // HOUR, seconds % HOUR // 60
    return "↻ " + (f"{hours}h{minutes:02d}m", f"{days}d{hours}h")[days > 0]


UNTIL = (blank, reset_due, countdown)


def until(iso: Any, now: datetime) -> str:
    seconds = (none_of, seconds_until)[all((isinstance(iso, str), bool(iso)))](iso, now)
    number = (0, seconds)[seconds is not None]
    return UNTIL[(seconds is not None) + (number > 0)](number)


def tone(pct: float) -> str:
    return TONES[bisect(TONE_LIMITS, pct)]


def zero(_value: Any) -> float:
    return 0.0


def number(value: Any) -> float:
    chosen = (zero, float)[isinstance(value, int | float)]
    return chosen(value)


def clamp(value: Any) -> float:
    return max(0.0, min(100.0, number(value)))


def blocks(pct: float, width: int) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def bar(value: Any) -> str:
    pct = clamp(value)
    return f'<span class="bar {tone(pct)}">{blocks(pct, BAR_CELLS)}</span> <b>{pct:.0f}%</b>'


def meter(value: Any, text: str, width: int) -> str:
    pct = clamp(value)
    filled = f'<span class="bar {tone(pct)}" title="{pct:.0f}%">{blocks(pct, width)}</span>'
    return f'<span class="dim">[</span>{filled}<span class="dim">]</span> {esc(text)}'


def fmt_elapsed(seconds: int) -> str:
    return (f"{seconds // 60}m{seconds % 60:02d}s", f"{seconds // HOUR}h{seconds % HOUR // 60:02d}m")[seconds >= HOUR]


def step_glyph(step: Step) -> str:
    return (GLYPHS.get(str(step.verdict), "·"), "▶")[step.status == "running"]


def switch(which: str) -> str:
    label, title = SWITCHES[which]
    return f'<button type="button" class="switch switch-{which}" id="{which}-switch" aria-pressed="false" title="{title}">{label}</button>'


def heading(title: str, note: str, which: str) -> str:
    return f"<h2><span>{title} <small>{note}</small></span>{switch(which)}</h2>"


def human_bytes(count: int) -> str:
    value = float(max(0, count))
    power = sum(map(value.__ge__, BYTE_STEPS))
    amount = value / 1024**power
    return (f"{amount:.1f}{BYTE_UNITS[power]}", f"{amount:.0f}B")[power == 0]


def projection(full: Any, now: datetime) -> str:
    return f" → 100% {esc(until(full, now))[2:]}"


def window_row(window: Record, now: datetime) -> str:
    full = window.get("projectedFullAt")
    note = (blank, projection)[bool(full)](full, now)
    reset = esc(until(window.get("resetsAt"), now))
    return f'<div class="win"><span class="wl">{esc(window.get("label"))}</span> {bar(window.get("usedPct"))} <span class="dim">{reset}{note}</span></div>'


def provider_label(provider: Record) -> str:
    name = esc(next(filter(None, (provider.get("displayName"), provider.get("id"))), None))
    label = f'<span class="name">{name}</span> <span class="dim">{esc(provider.get("planLabel"))}</span>'
    return label + ("", ROUTING_OFF)[not provider.get("eligible", True)]


def off_row(provider: Record, label: str, _windows: list[Record], _now: datetime) -> str:
    return f'<div class="prov off">{label} <span class="dim">{esc(first_text(provider.get("reason"), provider.get("status", "")))}</span></div>'


def empty_row(_provider: Record, label: str, _windows: list[Record], _now: datetime) -> str:
    return f'<div class="prov">{label}<div class="win dim">no usage windows</div></div>'


def full_row(_provider: Record, label: str, windows: list[Record], now: datetime) -> str:
    return f'<div class="prov has-win">{label}{"".join(map(partial(window_row, now=now), windows))}</div>'


PROVIDER_ROWS = (off_row, empty_row, full_row)


def provider_row(provider: Record, now: datetime) -> str:
    windows = list(filter(is_dict, list_of(provider.get("windows"))))
    kind = (provider.get("status", "") == "ok") * (1 + bool(windows))
    return PROVIDER_ROWS[kind](provider, provider_label(provider), windows, now)


def probe_note(state: Dandelion, now: float) -> str:
    return f"{('', 'probing…')[state.running]} {ago(state.at, now)}".strip()


def waiting(state: Dandelion, small: str, _now: float) -> str:
    reason = (FIRST_PROBE, esc(state.error))[bool(state.error)]
    return f'<h2>dandelion <small>{small}</small></h2><p class="dim">{reason}</p>'


def probed(state: Dandelion, small: str, now: float) -> str:
    data = dict_of(state.data)
    providers = filter(is_dict, list_of(data.get("providers")))
    rows = "".join(map(partial(provider_row, now=datetime.fromtimestamp(now, UTC)), providers))
    route = f'<p class="route">route → <b>{esc(data.get("route"))}</b> &nbsp; route --high → <b>{esc(data.get("routeHigh"))}</b></p>'
    error = ("", f'<p class="dim">last error: {esc(state.error)}</p>')[bool(state.error)]
    return f'{heading("dandelion", small, "dandelion")}{route}<div class="dandelion">{rows}<p class="dim none">no usage windows</p></div>{error}'


def render_dandelion(state: Dandelion, now: float) -> str:
    chosen = (probed, waiting)[state.data is None]
    return chosen(state, probe_note(state, now), now)


def idle_line(repo: RepoState) -> str:
    activity = first_text(repo.gate_activity, repo.runner_activity, "between steps")
    return f'<div class="role">▶ <span class="dim">{esc(activity)}</span></div>'


def process_detail(process: Process) -> str:
    return f"{fmt_elapsed(process.elapsed_s)} {esc(process.model)} {esc(process.backend)}"


def busy_line(repo: RepoState) -> str:
    worker = repo.worker
    detail = (blank, process_detail)[worker.process is not None](worker.process)
    return f'<div class="role">▶ {esc(worker.step.role)} <span class="dim">{detail}</span></div>'


def worker_line(repo: RepoState) -> str:
    chosen = (idle_line, busy_line)[repo.worker is not None]
    return chosen(repo)


def step_chip(step: Step) -> str:
    return f'<span class="st {esc(step.status)} v{esc(step.verdict)}" title="{esc(step.summary)}">{step_glyph(step)} {esc(step.label)}</span>'


def tail_div(text: str) -> str:
    return f'<div class="tail">{esc(text)[:TAIL_CHARS]}</div>'


def tail_lines(repo: RepoState) -> list[str]:
    return next(filter(None, (getattr(repo.worker, "tail_lines", None), repo.tail_lines)), [])


def bed_html(repo: RepoState) -> str:
    task = ("", f' <span class="task">{esc(repo.task)}</span>')[bool(repo.task)]
    head = f'<span class="name">{esc(repo.name)}</span> <span class="dim">{esc(repo.branch)} · {esc(repo.head)}</span>{task}'
    line = (blank, worker_line)[repo.alive](repo)
    steps = " ".join(map(step_chip, repo.steps[-RECENT_STEPS:]))
    tail = "".join(map(tail_div, tail_lines(repo)[-TAIL_LINES:]))
    return f'<div class="bed {("", "live")[repo.alive]}">{head}{line}<div class="steps">{steps}</div>{tail}</div>'


def fleet_error(message: str) -> str:
    return f'<h2>marestail watch</h2><p class="dim">{esc(message)}</p>'


def fleet_beds(found: list[RepoState]) -> str:
    live = sum(map(attrgetter("alive"), found))
    beds_html = "".join(map(bed_html, found))
    return f'{heading("marestail watch", f"{live} running · {len(found)} beds", "watch")}<div class="watch">{beds_html}<p class="dim none">nothing running</p></div>'


def render_fleet(found: list[RepoState] | str) -> str:
    chosen = (fleet_beds, fleet_error)[isinstance(found, str)]
    return chosen(found)


def run_row(run: Record, now: float) -> str:
    repo, task = esc(run.get("repo")), esc(run.get("task"))
    button = f'<button type="button" class="task-link" data-repo="{repo}" data-task="{task}">{task}</button>'
    where = esc(first_text(run.get("current_step"), run.get("last_step"), run.get("detail")))
    cells = f"<td>{repo}</td><td>{button}</td><td>{esc(run.get('state'))}</td><td>{where}</td><td>{esc(run.get('verdict'))}</td>"
    return f'<tr class="{esc(run.get("state"))}">{cells}<td class="dim">{ago(run.get("last_activity"), now)}</td></tr>'


def kept_run(run: Record) -> bool:
    return run.get("state") != "leftover"


def run_order(run: Record) -> tuple[bool, float]:
    return run.get("state") == "complete", -number(run.get("last_activity"))


def column(name: str) -> str:
    return f"<th>{name}</th>"


def render_runs(table: list[Record], now: float) -> str:
    runs = sorted(filter(kept_run, table), key=run_order)
    rows = "".join(map(partial(run_row, now=now), runs[:RUN_ROWS]))
    table_html = f'<table><thead><tr>{"".join(map(column, RUN_COLUMNS))}</tr></thead><tbody>{rows}</tbody></table>'
    return f'{heading("marestail runs", f"{len(runs)} known", "runs")}<div class="runs">{table_html}<p class="dim none">nothing running</p></div>'


def host_row(label: str, pct: float, text: str) -> str:
    return f'<div class="row"><span class="hl">{esc(label)}</span> {meter(pct, text, HOST_CELLS)}</div>'


def share(used: int, total: int) -> float:
    return (0.0, 100 * used / max(total, 1))[total > 0]


def sampling(_cpu: Any) -> str:
    return '<div class="row"><span class="hl">cpu</span> <span class="dim">sampling…</span></div>'


def core_span(core: tuple[str, float]) -> str:
    name, pct = core
    return f'<span class="core"><span class="dim">[{esc(name)}]</span> <span class="{tone(pct)}">{pct:4.1f}%</span></span>'


def core_block(cores: list[tuple[str, float]]) -> str:
    return f'<div class="cores">{"".join(map(core_span, cores))}</div>'


def sampled(cpu: Record) -> str:
    cores = (blank, core_block)[len(cpu["cores"]) > 1](cpu["cores"])
    return host_row("cpu", cpu["all"], f"{cpu['all']:5.1f}%") + cores


def cpu_rows(cpu: Record | None) -> str:
    chosen = (sampled, sampling)[cpu is None]
    return chosen(cpu)


def used_of(used: int, total: int) -> str:
    return f"{human_bytes(used)}/{human_bytes(total)}"


def meter_cell(cell: str) -> str:
    return f'<div class="meter">{cell}</div>'


def host_meters(host: Host) -> str:
    swap = host_row("swp", share(host.swap_used, host.swap_total), used_of(host.swap_used, host.swap_total))
    meters = (
        cpu_rows(host.cpu),
        host_row("mem", share(host.mem_used, host.mem_total), used_of(host.mem_used, host.mem_total)),
        ("", swap)[host.swap_total > 0],
        host_row("disk", share(host.disk_used, host.disk_total), used_of(host.disk_used, host.disk_total)),
    )
    return f'<section class="host-block"><h2>host</h2><div class="host">{"".join(map(meter_cell, filter(None, meters)))}</div></section>'


def host_error(message: str) -> str:
    return f'<h2>host</h2><p class="dim">{esc(message)}</p>'


def render_host(host: Host | str) -> str:
    chosen = (host_meters, host_error)[isinstance(host, str)]
    return chosen(host)


def document(machine: str, stamp: str, sections: Iterable[str]) -> str:
    head = f'<!doctype html><html><head><meta charset="utf-8"><title>marestail dash</title><style>{CSS}</style><script>{HEAD_JS}</script></head>'
    title = f'<h1>marestail · {esc(machine)} <small class="dim">{esc(stamp)} · refreshes every 30s</small></h1>'
    return f"{head}<body>{title}{''.join(sections)}{POP}<script>{TAIL_JS}</script></body></html>"
