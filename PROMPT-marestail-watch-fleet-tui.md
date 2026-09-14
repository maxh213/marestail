# Marestail Watch — fleet-monitoring TUI

## Overview

Build `marestail watch`, a live terminal UI that shows every marestail pipeline
running across repos on this machine. One glance tells the user which repos have
active workers, what each worker is doing right now (a one-line scrolling tail of
its latest output), and pressing Enter on a worker opens its full conversation
(the prompt it was sent, the handoff it wrote, the result JSON text). The panel
architecture must be a registry so future views — module dependency graphs,
coverage dashboards — drop in without surgery.

## Context & Constraints

- Target repo: this worktree (marestail). All new code goes in a new package
  `marestail/tui/` plus a `watch` subcommand in `marestail/cli.py` and a short
  README section.
- House style (enforced by the project's own gates elsewhere): pure Python
  stdlib — NO third-party dependencies. Type hints everywhere, dataclasses,
  small single-purpose functions. NO comments and NO docstrings anywhere.
  See `marestail/report.py` for the idiom.
- The TUI uses stdlib `curses` only.
- Assumption (made because the user was not interviewed): "hover over it and
  press enter" is implemented as keyboard selection (↑/↓/j/k) + Enter; mouse
  hover is not reliable across terminals and is out of scope.
- Assumption: with no paths argument, `watch` scans `~/workspace` when it
  exists, else the current directory, for immediate subdirectories containing
  `.marestail/` (plus the root itself).
- Live fixtures available right now (READ ONLY — never modify):
  `/home/max/workspace/data-tracker-gate/.marestail/` and
  `/home/max/workspace/animus-chat/.marestail/` both have running pipelines.
  Their state layout:
  - `.marestail/runs/overnight-<stamp>.log` — runner log. Step lines:
    `== coder (147-coder) attempt 1`; completion lines:
    `   147-coder finished in 7.3 min: turns=34 api-equivalent=$9.45 'Done. …'`;
    verdict lines: `   verdict BOUNCE`.
  - `.marestail/runs/<task>/<label>.json` — agent output JSON, has a `result`
    string field. `<label>.prompt.md` — the prompt sent.
  - `.marestail/handoffs/<task>/<label>.md` — the handoff the worker wrote.
- Live process detection: parse `ps -eo pid,etimes,args`; a pipeline is
  `python3 … cli.py run <task>` — associate it to a repo via
  `/proc/<pid>/cwd`; agent child processes have `claude`, `grok`, `agy`,
  `cursor-agent` or `kilo` in args, optionally with `--model <m>`.
- Aesthetic direction (commit to it): "the allotment" — marestail is a weed the
  user pulls from their garden. Dark background, fern/moss greens for active
  workers, amber headings, heather purple for judge roles (critic/hardener)
  and for the selection highlight, dim sage for secondary text. Glyphs:
  ❧ header flourishes, ⚘ running worker, ✿ done step, ✶ bounced, ✔ passed,
  ○ idle, ◆ section markers. Rounded borders (╭─╮│╰╯) per repo "bed".
  Header: `❧ M A R E S T A I L ❧` + right-aligned
  `N beds · M workers · HH:MM:SS` and a vine divider (─ ∙ ❧).

### Data-layer contract (both layers are built against exactly this — do not change it)

```python
# marestail/tui/model.py
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class Step:
    role: str
    label: str
    attempt: int
    status: str            # "running" | "done"
    summary: str
    verdict: str | None    # "BOUNCE" | "PASS" | None
    minutes: float | None

@dataclass
class Process:
    pid: int
    elapsed_s: int
    model: str
    backend: str

@dataclass
class Worker:
    step: Step
    process: Process | None
    result_path: Path | None
    prompt_path: Path | None
    handoff_path: Path | None

@dataclass
class RepoState:
    name: str
    root: Path
    branch: str
    head: str
    task: str | None
    log_path: Path | None
    steps: list[Step] = field(default_factory=list)
    worker: Worker | None = None
    alive: bool = False

@dataclass
class Fleet:
    repos: list[RepoState]
    scanned_at: float

# marestail/tui/collect.py
def discover(roots: list[Path]) -> list[Path]: ...
def collect_repo(root: Path) -> RepoState: ...
def collect_fleet(roots: list[Path]) -> Fleet: ...
def conversation_for(worker: Worker) -> list[tuple[str, str]]: ...
```

Parsing rules: a step is `running` when its `==` line has no later `finished`
line. `summary` for finished steps is the single-quoted text of the finished
line, quotes stripped, whitespace collapsed to one line; for the running step,
the last non-empty log line. `minutes` parsed from the finished line.
`conversation_for` returns sections `[("prompt", …), ("handoff", …),
("result", …)]` (`result` = the JSON `result` field, raw text as fallback);
skip missing/unreadable files, never raise. `collect_fleet` must be fast: read
file contents only in `conversation_for`, never every `runs/*.json`.

## Phases

### Phase 1: data layer
- Create `marestail/tui/__init__.py` (empty), `marestail/tui/model.py`,
  `marestail/tui/collect.py` implementing the contract above.

Deliverables: the three files; `collect_fleet` parses both live fixture repos.
Verify:
```
cd <worktree> && python3 -m compileall marestail/tui
python3 -c "
import sys; sys.path.insert(0, '.')
from pathlib import Path
from marestail.tui.collect import collect_fleet, conversation_for
fleet = collect_fleet([Path('/home/max/workspace')])
names = {r.name for r in fleet.repos}
assert {'data-tracker-gate', 'animus-chat'} <= names, names
dt = next(r for r in fleet.repos if r.name == 'data-tracker-gate')
assert dt.steps, 'no steps parsed'
print('OK', len(fleet.repos), 'repos')
"
```

### Phase 2: TUI
- Create `marestail/tui/theme.py` (palette + glyphs, monochrome fallback),
  `marestail/tui/panels.py` (Panel base: `title`, `render`, `on_key`; a
  `PANELS` registry list; FleetPanel showing repo beds with marquee worker
  tails; ConversationPanel), `marestail/tui/app.py` exposing
  `def run(roots: list[Path], refresh: float = 2.0) -> int`.
- Behaviour: fleet view auto-refreshes every `refresh` seconds without
  flicker (erase+refresh, no full clear); each worker row shows role glyph,
  label, elapsed, and a horizontally scrolling one-line tail (~8 chars/sec,
  ~1.5 s pause at start); ↑/↓/j/k move selection across worker rows across
  repos; Enter opens the conversation view (sections with ◆ headers, scrolled
  with j/k/↑/↓/PgUp/PgDn/Home/End, q/Esc back); Tab cycles PANELS; r refreshes;
  q quits from the fleet view. Terminals under 70x20 show a centered resize
  notice. Guard every addstr against window bounds; fall back to
  bold/dim/reverse when `has_colors()` is False.

Deliverables: the three files; headless smoke run exits 0.
Verify:
```
cd <worktree> && python3 -m compileall marestail/tui
printf 'q' | script -qec 'TERM=xterm-256color python3 -c "
import sys; sys.path.insert(0, \".\")
from pathlib import Path
from marestail.tui.app import run
raise SystemExit(run([Path(\"/home/max/workspace\")], 0.5))
"' /dev/null
```

### Phase 3: wiring and docs
- `marestail/cli.py`: add `watch` next to the existing commands, following the
  `add_gate`/`add_run` pattern. Positional `paths` (nargs='*', default
  `~/workspace` if it exists else cwd) and `--refresh` (float, default 2.0).
  The handler imports `from marestail.tui import app as tui_app` INSIDE the
  handler so `marestail watch --help` and every other command never import
  curses.
- `README.md`: a `## Watch` section right after `## Overnight`, ~5 lines in
  the README's existing voice: what it shows, Enter for the full conversation,
  curses-stdlib only, panels registry built to be extended (module graph and
  coverage views planned).

Deliverables: wired command, README section.
Verify:
```
cd <worktree> && python3 -m compileall marestail/cli.py marestail/tui
PATH=<worktree>/bin:$PATH marestail watch --help
printf 'q' | script -qec 'TERM=xterm-256color <worktree>/bin/marestail watch /home/max/workspace --refresh 0.5' /dev/null
```

## Success Criteria (all must be true)

- [ ] `python3 -m compileall marestail/cli.py marestail/tui` exits 0.
- [ ] `marestail watch --help` exits 0 and prints usage including `--refresh`.
- [ ] `collect_fleet([Path('/home/max/workspace')])` returns at least
      `data-tracker-gate` and `animus-chat` with non-empty `steps`.
- [ ] The headless pty smoke run (`printf 'q' | script -qec …`) exits 0.
- [ ] `pip freeze`-style check: no new third-party imports — only stdlib
      modules appear in `marestail/tui/` imports.
- [ ] No comment or docstring tokens in the new files (project gate rule).
- [ ] Panels register by appending one class to `PANELS`; adding a future
      panel touches no existing file.

## Out of Scope

- Module dependency graph panel and coverage panel (planned future panels; the
  registry must make them possible, but do not build them unless asked).
- Mouse support / hover (keyboard selection instead).
- Sending input to or controlling workers; watch is read-only.
- Merging the branch, tagging, or releasing.
- Setting up a test framework for the marestail repo.

## Rules for the Implementing Agent

- Never delete, skip, or weaken a test to make it pass; flag suspect tests in
  PROGRESS-marestail-watch-fleet-tui.md instead.
- Record failed approaches and key decisions in
  PROGRESS-marestail-watch-fleet-tui.md as you go.
- Commit after each completed phase.
- Stay inside this worktree. Do not touch sibling worktrees or the main checkout.
- The fixture repos under /home/max/workspace (data-tracker-gate, animus-chat)
  are read-only for you; two live pipelines are writing to them.
