# QA procedure: `marestail run --scope hyper` runs a short pipeline

`M` is the marestail-green checkout. `HYPER_ROLES` means `specifier, critic, coder, architect, hardener, qa`.

1. `cd $M`, activate `.venv`, run `python3 tools/test-run-hyper.py`.
   Expected: exit `0`, last line contains `ok`.
2. Build the capture wrapper `/tmp/q/capture` (then `chmod +x`):
   ```sh
   #!/bin/sh
   n=$(printf '%02d' $(( $(ls "$PROMPTS" | wc -l) + 1 )))
   tee "$PROMPTS/$n.txt" | "$M/tools/stub-claude"
   ```
   Export `M`, `MARESTAIL_CLAUDE=/tmp/q/capture`, `STUB_PLAN=/tmp/q/plan.txt`, `PROMPTS=/tmp/q/prompts`, and `PATH=$M/bin:$PATH`. Before each run below, `rm -rf $PROMPTS; mkdir -p $PROMPTS` and write the plan, one action per line.
3. Build a fresh fixture in `/tmp/q/hyper`: `git init -b main`, set a git user; write `marestail.toml` with `[git]` `base = "main"`, `[practices]` `enabled = false`, `[perf]` `enabled = false`; `.gitignore` with `.marestail/`; `tasks/t.md` with `# Add one`; `src.py` with `original`; `git add -A && git commit -m init`.
4. Plan `specify`, `judge PASS`, `code`, `worker architect`, `judge PASS`, `worker qa`. In `/tmp/q/hyper` run `marestail run tasks/t.md --scope hyper --auto --retries 2; echo exit=$?`.
   Expected: the `== ` lines name specifier, critic, coder, architect, hardener, qa in order. There is no cleaner, practices or perf and no `skipping` line. The last line is `pipeline complete`, then `exit=0`. `$PROMPTS` holds `01.txt` to `06.txt` and the plan file is empty. Keep these prompts as `/tmp/q/hyper-prompts` (`cp -r`).
5. In `/tmp/q/hyper-prompts`, `head -1 *.txt` shows `You are the specifier.` … `You are QA.`, and `grep -c 'This run is hyper-scoped' *.txt` shows `1` for each file. `grep -l 'This run has a hard scope' *.txt` lists nothing.
   Expected: each file's `# Scope` section has its role's sentence from the feature's table and none of the other roles' sentences. `06.txt` (qa) has only the all-roles sentence. `05.txt` (architect) has "Apply the boy scout rule to the code this change touches, and only that code" and "Do not reshape, move or rename anything the change does not touch".
6. `grep -h 'marestail gate --tier' /tmp/q/hyper-prompts/03.txt /tmp/q/hyper-prompts/04.txt`
   Expected: both lines read ``Run `marestail gate --tier full --scope hyper` and keep working until it prints GATE PASSED.``
7. Build a second fresh fixture `/tmp/q/hard` as in step 3. Plan `specify`, `judge PASS`, `code`, `worker cleaner`, `worker architect`, `judge PASS`, `worker qa`. Run `marestail run tasks/t.md --scope hard --focus src.py --auto --retries 2`.
   Expected: `== ` lines for specifier, critic, coder, cleaner, architect, then `practices disabled in marestail.toml; skipping`, then `perf disabled in marestail.toml; skipping`, then hardener and qa, and last line `pipeline complete`. No prompt contains `This run is hyper-scoped`. The coder prompt (`03.txt`) says `--tier fast` and the architect prompt (`05.txt`) says `--tier sonar`.
8. In `/tmp/q/hyper` (now prepared), with plan `code`, run `marestail run tasks/t.md --scope hyper --from cleaner --auto; echo exit=$?`. Then repeat with `--to cleaner`.
   Expected, each time: `unknown role cleaner; choose from specifier, critic, coder, architect, hardener, qa` and `exit=1`. `$PROMPTS` is empty and the plan still reads `code`.
9. In `/tmp/q/hyper`, with plan `code`, `worker architect`, run `marestail run tasks/t.md --scope hyper --from coder --to architect --auto --retries 2`.
   Expected: `== ` lines for coder then architect only, exit `0`.
10. In `/tmp/q/hyper`, with plan `judge BOUNCE cleaner`, `code`, `judge PASS`, run `marestail run tasks/t.md --scope hyper --from hardener --to hardener --auto --retries 2`.
    Expected: `== ` lines for hardener, coder, hardener in that order. No cleaner. Last line `pipeline complete`, exit `0`.
11. Record the runner's gate tiers. In `$M`, run `python3 -c` with a snippet that patches `marestail.runner.Run.gates` to print its `tier` argument and return `[]`, and then, with the working directory set to a fresh fixture, calls `runner.run_pipeline(Path("tasks/t.md"), None, None, True, None, 2, scope="hyper")` with step 4's plan.
    Expected: `full`, `full`, `full`, `qa`. With step 7's plan and `scope="hard"` (focus `src.py`), or with scope `all`, the tiers are `fast`, `sonar`, `sonar`, `full`, `qa`.
12. In `/tmp/q/hyper`, with plan `code`, run `SCOPE=hyper START_FROM=coder STOP_AT=coder $M/tools/overnight.sh tasks/t.md; echo exit=$?`.
    Expected: `exit=0`. The newest `.marestail/runs/overnight-*.md` contains `- exit 0`. `$PROMPTS/01.txt` starts `You are the coder.` and contains `This run is hyper-scoped.` and `--tier full --scope hyper`.
13. `sed -n '/## Pipeline/,/^## Best/p' $M/README.md`
    Expected: the table has a `hyper` column. It marks specifier, critic, coder, architect, hardener and qa as running, with `full` for coder and architect, and cleaner, practices and perf as not running.
14. `for f in $M/tools/test-*.py; do python3 $f >/dev/null 2>&1; echo "$f $?"; done`
    Expected: every script exits as it did on the base commit. `tools/test-perf.py` still exits `1`.
