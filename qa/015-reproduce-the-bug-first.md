# QA procedure: a bug task is reproduced before the specifier runs

Needs Chromium via Playwright (013 QA step 2). `$M` is the marestail-green checkout with `.venv` active and `$M/bin`
first on PATH. Work in `$T=/tmp/mt-repro`, wrappers in `$W=/tmp/mt-repro-bin`. Run every `marestail` command in `$T`.

1. Build the fixture as in 013 QA step 1, with `viewports = { desktop = "1440x900" }`, `[practices] enabled = false`,
   `[perf] enabled = false`. In `donate.html` replace the `#widget` div with
   `<iframe id="widget" src="/embed.html" style="width:440px;height:200px;border:0;border-radius:12px"></iframe>`
   and give the column `class="col block-full"`. Add `embed.html`:
   `<html><body style="margin:0"><form id="form" style="width:425px;height:180px;background:teal">Give</form></body></html>`.
   Write `tasks/t.md` as the five lines of the feature's Background (`where: /donate.html`, `symptom: ...`, `selector: #widget`).
   Commit on `main`, then `git checkout -q -b work`.
   Expected: `git status --porcelain` is empty.
2. Make the wrapper of 014 QA step 2 (`$W/claude`, `$W/kilo`, exports, `$ARGS`, `$PROMPTS`). Before each step empty
   `$ARGS`, `$PROMPTS` and `.marestail/`, and write the step's plan to `$W/plan`.
   Expected: `ls -l $W` shows the symlinks.
3. Plan: `specify observed`, `judge PASS`. Run `marestail run tasks/t.md --to critic --auto`.
   Expected: `== reported (/donate.html at the start commit)` and `reported desktop: .marestail/runs/t/visual/reported/desktop`
   print before `== specifier (`; exit 0.
4. Open `.marestail/runs/t/visual/reported/desktop/element.png`.
   Expected: the teal form with a round top-left corner and a square top-right corner, and a 15px gap on its right inside the frame.
5. `cat .marestail/runs/t/visual/reported/desktop/frame.json`.
   Expected: `"url": "http://localhost:3401/embed.html"`, `"width": 440`, `"height": 200`, largest `form#form` with box `0,0 425x180`.
6. Read `$PROMPTS/01.txt` (the specifier).
   Expected: `# Reported` right after the task; the symptom line; the two `.png` paths; the `geometry.json` with
   `"width": 440`; the markup with `<div class="col block-full" ...>` above the iframe; `## Frame` ending
   `the largest visible element is form#form at 0,0 425x180`; the last line starts `Write ## Observed`.
7. Read `$PROMPTS/02.txt` (the critic).
   Expected: `# Reported`, then `# Observed` holding `- radius does not clip: does not hold, left corner round at 434,100`.
8. `git status --porcelain; git log --name-only --format= | grep -c '^.marestail/'`.
   Expected: empty output, then `0`.
9. Plan: `specify`, `specify observed`, `judge PASS`. Run with `--retries 2`.
   Expected: `missing ## Observed section in .marestail/handoffs/t/01-specifier.md`; `$PROMPTS/02.txt` is the specifier
   again with that line under `# Why the work came back to you`; `== reported (` printed once; exit 0.
10. Change `tasks/t.md` to `where: /nope.html`. Run step 3's command.
    Expected: `reported: /nope.html answered 404`, then `pipeline stopped before specifier: the bug could not be reproduced`;
    `echo $?` prints 1; `ls $PROMPTS` is empty. Restore `where: /donate.html`.
11. Change `selector:` to `#gone`. Run step 3's command.
    Expected: `reported: desktop: #gone not found at base`, the stop line, exit 1. Restore `#widget`.
12. Plan as step 3; run with `--agent kilo --model x`.
    Expected: `$PROMPTS/01.txt` has `The pictures could not be shown: kilo cannot read images. Judge from the geometry and markup alone.`
    and `form#form at 0,0 425x180`; `grep -c png $PROMPTS/01.txt` prints 0.
13. Rerun step 3 (plan `specify observed`, `judge PASS`), then run `MARESTAIL_TASK=t marestail gate --tier qa --only visual`.
    Expected: `.marestail/runs/t/visual/reported/desktop/viewport.png` still exists.
14. Remove the `where:` line from `tasks/t.md`; plan `specify`, `judge PASS`; run step 3's command.
    Expected: `== specifier (` then `== critic (` print and no line starts `== reported` or `reported`;
    `echo $?` prints 0; `.marestail/runs/t/visual/reported` does not exist. Restore the `where:` line.
15. Change `selector:` to `iframe` and add `<iframe id="pixel" src="/embed.html" style="width:1px;height:1px;border:0"></iframe>`
    right after `<main>` in `donate.html` (commit it). Plan as step 3; run step 3's command.
    Expected: exit 0; `reported/desktop/geometry.json` has box `434,100 440x200`; `markup.html` has no `pixel`.
    Undo both changes (commit).
16. Set the iframe's `src` to `/missing.html` (commit). Plan as step 3; run step 3's command.
    Expected: exit 0; `$PROMPTS/01.txt` has `Loaded alone: http://localhost:3401/missing.html answered 404`. Undo (commit).
17. `grep -n 'reproduced first' $M/README.md; grep -n 'where:' $M/tasks/README.md $M/templates/tasks-README.md`.
    Expected: one hit in the Pipeline section; `where: /donate.html` in both task READMEs.
18. `cd $M && python3 tools/test-reproduce-first.py`.
    Expected: exit 0, last line `reproduce first ok`.
