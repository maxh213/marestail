# Marestail — deliverable-scoped gates

## Overview

Let a developer gate only the code a task touches, so `marestail gate` can
demand full cleanliness — 100% coverage, CRAP ≤ 4, no comments, clean lint,
clean Sonar, correct dependency direction — **for the new and changed code
needed to complete the task**, without forcing a whole-repo cleanup first.
Scope follows the change, not a static path list: if the work splits one file
into three or moves code into another existing file, those files and the new
functions in them enter scope automatically because they are in the diff. A
manual `--focus <path>` (repeatable) / `[focus] paths` in marestail.toml adds
whole files or folders to the scope on top.

## Context & Constraints

- Target repo: this worktree (marestail). House style: stdlib only, type
  hints, dataclasses, NO comments, NO docstrings, small functions.
- Existing machinery to generalize (READ IT FIRST):
  `marestail/context.py` (every gate receives a `Context`; `scope_changed`
  plus `changed: set[str]` of repo-relative paths),
  `marestail/changes.py: changed_files(root, base)` (committed diff vs
  `[git] base` ∪ working-tree/untracked), and ~30 gates under
  `marestail/gates/` already consuming `scope_changed`/`changed_under`.
  `marestail/cli.py: run_gates` builds the Context; `--scope` is currently
  `all|changed`.
- Semantics (chosen, not to be re-litigated):
  - `--scope changed` is UPGRADED to true deliverable scoping (its current
    whole-file crudeness is a bug relative to its name; README is updated to
    match). Under it:
    - **Coverage gates (`*_tests.py`)**: 100% line+branch coverage of the
      CHANGED lines/branches, not of whole files. Changed lines come from
      `git diff <base>...HEAD` + working-tree diff + untracked files (an
      untracked/new file is changed everywhere — full coverage of it
      required). Coverage reports already parsed per language
      (rb-coverage.json, ex-coverage.json, coverage.py, istanbul, coverlet)
      are intersected with the changed-line set per file. Branches: where the
      report exposes branch data on changed lines, those arms must be covered.
      Test selection stays whole-suite.
    - **CRAP (`*_crap.py`)**: only functions/methods that are new or whose
      body intersects a changed hunk (each CRAP gate already parses functions
      with an AST — map diff hunk line ranges to enclosing function ranges).
      If a function is moved verbatim to another file, it is in the diff
      there and counts as new.
    - **comments.py / deadcode.py**: changed files are checked whole (a file
      you touched must be comment-free and must not gain dead code); deadcode
      only reports unreachable definitions IN changed files.
    - **lint/type (`*_lint.py`)**: changed files only (tool path args where
      supported, else post-filter findings).
    - **deps (`*_deps.py`)**: only violations whose offending source file is
      a changed file.
    - **mutation (`*_mutation.py`)**: changed files only (already the case).
    - **sonar.py**: scanner stays project-wide; open issues / duplication /
      hotspots post-filtered to changed files. Global quality-gate status is
      informational under scoping; pass/fail comes from the filtered
      findings. Document this.
    - **docs.py / qa.py / py_runtime.py**: stay global (repo-level by
      nature), printing a one-line scope note.
  - `--focus PATH` (repeatable, must exist — typo is a hard error) and
    `[focus] paths = [...]` add whole files/directories to the scope: a
    focused file is treated as fully changed (full coverage, all its
    functions CRAP-checked). `--focus` requires scoping to be active: using
    it implies `--scope changed` semantics for the focus paths UNION the diff
    (focus ∪ changed). `--scope all` + `--focus` is a usage error.
  - `marestail gate --hook` (Stop-hook mode) picks up `[focus]` from config
    and works with the changed set of the repo it runs in.
  - Every gate MUST honor the scope — a gate silently measuring the whole
    repo makes the feature a liar. Where a gate truly cannot be scoped it
    says `skipped: <reason>` (see `Result.skipped`), never fakes it.
  - The report (`marestail/report.py`) shows the scope when active, e.g.
    `scope: changed (14 files, 412 lines) + focus: app/services`; `--json`
    gains `scope`/`focus` fields.
  - Assumption (no interview, auto mode): `marestail run` pipeline is NOT
    changed; pipeline-level task scoping is Out of Scope.
  - Assumption: line-hunk parsing lives in `marestail/changes.py`
    (`changed_lines(root, base) -> dict[path, set[int]]` built from
    `git diff -U0` for committed+working state; untracked files map to all
    their lines) and is shared by every gate via Context helpers:
    `ctx.changed_line_set(path)`, `ctx.gated_lines(path) -> set[int] | None`
    (None = whole file: unfocused+unchanged when scoped, or always in
    `--scope all`), `ctx.in_scope(path)`.
- Fixtures: verification uses disposable synthetic repos in /tmp via scripts
  under `tools/samples/` — never touch the real repos on this machine.

## Phases

### Phase 1: scope plumbing
- `changed_lines(root, base)` in changes.py (git `-U0` hunk parse, committed
  ∪ working tree; untracked = all lines; binary/deleted files excluded).
- Context: `changed_lines_map`, `focus`, `in_scope(path)`,
  `gated_lines(path)`, plus `scope_name`/`scope_summary` for the report.
