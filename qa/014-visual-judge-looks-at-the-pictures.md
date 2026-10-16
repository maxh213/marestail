# QA procedure: the visual judge looks at the pictures

Needs Chromium via Playwright (013 QA step 2). `$M` is the marestail-green checkout with `.venv` active and
`$M/bin` first on PATH. Work in `$T=/tmp/mt-vjudge`; `$W=/tmp/mt-vjudge-bin`. Run every `marestail` command in `$T`.

1. Build the fixture as in 013 QA step 1, with `viewports = { desktop = "1440x900", phone = "390x844@2 touch" }`,
   `[practices] enabled = false`, `[perf] enabled = false`, the block plus `symptom: the widget is 440px wide`,
   `tasks/t.md` (`# Change the page text`), and `features/t.feature` written by hand as the five lines `Feature: t`,
   `  Scenario: Adds one`, `    Given x`, `  Scenario Outline: Rejects bad input`, `    Given y` (do not run
   `tools/stub-claude specify`: it also overwrites `qa/t.md`).
   Commit on `main`, `git checkout -q -b work`, change `Page text` to `Other text`, commit.
   Expected: `git log --oneline | wc -l` prints 2.
2. Make the wrapper: `$W/wrap` saves `$(basename $0) $*` to `$ARGS/NN.txt` and stdin to `$PROMPTS/NN.txt`
   (next free NN), then pipes stdin to `$M/tools/stub-claude`. Symlink it as `$W/claude`, `$W/cursor-agent`, `$W/kilo`.
   Export `MARESTAIL_CLAUDE=$W/claude MARESTAIL_CURSOR=$W/cursor-agent MARESTAIL_KILO=$W/kilo
   MARESTAIL_LIMIT_WAIT_SECONDS=0 STUB_PLAN=$W/plan ARGS=$W/args PROMPTS=$W/prompts`.
   Before each step below: empty `$ARGS` and `$PROMPTS`, write the step's plan to `$W/plan`, one action per line.
   Expected: `ls -l $W` shows the three symlinks.
3. Plan `judge PASS`, `judge PASS`, `worker qa`. Run `marestail run tasks/t.md --from hardener --to qa --auto --retries 2`.
   Expected: `== hardener (`, `== visual (`, `== qa (` in that order; exit 0; last line `pipeline complete`.
   `$ARGS/02.txt` contains `--model claude-fable-5-1`, no `--effort`. `$PROMPTS/02.txt` starts `You are the visual judge.`,
   has a line `# Visual` followed later by `Symptom: the widget is 440px wide`, eight `.png` lines (desktop then phone; base element, base viewport,
   head element, head viewport), and the rows `| desktop | width | 440 | 440 |` and `| phone | scrollWidth | 572 | 572 |`.
   Open `.marestail/runs/t/visual/head/desktop/element.png`: the 440px widget.
   `git log --format=%s | grep 'visual verdict'` prints `[claude-fable-5-1] visual verdict: PASS`.
3a. Run `marestail watch $T --all` in another terminal and open the run `t`.
   Expected: its steps list `hardener`, `visual`, `qa` in that order; `visual` has the same colour as `hardener`. Quit.
4. Same plan and command, with `--agent cursor --model gpt-9` added.
   Expected: `$ARGS/01.txt` and `03.txt` start with `cursor-agent` and have `--model gpt-9`; `02.txt` starts with `claude` and has
   `--model claude-fable-5-1`. The newest visual verdict subject starts `[claude-fable-5-1]`, the hardener's `[gpt-9]`.
5. Plan `limit`, `judge PASS`. Run `marestail run tasks/t.md --from visual --to visual --agent cursor --model gpt-9 --auto`.
   Expected: stdout has `visual: claude-fable-5-1 is out of usage; judging with cursor gpt-9` and no `rate limited; waiting`;
   `$ARGS/02.txt` starts with `cursor-agent`; the visual step in `.marestail/runs/t/timeline.json` has `"waits": []`;
   the verdict subject is `[gpt-9] visual verdict: PASS`; exit 0.
