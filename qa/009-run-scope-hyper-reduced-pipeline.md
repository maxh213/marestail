# QA procedure: `marestail run --scope hyper` runs a short pipeline

1. `cd` to the marestail-green repo root with `.venv` active.
2. `python3 tools/test-run-hyper.py`
   Expected: exit `0`, last line contains `ok`.
3. Make a temp git repo like the one `tools/test-timeline.py` builds: marestail installed, `tasks/t.md`, `MARESTAIL_CLAUDE=<repo>/tools/stub-claude`, and `marestail.toml` with `[practices] enabled = false` and `[perf] enabled = false`. Give it a `STUB_PLAN` in which every worker commits a handoff and every judge writes `VERDICT: PASS`. Run:
   `marestail run tasks/t.md --scope hyper --auto`
   Expected: the files in `.marestail/handoffs/t/` (or its archive) come from specifier, critic, coder, architect, hardener and qa, in that order. There is no `-cleaner`, `-practices` or `-perf` file, stdout has no `disabled in marestail.toml; skipping` line, and the last line starts with `pipeline complete`.
4. Same repo and plan, but run `marestail run tasks/t.md --scope hard --focus src --auto`.
   Expected: handoffs from specifier, critic, coder, cleaner, architect, hardener and qa. Stdout has `practices disabled in marestail.toml; skipping` and `perf disabled in marestail.toml; skipping`, printed between architect and hardener.
5. `marestail run tasks/t.md --from cleaner --scope hyper --auto; echo exit=$?`
   Expected: a non-zero exit, the output contains `unknown role cleaner; choose from specifier, critic, coder, architect, hardener, qa`, and the stub is never invoked (the `STUB_PLAN` file is unchanged).
6. `marestail run tasks/t.md --scope hyper --from coder --to architect --auto`
   Expected: exactly one coder and one architect handoff, and exit `0`.
7. `marestail run tasks/t.md --scope hyper --from hardener --to hardener --auto --retries 2`, with the plan: hardener `VERDICT: BOUNCE cleaner`, coder commits, hardener `VERDICT: PASS`.
   Expected: the next role run after the bounce is the coder (a new `-coder.md` handoff), then the hardener again. No cleaner runs, and the run ends `pipeline complete`.
8. Save the prompt each role receives (set the stub to write stdin to `prompt-<role>.txt`) during step 3's run. Then run `grep -c 'This run is hyper-scoped' prompt-*.txt; grep -l 'This run has a hard scope' prompt-*.txt`
   Expected: every file counts `1`, and the second grep lists nothing. Each file has its role's sentence from the feature's table. The architect file contains "Apply the boy scout rule to the code this change touches, and only that code" and "Do not reshape, move or rename anything the change does not touch".
9. `grep -h 'marestail gate --tier' prompt-coder.txt prompt-architect.txt`
   Expected: both show `marestail gate --tier full --scope hyper`.
10. Repeat step 8 with the prompts from step 4's hard run.
    Expected: no file contains `This run is hyper-scoped`. The coder prompt says `--tier fast` and the architect prompt says `--tier sonar`.
11. `SCOPE=hyper START_FROM=coder STOP_AT=coder tools/overnight.sh tasks/t.md` in that temp repo.
    Expected: the overnight summary records exit `0` for `tasks/t.md`, and the saved coder prompt contains `This run is hyper-scoped.` and `--tier full --scope hyper`.
12. `sed -n '/## Pipeline/,/^## /p' README.md`
    Expected: the pipeline table has a `hyper` column that marks specifier, critic, coder, architect, hardener and qa as running and cleaner, practices and perf as not running. It shows `full` for coder and architect under hyper.
13. `for f in tools/test-*.py; do python3 $f >/dev/null 2>&1; echo "$f $?"; done`
    Expected: every script exits as it did before this task. `tools/test-perf.py` still exits `1`.
