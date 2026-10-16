# 013 — marestail photographs the real page before and after a change, and fails when the layout moved

After this task, a run that changes how something looks is checked against the project's running app at the base commit and at HEAD. marestail takes the pictures and the measurements itself. A change that pushes an element out of its column, makes the page scroll sideways or lays one thing over another fails without anyone having to look.

On otwarteklatki/next-boilerplate#537 every gate passed and the widget went from 440px to 1408px wide on the real page, over the page text. One measurement of the element's box at base and at HEAD shows it. Nobody took one, because the tests ran against a hand-made layout and a fake third-party frame. A fixture built by the agent that holds a belief cannot falsify that belief; the check has to come from outside.

The perf judge is the precedent: it records a start commit, adds a detached worktree for the baseline, and the runner takes the measurements, not the agent. Reuse that. Task 006 added starting the app; reuse that too. The browser script follows `~/workspace/animus-harness/animus_harness/js/axe.mjs`.

## What changes

**Config, written once by a human:**

```toml
[visual]
enabled = true
setup = "yarn install --frozen-lockfile"   # run in the baseline worktree
start = "yarn dev -p $PORT"                 # defaults to [qa] start
ready = "/"
port = 3400                                 # head; baseline takes the next free port
env = { CMS_URL = "https://care.panel-ai.ok.k8s.dance/graphql" }
viewports = { desktop = "1440x900", phone = "390x844@2 touch" }
hide = ["#CybotCookiebotDialog", "nextjs-portal"]
block = ["*googletagmanager*"]
tolerance_px = 2
```

**Per task, written by the specifier into `qa/<task>.md`** as a fenced `visual` block. The specifier already owns `qa/**`.

```
route: /donate
selector: iframe[name=donorbox]
scroll: true
wait: iframe[src*=donorbox]
styles: border-radius, overflow, width
inside: .block-column
unchanged: x-centre
must_not_change: header, main h1
symptom: the teal bar's top-left corner is round and its top-right corner is square
```

A task with no `visual` block skips the check and prints `visual: no block in qa/<task>.md; skipped`. `roles/specifier.md` and `roles/critic.md` gain one sentence each: write the block when the task changes what a user sees; bounce a spec that changes what a user sees without one.

**`marestail visual capture [task]`,** usable by hand and by the gate. For the baseline worktree and for HEAD, per viewport: start the app, block the listed URLs, hide the listed selectors, switch off animations and transitions, do one uncaptured warm-up load, open the route, scroll to the selector, wait for it and for `wait`, wait until two bounding boxes in a row agree, then write to `.marestail/runs/<task>/visual/<tree>/<viewport>/`:

- `element.png`, clipped to the selector with a 24px margin
- `viewport.png`
- `geometry.json`: the selector's box, the named computed styles, the page's `scrollWidth` and `clientWidth`, the `inside` element's box, the boxes of the `must_not_change` selectors, the siblings the element overlaps, page errors

Third-party frames are captured as they render. Nothing inside them is masked.

**A `visual` gate in the `qa` tier,** deterministic, no model. It fails with one finding each when, at HEAD:

- the selector is missing in either tree
- the page scrolls sideways and did not at base
- the element is not within its `inside` element
- the element overlaps a sibling it did not overlap at base
- a measure named in `unchanged` moved by more than `tolerance_px`
- a `must_not_change` box moved by more than `tolerance_px`
- capturing twice gives two different geometry records (reported as `unstable`, with both)

Findings carry both values: `desktop: iframe[name=donorbox] is 1408px wide, .block-column is 572px (base: 440px)`. Both screenshots are kept for a human whatever the result.

Playwright becomes marestail's first npm dependency, under `marestail/js/`, installed by `marestail install` only when `[visual] enabled` is true.

## What must not change

- Targets with no `[visual]` section, and tasks with no `visual` block.
- The perf judge, its worktrees and its results. Generalise the shared worktree code; do not fork it.
- QA stays a worker. The gate failing is what stops the run, through QA's existing retry and repeat limit.
- Baseline worktrees and both apps are gone after the gate, on success, failure, timeout and Ctrl-C.
- No image, URL or `env` value is put into a commit.

## Tests

`tools/test-visual.py`, with a two-page static "app" served by a tiny Python server, where HEAD differs from base:

- a block whose element stays put passes and leaves six files per tree per viewport
- HEAD makes the element wider than its `inside` element: the gate fails with both widths in the finding
- HEAD makes the page scroll sideways: fails
- HEAD moves a `must_not_change` element: fails
- a selector missing at HEAD: fails, naming the tree
- no `visual` block: skipped with the message above
- `[visual]` absent: the `qa` tier is what it is today
- the baseline worktree and both servers are gone afterwards

## Done when

The new test passes, the other `tools/test-*.py` still pass, `marestail visual capture` works by hand on a repo with a `[visual]` section, and README documents the section, the block and where the pictures go.
