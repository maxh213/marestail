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
- [x] `marestail perf run` (no --db)
- [x] compile/validate/classify results
- [x] audit + retry with feedback
- [x] PERFORMANCE.md matrix on PASS
- [x] `## Performance changes` summary + overnight.sh
- [x] stub `perf PASS|BOUNCE` + plan lines
- [x] tests
- [x] Verify (test-perf ok, agent backends ok, dryrun exit 0, 0 plan lines, table header and rows as specified, BOUNCE before PASS, bench tracked 100755, 1 worktree) + commit

## Phase 4: The performance database
- [x] image detection
- [x] effective rows + validation
- [x] helper container, golden hash/build/META/disk check, status/url/prune/down
- [x] reset per --db sample, runner pre-build, finally cleanup
- [x] test-perf.py detection/rows/disk tests
- [x] tools/test-perf-db.py
- [x] Verify (test-perf-db `perf db ok`, test-perf ok, agent backends ok, dryrun exit 0, no marestail-perf-test- containers or volume left, other containers untouched) + commit

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
- Phase 3 modules: `samples.py` (`marestail perf run`, which runs the bench with its own `subprocess.run` so stdout JSON can be parsed apart from stderr; `shell.run` merges them), `results.py` (`Measurement`, `Classified`, p50/p95, validation, classification, audit), `review.py` (loads samples, writes `<report>.results.json`, runs the audit, writes and stages the table, builds the summary), `table.py` (parse/upsert/render). `templates/PERFORMANCE.md` was created in Phase 3 because the table writer needs it; Phase 6 wires it into install.
- `perf run --db` exits 2 with `configure [perf.db] migrate in marestail.toml` until Phase 4 implements the database.
- A rejected perf verdict is not committed; `judge_attempt` returns the problems as feedback and `run_judge` passes them into the next attempt's prompt under `# Why your verdict was rejected`. Each attempt gets fresh trees and an empty `samples.jsonl`.
- A PASS with zero measurements (no benches, no existing columns) writes no table row. Writing an empty row would make the table "have rows" and permanently skip the pre-marestail measurement.
- Deviation from decision 13's `f"{value:g}"`: values render with `f"{value:.10g}"`. `:g` keeps only 6 significant digits, so a value like 1234567 would render as `1.23457e+06`; `.10g` still renders `20.0` as `20` and `0.02` as `0.02`.
- `percent_change` computes `(head - baseline) * 100 / baseline` and rounds to 9 decimals, so exactly 10% compares as 10.0 and counts as changed.
- The audit reports one unflagged problem per target, using its first (p50) measurement. A dict comprehension first kept the p95 item; caught by the retry test.
- The summary lives on `Run.perf_changes` and is printed after the proposals summary. It is set on any accepted perf verdict, including BOUNCE, so a pipeline stopped after a perf bounce still reports it.
- `tools/overnight.sh`: the `## Config changes` capture now stops before `## Performance changes`, and a second `sed` captures the performance section.
- Phase 4 modules: `image.py` (Postgres image detection, helper image) and `db.py` (settings, helper container, goldens, reset, CLI actions, `prepare`/`release`). `runner.measuring` calls `perf_db.prepare` after the trees exist and `perf_db.release` in a `finally` before the trees close.
- Deviation from decision 16: `marestail perf db prune` removes only goldens whose `META.json` `root` is this repo, not every golden on the machine when no run is active. The volume is shared by every repo, so the literal rule would delete other repos' goldens. `META.json` gains `name` and `root` for this.
- `MARESTAIL_PERF_DB_HOME` (default `~/.config/marestail`) sets where `perf-db.json` and `perf-db/<golden>.json|.log` live, so `tools/test-perf-db.py` never writes into the real config dir.
- `perf/seed.sql` runs through psql with `-v rows=<n>`, so a SQL seed can use `:rows`; executable seeds get `MARESTAIL_PERF_ROWS`.
- `trees.json` records `image`, `image_source`, `rows` and `rows_source`. Every later `perf run --db` and `perf db …` in that run uses the recorded values, so an agent's mid-run edit to a compose file (discarded afterwards) cannot change the image partway through.
- Docker exec only passes `-i` when stdin is sent. `shell.run` otherwise inherits the parent's stdin, and an attached `-i` could wait on an agent's open pipe.
- The count bench in `tools/test-perf-db.py` reads `count(*)` before inserting its row. The criterion is that both samples report the golden's row count, which a count taken after the insert could never show.
- Readiness is `pg_isready -h 127.0.0.1` inside the container. The image's init-time temporary server listens on no TCP address, so this only succeeds once the real server is up.
- The golden password is baked in at initdb. Deleting `perf-db.json` generates a new password that existing goldens do not accept; run `marestail perf db prune` (or remove the volume) after doing that.
- The verdict instructions now depend on the judge: a judge with writes is told which files it may edit, and a pinned judge is told only PASS or BOUNCE (to its `bounce_to`).

## Gate exclusion mechanisms changed
