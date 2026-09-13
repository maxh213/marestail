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
- [ ] Gate audit for root `guidance/` (record outcome here)
- [ ] `README.md` pipeline row + `## Best practices` section
- [ ] `templates/marestail.toml` commented `[practices]` block
- [ ] `templates/tasks-README.md` pipeline order sentence
- [ ] `roles/hardener.md` guidance sentence
- [ ] Verify + commit

## Decisions and notes
