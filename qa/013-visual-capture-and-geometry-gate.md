# QA procedure: visual capture and geometry gate

Needs Chromium via Playwright (`marestail install` into a target with `[visual] enabled = true`, step 2).
`$M` is the marestail-green checkout with `.venv` active and `$M/bin` first on PATH. Work in `$T=/tmp/mt-visual`.

1. Build the fixture:
   `rm -rf $T && mkdir $T && cd $T && git init -q -b main && printf '.marestail/\n' > .gitignore`.
   Write `index.html` (`<html><body>home</body></html>`), `donate.html` exactly as in the feature Background
   (wrapped in `<html><body style="margin:0">…</body></html>`), `marestail.toml` with `[git] base = "main"`,
   `[qa] cmd = "true"` and the Background `[visual]` section, and `qa/t.md` holding the Background block in a
   fence with info string `visual`. Commit, then `git checkout -q -b work`.
   Expected: `git worktree list` prints one line.
2. `marestail install $T`.
   Expected: exit 0; `$M/marestail/js/node_modules/playwright` exists.
3. Change `Page text` to `Other text` in `donate.html`, commit. Run `MARESTAIL_TASK=t marestail gate --tier qa --only visual`.
   Expected: `[ok  ] visual` with `1 viewport, geometry holds`, then `GATE PASSED`. `ls .marestail/runs/t/visual/base/desktop .marestail/runs/t/visual/head/desktop`
   shows `element.png geometry.json viewport.png` in each. Open both `element.png`: the same 440px widget with a 24px margin.
   `head/desktop/geometry.json` has `"width": 440` in `box` and `"width": "440px"` in `styles`.
4. Add `phone = "390x844@2 touch"` to `viewports`, rerun step 3's gate.
   Expected: `2 viewports, geometry holds`; `head/phone/geometry.json` has `"scale": 2` and `"touch": true`.
   Remove `phone` again.
5. Set `#widget` to `width:1408px`, commit, rerun the gate.
   Expected: exit 1, `[FAIL] visual`, findings include
   `desktop: #widget is 1408px wide, .col is 572px (base: 440px)` and
   `desktop: page scrolls sideways at HEAD, scrollWidth 1842px > clientWidth 1440px (base: scrollWidth 1440px)`.
   Both `element.png` files still exist; `head/desktop/viewport.png` shows the widget running past the column.
6. `git revert --no-edit HEAD`. Add `<div style="width:1600px">wide</div>` after `.col` in `main`, commit, rerun.
   Expected: the only finding is `desktop: page scrolls sideways at HEAD, scrollWidth 1600px > clientWidth 1440px (base: scrollWidth 1440px)`.
7. Revert. Set `#widget` style to `width:440px;height:200px;margin-bottom:-120px`, commit, rerun.
   Expected: only finding `desktop: #widget overlaps p#text at HEAD (base: no overlap)`.
8. Revert. Set `#widget` style to `width:440px;height:200px;margin-left:40px`, commit, rerun.
   Expected: only finding `desktop: #widget x-centre moved 40px (base: 654px, HEAD: 694px)`.
9. Revert. Set `header` to `height:62px`, commit, rerun. Expected: passes.
   Amend it to `height:90px`, rerun. Expected: only finding `desktop: must_not_change header changed (base: 0,0 1440x60; HEAD: 0,0 1440x90)`.
10. Revert. Rename `id="widget"` to `id="widget2"`, commit, rerun.
    Expected: only finding `desktop: #widget not found at HEAD`; no `head/desktop/element.png`.
11. Revert. Add the random-width `<script>` from the feature after `#widget`, commit, rerun.
    Expected: finding `desktop: unstable at HEAD: two captures gave different geometry`, then `first: {…}` and `second: {…}` with different `box.width`.
12. Revert. Add `<div id="banner" style="position:absolute;top:0;left:0;width:100%;height:1000px">b</div>` inside `.col`, commit, rerun.
    Expected: finding `desktop: #widget overlaps div#banner at HEAD (base: no overlap)`. Add `hide = ["#banner"]` to `[visual]`, rerun. Expected: passes.
13. Revert and remove `hide`. Add `<script src="/tracker.js"></script>`, commit, set `block = ["*tracker*"]`, rerun.
    Expected: passes; `grep tracker .marestail/runs/t/visual/head/app.log` prints nothing.
14. Set `env = { CMS_URL = "https://cms.example.test/graphql" }` and `start = "echo $CMS_URL; python3 -m http.server $PORT --bind 127.0.0.1"`, rerun.
    Expected: both `base/app.log` and `head/app.log` contain the URL; `git status --porcelain` shows only `marestail.toml`; `git grep -l cms.example.test` finds only `marestail.toml` if you commit it (nothing marestail wrote).
15. Set `setup = "echo installing; exit 1"`, rerun.
    Expected: finding `base: setup failed (exit 1)` then `installing`; `base/setup.log` contains `installing`. Remove `setup`.
16. Set `start = "sleep 60"`, `ready_timeout = 2`, rerun.
    Expected: a finding `… app did not answer on http://localhost:<p>/ within 2s`; `git worktree list` one line; `ss -ltn | grep -E ':340[0-9]'` prints nothing.
17. Restore `start`, set `ready_timeout = 60`, run the gate in the background and send `kill -INT <pid>` once `.marestail/runs/t/visual/head/app.log` exists.
    Expected: gate exits; `git worktree list` one line; nothing listening on 3400 or the base port.
18. `marestail visual capture t`.
    Expected: first line `no recorded start commit for t; using git merge-base main HEAD`, then `base desktop: .marestail/runs/t/visual/base/desktop` and `head desktop: .marestail/runs/t/visual/head/desktop`; exit 0; no PASS/FAIL line.
19. Remove the block from `qa/t.md`. Run `marestail visual capture t`.
    Expected: exactly `visual: no block in qa/t.md; skipped`, exit 0. The gate shows `visual: no block in qa/t.md; skipped`.
20. Run the gate without `MARESTAIL_TASK`. Expected: `skipped: no task; set MARESTAIL_TASK`. Set `enabled = false`, rerun with it. Expected: `skipped: [visual] enabled = false`.
21. Delete the `[visual]` section; run `marestail gate --tier qa`. Expected: no line names `visual`.
22. From `$M`: `python3 tools/test-visual.py`. Expected: exit 0, last line `visual ok`.
    `python3 tools/test-perf.py`. Expected: exit 1, last line `verdict-commit-files: '' != 'perf/bench_x.py'`.
    `grep -rn '"worktree", "add"' marestail/`. Expected: one line.
23. `grep -n 'visual block' roles/specifier.md roles/critic.md` and read README's `[visual]` section.
    Expected: the two sentences from the feature; README names the `[visual]` keys, the block keys and `.marestail/runs/<task>/visual/<tree>/<viewport>/`.
