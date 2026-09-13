# PROGRESS: marestail practices tsar

Worktree `/home/max/workspace/marestail-marestail-practices-tsar`, branch `feat/marestail-practices-tsar`, base `main` 9d364df.

## Phase 1: The practices step exists and is skippable
- [x] `Judge("practices", None, bounce_to="coder", pinned_bounce=True, optional=True)` between architect and perf in `PIPELINE`
- [x] `practices` in `JUDGE_ROLES` (`marestail/tui/theme.py`)
- [x] `marestail/practices.py` (`files()`) + name-based no-guidance skip in `runner.run_step`
- [x] `guidance/**` in `freeze.SPEC`
- [x] `roles/practices.md`
- [x] `tools/test-practices.py` (order/fields, files(), no-guidance skip, disabled skip, freeze, PASS commit, role file)
- [x] dryrun: committed `guidance/ts.md` in the throwaway repo + `judge BOUNCE` / `code good` / `judge PASS` between architect and perf plan lines
- [x] Verify (`python3 tools/test-practices.py && python3 tools/test-agent-backends.py && tools/dryrun.sh`) + commit (a10777f)

## Phase 2: The TypeScript rulebook and install
- [x] `templates/guidance/ts.md` distilled from the two cheat sheets (decision 8): 48 rules (`TS-1`…`TS-48`) in the four sections
- [x] `install()` copies it to `guidance/ts.md` with `copy_if_missing`
- [x] Tests: install creates/does not overwrite, gitignore modes exclude it, template content checks
- [x] Verify + commit

## Phase 3: Gates and documentation
- [x] Gate audit for root `guidance/`: no changes needed — every walker selects source suffixes (`.py`, `.ts`, …) or markup (`.html`, `.css`, …) and never `.md` (`gates/comments.py` `files()` suffixes; `gates/docs.py` `source_files` suffixes and `doc_files` default `README.md` with prefix-gated `path_findings`). A rulebook that would trip a gate would trip `README.md` too.
- [x] `README.md` pipeline row + `## Best practices` section
- [x] `templates/marestail.toml` commented `[practices]` block
- [x] `templates/tasks-README.md` pipeline order sentence
- [x] `roles/hardener.md` guidance sentence
- [x] Verify + commit (0318443)

## Final verification (2026-09-13, all 10 success criteria)
- `python3 tools/test-practices.py` → `practices ok`
- `python3 tools/test-agent-backends.py` → `agent backends ok`
- `tools/dryrun.sh` → exit 0, `remaining plan lines: 0`
- `marestail.pipeline.names()` == `["specifier", "critic", "coder", "cleaner", "architect", "practices", "perf", "hardener", "qa"]`
- dry-run log: `practices verdict: BOUNCE` (line 13) before `practices verdict: PASS` (line 15); `guidance/ts.md` tracked
- no-guidance skip and `[practices] enabled = false` skip covered in `tools/test-practices.py`
- `guidance/**` in `freeze.SPEC`; neither install mode adds it to `.gitignore`
- `marestail install` creates `guidance/ts.md` and does not overwrite
- `templates/guidance/ts.md`: 4 sections, 48 `TS-n` rules, 61 lines
- `grep -nE '^\s*#' marestail/practices.py` prints nothing; only import is stdlib `pathlib`

## Decisions and notes
- `marestail/practices.py` is a single module, not a package: `files(root)` lists sorted `*.md` directly under `<root>/guidance/`, no recursion. The name-based skip in `run_step` follows the `measuring()` precedent.
- The dry-run stub needs no dedicated action: the generic `judge <verdict>` plan line covers a verdict-only judge, so `tools/stub-claude` is unchanged.
- `install()` copies `templates/guidance/ts.md` unconditionally (the `PERFORMANCE.md` precedent); existing repos never re-run install, so the no-guidance skip keeps the step off for them until a maintainer adds a rulebook.
