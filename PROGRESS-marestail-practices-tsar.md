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
- [ ] Verify (`python3 tools/test-practices.py && python3 tools/test-agent-backends.py && tools/dryrun.sh`) + commit

## Phase 2: The TypeScript rulebook and install
- [ ] `templates/guidance/ts.md` distilled from the two cheat sheets (decision 8)
- [ ] `install()` copies it to `guidance/ts.md` with `copy_if_missing`
- [ ] Tests: install creates/does not overwrite, gitignore modes exclude it, template content checks
- [ ] Verify + commit

## Phase 3: Gates and documentation
- [x] Gate audit for root `guidance/`: no changes needed — every walker selects source suffixes (`.py`, `.ts`, …) or markup (`.html`, `.css`, …) and never `.md` (`gates/comments.py` `files()` suffixes; `gates/docs.py` `source_files` suffixes and `doc_files` default `README.md` with prefix-gated `path_findings`). A rulebook that would trip a gate would trip `README.md` too.
- [x] `README.md` pipeline row + `## Best practices` section
- [x] `templates/marestail.toml` commented `[practices]` block
- [x] `templates/tasks-README.md` pipeline order sentence
- [x] `roles/hardener.md` guidance sentence
- [ ] Verify + commit

## Decisions and notes
- `marestail/practices.py` is a single module, not a package: `files(root)` lists sorted `*.md` directly under `<root>/guidance/`, no recursion. The name-based skip in `run_step` follows the `measuring()` precedent.
- The dry-run stub needs no dedicated action: the generic `judge <verdict>` plan line covers a verdict-only judge, so `tools/stub-claude` is unchanged.
- `install()` copies `templates/guidance/ts.md` unconditionally (the `PERFORMANCE.md` precedent); existing repos never re-run install, so the no-guidance skip keeps the step off for them until a maintainer adds a rulebook.
