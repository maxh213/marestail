Feature: for a bug task, marestail captures the symptom on the real app before the specifier runs

  Pinned rules (every scenario relies on them):
  - Task lines: the first line of `tasks/<task>.md` starting `where: `, `symptom: ` or `selector: ` gives that value,
    trimmed. The run reproduces first only when `where` and `symptom` are both there, `[visual]` exists and its
    `enabled` is not false. Otherwise nothing below happens and every line printed and every prompt is today's.
  - When: once per run, before the first of `specifier` or `critic` in the run's window, before that step's `== ` line.
    A window with neither (`--from coder`) captures nothing. Bounces and retries in the same run reuse the files.
  - Capture: 013's base-tree capture at `perf.trees.start_commit` (the commit recorded at run start), through the same
    worktree, `setup`, `start`, `env`, `hide`, `block`, `ready` and timeouts, base port `port + 1`, every configured
    viewport, one warm-up load then one capture per viewport. HEAD is not captured. No geometry rule is judged.
    A `where` starting `http://` or `https://` is loaded as is: no worktree, no setup, no app.
  - Files, under `.marestail/runs/<task>/visual/reported/`: `app.log` (and `setup.log` when `setup` is set); per viewport
    `<vp>/viewport.png` and `<vp>/geometry.json` (013 keys; `selector` and `box` null without a `selector:` line);
    `<vp>/element.png` when a selector box was read (013's clip: box plus 24px), the element being the largest
    visible one matching the selector;
    `<vp>/markup.html` and, when that element is an `<iframe>`, `<vp>/frame.json`. A later `visual` gate run
    (which clears `visual/base` and `visual/head`) leaves `reported/` in place. Nothing captured is ever committed.
  - Markup: the opening tag of each ancestor from `body` down, then the element's outerHTML, one level of two-space
    indent per depth, attributes as the page has them; cut to 200 lines, then one line `… <n> more lines trimmed`.
  - Frame: the iframe's `src` resolved against the page URL, loaded alone in a fresh context whose viewport is the
    frame's box size; `frame.json` = `{url, width, height, largest: {name, box}}`, where `largest` is the visible
    element with the largest area inside its `body` (named as 013 names overlaps, box in that page's coordinates).
  - Stdout on success: `== reported (<where> at the start commit)`, then one line per viewport
    `reported <vp>: .marestail/runs/<task>/visual/reported/<vp>`.
  - Failure: the reason lines, each prefixed `reported: `, then `pipeline stopped before specifier: the bug could not be
    reproduced` (`before critic` when the window starts there); exit 1; no agent is invoked. Reasons: 013's texts (app,
    setup, tools, `<selector> not found at base`, `capture failed at base: ...`) and `<where> answered <status>` for a
    route whose response status is 400 or more.
  - Prompt `# Reported` section, in the specifier and critic prompts right after `# Task`:
      `Symptom: <symptom>`, `Where: <where> at <full start sha>`, `## Pictures` with one `- <path>` line per picture
      (per viewport in config order: `viewport.png`, then `element.png` when present), `## Geometry` with each
      viewport's `geometry.json` as `### <vp>` plus a ```json fence, `## Markup` with `### <vp>` plus an ```html fence
      of `markup.html` (or the one line `none: the task has no selector: line`), and, for a frame, `## Frame` with
      `### <vp>`, `URL: <url>` and `Loaded alone at <w>x<h>, the largest visible element is <name> at <x>,<y> <w>x<h>`.
      On a backend that cannot read images (014: `kilo`) `## Pictures` is the one line
      `The pictures could not be shown: <backend> cannot read images. Judge from the geometry and markup alone.`
      and the prompt contains no `.png`.
    The specifier's section ends with the line `Write ## Observed in your handoff: what the capture shows, then one
    line per cause the task states, marked holds or does not hold, with the measurement that says so.`
  - Observed: the specifier's handoff must contain a line `## Observed`. Without one the attempt is rejected with
    `missing ## Observed section in <handoff path>` and retried with that text under `# Why the work came back to you`.
    The critic prompt gets `# Observed`, right after `# Reported`, holding the body of `## Observed` from the newest
    specifier handoff (up to the next `## ` line).

  Background:
    Given the fixture of `tools/test-visual.py` (013) whose `donate.html` has, in place of the `#widget` div,
      `<iframe id="widget" src="/embed.html" style="width:440px;height:200px;border:0;border-radius:12px"></iframe>`
      and whose `.col` div has `class="col block-full"`
    And `embed.html` is `<html><body style="margin:0"><form id="form" style="width:425px;height:180px;background:teal">Give</form></body></html>`
    And `[visual]` has `viewports = { desktop = "1440x900" }`, `[practices] enabled = false`, `[perf] enabled = false`
    And `tasks/t.md` is
      """
      # Round the teal bar's corners
      Radius on an iframe does not clip Donorbox's nested document. Make the widget fill the donate column.
      where: /donate.html
      symptom: the teal bar's top-left corner is round and its top-right corner is square
      selector: #widget
      """
    And the capture wrapper and `STUB_PLAN` of 014's Background, and `tools/stub-claude specify` gains the word
      `observed`, which appends `## Observed\nthe frame is 440px, the form inside is 425px\n- radius does not clip: does not hold, left corner round at 434,100\n` to the handoff

  Scenario: the symptom is captured before the specifier and shown to it
    Given the plan `specify observed`, `judge PASS`
    When I run `marestail run tasks/t.md --to critic --auto`
    Then stdout contains `== reported (/donate.html at the start commit)` then
      `reported desktop: .marestail/runs/t/visual/reported/desktop` before `== specifier (`
    And `reported/desktop/viewport.png`, `element.png`, `geometry.json`, `markup.html` and `frame.json` exist,
      with modification times earlier than `$PROMPTS/01.txt`
    And `reported/desktop/geometry.json` has `box` `{"x": 434, "y": 100, "width": 440, "height": 200}`
    And `$PROMPTS/01.txt` has `# Reported` after `# Task`, the lines
      `Symptom: the teal bar's top-left corner is round and its top-right corner is square`,
      `- .marestail/runs/t/visual/reported/desktop/viewport.png`, `- .marestail/runs/t/visual/reported/desktop/element.png`,
      `    <div class="col block-full" style="width:572px;margin:0 auto">`,
      `URL: http://localhost:3401/embed.html` and
      `Loaded alone at 440x200, the largest visible element is form#form at 0,0 425x180`
    And the run exits 0 and `git status --porcelain` is empty and no commit touches `.marestail/`

  Scenario: the critic sees the capture and the specifier's Observed
    Given the plan `specify observed`, `judge PASS`
    When I run `marestail run tasks/t.md --to critic --auto`
    Then `$PROMPTS/02.txt` starts `You are the critic.` and has `# Reported` then `# Observed`, holding
      `- radius does not clip: does not hold, left corner round at 434,100`
    And its `# Reported` has no line starting `Write ## Observed`

  Scenario: a specifier handoff without Observed is rejected and retried
    Given the plan `specify`, `specify observed`, `judge PASS`
    When I run `marestail run tasks/t.md --to critic --auto --retries 2`
    Then stdout contains `missing ## Observed section in .marestail/handoffs/t/01-specifier.md`
    And `$PROMPTS/02.txt` starts `You are the specifier.` and its `# Why the work came back to you` contains that line
    And the capture ran once: stdout contains `== reported (` once
    And the run exits 0

  Scenario: a route that 404s stops the run before the specifier
    Given `tasks/t.md` says `where: /nope.html`
    When I run `marestail run tasks/t.md --to critic --auto`
    Then stdout contains `reported: /nope.html answered 404` then `pipeline stopped before specifier: the bug could not be reproduced`
    And the run exits 1, `$PROMPTS` is empty and `git log` gained no commit

  Scenario Outline: other capture failures also stop the run
    Given <change>
    When I run `marestail run tasks/t.md --to critic --auto`
    Then stdout contains `<line>` then `pipeline stopped before specifier: the bug could not be reproduced`
    And the run exits 1 and `$PROMPTS` is empty

    Examples:
      | change                                                   | line                                                         |
      | `tasks/t.md` says `selector: #gone`                      | reported: desktop: #gone not found at base                   |
      | `[visual] start = "exit 7"`                              | reported: base: app exited with 7 before answering           |
      | PATH without `node`                                      | reported: node not found on PATH; run marestail install      |

  Scenario: no selector line captures the viewport only
    Given `tasks/t.md` has no `selector:` line and the plan `specify observed`, `judge PASS`
    When I run `marestail run tasks/t.md --to critic --auto`
    Then `reported/desktop/viewport.png` and `geometry.json` exist and `element.png`, `markup.html`, `frame.json` do not
    And `$PROMPTS/01.txt` contains `none: the task has no selector: line` and no `## Frame`

  Scenario: a full URL is captured without starting the app
    Given `[visual] start = "exit 7"` and a server I started on port 3500 serving the fixture
    And `tasks/t.md` says `where: http://127.0.0.1:3500/donate.html`
    When I run `marestail run tasks/t.md --to critic --auto` with the plan `specify observed`, `judge PASS`
    Then stdout contains `== reported (http://127.0.0.1:3500/donate.html at the start commit)` and the run exits 0
    And `reported/app.log` does not exist

  Scenario: a backend that cannot read images still gets geometry and markup
    Given the plan `specify observed`, `judge PASS`
    When I run `marestail run tasks/t.md --to critic --auto --agent kilo --model x`
    Then `$PROMPTS/01.txt` contains
      `The pictures could not be shown: kilo cannot read images. Judge from the geometry and markup alone.`,
      `"width": 440` and `form#form at 0,0 425x180`, and no `.png`

  Scenario: a later visual gate run keeps the reported capture
    Given a run that captured `reported/`
    When I run `MARESTAIL_TASK=t marestail gate --tier qa --only visual`
    Then `.marestail/runs/t/visual/reported/desktop/viewport.png` still exists

  Scenario Outline: tasks and targets outside the rule start as today
    Given <setup> and the plan `specify`, `judge PASS`
    When I run `marestail run tasks/t.md --to critic --auto`
    Then stdout has no line starting `== reported` or `reported`
    And the specifier and critic prompts equal those built at the base commit for the same fixture, apart from the one
      sentence each that this task adds to `roles/specifier.md` and `roles/critic.md`
    And a specifier handoff without `## Observed` is accepted

    Examples:
      | setup                                              |
      | `tasks/t.md` without the `where:` line             |
      | `tasks/t.md` without the `symptom:` line           |
      | no `[visual]` section in `marestail.toml`          |
      | `[visual] enabled = false`                         |

  Scenario: a window that skips the specifier and critic captures nothing
    Given the plan `code`
    When I run `marestail run tasks/t.md --from coder --to coder --auto`
    Then stdout has no line starting `== reported` and `.marestail/runs/t/visual/reported` does not exist

  Scenario: roles, task docs and README say so
    Then `roles/critic.md` contains `Bounce a spec whose cause is not supported by the capture, or whose scenarios model the page differently from the captured markup.`
    And `roles/specifier.md` contains `A spec that models a layout takes it from the captured markup in # Reported.`
    And `tasks/README.md` and `templates/tasks-README.md` both contain `where: /donate.html` and
      `symptom: the teal bar's top-left corner is round and its top-right corner is square` and say a bug task should carry them
    And the README section `## Pipeline` contains `reproduced first`
    And `tools/test-reproduce-first.py` exits 0 with last line `reproduce first ok`, and every other `tools/test-*.py`
      exits as it does at the base commit (`tools/test-perf.py` still ends `verdict-commit-files: '' != 'perf/bench_x.py'`)
