# QA: under hyper, the hunk check and the blast judge reject changes the fix did not need

Work in a scratch directory. `M` is the marestail-green checkout. The stub actions are the ones listed in the header of `features/010-hyper-blast-judge.feature`.

1. Build the fixture:
   `git init -q -b main repo && cd repo && git config user.email t@t && git config user.name t`.
   Write `marestail.toml` with `[git]` `base = "main"`, `[practices]` `enabled = false` and `[perf]` `enabled = false`.
   Write `.gitignore` as `.marestail/`, `tasks/t.md` as `# Add one`, `src.py` as `original`, and `util.py` as the four lines
   `def untouched(value):` / `    if value:` / `        return 1` / `    return 0`.
   Commit everything as `init`.
   Expected: `git log --oneline` shows one commit.
2. Set up the stub. Write `../capture` as the wrapper from `tools/test-run-hyper.py` (`CAPTURE`) and `chmod +x` it, then:
   `mkdir ../prompts`, `export MARESTAIL_CLAUDE=$PWD/../capture STUB_PLAN=$PWD/../plan.txt PROMPTS=$PWD/../prompts PATH=$M/bin:$PATH`.
   Unset `MARESTAIL_AGENT`, `MARESTAIL_SCOPE` and `MARESTAIL_FOCUS`.
   Expected: `echo $STUB_PLAN` prints the plan path.
3. `printf 'specify\njudge PASS\ncode\narchitect\njudge PASS\njudge PASS\nworker qa\n' > $STUB_PLAN`, then
   `python3 $M/marestail/cli.py run tasks/t.md --scope hyper --auto --retries 2`.
   Expected: the `== ` lines name specifier, critic, coder, architect, blast, hardener, qa in that order. `../prompts` holds 7 files. The last line is `pipeline complete` and the exit code is 0.
4. Open `../prompts/05.txt`.
   Expected: the first line is `You are the blast judge. You judge the diff; you never edit it.` The file has `# Diff stat` (listing `src.py`), `# Diff` (containing `+def add_one(x):`) and `# Hunks` (containing `src.py:1-2 — the fix needs it`), and no `# Gate report`.
5. `grep -l 'Add a `## Hunks` section' ../prompts/*.txt`.
   Expected: exactly `03.txt` and `04.txt`. Then check that `06.txt` contains `Do not bounce for a reason that would grow the diff beyond the fix; if you believe the fix is wrong, bounce to the specifier.`
   This repo is now "prepared". Before each of steps 6 to 12, empty `../prompts`, write the plan, and run the command from the repo.
6. Plan `code rename`. Run `python3 $M/marestail/cli.py run tasks/t.md --scope hyper --from coder --to coder --auto --retries 1`.
   Expected: stdout contains `util.py -> helpers.py: renamed or moved; under hyper no file may be renamed, moved or deleted`, then `pipeline stopped at coder`, and the exit code is 1.
   Then `git reset --hard HEAD~1`, which undoes the stub's commit so the next step starts clean.
7. Repeat step 6 with plan `code reindent`.
   Expected: stdout contains `util.py:2-4: whitespace or formatting only; under hyper leave code the fix does not need as it is`, and the exit code is 1. Reset as in step 6.
8. Repeat step 6 with plan `code miss-util`.
   Expected: stdout contains `util.py:3-3: not listed under ## Hunks`, and the exit code is 1. Reset as in step 6.
9. Plan `code package explain` then `code`. Run the step 6 command with `--retries 2`.
   Expected: stdout contains `package.json: frozen, reverted. Your reason was recorded as`. `git log --format=%B` shows `Revert change to frozen files by NN-coder, recorded as a proposal` and `Proposed by NN-coder: package.json`. `git cat-file -e HEAD:package.json` fails. The last line is `pipeline complete`.
10. Plan `code five-tests`. Run the step 6 command.
    Expected: exit code 0 and `pipeline complete`, and `ls tests` lists five `test_*.py` files.
11. Plan `code`, then `architect extract`. Run with `--scope hyper --from coder --to architect --auto --retries 1`.
    Expected: exit code 0 and `pipeline complete`. `sed -n 5p src.py` prints `def increment(x):`.
12. Plan `architect`, `judge BOUNCE coder`, `code`, `judge PASS`, `judge PASS`. Run with `--scope hyper --from architect --to hardener --auto --retries 2`.
    Expected: the `== ` lines are architect, blast, coder, blast, hardener, and the run ends `pipeline complete`.
    Repeat with `judge BOUNCE architect`, `architect` in place of `judge BOUNCE coder`, `code`.
    Expected: architect, blast, architect, blast, hardener.
13. Plan `code no-hunks package reindent`. Run the step 6 command with `--scope changed` in place of `--scope hyper`.
    Expected: exit code 0 and `pipeline complete`, `git show HEAD:package.json` prints `{}`, and stdout contains no `not listed under ## Hunks`.
14. In a fresh fixture (steps 1 and 2), use plan `specify`, `judge PASS`, `code`, `worker cleaner`, `worker architect`, `judge PASS`, `worker qa`, and run `python3 $M/marestail/cli.py run tasks/t.md --scope hard --focus src.py --auto --retries 2`.
    Expected: all nine roles are visited, there is no `== blast` line, and the run ends `pipeline complete`.
15. `python3 $M/marestail/cli.py run tasks/t.md --from blast --auto` in that fixture.
    Expected: exit code 1 and stderr `unknown role blast; choose from specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa`.
16. Open `$M/README.md` at `## Pipeline`.
    Expected: a row `| blast | judge | — | none |` sits between perf and hardener. The text below the table says what `—` means in the Gate column. The hyper section names the four tool rules and has one `boy scout` sentence and one `no line budget` sentence.
17. `cd $M && python3 tools/test-run-hyper.py`.
    Expected: the last line is `run hyper ok`.
