# PROGRESS: marestail perf tsar

Worktree `/home/max/workspace/marestail-marestail-perf-tsar`, branch `feat/marestail-perf-tsar`, base `main` 88c3adb.

## Phase 1: The perf step exists and is skippable
- [x] Judge `writes` + coder-only bounce target
- [x] perf in PIPELINE between architect and hardener
- [x] `[perf] enabled = false` skip
- [x] discard_edits keep patterns + stage kept, non-ignored paths before verdict commit
- [x] freeze.SPEC gains perf/** and PERFORMANCE.md
- [x] JUDGE_ROLES gains perf
- [x] roles/perf.md
- [x] tools/test-perf.py (pipeline order, coder target, freeze, discard_edits, disabled skip)
- [x] stub-claude `perf PASS` + dryrun-plan line
- [x] Verify (test-perf ok, agent backends ok, dryrun exit 0 with remaining plan lines: 0) + commit

## Phase 2: Commits, worktrees, trees.json
- [x] start-commit record, merge-base fallback, archive on completion
- [x] pre-marestail commit detection
- [x] worktrees + setup + trees.json + samples.jsonl truncate + finally cleanup
- [x] `# Trees` prompt section
- [x] dryrun.sh: README commit before marestail.toml
- [x] tests
- [x] Verify (test-perf ok, agent backends ok, dryrun exit 0, 0 plan lines, 1 worktree) + commit

## Phase 3: Sampling, results, audit, table, summary
- [ ] `marestail perf run` (no --db)
- [ ] compile/validate/classify results
- [ ] audit + retry with feedback
- [ ] PERFORMANCE.md matrix on PASS
- [ ] `## Performance changes` summary + overnight.sh
- [ ] stub `perf PASS|BOUNCE` + plan lines
- [ ] tests
- [ ] Verify + commit

## Phase 4: The performance database
- [ ] image detection
- [ ] effective rows + validation
- [ ] helper container, golden hash/build/META/disk check, status/url/prune/down
- [ ] reset per --db sample, runner pre-build, finally cleanup
- [ ] test-perf.py detection/rows/disk tests
- [ ] tools/test-perf-db.py
- [ ] Verify + commit

## Phase 5: Scale check at 50M rows
- [ ] tools/perf-db-scale.py, run, record line
- [ ] Verify + commit

## Phase 6: Install and gate exclusions
- [ ] templates/PERFORMANCE.md + install + gitignore lines
- [ ] exclude root perf/ from every gate (list below)
- [ ] tests
- [ ] Verify + commit

## Phase 7: Documentation
- [ ] README, templates/marestail.toml, tasks-README, hardener.md
- [ ] Verify + commit

## Baseline
- 2026-09-13, base 88c3adb: `python3 tools/test-agent-backends.py` passes. `tools/dryrun.sh` exits 0 but printed `remaining plan lines: 1`, and it was not testing this checkout (see below).

## Decisions and notes
- `tools/stub-claude` wrote `"\n".join(rest) + "\n"`, so an emptied plan still held one newline and `wc -l` printed 1. It now writes one line per remaining action, so an exhausted plan prints 0. This was needed for the success criterion `remaining plan lines: 0`.
- `tools/dryrun.sh` appended `bin/` to `PATH`, so the `marestail` already on the user's `PATH` (`/home/max/workspace/marestail/bin`, the main checkout) ran instead of this worktree's. It now prepends `bin/`. Found when the perf step did not appear in the dry run.
- Deviation from decision 7: perf's `writes` is `("perf/**",)` only, not `PERFORMANCE.md`. The runner writes and stages the table itself on PASS; letting the agent edit it would commit agent table edits on BOUNCE. `PERFORMANCE.md` is still in `freeze.SPEC`.
- `Judge` gained `writes`, `pinned_bounce` (the named target is dropped, so the bounce and commit subject always use `bounce_to`) and `optional` (`[<name>] enabled = false` skips it), instead of hard-coding `perf` in the runner.
- `discard_edits` keeps the old `git checkout -- .` / `git clean` path for judges with no writes, so critic and hardener behave as before. Judges with writes get a per-path restore (tracked paths from HEAD; untracked paths unstaged and deleted).
- `stage_writes` stages changed paths matching `writes` from `git status --porcelain --untracked-files=all`, which never lists ignored files. So ignored paths are skipped without an explicit `git check-ignore`.
- Phase 2 layout: `marestail/perf/` package with `settings.py` (`[perf]` getters), `table.py` (parse `PERFORMANCE.md`; Phase 3 adds render) and `trees.py` (commits, worktrees, `trees.json`, prompt section). `runner.run_judge` wraps each attempt in `measuring()`, which opens trees only for the judge named `perf`, the same name-based precedent as `critic` in `prompts.py`. The attempt body moved into `judge_attempt`.
- A failing `[perf] setup` does not stop the step: the failure goes into the `# Trees` prompt section as a note, so the agent sees it. A failing `git worktree add` raises, and `measuring` still cleans up what was created.
- When the pipeline completes with no handoffs folder to archive into, `start-commit` is deleted rather than kept, so a re-run still records a fresh start.
- The table header includes `Rows` from the start (`| Task | Commit | Date | Rows |`); the prompt's decision 18 template text predates the `Rows` column.
- The verdict instructions now depend on the judge: a judge with writes is told which files it may edit, and a pinned judge is told only PASS or BOUNCE (to its `bounce_to`).

## Gate exclusion mechanisms changed
