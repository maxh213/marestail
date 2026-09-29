Feature: marestail captures the real page at base and HEAD and fails when the layout moved

  Pinned rules (every scenario relies on them):
  - Task: `marestail visual capture <task>` takes the task stem; the `visual` gate reads it from
    `MARESTAIL_TASK` (the runner sets it, task 006). The block is the first fenced block whose info
    string is `visual` in `qa/<task>.md`, one `key: value` per line; lists are comma-separated.
    No `qa/<task>.md` at all: `visual: no qa/<task>.md; skipped`, like a missing block.
  - Block keys. Required: `route`, `selector`. Optional, with defaults: `scroll` false, `wait` none
    (only `selector` is waited for), `styles` none, `inside` none (check off), `unchanged` none,
    `must_not_change` none, `symptom` none. `symptom` is free text for the human: never a finding; when the gate
    fails it is the last entry of `Result.findings`, as the detail line `  symptom: <text>`.
  - Malformed input fails before any app or worktree starts, one finding each:
      `qa/<task>.md visual block: missing <key>` / `qa/<task>.md visual block: unknown key <key>`
      `qa/<task>.md visual block: unknown measure <m> in unchanged`
      `[visual] viewports: bad viewport <name> = "<value>"; expected WxH[@scale][ touch]`
    Order: missing keys (`route` then `selector`), unknown keys (block order), unknown measures (block
    order), bad viewports (config order). `marestail visual capture` prints the same lines and exits 2.
  - Tools: missing `node` on PATH fails with the one finding `node not found on PATH; run marestail install`;
    missing Playwright Chromium with `Playwright Chromium missing; run marestail install`. Capture exits 2.
  - Base commit: exactly `perf.trees.start_commit` (recorded `.marestail/runs/<task>/start-commit`,
    else `git merge-base <[git] base> HEAD`). Only `marestail visual capture` prints its fallback note (first
    line); the gate neither prints it nor puts it in `Result.findings`. The baseline is a
    detached worktree added and removed through the same code the perf judge uses: `marestail/` holds
    exactly one `git worktree add` call site. HEAD is the working tree itself.
  - Order: base first, then HEAD; never both apps at once. Base: `setup` (worktree only, `setup_timeout`
    default 900 s), start app, capture, stop app. Then HEAD: start app, capture, stop app. A base failure
    (setup, app) does not skip HEAD: HEAD is still captured and its files kept, but no comparison findings
    are made; the findings are the failure(s) alone. The worktree is removed after both.
  - Apps: through `_serve.ready_app`, unchanged. `start` (default `[qa] start`) runs through `bash -lc`
    with `PORT` and `[visual] env`, in the tree's root. HEAD prefers `port` (default 3400), base prefers
    `port + 1`; either falls back to a random free port, exactly as `[qa]` does today (`_serve` and `[qa]`
    port handling do not change). `ready` (default `/`) must answer below 500 within `ready_timeout`
    (default `[qa] ready_timeout`, else 180) seconds.
  - Loads: per tree and viewport, first one warm-up load of the route whose result is thrown away, then the
    captures (two under the gate, one by hand). Every load, warm-up included, is a fresh browser context, so with
    the static app each tree's `app.log` has 3 lines containing `GET <route>` per viewport under the gate and 2 by
    hand. `app.log` and `setup.log` are rewritten on every run.
  - One capture: open the route, wait for the selector; if `scroll` is true call
    `scrollIntoView({block: "center", inline: "nearest"})` on it, else the page stays at scroll 0,0; wait for
    `wait`; wait for two agreeing box reads; then read the geometry and take the pictures.
  - Coordinates: every box (`box`, `inside.box`, `must_not_change`, and the boxes used for overlaps) is in
    document coordinates, `getBoundingClientRect()` plus `scrollX`/`scrollY`, so the scroll position never
    changes a number. `viewport.png` is the viewport as scrolled. `element.png` is a full-page screenshot
    clipped to the box plus 24px each side (clamped to the document), so it shows the element even below the fold.
  - Waits: the selector, `wait` and two agreeing box reads (200 ms apart) each get `capture_timeout`
    (default 30) seconds. A selector not there by then is "not found".
    A selector not found, a `wait` that never matched or a box that did not settle in a tree ends that
    capture and that viewport's judging: its one finding (first of these to happen) stands alone for the
    tree, the capture is not repeated, and no unstable, scroll, inside, overlap, `unchanged` or
    `must_not_change` finding is made for that viewport. The capture still writes `viewport.png` and
    `geometry.json` (`box` = the last box read, null when not found); `element.png` only when a box was read.
    An `inside` or `must_not_change` selector not found skips only its own rule.
  - Files, per tree (`base`, `head`) and viewport name, under `.marestail/runs/<task>/visual/<tree>/<viewport>/`:
    `element.png`, `viewport.png`, `geometry.json`. Per tree: `.marestail/runs/<task>/visual/<tree>/app.log`;
    base also `setup.log`. Kept whatever the result.
  - `geometry.json` keys: `viewport` {width, height, scale, touch}, `selector`, `box` {x, y, width, height}
    or null, `styles` {name: computed value}, `scrollWidth`, `clientWidth`, `inside` {selector, box},
    `must_not_change` {selector: box or null}, `overlaps` [names], `errors` [page error messages],
    `scrollY` (the page's scroll position when the geometry was read).
    Numbers are CSS px rounded with JavaScript `Math.round`.
  - Overlap candidates: the children of each ancestor of the element up to `body` (its siblings and its
    ancestors' siblings), minus the element's own ancestors. Left out: `display: none`, `visibility: hidden`
    (so every `hide`d element), and zero width or height. Overlap means an intersection of positive area.
    Named `<tag>#<id>`, else `<tag>.<first class>`, else `<tag>`.
  - Inside rule, at HEAD alone: if the element is wider than `inside` by more than `tolerance_px`, one
    width finding; otherwise one finding per side (left, right) where its edge is outside the `inside` box
    by more than `tolerance_px`. Top and bottom are not checked: containers grow with their content.
  - `desktop = "1440x900"` is 1440x900, scale 1, no touch; `phone = "390x844@2 touch"` is 390x844, scale 2, touch.
  - `hide` selectors get `visibility: hidden !important`. Each `block` pattern is matched against the full
    request URL with Python `fnmatch.fnmatchcase` semantics (`*` and `?` match any characters, `/` included),
    so `*tracker*` matches `http://localhost:3400/tracker.js`; any match aborts the request. An aborted
    request is neither a page error nor a finding. Animations and transitions are off. Page errors are
    recorded, never a finding.
  - The gate captures each tree twice per viewport, each in a fresh browser context, and compares `box`,
    `scrollWidth`, `clientWidth`, `inside.box` and `must_not_change` (numbers within `tolerance_px`) and
    `overlaps` (exactly). `styles`, `errors` and `viewport` are not compared. The kept files are from the
    first capture. When a tree is unstable in a viewport, that viewport gets the `unstable` finding alone:
    no scroll, inside, overlap, `unchanged` or `must_not_change` finding is made for it. `marestail visual capture` captures once and judges nothing.
  - Gate name `visual`, tier `qa`, listed only when `[visual]` exists. Pass summary:
    `<n> viewport(s), geometry holds` (`1 viewport, geometry holds`, `2 viewports, geometry holds`).
    Findings and detail lines: `Result.findings` stays a flat `list[str]`, one entry per printed line.
    An entry starting with two spaces is a detail line and belongs to the finding before it (the symptom
    line belongs to the result). Every other entry is a finding. `<n>` counts findings only, and so do
    "the only finding", "the findings are exactly" and "<n> findings" below; detail lines are named
    separately. Order: setup and app findings (base, then HEAD); then per viewport in config order:
    not-found (selector, inside, must_not_change; base before HEAD), wait, settle, unstable, scroll, inside,
    overlaps, `unchanged` (block order), `must_not_change` (block order); then the symptom line.
    Fail summary: `<n> visual findings`. `<tree>` in texts is `base` or `HEAD`. Finding texts (tolerance default 2):
      `<vp>: <sel> not found at <tree>`
      `<vp>: inside <sel> not found at <tree>` / `<vp>: must_not_change <sel> not found at <tree>`
      `<vp>: wait <wait> never matched at <tree> within <n>s`
      `<vp>: <sel> box did not settle at <tree> within <n>s`
      `<vp>: page scrolls sideways at HEAD, scrollWidth <s>px > clientWidth <c>px (base: scrollWidth <b>px)`
      `<vp>: <sel> is <w>px wide, <inside> is <iw>px (base: <bw>px)`
      `<vp>: <sel> <left|right> edge is <e>px, <inside> <left|right> edge is <ie>px (base: <be>px)`
      `<vp>: <sel> overlaps <sibling> at HEAD (base: no overlap)`
      `<vp>: <sel> <measure> moved <d>px (base: <b>px, HEAD: <h>px)`
      `<vp>: must_not_change <sel> changed (base: <x>,<y> <w>x<h>; HEAD: <x>,<y> <w>x<h>)`
      `<vp>: unstable at <tree>: two captures gave different geometry`, with detail lines
      `  first: <json>` and `  second: <json>` (each `geometry.json` record on one line)
      `<tree>: app did not answer on http://localhost:<p><ready> within <n>s`
      `<tree>: app exited with <n> before answering`
      `base: setup failed (exit <n>)` / `base: setup did not finish within <n>s`, with detail lines
      `  <line>` for the last 10 lines of `setup.log`; app findings likewise for the last 10 lines of `app.log`
    `unchanged` measures: `x-centre`, `y-centre`, `left`, `right`, `top`, `bottom`, `width`, `height`.

  Background:
    Given a git repo on branch `main` with `[git] base = "main"` and `.marestail/` in `.gitignore`
    And a static app served by `start = "python3 -m http.server $PORT --bind 127.0.0.1"`
    And `index.html` is `<html><body>home</body></html>`
    And `donate.html` at the base commit is, with `body` margin 0 and no scrollbars:
      """
      <header id="top" style="height:60px">Top</header>
      <main><div class="col" style="width:572px;margin:0 auto">
        <h1 style="margin:0;height:40px">Donate</h1>
        <div id="widget" style="width:440px;height:200px">w</div>
        <p id="text" style="margin:0">Page text</p>
      </div></main>
      """
    And `marestail.toml` has `[qa] cmd = "true"` and:
      """
      [visual]
      enabled = true
      start = "python3 -m http.server $PORT --bind 127.0.0.1"
      ready = "/"
      port = 3400
      viewports = { desktop = "1440x900" }
      tolerance_px = 2
      """
    And `qa/t.md` holds the block:
      """
      route: /donate.html
      selector: #widget
      scroll: true
      wait: #widget
      styles: border-radius, overflow, width
      inside: .col
      unchanged: x-centre
      must_not_change: header
      """
    And branch `work` is checked out from `main` with one commit that changes `donate.html` as each scenario says
    And no `.marestail/runs/t/start-commit` exists

  Scenario: an element that stays put passes and leaves the pictures
    Given the HEAD commit only changes `Page text` to `Other text`
    And `viewports = { desktop = "1440x900", phone = "390x844@2 touch" }`
    When `MARESTAIL_TASK=t marestail gate --tier qa --only visual` runs
    Then the output has `[ok  ] visual` with summary `2 viewports, geometry holds` and ends `GATE PASSED`
    And each of `base/desktop`, `base/phone`, `head/desktop`, `head/phone` under `.marestail/runs/t/visual/`
      holds exactly `element.png`, `viewport.png` and `geometry.json`
    And `head/desktop/geometry.json` has `box` {x: 434, y: 100, width: 440, height: 200},
      `styles` {border-radius: "0px", overflow: "visible", width: "440px"}, `scrollWidth` 1440,
      `clientWidth` 1440, `inside` {selector: ".col", box: {x: 434, y: 60, width: 572, height: <n>}},
      `overlaps` [], `scrollY` 0 and `viewport` {width: 1440, height: 900, scale: 1, touch: false}
    And `base/app.log` and `head/app.log` each have exactly 6 lines containing `GET /donate.html`
      (per viewport: 1 warm-up and 2 captures)
    And `head/phone/geometry.json` has `viewport` {width: 390, height: 844, scale: 2, touch: true}

  Scenario: the element grows wider than its column
    Given the HEAD commit sets `#widget` to `width:1408px`
    When the gate runs as above
    Then it fails with summary `3 visual findings` and the entries are exactly, in order:
      `desktop: page scrolls sideways at HEAD, scrollWidth 1842px > clientWidth 1440px (base: scrollWidth 1440px)`,
      `desktop: #widget is 1408px wide, .col is 572px (base: 440px)` and
      `desktop: #widget x-centre moved 484px (base: 654px, HEAD: 1138px)`
    And both `base/desktop/element.png` and `head/desktop/element.png` still exist

  Scenario: something else makes the page scroll sideways
    Given the HEAD commit adds `<div style="width:1600px">wide</div>` after `.col` inside `main`
    When the gate runs
    Then the only finding is `desktop: page scrolls sideways at HEAD, scrollWidth 1600px > clientWidth 1440px (base: scrollWidth 1440px)`

  Scenario: the element now lies over a sibling
    Given the HEAD commit sets `#widget` to `width:440px;height:200px;margin-bottom:-120px`
    When the gate runs
    Then the only finding is `desktop: #widget overlaps p#text at HEAD (base: no overlap)`

  Scenario: a hidden element is not counted as overlapping
    Given the HEAD commit adds `<div id="banner" style="position:absolute;top:0;left:0;width:100%;height:1000px">b</div>` inside `.col`
    When the gate runs
    Then it fails with a finding `desktop: #widget overlaps div#banner at HEAD (base: no overlap)`
    When `[visual] hide = ["#banner"]` is added and the gate runs again
    Then `visual` passes

  Scenario: an unchanged measure moves beyond the tolerance
    Given the HEAD commit sets `#widget` to `width:440px;height:200px;margin-left:40px`
    When the gate runs
    Then the only finding is `desktop: #widget x-centre moved 40px (base: 654px, HEAD: 694px)`

  Scenario Outline: a must_not_change box
    Given the HEAD commit sets `header` to `height:<h>px`
    When the gate runs
    Then `visual` <result>

    Examples:
      | h  | result                                                                                              |
      | 62 | passes (2px is within `tolerance_px = 2`)                                                           |
      | 90 | fails with the finding `desktop: must_not_change header changed (base: 0,0 1440x60; HEAD: 0,0 1440x90)` |

  Scenario: the selector is missing at HEAD
    Given the HEAD commit renames `id="widget"` to `id="widget2"`
    When the gate runs
    Then the only finding is `desktop: #widget not found at HEAD`
    And `head/desktop/geometry.json` has `box` null and `head/desktop/element.png` does not exist

  Scenario: a page whose layout differs between loads is unstable
    Given the HEAD commit adds `<script>document.getElementById("widget").style.width=(400+Math.floor(Math.random()*1000))+"px"</script>` after `#widget`
    When the gate runs
    Then the only finding is `desktop: unstable at HEAD: two captures gave different geometry`
    And it is followed by exactly two detail lines, starting `  first: {` and `  second: {`, whose `box.width` values differ
    And no scroll, inside or x-centre finding is made, whatever widths the two loads drew
    And test-visual may rerun this case once when the two loads drew widths within `tolerance_px` of each other

  Scenario: the element sticks out of its column without being wider
    Given the HEAD commit sets `#widget` to `width:440px;height:200px;margin-left:200px`
    When the gate runs
    Then the findings are exactly `desktop: #widget right edge is 1074px, .col right edge is 1006px (base: 874px)`
      and `desktop: #widget x-centre moved 200px (base: 654px, HEAD: 854px)`

  Scenario: an overlapping element outside the element's parent counts
    Given the HEAD commit adds `<p id="aside" style="position:absolute;top:150px;left:500px;margin:0">aside</p>` as the first child of `main`
    When the gate runs
    Then the only finding is `desktop: #widget overlaps p#aside at HEAD (base: no overlap)`

  Scenario: on a tall page the scroll position changes no number
    Given the `main` commit also has `<div style="height:2000px"></div>` as the first child of `main`, so both trees have it
    And the block says `unchanged: x-centre, y-centre` and keeps `scroll: true` and `must_not_change: header`
    And the HEAD commit sets `#widget` to `width:440px;height:200px;margin-top:300px`
    When the gate runs
    Then the only finding is `desktop: #widget y-centre moved 300px (base: 2200px, HEAD: 2500px)`
    And `base/desktop/geometry.json` has `box` {x: 434, y: 2100, width: 440, height: 200}, `inside` box y 2060,
      `must_not_change` {header: {x: 0, y: 0, width: 1440, height: 60}} and `scrollY` greater than 1000
    And `head/desktop/geometry.json` has `box` y 2400, the same `header` box and `scrollY` greater than 1000
    When the block says `scroll: false` and the gate runs again
    Then the findings are the same, both `geometry.json` have `scrollY` 0 and the same boxes as before
    And `head/desktop/element.png` is 488x248 pixels (the 440x200 box plus 24px each side)

  Scenario: a named selector the page does not have
    Given the HEAD commit only changes `Page text` to `Other text`
    And the block says `inside: .column` and `must_not_change: header, nav`
    When the gate runs
    Then the findings are exactly `desktop: inside .column not found at base`, `desktop: inside .column not found at HEAD`,
      `desktop: must_not_change nav not found at base` and `desktop: must_not_change nav not found at HEAD`

  Scenario: wait never matches, and a box that never settles
    Given `[visual] capture_timeout = 2` and the block says `wait: #never`
    When the gate runs on the "stays put" change
    Then the findings are exactly `desktop: wait #never never matched at base within 2s` and `desktop: wait #never never matched at HEAD within 2s`
    Given `wait: #widget` again and the HEAD commit adds `<script>setInterval(()=>{const w=document.getElementById("widget");w.style.marginLeft=(parseInt(w.style.marginLeft||0)+10)%100+"px"},50)</script>`
    When the gate runs
    Then the only finding is `desktop: #widget box did not settle at HEAD within 2s`, with no detail line
    And `head/desktop/` holds `element.png`, `viewport.png` and `geometry.json`, whose `box` is not null

  Scenario Outline: malformed input fails before anything starts
    Given <change>
    When the gate runs
    Then the only finding is `<finding>` and no app, worktree or browser starts
    And `marestail visual capture t` prints the same line and exits 2

    Examples:
      | change                                          | finding                                                                        |
      | the block has no `selector` line                | qa/t.md visual block: missing selector                                         |
      | the block has `colour: red`                     | qa/t.md visual block: unknown key colour                                       |
      | the block says `unchanged: middle`              | qa/t.md visual block: unknown measure middle in unchanged                      |
      | `viewports = { phone = "390by844" }`            | [visual] viewports: bad viewport phone = "390by844"; expected WxH[@scale][ touch] |

  Scenario: symptom is shown to the human but judges nothing
    Given the block adds `symptom: the widget runs over the page text` and the HEAD commit sets `#widget` to `width:1408px`
    When the gate runs
    Then the summary is `3 visual findings`, the three findings are those of "the element grows wider than its column"
    And the last entry is the detail line `  symptom: the widget runs over the page text`

  Scenario: node or Chromium missing
    Given `node` is not on PATH
    When the gate runs
    Then the only finding is `node not found on PATH; run marestail install` and `marestail visual capture t` exits 2
    Given `node` is on PATH and `PLAYWRIGHT_BROWSERS_PATH` points at an empty directory
    When the gate runs
    Then the only finding is `Playwright Chromium missing; run marestail install`

  Scenario: a missing QA file is skipped like a missing block
    Given `qa/t.md` does not exist
    When `MARESTAIL_TASK=t marestail gate --tier qa --only visual` runs
    Then `visual` is ok with summary exactly `visual: no qa/t.md; skipped`

  Scenario: a page error is recorded but is not a finding
    Given the HEAD commit adds `<script>throw new Error("boom")</script>` at the end of `body`
    When the gate runs
    Then `visual` passes and `head/desktop/geometry.json` `errors` has one entry containing `boom`

  Scenario: blocked URLs are never requested
    Given the HEAD commit adds `<script src="/tracker.js"></script>` and `[visual] block = ["*tracker*"]`
    When the gate runs
    Then `.marestail/runs/t/visual/head/app.log` has no line containing `/tracker.js`
    And `visual` passes and `head/desktop/geometry.json` `errors` is empty

  Scenario: a block pattern that matches nothing blocks nothing
    Given the HEAD commit adds `<script src="/tracker.js"></script>` and `[visual] block = ["*nomatch*"]`
    When the gate runs
    Then `.marestail/runs/t/visual/head/app.log` has a line containing `GET /tracker.js`

  Scenario: an app that never answers fails for both trees and leaves nothing behind
    Given `[visual] start = "sleep 60"` and `ready_timeout = 2`, and ports 3400 and 3401 are free
    When the gate runs
    Then the findings are exactly `base: app did not answer on http://localhost:3401/ within 2s`
      and `HEAD: app did not answer on http://localhost:3400/ within 2s`, in that order
    And afterwards `git worktree list` has exactly one line and nothing listens on 3400 or 3401

  Scenario: an app that exits before answering
    Given `[visual] start = "echo dying; exit 3"`
    When the gate runs
    Then the findings are exactly `base: app exited with 3 before answering` and
      `HEAD: app exited with 3 before answering`, each followed by the detail line `  dying`

  Scenario: setup runs in the baseline worktree and its failure is reported
    Given `[visual] setup = "echo installing; exit 1"` and the "stays put" change
    When the gate runs
    Then the only finding is `base: setup failed (exit 1)`, followed by the detail line `  installing`
    And `.marestail/runs/t/visual/base/setup.log` contains `installing`
    And `head/desktop/geometry.json` exists and `base/desktop/` does not
    When `setup = "sleep 60"` and `setup_timeout = 1`
    Then the only finding is `base: setup did not finish within 1s`

  Scenario: cleanup after pass, failure and Ctrl-C
    When the gate passes, when it fails, and when `SIGINT` is sent to it after `head/app.log` exists
    Then each time `git worktree list` afterwards has exactly one line, no directory under `.marestail/`
      holds a checkout of the base commit, and nothing listens on either app's port

  Scenario: env reaches both apps and is never committed
    Given `[visual] env = { CMS_URL = "https://cms.example.test/graphql" }` and `start = "echo $CMS_URL; python3 -m http.server $PORT --bind 127.0.0.1"`
    When the gate runs
    Then `base/app.log` and `head/app.log` both contain `https://cms.example.test/graphql`
    And `git status --porcelain` prints exactly ` M marestail.toml` and `git rev-parse HEAD` is what it was before the gate
    And no file outside `.marestail/` other than `marestail.toml` contains `cms.example.test` or `localhost:34`,
      and no file outside `.marestail/` ends in `.png`

  Scenario: start falls back to [qa] start
    Given `[visual]` has no `start` and `[qa] start = "python3 -m http.server $PORT --bind 127.0.0.1"`
    When the gate runs on the "stays put" change
    Then `visual` passes

  Scenario: a task with no visual block is skipped
    Given `qa/t.md` has no `visual` block
    When `marestail visual capture t` runs
    Then it prints exactly `visual: no block in qa/t.md; skipped`, exits 0 and starts no app and no worktree
    And `MARESTAIL_TASK=t marestail gate --tier qa --only visual` shows `visual` ok with summary exactly `visual: no block in qa/t.md; skipped`

  Scenario: the gate needs a task
    Given `MARESTAIL_TASK` is unset
    When `marestail gate --tier qa --only visual` runs
    Then `visual` is ok with summary exactly `skipped: no task; set MARESTAIL_TASK`

  Scenario: [visual] enabled = false skips
    Given `[visual] enabled = false`
    When the gate runs
    Then `visual` is ok with summary exactly `skipped: [visual] enabled = false` and no app starts

  Scenario: capture by hand writes the pictures and runs no checks
    Given the HEAD commit sets `#widget` to `width:1408px`
    When `marestail visual capture t` runs
    Then it prints `no recorded start commit for t; using git merge-base main HEAD`, exits 0
    And `head/app.log` has exactly 2 lines containing `GET /donate.html` (1 warm-up, 1 capture)
    And prints one line per tree and viewport: `base desktop: .marestail/runs/t/visual/base/desktop` and `head desktop: .marestail/runs/t/visual/head/desktop`
    And the three files exist in both folders and afterwards `git worktree list` has exactly one line

  Scenario: targets without [visual] are unchanged
    Given `marestail.toml` has no `[visual]` section
    When `marestail gate --tier qa` runs
    Then no result line names `visual` and the gate lines are exactly those printed before this task
    And `marestail install <target>` into a target without `[visual]` never runs `npm` or `npx` in `marestail/js`

  Scenario: install fetches Playwright and Chromium only when [visual] is enabled
    Given a target whose `marestail.toml` has `[visual] enabled = true`
    And fake `npm` and `npx` first on PATH that each append `<name> <argv> @ <cwd>` to a log and exit 0
    When `marestail install <target>` runs
    Then the log is exactly two lines: `npm install @ <marestail checkout>/marestail/js`
      then `npx playwright install chromium @ <marestail checkout>/marestail/js`
    And `marestail/js/package.json` lists `playwright` under `dependencies` and nothing else
    And with `enabled = false`, or no `[visual]`, the log stays empty
    Given the fake `npx` exits 1
    Then install prints `npx playwright install chromium failed (exit 1); everything else is installed` and exits 1

  Scenario: the perf judge and its worktrees are unchanged
    When `python3 tools/test-perf.py` runs
    Then it exits 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`, as before this task
    And `grep -rn '"worktree", "add"' marestail/` finds exactly one line

  Scenario: QA stays a worker and a visual failure stops the run
    Given the "wider than its column" change and a stub QA agent that writes its handoff with `ran-against: app` and changes nothing else
    When `marestail run tasks/t.md --from qa --to qa --auto` runs (default `--retries 0`)
    Then stdout has `== qa (` ... `) attempt 1`, `attempt 2` and `attempt 3`, and no `attempt 4`
    And each attempt's verification gate shows `[FAIL] visual`
    And stdout has `qa got the same problems back 3 times in a row; the worker is not making progress, stopping for a human`
      then `pipeline stopped at qa`, does not contain `pipeline complete`, and the exit code is 1

  Scenario: roles and README
    Then `roles/specifier.md` contains `When the task changes what a user sees, add a fenced visual block to qa/<task>.md.`
    And `roles/critic.md` contains `Bounce a spec that changes what a user sees and has no visual block in qa/<task>.md.`
    And README documents the `[visual]` keys, the `visual` block keys and `.marestail/runs/<task>/visual/<tree>/<viewport>/`

  Scenario: the diagnostic scripts pass
    When `python3 tools/test-visual.py` runs
    Then it exits 0 with last line `visual ok`
    And every other `tools/test-*.py` exits as it did before this task