- cli.py: `--focus` repeatable on the gate command (implies changed-scope for
  those paths; error with `--scope all`; error on nonexistent path);
  `[focus] paths` in config.py; hook mode picks config up.
- report.py: scope line in render + `scope`/`focus` in `--json`.
Deliverables: plumbing + unit-level proof.
Verify:
```
cd <worktree> && python3 -m compileall marestail
python3 - <<'EOF'
import subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, '.')
from marestail.changes import changed_lines
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    subprocess.run(['git', 'init', '-q'], cwd=d)
    subprocess.run(['git', 'commit', '-q', '--allow-empty', '-m', 'x'], cwd=d)
    (root / 'a.py').write_text('one\ntwo\nthree\n')
    subprocess.run(['git', 'add', '.'], cwd=d)
    subprocess.run(['git', 'commit', '-q', '-m', 'add'], cwd=d)
    (root / 'a.py').write_text('one\nTWO\nthree\nfour\n')
    (root / 'b.py').write_text('new\nfile\n')
    lines = changed_lines(root, 'HEAD')
    assert lines['a.py'] == {2, 4}, lines
    assert lines['b.py'] == {1, 2}, lines
print('changed_lines OK')
EOF
```

### Phase 2: Python gates + fixture
- py_tests (diff coverage), py_crap (changed functions), py_lint,
  py_mutation, py_deps, comments, deadcode, depth honor the scope.
- `tools/samples/scope-py.sh`: builds a /tmp Python repo with a dirty
  pre-existing module (uncovered, CRAP > 4, commented, dead code — committed
  to the base) and a clean changed module, proves
  `marestail gate --tier fast --scope changed` PASSES while `--scope all`
  FAILS; then dirties the changed module and proves the scoped run FAILS too
  (scope is honest, not a dodge).
Verify: `tools/samples/scope-py.sh` exits 0 printing the three expected
verdicts.

### Phase 3: Ruby gates + fixture
- rb_tests (SimpleCov diff coverage), rb_crap (Ripper functions ∩ hunks),
  rb_lint, rb_mutation, rb_deps honor the scope;
  `tools/samples/scope-rb.sh` mirrors the py fixture (skip with a note in
  PROGRESS if bundler/rspec is unavailable).
Verify: `tools/samples/scope-rb.sh` exits 0 (or documented skip).

### Phase 4: remaining languages + sonar
- ex_*, ts_*, cs_*, er_* tests/crap/lint/mutation/deps honor the scope;
  sonar.py post-filters to scoped files (unit-level: assertion on its
  filtering with a fabricated issue list; no live Sonar needed).
- Fixture scripts only where the toolchain exists on this machine (elixir
  does; check node/dotnet availability first, document skips in PROGRESS).
Verify: available `tools/samples/scope-<lang>.sh` exit 0; sonar filter
assertion passes.

### Phase 5: docs + audit
- README: rewrite the `--scope` mention / add a short `## Scope` paragraph
  (changed = the diff, grows as code moves/splits; `--focus` adds paths;
  sonar is filtered-not-rescoped; docs/qa/runtime stay global).
- Grep audit: every file under marestail/gates/ either consumes the new
  Context scope helpers or is docs.py/qa.py/py_runtime.py.
Verify: audit prints only the three globals; compileall clean;
`marestail gate --help` shows `--focus`; `--scope all --focus x` errors.

## Success Criteria (all must be true)

- [ ] `changed_lines` passes the Phase 1 hunk-parse proof (committed +
      working tree + untracked).
- [ ] py fixture: scoped PASS / all-scope FAIL / dirtied-scope FAIL (three
      verdicts, honest both ways).
- [ ] Coverage measured on changed lines only — a base file with uncovered
      pre-existing lines does not fail the scoped run, but one uncovered NEW
      line does (asserted in the py fixture; rb fixture if available).
- [ ] A function moved from file A to existing file B is gated in B (new
      hunks) — asserted by fixture or unit-level check on the hunk→function
      mapping.
- [ ] Every gate file consumes scope helpers or is one of the three
      documented globals (grep audit).
- [ ] `--focus` nonexistent path = hard error; `--scope all --focus` = usage
      error; hook mode honors `[focus] paths`.
- [ ] Report shows scope summary when active; `--json` has `scope`/`focus`.
- [ ] `--scope all` behavior on an unchanged repo is byte-identical to
      before the feature (diff the gate report on a fixture before/after).
- [ ] compileall clean; zero comments/docstrings in new/changed code.

## Out of Scope

- Scoping the `marestail run` pipeline (task-declared scope, per-role gates)
  — future feature, do not build unless asked.
- SonarQube server-side path-scoped metrics (impossible scanner-side).
- Any threshold/behavior change under `--scope all`.
- Test framework setup for the marestail repo (tools/samples scripts are the
  verification).

## Rules for the Implementing Agent

- Never delete, skip, or weaken a test to make it pass; flag suspect tests in
  PROGRESS-marestail-scoped-focus-gates.md instead.
- Record failed approaches and key decisions in
  PROGRESS-marestail-scoped-focus-gates.md as you go.
- Commit after each completed phase.
- Stay inside this worktree. Do not touch sibling worktrees or the main checkout.
- Do not modify any repo under /home/max/workspace outside this worktree;
  fixtures live in /tmp.