6. Plan `limit`, `judge PASS`, `worker qa`. Run `marestail run tasks/t.md --from visual --to qa --agent kilo --model kilo/x --auto --retries 2`.
   Expected: `judging with kilo kilo/x`; `$PROMPTS/02.txt` has `The pictures could not be shown: kilo cannot read images.
   Judge from the geometry table alone.` and `grep -c png $PROMPTS/02.txt` prints 0; stdout has
   `   verdict PASS (geometry only, pictures not seen)`; last line `pipeline complete, NOT verified by eye`; `echo $?` prints 3.
7. Plan `judge PASS`, `judge BOUNCE coder`, `code`, `judge PASS`, `worker qa`; run step 3's command.
   Expected: roles in order hardener, visual, coder, visual, qa; exit 0.
8. Plan `judge PASS`, `judge BOUNCE specifier`, `worker specifier`, `judge PASS`, `worker qa`; run step 3's command.
   Expected: hardener, visual, specifier, visual, qa; exit 0.
9. `KEEP=$(git rev-parse HEAD)`. Set `#widget` to `width:1408px`, commit. `git rev-parse main > .marestail/runs/t/start-commit`.
   Plan `judge PASS`, `code`, `judge PASS`, `worker coder`. Run `marestail run tasks/t.md --from visual --to visual --auto --retries 1`.
   Expected: `   verdict BOUNCE` after the first `== visual (` though the stub wrote PASS; roles in order visual, coder,
   visual, coder; `$PROMPTS/02.txt` (coder) has `[FAIL] visual` and `desktop: #widget is 1408px wide, .col is 572px (base: 440px)`;
   no `repeated the same findings`; last line `pipeline stopped at visual`, exit 1.
   Clean up: `git reset -q --hard $KEEP; rm .marestail/runs/t/start-commit`. `git rev-parse HEAD` prints `$KEEP` and `git grep -c 1408` prints nothing.
9a. Save the stub dandelion from `tools/test-timeline.py` (`stub_dandelion`) as `$W/dandelion`, `chmod +x`. Write
   `0 gpt-9 cursor` twice to `$W/dplan`; `: > $W/calls`. Plan `limit`, `judge PASS`, `worker qa`. Run
   `MARESTAIL_DANDELION=$W/dandelion PLAN=$W/dplan CALLS=$W/calls marestail run tasks/t.md --from visual --to qa --model dandelion/route --auto --retries 2`.
   Expected: `$ARGS/01.txt` starts `claude` with `--model claude-fable-5-1`; stdout has
   `visual: claude-fable-5-1 is out of usage; judging with dandelion/route`, then `   dandelion/route: gpt-9 cursor`;
   `$ARGS/02.txt` and `03.txt` start with `cursor-agent` and do not contain `claude-fable-5-1`; `wc -l < $W/calls` prints 2; exit 0.
10. Add `judge_model = "claude-opus-5-5"` under `[visual]`. Plan `judge PASS`; run `marestail run tasks/t.md --from visual --to visual --auto`.
    Expected: `$ARGS/01.txt` has `--model claude-opus-5-5`. Remove the line.
11. Plan `judge PASS`, `worker qa`; run step 3's command three times: with the block removed from `qa/t.md`, then with
    `enabled = false`, then with the `[visual]` section removed (restore between runs).
    Expected, in order: `visual: no block in qa/t.md; skipping`; `visual disabled in marestail.toml; skipping`; no line
    containing `visual` at all. Each time 2 prompts, exit 0.
12. With `[visual]` still removed, run `marestail run tasks/t.md --from visual --auto`.
    Expected: exit 1, stderr `unknown role visual; choose from specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa`.
13. Restore `[visual]`. Plan `limit`, `judge PASS`; run `marestail run tasks/t.md --from hardener --to hardener --auto`.
    Expected: `rate limited; waiting 0 min before retrying`, no `out of usage`.
14. In `$M`: `python3 tools/test-visual-judge.py`. Expected: exit 0, last line `visual judge ok`.
    `cat roles/visual.md` starts `You are the visual judge.`; README's pipeline table has a `visual` row between
    `hardener` and `qa`, and a paragraph on `judge_model` and the fallback.
