Feature: marestail captures the real page at base and HEAD and fails when the layout moved

  Pinned rules (every scenario relies on them):
  - Task: `marestail visual capture <task>` takes the task stem; the `visual` gate reads it from
    `MARESTAIL_TASK` (the runner sets it, task 006). The block is the first fenced block whose info
    string is `visual` in `qa/<task>.md`, one `key: value` per line; lists are comma-separated.
  - Base commit: exactly `perf.trees.start_commit` (recorded `.marestail/runs/<task>/start-commit`,
    else `git merge-base <[git] base> HEAD`), printing its note when it falls back. The baseline is a
    detached worktree added and removed through the same code the perf judge uses: `marestail/` holds
    exactly one `git worktree add` call site. HEAD is the working tree itself.
  - Apps: `start` (default `[qa] start`) runs through `bash -lc` with `PORT` and `[visual] env`, in the
    tree's root, as `[qa] start` does today. HEAD uses `port` (default 3400), or the next free port;
    base uses the first free port above HEAD's. `ready` (default `/`) must answer below 500 within
    `ready_timeout` (default 180) seconds. `setup` runs in the baseline worktree only, before its app.
  - Files, per tree (`base`, `head`) and viewport name, under `.marestail/runs/<task>/visual/<tree>/<viewport>/`:
    `element.png`, `viewport.png`, `geometry.json`. Per tree: `.marestail/runs/<task>/visual/<tree>/app.log`;
    base also `setup.log`. Kept whatever the result.
  - `geometry.json` keys: `viewport` {width, height, scale, touch}, `selector`, `box` {x, y, width, height}
    or null, `styles` {name: computed value}, `scrollWidth`, `clientWidth`, `inside` {selector, box},
    `must_not_change` {selector: box or null}, `overlaps` [sibling names], `errors` [page error messages].
    A sibling is named `<tag>#<id>`, else `<tag>.<first class>`, else `<tag>`. Numbers are whole px.
  - `desktop = "1440x900"` is 1440x900, scale 1, no touch; `phone = "390x844@2 touch"` is 390x844, scale 2, touch.
  - `hide` selectors get `visibility: hidden !important`; `block` globs abort matching requests;
    animations and transitions are off. Page errors are recorded, never a finding.
  - The gate captures each tree twice per viewport and compares the two records; the kept files are
    from the first. `marestail visual capture` captures once.
  - Gate name `visual`, tier `qa`, listed only when `[visual]` exists. Pass summary:
    `<n> viewport(s), geometry holds` (`1 viewport, geometry holds`, `2 viewports, geometry holds`).
    Fail summary: `<n> visual findings`. Finding texts (tolerance default 2):
      `<vp>: <sel> not found at base` / `<vp>: <sel> not found at HEAD`
      `<vp>: page scrolls sideways at HEAD, scrollWidth <s>px > clientWidth <c>px (base: scrollWidth <b>px)`
      `<vp>: <sel> is <w>px wide, <inside> is <iw>px (base: <bw>px)`
      `<vp>: <sel> overlaps <sibling> at HEAD (base: no overlap)`
      `<vp>: <sel> <measure> moved <d>px (base: <b>px, HEAD: <h>px)`
      `<vp>: must_not_change <sel> changed (base: <x>,<y> <w>x<h>; HEAD: <x>,<y> <w>x<h>)`
      `<vp>: unstable at <base|HEAD>: two captures gave different geometry`, followed by the two
      records as `first: <json>` and `second: <json>`
      `<base|HEAD>: app did not answer on http://localhost:<p><ready> within <n>s`
      `base: setup failed (exit <n>)`, followed by the last 10 lines of `setup.log`
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
      `overlaps` [] and `viewport` {width: 1440, height: 900, scale: 1, touch: false}
    And `head/phone/geometry.json` has `viewport` {width: 390, height: 844, scale: 2, touch: true}

  Scenario: the element grows wider than its column
    Given the HEAD commit sets `#widget` to `width:1408px`
    When the gate runs as above
    Then it fails with a finding exactly `desktop: #widget is 1408px wide, .col is 572px (base: 440px)`
    And a finding exactly `desktop: page scrolls sideways at HEAD, scrollWidth 1842px > clientWidth 1440px (base: scrollWidth 1440px)`
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
    Then it fails with a finding `desktop: unstable at HEAD: two captures gave different geometry`
    And the next two findings start `first: {` and `second: {` and their `box.width` values differ

  Scenario: a page error is recorded but is not a finding
    Given the HEAD commit adds `<script>throw new Error("boom")</script>` at the end of `body`
    When the gate runs
    Then `visual` passes and `head/desktop/geometry.json` `errors` has one entry containing `boom`

  Scenario: blocked URLs are never requested
    Given the HEAD commit adds `<script src="/tracker.js"></script>` and `[visual] block = ["*tracker*"]`
    When the gate runs
    Then `.marestail/runs/t/visual/head/app.log` has no line containing `/tracker.js`

  Scenario: an app that never answers fails and leaves nothing behind
    Given `[visual] start = "sleep 60"` and `ready_timeout = 2`
    When the gate runs
    Then it fails with a finding `base: app did not answer on http://localhost:<p>/ within 2s` or the HEAD equivalent
    And afterwards `git worktree list` has exactly one line and no process listens on 3400 or the base port

  Scenario: setup runs in the baseline worktree and its failure is reported
    Given `[visual] setup = "echo installing; exit 1"`
    When the gate runs
    Then it fails with a finding `base: setup failed (exit 1)` followed by `installing`
    And `.marestail/runs/t/visual/base/setup.log` contains `installing`

  Scenario: cleanup after pass, failure and Ctrl-C
    When the gate passes, when it fails, and when `SIGINT` is sent to it after `head/app.log` exists
    Then each time `git worktree list` afterwards has exactly one line, no directory under `.marestail/`
      holds a checkout of the base commit, and nothing listens on either app's port

  Scenario: env reaches both apps and is never committed
    Given `[visual] env = { CMS_URL = "https://cms.example.test/graphql" }` and `start = "echo $CMS_URL; python3 -m http.server $PORT --bind 127.0.0.1"`
    When the gate runs
    Then `base/app.log` and `head/app.log` both contain `https://cms.example.test/graphql`
    And `git status --porcelain` is empty and no tracked file contains `cms.example.test`, `.png` or `localhost:34`

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
    And prints one line per tree and viewport: `base desktop: .marestail/runs/t/visual/base/desktop` and `head desktop: .marestail/runs/t/visual/head/desktop`
    And the three files exist in both folders and afterwards `git worktree list` has exactly one line

  Scenario: targets without [visual] are unchanged
    Given `marestail.toml` has no `[visual]` section
    When `marestail gate --tier qa` runs
    Then no result line names `visual` and the gate lines are exactly those printed before this task
    And `marestail install <target>` into a target without `[visual]` never runs `npm`

  Scenario: install fetches Playwright only when [visual] is enabled
    Given a target whose `marestail.toml` has `[visual] enabled = true` and a fake `npm` first on PATH that records its argv and cwd
    When `marestail install <target>` runs
    Then `npm install` ran with cwd `<marestail checkout>/marestail/js`
    And `marestail/js/package.json` lists `playwright` under `dependencies` and nothing else
    And with `enabled = false` the fake `npm` is never called

  Scenario: the perf judge and its worktrees are unchanged
    When `python3 tools/test-perf.py` runs
    Then it exits 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`, as before this task
    And `grep -rn '"worktree", "add"' marestail/` finds exactly one line

  Scenario: QA stays a worker and a visual failure stops the run
    Given the "wider than its column" change and a stub QA agent that writes its handoff with `ran-against: app`
    When `marestail run tasks/t.md --from qa --to qa --auto` runs
    Then the qa verification gate shows `[FAIL] visual` and stdout does not contain `pipeline complete`
    And the run stops through QA's existing retry and repeat limit, with no new role

  Scenario: roles and README
    Then `roles/specifier.md` contains `When the task changes what a user sees, add a fenced visual block to qa/<task>.md.`
    And `roles/critic.md` contains `Bounce a spec that changes what a user sees and has no visual block in qa/<task>.md.`
    And README documents the `[visual]` keys, the `visual` block keys and `.marestail/runs/<task>/visual/<tree>/<viewport>/`

  Scenario: the diagnostic scripts pass
    When `python3 tools/test-visual.py` runs
    Then it exits 0 with last line `visual ok`
    And every other `tools/test-*.py` exits as it did before this task
